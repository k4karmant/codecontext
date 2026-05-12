# codecontext/config.py — all tuneable constants and settings.
# Change values here; never hard-code them in business logic.

OLLAMA_BASE_URL: str = "http://localhost:11434"
OLLAMA_MODEL: str = "gemma4:e2b"
OLLAMA_TIMEOUT: int = 120  # seconds per request

CODECONTEXT_DIR: str = ".codecontext"
DB_FILENAME: str = "index.db"

WEB_HOST: str = "localhost"
WEB_PORT: int = 7842

# Maximum functions bundled into a single Ollama call.
# Tuned for gemma3:2b's ~8 k-token context window.
FUNCTION_BATCH_SIZE: int = 5

# Extension → language name used throughout the project.
EXTENSION_LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
}

# Directories that are never walked during indexing.
SKIP_DIRS: set[str] = {
    ".git",
    ".codecontext",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "env",
    ".env",
    ".uv",
    ".uv-cache",
    ".pytest_cache",
    ".mypy_cache",
    "build",
    "dist",
    ".idea",
    ".vscode",
    "target",
    "out",
    ".gradle",
}
