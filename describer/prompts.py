"""
codecontext/describer/prompts.py

All LLM prompt templates.  One constant per description level.
Import and format these — never hard-code prompts in business logic.
"""

# ---------------------------------------------------------------------------
# Level 1 — Batch of functions / methods
# ---------------------------------------------------------------------------
FUNCTION_PROMPT = """\
You are a senior software engineer writing concise technical documentation.
Below are {count} function(s) from a {language} codebase.
For EACH function, write a single focused paragraph (2–4 sentences) describing:
  • What it does
  • Its inputs and outputs
  • Any important side effects or exceptions

Return EXACTLY {count} descriptions, separated by the delimiter:
<<<NEXT>>>

Do NOT include function names, numbering, or any other text — only the descriptions.

FUNCTIONS:
{functions_block}
"""

# ---------------------------------------------------------------------------
# Level 2 — File
# ---------------------------------------------------------------------------
FILE_PROMPT = """\
You are a senior software engineer writing concise technical documentation.
Given the descriptions of every function/class in a {language} file, plus the \
call relationships between them, write a single focused paragraph (3–5 sentences) that describes:
  • The primary responsibility of this file/module
  • How the major functions/classes relate to and depend on each other
  • What the module exposes to its callers (its public surface)

Do NOT list function names as a bullet list. Write flowing prose.

FILE: {file_path}

CHILD DESCRIPTIONS:
{children_block}

CALL RELATIONSHIPS:
{edges_block}

IMPORTS:
{imports_block}
"""

# ---------------------------------------------------------------------------
# Level 3 — Package
# ---------------------------------------------------------------------------
PACKAGE_PROMPT = """\
You are a senior software engineer writing concise technical documentation.
Given the descriptions of every file in a package/directory, plus inter-file \
import relationships, write a single focused paragraph (3–5 sentences) that describes:
  • The role of this package within the overall system
  • The internal structure and how files collaborate
  • The public surface exposed to other packages

Do NOT list file names as a bullet list. Write flowing prose.

PACKAGE: {package_path}

FILE DESCRIPTIONS:
{files_block}

INTER-FILE IMPORTS:
{imports_block}
"""

# ---------------------------------------------------------------------------
# Level 4 — Project
# ---------------------------------------------------------------------------
PROJECT_PROMPT = """\
You are a senior software engineer writing concise technical documentation.
Given the descriptions of every package in a project, write a high-level \
system map paragraph (4–6 sentences) that describes:
  • What the overall system does
  • The major architectural boundaries and their responsibilities
  • How the packages relate to each other
  • The primary entry points or public APIs

Do NOT list package names as a bullet list. Write flowing prose.

PACKAGE DESCRIPTIONS:
{packages_block}
"""
