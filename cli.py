"""
codecontext/cli.py

Typer CLI entry point.  Registered as ``codecontext`` in pyproject.toml.

Commands:
    index   — parse and describe a project (full or incremental)
    lookup  — find a symbol and show its description + staleness
    status  — show project-wide staleness summary
    view    — launch the web visualizer
    refresh — re-describe a specific node by ID
"""
from __future__ import annotations

import os
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from codecontext.config import (
    CODECONTEXT_DIR, DB_FILENAME, WEB_HOST, WEB_PORT,
    EXTENSION_LANGUAGE_MAP,
)

app = typer.Typer(
    name="codecontext",
    help="Local semantic index for codebases.",
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db_path(project_root: Path) -> Path:
    return project_root / CODECONTEXT_DIR / DB_FILENAME


def _open_db(project_root: Path):
    from codecontext.storage.db import Database
    db_path = _db_path(project_root)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return Database(db_path)


def _find_project_root(start: Path = Path.cwd()) -> Optional[Path]:
    """Walk up from *start* to find the nearest .codecontext directory."""
    for parent in [start, *start.parents]:
        if (parent / CODECONTEXT_DIR).is_dir():
            return parent
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _status_dot(status: str) -> str:
    return {"fresh": "🟢", "stale": "🟡", "missing": "🔴"}.get(status, "⚪")


def _sanity_check(client) -> bool:
    """Run pre-flight checks against Ollama. Print results and return True if all pass."""
    from codecontext.config import OLLAMA_BASE_URL, OLLAMA_MODEL

    console.rule("[bold]🔍 Ollama Sanity Check[/bold]")

    # 1 — Server reachable?
    console.print(f"  Checking server at [bold]{OLLAMA_BASE_URL}[/bold] …", end=" ")
    if not client.is_available():
        console.print("[red]FAIL[/red]")
        console.print(
            "  [red]✗ Cannot reach Ollama.[/red]\n"
            "  Make sure Ollama is installed and running:\n"
            "    [bold]ollama serve[/bold]"
        )
        return False
    console.print("[green]OK[/green]")

    # 2 — Model pulled?
    console.print(f"  Checking model [bold]{OLLAMA_MODEL}[/bold] …", end=" ")
    available_models = client.list_models()
    model_ok = client.is_model_available()
    if not model_ok:
        console.print("[red]FAIL[/red]")
        if available_models:
            console.print(f"  [red]✗ Model '{OLLAMA_MODEL}' is not pulled.[/red]")
            console.print(f"  Available models: {', '.join(available_models)}")
        else:
            console.print("  [red]✗ No models found in Ollama.[/red]")
        console.print(f"  Run: [bold]ollama pull {OLLAMA_MODEL}[/bold]")
        return False
    console.print("[green]OK[/green]")

    # 3 — Quick smoke test (minimal prompt)
    console.print("  Smoke-testing generation …", end=" ")
    try:
        response = client.generate("Reply with the single word: ready", retries=1)
        if response:
            console.print(f"[green]OK[/green] [dim](model replied)[/dim]")
        else:
            console.print("[yellow]WARN[/yellow] [dim](empty response — may be slow)[/dim]")
    except Exception as exc:
        console.print(f"[yellow]WARN[/yellow] [dim]{exc}[/dim]")

    console.rule("[green]✓ All checks passed[/green]")
    return True


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------

@app.command()
def index(
    project_path: Path = typer.Argument(..., help="Path to the project root to index."),
    incremental: bool = typer.Option(False, "--incremental", "-i", help="Only re-index changed files."),
    no_llm: bool = typer.Option(False, "--no-llm", help="Parse only — skip Ollama description generation."),
) -> None:
    """Parse a project and generate semantic descriptions with Ollama."""
    from codecontext.storage.hasher import hash_file
    from codecontext.extractor.ast_walker import walk_project, detect_changed_files
    from codecontext.extractor.call_graph import resolve_edges, drop_external_edges
    from codecontext.describer.ollama_client import OllamaClient
    from codecontext.describer.summarizer import run_full_summarization

    project_root = project_path.resolve()
    if not project_root.is_dir():
        console.print(f"[red]Error:[/red] {project_root} is not a directory.")
        raise typer.Exit(1)

    console.print(Panel(
        f"[bold]CodeContext Index[/bold]\n"
        f"Project: [cyan]{project_root}[/cyan]\n"
        f"Mode:    {'[yellow]incremental[/yellow]' if incremental else '[green]full[/green]'}\n"
        f"LLM:     {'[red]disabled (--no-llm)[/red]' if no_llm else '[green]enabled[/green]'}",
        expand=False,
    ))

    # ── Sanity check ─────────────────────────────────────────────────────────
    client = OllamaClient()
    if not no_llm:
        if not _sanity_check(client):
            raise typer.Exit(1)
    else:
        console.print("[dim]Skipping Ollama sanity check (--no-llm).[/dim]")

    with _open_db(project_root) as db:
        db.set_meta("project_root", str(project_root))

        # ── Incremental detection ────────────────────────────────────────────
        changed_files: set[str] | None = None
        if incremental:
            console.rule("[bold]📂 Detecting Changed Files[/bold]")
            changed_files = detect_changed_files(project_root, db)
            if not changed_files:
                console.print("[green]✓ Everything is up to date — nothing to re-index.[/green]")
                return
            console.print(f"  [cyan]{len(changed_files)} changed file(s):[/cyan]")
            for f in sorted(changed_files):
                console.print(f"    • {f}")

        # ── Project root node ────────────────────────────────────────────────
        project_id = "project::root"
        if not db.get_node(project_id):
            db.upsert_node({
                "id": project_id,
                "name": project_root.name,
                "kind": "project",
                "language": None,
                "file_path": None,
                "line_start": None,
                "line_end": None,
                "source_hash": "",
                "description": None,
                "annotation": None,
                "parent_id": None,
                "status": "missing",
                "updated_at": _now_iso(),
            })

        # ── Parse ────────────────────────────────────────────────────────────
        console.rule("[bold]📄 Parsing Source Files[/bold]")
        results = walk_project(project_root, changed_files)
        console.print(f"  Found [bold]{len(results)}[/bold] file(s) to parse.")

        packages_seen: dict[str, str] = {}
        total_nodes = 0
        total_edges = 0
        new_node_count = 0

        for rel_path, language, parse_result in results:
            n = len(parse_result.nodes)
            e = len(parse_result.edges)
            total_nodes += n
            total_edges += e
            console.print(
                f"  [dim]parsed[/dim] [cyan]{rel_path}[/cyan] "
                f"[dim]({language}, {n} nodes, {e} edges)[/dim]"
            )

            pkg_dir = str(Path(rel_path).parent)
            if pkg_dir not in packages_seen:
                pkg_id = f"package::{pkg_dir}"
                if not db.get_node(pkg_id):
                    db.upsert_node({
                        "id": pkg_id,
                        "name": pkg_dir,
                        "kind": "package",
                        "language": None,
                        "file_path": None,
                        "line_start": None,
                        "line_end": None,
                        "source_hash": "",
                        "description": None,
                        "annotation": None,
                        "parent_id": project_id,
                        "status": "missing",
                        "updated_at": _now_iso(),
                    })
                packages_seen[pkg_dir] = pkg_id

            file_id = f"file::{rel_path}"
            pkg_id = packages_seen[pkg_dir]
            file_hash = hash_file(project_root / rel_path)
            if not db.get_node(file_id):
                db.upsert_node({
                    "id": file_id,
                    "name": Path(rel_path).name,
                    "kind": "file",
                    "language": language,
                    "file_path": rel_path,
                    "line_start": None,
                    "line_end": None,
                    "source_hash": file_hash,
                    "description": None,
                    "annotation": None,
                    "parent_id": pkg_id,
                    "status": "missing",
                    "updated_at": _now_iso(),
                })

            if incremental:
                db.delete_edges_for_file(rel_path)

            for node in parse_result.nodes:
                # Smart upsert: if the node already exists with the same hash
                # and is fresh, preserve its description — don't overwrite.
                existing = db.get_node(node["id"])
                if (
                    incremental
                    and existing
                    and existing["source_hash"] == node["source_hash"]
                    and existing["status"] == "fresh"
                ):
                    continue  # unchanged node — keep existing description
                db.upsert_node(node)
                new_node_count += 1
            for from_id, to_id, kind in parse_result.edges:
                db.insert_edge(from_id, to_id, kind)
            db.upsert_file(rel_path, language, file_hash)

        console.print(
            f"  [green]✓[/green] Parsed [bold]{total_nodes}[/bold] nodes, [bold]{total_edges}[/bold] raw edges"
            + (f" ([bold]{new_node_count}[/bold] new/changed)" if incremental else "")
            + "."
        )

        # ── Call graph ───────────────────────────────────────────────────────
        console.rule("[bold]🔗 Resolving Call Graph[/bold]")
        resolve_edges(db)
        drop_external_edges(db)
        stats = db.get_stats()
        console.print(
            f"  [green]✓[/green] [bold]{stats['total_nodes']}[/bold] nodes  "
            f"[bold]{stats['total_edges']}[/bold] resolved edges  "
            f"[bold]{stats['total_files']}[/bold] files"
        )

        # ── LLM descriptions ─────────────────────────────────────────────────
        if not no_llm:
            console.rule("[bold]🤖 Generating Descriptions (Ollama)[/bold]")
            if incremental:
                # Only describe new/changed nodes and bubble up their ancestors.
                affected_rel_paths = changed_files or {r for r, _, _ in results}
                from codecontext.describer.summarizer import run_incremental_summarization
                run_incremental_summarization(db, project_root, client, affected_rel_paths)
            else:
                run_full_summarization(db, project_root, client)
            console.rule("[bold green]✓ Indexing Complete[/bold green]")
        else:
            console.print("\n[yellow]Skipped LLM descriptions (--no-llm).[/yellow]")
            console.rule("[bold green]✓ Parsing Complete[/bold green]")

        db.set_meta("last_full_index", _now_iso())


# ---------------------------------------------------------------------------
# describe
# ---------------------------------------------------------------------------

@app.command()
def describe(
    project_path: Path = typer.Argument(..., help="Path to the project root."),
    only_missing: bool = typer.Option(
        True, "--only-missing/--all",
        help="Only describe nodes that have no description yet (default). "
             "Use --all to re-describe every node.",
    ),
) -> None:
    """Run Ollama descriptions on an already-parsed index.

    Use this after [bold]codecontext index --no-llm[/bold] to add descriptions
    in a separate step, or to fill in descriptions for any nodes still missing them.

    Examples:

      # Step 1 — fast parse, no LLM
      codecontext index ./myproject --no-llm

      # Step 2 — generate descriptions whenever ready
      codecontext describe ./myproject

      # Re-describe everything (ignores existing descriptions)
      codecontext describe ./myproject --all
    """
    from codecontext.describer.ollama_client import OllamaClient
    from codecontext.describer.summarizer import run_full_summarization, run_incremental_summarization

    root = project_path.resolve()
    db_path = _db_path(root)
    if not db_path.exists():
        console.print(
            "[red]Error:[/red] No index found. "
            "Run [bold]codecontext index --no-llm[/bold] first to build the tree."
        )
        raise typer.Exit(1)

    client = OllamaClient()
    if not _sanity_check(client):
        raise typer.Exit(1)

    with _open_db(root) as db:
        stats = db.get_stats()
        missing_count = stats["by_status"].get("missing", 0)

        if only_missing and missing_count == 0:
            console.print("[green]✓ All nodes already have descriptions.[/green]")
            console.print("  Use [bold]codecontext describe --all[/bold] to force re-description.")
            return

        if only_missing:
            console.print(
                f"  [cyan]{missing_count}[/cyan] node(s) without descriptions — "
                f"[dim]{stats['by_status'].get('fresh', 0)} already fresh, skipping those.[/dim]"
            )
            console.rule("[bold]🤖 Generating Missing Descriptions (Ollama)[/bold]")
            # Collect all affected file paths from missing nodes
            missing_nodes = [n for n in db.get_all_nodes() if n["status"] == "missing" and n.get("file_path")]
            affected_paths = {n["file_path"] for n in missing_nodes}
            run_incremental_summarization(db, root, client, affected_paths)
        else:
            console.print(f"  [cyan]{stats['total_nodes']}[/cyan] total nodes — re-describing all.")
            console.rule("[bold]🤖 Re-Generating All Descriptions (Ollama)[/bold]")
            run_full_summarization(db, root, client)

    console.rule("[bold green]✓ Descriptions Complete[/bold green]")


# ---------------------------------------------------------------------------
# lookup
# ---------------------------------------------------------------------------

@app.command()
def lookup(
    name: str = typer.Argument(..., help="Symbol name to look up (case-insensitive)."),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="File path hint to disambiguate."),
    project: Optional[Path] = typer.Option(None, "--project", "-p", help="Project root (auto-detected if omitted)."),
) -> None:
    """Look up a symbol and show its description and staleness status."""
    from codecontext.query.lookup import lookup_symbol

    root = (project.resolve() if project else None) or _find_project_root()
    if not root:
        console.print("[red]Error:[/red] Cannot find .codecontext/ directory. Run [bold]codecontext index[/bold] first.")
        raise typer.Exit(1)

    with _open_db(root) as db:
        results = lookup_symbol(db, name, root, file_hint=file)

    if not results:
        console.print(f"[yellow]No symbol named '{name}' found in the index.[/yellow]")
        return

    for node in results:
        status = node.get("live_status", node.get("status", "missing"))
        dot = _status_dot(status)

        panel_content = (
            f"[bold]{node['name']}[/bold]  {dot} [italic]{status}[/italic]\n"
            f"Kind:     {node['kind']}\n"
            f"File:     {node.get('file_path', 'N/A')}\n"
            f"Lines:    {node.get('line_start')}–{node.get('line_end')}\n"
            f"Language: {node.get('language', 'N/A')}\n"
        )

        if status == "stale":
            panel_content += "\n[yellow]⚠ Source has changed since last index. Re-run codecontext index --incremental.[/yellow]\n"

        desc = node.get("annotation") or node.get("description")
        if desc:
            panel_content += f"\n[bold]Description[/bold]\n{desc}\n"
        else:
            panel_content += "\n[dim]No description yet — run codecontext index to generate one.[/dim]\n"

        calls = node.get("calls", [])
        called_by = node.get("called_by", [])

        if called_by:
            panel_content += "\n[bold]Called by[/bold]\n" + "\n".join(
                f"  → {e['from_node'].split('::')[-1]}" for e in called_by
            ) + "\n"

        if calls:
            panel_content += "\n[bold]Calls[/bold]\n" + "\n".join(
                f"  → {e['to_node'].split('::')[-1]}" for e in calls
            ) + "\n"

        console.print(Panel(panel_content, title=f"[cyan]{node['id']}[/cyan]", expand=False))


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@app.command()
def status(
    project_path: Path = typer.Argument(..., help="Path to the project root."),
) -> None:
    """Show project-wide staleness summary."""
    from codecontext.query.lookup import check_project_staleness

    root = project_path.resolve()
    with _open_db(root) as db:
        summary = check_project_staleness(db, root)
        db_stats = db.get_stats()

    table = Table(title="CodeContext Status", box=box.ROUNDED)
    table.add_column("Status", style="bold")
    table.add_column("Count", justify="right")

    table.add_row("🟢 Fresh",   str(summary["fresh"]))
    table.add_row("🟡 Stale",   str(summary["stale"]))
    table.add_row("🔴 Missing", str(summary["missing"]))
    table.add_row("─" * 10, "─" * 6)
    table.add_row("Total edges", str(db_stats["total_edges"]))
    table.add_row("Total files", str(db_stats["total_files"]))
    console.print(table)

    if summary["stale_nodes"]:
        console.print("\n[yellow]Stale nodes:[/yellow]")
        for node in summary["stale_nodes"][:20]:
            console.print(f"  🟡 {node['id']}")
        if len(summary["stale_nodes"]) > 20:
            console.print(f"  … and {len(summary['stale_nodes']) - 20} more.")


# ---------------------------------------------------------------------------
# view
# ---------------------------------------------------------------------------

@app.command()
def view(
    project_path: Path = typer.Argument(..., help="Path to the project root."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Start server without opening browser."),
) -> None:
    """Launch the web visualizer at http://localhost:7842."""
    from codecontext.web.server import start_server

    root = project_path.resolve()
    db_path = _db_path(root)
    if not db_path.exists():
        console.print("[red]Error:[/red] No index found. Run [bold]codecontext index[/bold] first.")
        raise typer.Exit(1)

    url = f"http://{WEB_HOST}:{WEB_PORT}"
    console.print(f"[green]Starting CodeContext visualizer at {url}[/green]")
    console.print("Press [bold]Ctrl+C[/bold] to stop.")

    if not no_browser:
        webbrowser.open(url)

    start_server(db_path, host=WEB_HOST, port=WEB_PORT)


# ---------------------------------------------------------------------------
# mcp
# ---------------------------------------------------------------------------

@app.command()
def mcp(
    project_path: Path = typer.Argument(..., help="Path to the project root."),
) -> None:
    """Start the Model Context Protocol (MCP) server for this project via stdio."""
    from codecontext.mcp_server import start_mcp_server

    root = project_path.resolve()
    db_path = _db_path(root)
    if not db_path.exists():
        console.print("[red]Error:[/red] No index found. Run [bold]codecontext index[/bold] first.")
        raise typer.Exit(1)
        
    # Standard MCP operates via stdio so we don't print any greeting text 
    # to stdout, otherwise we'd break the JSON-RPC stream.
    start_mcp_server(root)


# ---------------------------------------------------------------------------
# refresh
# ---------------------------------------------------------------------------

@app.command()
def refresh(
    node_id: str = typer.Argument(..., help="Full node ID to re-describe (e.g. function::src/auth.py::login)."),
    project: Optional[Path] = typer.Option(None, "--project", "-p", help="Project root (auto-detected if omitted)."),
) -> None:
    """Re-generate the description for a specific node."""
    from codecontext.describer.ollama_client import OllamaClient, OllamaError
    from codecontext.storage.hasher import hash_lines

    root = (project.resolve() if project else None) or _find_project_root()
    if not root:
        console.print("[red]Error:[/red] Cannot find .codecontext/ directory.")
        raise typer.Exit(1)

    client = OllamaClient()
    if not client.is_available():
        console.print("[red]Error:[/red] Ollama is not running.")
        raise typer.Exit(1)

    with _open_db(root) as db:
        node = db.get_node(node_id)
        if not node:
            console.print(f"[red]Error:[/red] Node '{node_id}' not found in index.")
            raise typer.Exit(1)

        kind = node["kind"]
        if kind == "function":
            from codecontext.describer.summarizer import describe_functions
            describe_functions(db, [node], root, client)
        elif kind == "class":
            from codecontext.describer.summarizer import describe_class
            describe_class(db, node, root, client)
        elif kind == "file":
            from codecontext.describer.summarizer import describe_file
            describe_file(db, node, root, client)
        elif kind == "package":
            from codecontext.describer.summarizer import describe_package
            describe_package(db, node, client)
        elif kind == "project":
            from codecontext.describer.summarizer import describe_project
            describe_project(db, node, client)
        else:
            console.print(f"[yellow]Unknown kind '{kind}' — cannot refresh.[/yellow]")
            raise typer.Exit(1)

    console.print(f"[green]✓ Refreshed:[/green] {node_id}")


if __name__ == "__main__":
    app()
