# CodeContext

**CodeContext** is a local CLI tool that builds a **semantic index** of a codebase. It parses your source code, generates natural-language descriptions of every function, class, and file using a local LLM, and stores them in a hierarchical tree.

The goal is to let AI coding assistants (like Claude, Cursor, or Nexus) understand your project quickly by reading the semantic index instead of raw, unparsed source code.

## Key Features

- **100% Local**: No external API calls, no cloud storage. Uses **Ollama** and local SQLite.
- **Staleness-Aware**: Real-time hashing ensures descriptions are never served if the source code has been modified since it was indexed.
- **Hierarchical Summarization**: Bottom-up description generation (Function → Class → File → Package → Project) keeps LLM prompts small and token generation fast.
- **Incremental Indexing**: Only parses and re-describes files that actually changed.
- **Native MCP Server**: Exposes the semantic index directly to AI agents via the Anthropic Model Context Protocol.
- **Web Visualizer**: A zero-dependency, local UI to browse your semantic graph.

---

## Installation

### Prerequisites
1. **Python 3.11+**
2. **Ollama** installed and running locally (`http://localhost:11434`)
3. A coding model pulled (e.g., `ollama pull qwen2.5-coder:1.5b` or `ollama pull gemma4:e2b`)

### Setup
Clone the repository and install it globally:
```bash
git clone <repo>
cd codecontext
pip install -e .
```

---

## Usage Workflow

CodeContext supports a lightning-fast two-phase workflow, ensuring you never have to wait on the LLM to get a searchable tree.

### 1. Build the Tree (Fast)
Parse the entire project, resolve call graphs, and build the node tree without waiting for LLM descriptions.
```bash
codecontext index ./myproject --no-llm
```

### 2. Generate Semantic Descriptions (Background)
Run the local LLM only on nodes that don't have descriptions yet.
```bash
codecontext describe ./myproject
```

### 3. Incremental Updates
When you modify files or add new code, just run an incremental index. It will detect file hashes, parse only what changed, and re-describe just the affected nodes.
```bash
codecontext index ./myproject --incremental
```

---

## Tooling & Commands

#### `lookup` - Instant Semantic Search
Look up any symbol in your codebase and get its description, call graph, and live staleness status.
```bash
codecontext lookup login
codecontext lookup AuthManager --file src/auth.py
```

#### `view` - Web Visualizer
Launch a local UI to visually explore your codebase's semantic tree.
```bash
codecontext view ./myproject
# Opens http://localhost:7842
```

#### `status` - Project Health
Check the staleness of your entire codebase at a glance.
```bash
codecontext status ./myproject
```

#### `refresh` - Manual Node Update
Force the LLM to re-generate the description for a single, specific node.
```bash
codecontext refresh function::src/auth/login.py::parse_token
```

---

## Native MCP Server (AI Integration)

CodeContext ships with a native **Model Context Protocol (MCP)** server via `stdio`, allowing AI agents to query the index directly. 

To give an AI agent (like Claude Desktop or Nexus) access to the index, add the following to its MCP configuration:

```json
{
  "mcpServers": {
    "codecontext": {
      "command": "codecontext",
      "args": [
        "mcp",
        "D:/path/to/your/project"
      ]
    }
  }
}
```

This exposes two tools to the AI:
1. `codecontext_lookup(symbol_name, file_hint)`: Semantic code search without reading raw files.
2. `codecontext_status()`: Project-wide node staleness statistics.

---

## Configuration & Tuning

Configuration constants are found in `codecontext/config.py`. 

For extreme performance, it is highly recommended to:
1. Change `OLLAMA_MODEL` to `qwen2.5-coder:1.5b`.
2. Increase `FUNCTION_BATCH_SIZE` to `10` or `15`.
3. Force extreme conciseness in `codecontext/describer/prompts.py`.
