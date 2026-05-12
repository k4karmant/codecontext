"""
codecontext/describer/summarizer.py

Bottom-up description generation using Ollama.

Level 1 — Functions  (batched, FUNCTION_BATCH_SIZE per call)
Level 2 — Classes    (one call per class, from method descriptions)
Level 3 — Files      (one call per file, from child descriptions + edges)
Level 4 — Packages   (one call per package, from file descriptions)
Level 5 — Project    (single call, from package descriptions)
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from codecontext.config import FUNCTION_BATCH_SIZE
from codecontext.describer.ollama_client import OllamaClient, OllamaError
from codecontext.describer.prompts import (
    FUNCTION_PROMPT,
    FILE_PROMPT,
    PACKAGE_PROMPT,
    PROJECT_PROMPT,
)
from codecontext.storage.db import Database
from codecontext.storage.hasher import hash_lines

console = Console(stderr=False)

_DELIMITER = "<<<NEXT>>>"


def _log(msg: str) -> None:
    """Emit a timestamped live log line."""
    from datetime import datetime
    ts = datetime.now().strftime("%H:%M:%S")
    console.print(f"  [dim]{ts}[/dim] {msg}")


def _safe_generate(client: OllamaClient, prompt: str, label: str) -> str:
    """Call Ollama and return the response; return empty string on error."""
    try:
        return client.generate(prompt)
    except OllamaError as exc:
        console.print(f"  [yellow]⚠ Ollama error ({label}): {exc}[/yellow]")
        return ""


# ---------------------------------------------------------------------------
# Level 1 — Functions
# ---------------------------------------------------------------------------

def _build_function_block(nodes: list[dict], project_root: Path) -> str:
    """Format a batch of function nodes for the FUNCTION_PROMPT."""
    parts: list[str] = []
    for node in nodes:
        sig = f"{node['name']} (lines {node['line_start']}–{node['line_end']})"
        try:
            src_lines = (project_root / node["file_path"]).read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
            body = "\n".join(
                src_lines[max(0, node["line_start"] - 1): min(len(src_lines), node["line_end"])]
            )
        except OSError:
            body = "<source unavailable>"
        parts.append(f"### {sig}\n```\n{textwrap.shorten(body, 1500, placeholder='...')}\n```")
    return "\n\n".join(parts)


def describe_functions(
    db: Database,
    nodes: list[dict],
    project_root: Path,
    client: OllamaClient,
) -> None:
    """Generate and persist descriptions for a list of function/method nodes."""
    if not nodes:
        return

    total_batches = (len(nodes) + FUNCTION_BATCH_SIZE - 1) // FUNCTION_BATCH_SIZE
    for i in range(0, len(nodes), FUNCTION_BATCH_SIZE):
        batch = nodes[i: i + FUNCTION_BATCH_SIZE]
        batch_num = i // FUNCTION_BATCH_SIZE + 1
        names = ", ".join(n["name"] for n in batch)
        _log(f"[cyan]fn batch {batch_num}/{total_batches}[/cyan] → {names}")

        block = _build_function_block(batch, project_root)
        lang = batch[0].get("language", "unknown")
        prompt = FUNCTION_PROMPT.format(
            count=len(batch),
            language=lang,
            functions_block=block,
        )
        response = _safe_generate(client, prompt, f"functions batch {batch_num}")
        if not response:
            _log(f"[yellow]  ↳ no response for batch {batch_num}[/yellow]")
            continue

        # Split response by delimiter — one description per function in the batch
        parts = [p.strip() for p in response.split(_DELIMITER)]
        for j, node in enumerate(batch):
            desc = parts[j] if j < len(parts) else ""
            if desc:
                src_hash = hash_lines(
                    project_root / node["file_path"],
                    node["line_start"],
                    node["line_end"],
                )
                db.update_node_description(node["id"], desc, src_hash)
                _log(f"  [green]✓[/green] {node['name']}")
            else:
                _log(f"  [yellow]⚠ no description parsed for {node['name']}[/yellow]")


# ---------------------------------------------------------------------------
# Level 2 — Classes
# ---------------------------------------------------------------------------

def describe_class(
    db: Database,
    class_node: dict,
    project_root: Path,
    client: OllamaClient,
) -> None:
    """Generate a description for a class from its method descriptions."""
    children = db.get_children(class_node["id"])
    if not children:
        return

    _log(f"[cyan]class[/cyan] → {class_node['name']}")
    children_block = "\n".join(
        f"- {c['name']}: {c['description'] or '(no description)'}"
        for c in children
    )
    prompt = (
        f"You are a senior software engineer. Given the following methods of a "
        f"{class_node.get('language', 'code')} class named `{class_node['name']}`, "
        f"write a single focused paragraph (2–4 sentences) describing the class's "
        f"responsibility and design.\n\nMETHODS:\n{children_block}"
    )
    response = _safe_generate(client, prompt, f"class {class_node['name']}")
    if response:
        src_hash = hash_lines(
            project_root / class_node["file_path"],
            class_node["line_start"],
            class_node["line_end"],
        )
        db.update_node_description(class_node["id"], response, src_hash)
        _log(f"  [green]✓[/green] {class_node['name']}")


# ---------------------------------------------------------------------------
# Level 3 — Files
# ---------------------------------------------------------------------------

def describe_file(
    db: Database,
    file_node: dict,
    project_root: Path,
    client: OllamaClient,
) -> None:
    """Generate a description for a file node from its children's descriptions."""
    children = db.get_children(file_node["id"])
    if not children:
        return

    _log(f"[cyan]file[/cyan] → {file_node['file_path']}")

    children_block = "\n".join(
        f"- {c['name']} ({c['kind']}): {c['description'] or '(no description)'}"
        for c in children
    )

    # Build call edge summary
    edge_lines: list[str] = []
    for child in children:
        for edge in db.get_edges_from(child["id"]):
            if not edge["to_node"].startswith("external::"):
                edge_lines.append(f"  {child['name']} → {edge['to_node'].split('::')[-1]} ({edge['kind']})")
    edges_block = "\n".join(edge_lines) or "  (none)"

    # Import edges from the file node itself
    imports = [
        e["to_node"].replace("external::", "")
        for e in db.get_edges_from(file_node["id"])
        if e["to_node"].startswith("external::")
    ]
    imports_block = ", ".join(imports) or "(none)"

    lang = file_node.get("language") or children[0].get("language", "unknown")
    prompt = FILE_PROMPT.format(
        language=lang,
        file_path=file_node["file_path"],
        children_block=children_block,
        edges_block=edges_block,
        imports_block=imports_block,
    )
    response = _safe_generate(client, prompt, f"file {file_node['file_path']}")
    if response:
        db.update_node_description(file_node["id"], response, "")
        _log(f"  [green]✓[/green] {file_node['file_path']}")


# ---------------------------------------------------------------------------
# Level 4 — Packages
# ---------------------------------------------------------------------------

def describe_package(
    db: Database,
    package_node: dict,
    client: OllamaClient,
) -> None:
    """Generate a description for a package node from its file descriptions."""
    children = db.get_children(package_node["id"])
    if not children:
        return

    _log(f"[cyan]package[/cyan] → {package_node['name']}")

    files_block = "\n".join(
        f"- {c['file_path'] or c['name']}: {c['description'] or '(no description)'}"
        for c in children
    )
    imports_block = "(see individual file imports)"

    prompt = PACKAGE_PROMPT.format(
        package_path=package_node["name"],
        files_block=files_block,
        imports_block=imports_block,
    )
    response = _safe_generate(client, prompt, f"package {package_node['name']}")
    if response:
        db.update_node_description(package_node["id"], response, "")
        _log(f"  [green]✓[/green] package {package_node['name']}")


# ---------------------------------------------------------------------------
# Level 5 — Project
# ---------------------------------------------------------------------------

def describe_project(db: Database, project_node: dict, client: OllamaClient) -> None:
    """Generate the top-level project description from package descriptions."""
    _log("[cyan]project[/cyan] → generating top-level summary")
    packages = db.get_nodes_by_kind("package")
    if not packages:
        packages = db.get_nodes_by_kind("file")

    packages_block = "\n".join(
        f"- {p['name']}: {p['description'] or '(no description)'}"
        for p in packages
    )
    prompt = PROJECT_PROMPT.format(packages_block=packages_block)
    response = _safe_generate(client, prompt, "project")
    if response:
        db.update_node_description(project_node["id"], response, "")
        _log("  [green]✓[/green] project summary written")


# ---------------------------------------------------------------------------
# Orchestrators
# ---------------------------------------------------------------------------

def run_incremental_summarization(
    db: Database,
    project_root: Path,
    client: OllamaClient,
    affected_rel_paths: set[str],
) -> None:
    """Describe ONLY newly added / changed nodes and bubble up their ancestors.

    This is the incremental path:
      - Only nodes with ``status == 'missing'`` are described at Level 1/2.
      - Their parent file is always re-described (child list changed).
      - Their parent package and the project are re-described to stay coherent.

    Args:
        affected_rel_paths: rel_paths of files that were parsed this run.
    """
    all_nodes = db.get_all_nodes()
    by_id = {n["id"]: n for n in all_nodes}

    # --- Level 1: missing functions only ------------------------------------
    missing_fns = [
        n for n in all_nodes
        if n["kind"] == "function" and n["status"] == "missing"
    ]

    if missing_fns:
        console.rule(f"[bold cyan]Level 1 — New/Changed Functions ({len(missing_fns)})[/bold cyan]")
        files_with_fns: dict[str, list[dict]] = {}
        for fn in missing_fns:
            files_with_fns.setdefault(fn["file_path"], []).append(fn)
        for file_path, fns in files_with_fns.items():
            console.print(f"\n[bold]📄 {file_path}[/bold] — {len(fns)} new/changed function(s)")
            describe_functions(db, fns, project_root, client)

    # --- Level 2: classes whose children changed ----------------------------
    affected_class_ids: set[str] = set()
    for fn in missing_fns:
        parent = by_id.get(fn["parent_id"], {})
        if parent.get("kind") == "class":
            affected_class_ids.add(fn["parent_id"])

    # Also include any missing class nodes themselves
    missing_classes = [n for n in all_nodes if n["kind"] == "class" and n["status"] == "missing"]
    for cls in missing_classes:
        affected_class_ids.add(cls["id"])

    if affected_class_ids:
        console.rule(f"[bold cyan]Level 2 — Affected Classes ({len(affected_class_ids)})[/bold cyan]")
        for cls_id in affected_class_ids:
            cls_node = db.get_node(cls_id)
            if cls_node:
                describe_class(db, cls_node, project_root, client)

    # --- Level 3: all affected files ----------------------------------------
    console.rule(f"[bold cyan]Level 3 — Affected Files ({len(affected_rel_paths)})[/bold cyan]")
    affected_pkg_ids: set[str] = set()
    for rel_path in affected_rel_paths:
        file_node = db.get_node(f"file::{rel_path}")
        if file_node:
            describe_file(db, file_node, project_root, client)
            parent = by_id.get(file_node.get("parent_id", ""), {})
            if parent.get("kind") == "package":
                affected_pkg_ids.add(file_node["parent_id"])

    # --- Level 4: affected packages -----------------------------------------
    if affected_pkg_ids:
        console.rule(f"[bold cyan]Level 4 — Affected Packages ({len(affected_pkg_ids)})[/bold cyan]")
        for pkg_id in affected_pkg_ids:
            pkg_node = db.get_node(pkg_id)
            if pkg_node:
                describe_package(db, pkg_node, client)

    # --- Level 5: project (always refresh when anything changed) ------------
    console.rule("[bold cyan]Level 5 — Project[/bold cyan]")
    projects = db.get_nodes_by_kind("project")
    if projects:
        describe_project(db, projects[0], client)
    else:
        _log("[yellow]No project node found — skipping.[/yellow]")


def run_full_summarization(
    db: Database,
    project_root: Path,
    client: OllamaClient,
) -> None:
    """Run the complete bottom-up description pipeline.

    Order:
      1. All function/method nodes (batched per file)
      2. All class nodes
      3. All file nodes
      4. All package nodes
      5. The project node
    """
    all_nodes = db.get_all_nodes()
    by_kind: dict[str, list[dict]] = {}
    for node in all_nodes:
        by_kind.setdefault(node["kind"], []).append(node)

    functions = by_kind.get("function", [])
    classes   = by_kind.get("class", [])
    files     = by_kind.get("file", [])
    packages  = by_kind.get("package", [])
    projects  = by_kind.get("project", [])

    # ── Level 1 — Functions ────────────────────────────────────────────────
    console.rule(f"[bold cyan]Level 1 — Functions ({len(functions)} total)[/bold cyan]")
    files_with_fns: dict[str, list[dict]] = {}
    for fn in functions:
        files_with_fns.setdefault(fn["file_path"], []).append(fn)

    for file_path, fns in files_with_fns.items():
        console.print(f"\n[bold]📄 {file_path}[/bold] — {len(fns)} function(s)")
        describe_functions(db, fns, project_root, client)

    # ── Level 2 — Classes ─────────────────────────────────────────────────
    if classes:
        console.rule(f"[bold cyan]Level 2 — Classes ({len(classes)} total)[/bold cyan]")
        for cls in classes:
            describe_class(db, cls, project_root, client)

    # ── Level 3 — Files ───────────────────────────────────────────────────
    console.rule(f"[bold cyan]Level 3 — Files ({len(files)} total)[/bold cyan]")
    for file_node in files:
        describe_file(db, file_node, project_root, client)

    # ── Level 4 — Packages ────────────────────────────────────────────────
    if packages:
        console.rule(f"[bold cyan]Level 4 — Packages ({len(packages)} total)[/bold cyan]")
        for pkg in packages:
            describe_package(db, pkg, client)

    # ── Level 5 — Project ─────────────────────────────────────────────────
    console.rule("[bold cyan]Level 5 — Project[/bold cyan]")
    if projects:
        describe_project(db, projects[0], client)
    else:
        _log("[yellow]No project node found — skipping.[/yellow]")
