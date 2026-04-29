#!/usr/bin/env python3
"""Detect drift between code-referenced env vars and .env.example.

Scans the repo for references to environment variables (Python, JS/TS,
shell, docker-compose interpolation) and compares the result against the
keys documented in ``.env.example``. Exits non-zero with a markdown table
of any keys that are referenced in code but missing from ``.env.example``.

Usage::

    python3 scripts/check_env_example.py [--root .] [--env-file .env.example]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# Directories that should never be scanned (vendored code, build outputs).
SKIP_DIR_PARTS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
    "htmlcov",
    "dist",
    "build",
}
# Path prefixes (relative to root) that should also be skipped.
SKIP_PATH_PREFIXES = (
    "docs-site/site/",
    "docs-site/.venv/",
    "frontend/node_modules/",
    "frontend/dist/",
    "scripts/validate_coverage_predict.py",
)

PY_PATTERNS = [
    re.compile(r"""os\.environ\.get\(\s*['"]([A-Z_][A-Z0-9_]*)['"]"""),
    re.compile(r"""os\.getenv\(\s*['"]([A-Z_][A-Z0-9_]*)['"]"""),
    re.compile(r"""os\.environ\[\s*['"]([A-Z_][A-Z0-9_]*)['"]\s*\]"""),
    re.compile(r"""(?<!\.)environ\.get\(\s*['"]([A-Z_][A-Z0-9_]*)['"]"""),
]
JS_PATTERNS = [
    re.compile(r"""process\.env\.([A-Z_][A-Z0-9_]*)"""),
    re.compile(r"""process\.env\[\s*['"]([A-Z_][A-Z0-9_]*)['"]\s*\]"""),
    re.compile(r"""import\.meta\.env\.([A-Z_][A-Z0-9_]*)"""),
]
# docker-compose / Caddyfile / shell-style ${VAR}.  Only applied to
# specific filenames to avoid noise.
COMPOSE_PATTERN = re.compile(r"""\$\{([A-Z_][A-Z0-9_]*)(?::[-?][^}]*)?\}""")

PY_EXTS = {".py"}
JS_EXTS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
COMPOSE_FILENAMES = {
    "docker-compose.yml",
    "docker-compose.yaml",
    "docker-compose.override.yml",
    "Caddyfile",
}

# Names that look like env vars but are runtime/system-injected and should
# never be required in .env.example.
EXCLUDE = {
    # AWS CloudFormation / SAM pseudo-parameters
    "AWS",
    # POSIX / shell
    "HOME", "PATH", "PWD", "USER", "SHELL", "LANG", "LC_ALL", "LC_CTYPE",
    "TERM", "TZ", "TMPDIR", "HOSTNAME", "DEBUG", "EDITOR",
    # Python
    "PYTHONPATH", "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE",
    "VIRTUAL_ENV", "PYTEST_CURRENT_TEST",
    # AWS Lambda / runtime injected
    "AWS_LAMBDA_FUNCTION_NAME", "AWS_REGION", "AWS_DEFAULT_REGION",
    "AWS_LAMBDA_RUNTIME_API", "AWS_EXECUTION_ENV",
    "AWS_LAMBDA_LOG_GROUP_NAME", "AWS_LAMBDA_LOG_STREAM_NAME",
    "AWS_LAMBDA_FUNCTION_VERSION", "AWS_LAMBDA_FUNCTION_MEMORY_SIZE",
    "AWS_SESSION_TOKEN", "AWS_PROFILE",
    # Railway-injected
    "RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID",
    "RAILWAY_DEPLOYMENT_ID", "RAILWAY_GIT_COMMIT_SHA",
    "RAILWAY_PUBLIC_DOMAIN", "RAILWAY_PRIVATE_DOMAIN",
    "RAILWAY_STATIC_URL", "RAILWAY_REPLICA_ID",
    # CI
    "CI", "GITHUB_ACTIONS", "GITHUB_RUN_ID", "GITHUB_SHA", "GITHUB_TOKEN",
    "GITHUB_REPOSITORY", "GITHUB_REF", "GITHUB_WORKFLOW",
    "GITHUB_EVENT_NAME", "GITHUB_STEP_SUMMARY", "GITHUB_OUTPUT",
    "RUNNER_OS", "RUNNER_TEMP",
    # Node
    "NODE_ENV", "NODE_OPTIONS",
    # Test fixtures / load testing harness (not deployed)
    "TEST_API_BASE", "TEST_API_KEY", "TEST_DEMO_KEYS",
    "LOCUST_API_KEY", "LOCUST_TOWER_ID",
}


def should_skip(rel: Path) -> bool:
    parts = set(rel.parts)
    if parts & SKIP_DIR_PARTS:
        return True
    s = rel.as_posix()
    return any(s.startswith(p) for p in SKIP_PATH_PREFIXES)


def collect_referenced(root: Path) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if should_skip(rel):
            continue
        suf = path.suffix.lower()
        name = path.name
        patterns: list[re.Pattern[str]] = []
        if suf in PY_EXTS:
            patterns = list(PY_PATTERNS)
        elif suf in JS_EXTS:
            patterns = list(JS_PATTERNS)
        if name in COMPOSE_FILENAMES:
            patterns.append(COMPOSE_PATTERN)
        if not patterns:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pat in patterns:
            for m in pat.finditer(text):
                key = m.group(1)
                if key in EXCLUDE:
                    continue
                found.setdefault(key, set()).add(rel.as_posix())
    return found


def collect_documented(env_file: Path) -> set[str]:
    if not env_file.exists():
        return set()
    documented: set[str] = set()
    # Accept both ``KEY=value`` and commented forms like ``# KEY=value`` so a
    # variable that's documented-but-disabled-by-default still counts.
    line_re = re.compile(r"^\s*#?\s*([A-Z_][A-Z0-9_]*)=")
    for line in env_file.read_text().splitlines():
        m = line_re.match(line)
        if m:
            documented.add(m.group(1))
    return documented


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Repository root to scan")
    parser.add_argument(
        "--env-file",
        default=".env.example",
        help="Path to the documentation env file",
    )
    parser.add_argument(
        "--github-summary",
        action="store_true",
        help="Also write a markdown table to $GITHUB_STEP_SUMMARY",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    env_file = (root / args.env_file).resolve()

    referenced = collect_referenced(root)
    documented = collect_documented(env_file)
    missing = sorted(set(referenced) - documented)

    print(f"Scanned root      : {root}")
    print(f"Reference file    : {env_file.relative_to(root) if env_file.is_relative_to(root) else env_file}")
    print(f"Referenced in code: {len(referenced)}")
    print(f"Documented        : {len(documented)}")
    print(f"Missing           : {len(missing)}")

    if not missing:
        print("\n.env.example is in sync with the code base.")
        return 0

    print("\nThe following env vars are referenced in code but missing from "
          f"{env_file.name}:\n")
    print("| Variable | First reference |")
    print("|---|---|")
    for k in missing:
        sample = sorted(referenced[k])[0]
        print(f"| `{k}` | `{sample}` |")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if args.github_summary and summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"## ❌ `.env.example` drift detected\n\n")
            fh.write(f"{len(missing)} env var(s) referenced in code are not "
                     f"documented in `{env_file.name}`.\n\n")
            fh.write("| Variable | First reference |\n|---|---|\n")
            for k in missing:
                sample = sorted(referenced[k])[0]
                fh.write(f"| `{k}` | `{sample}` |\n")
            fh.write("\nAdd the missing entries (with empty/example values "
                     "and a brief comment) to keep deployment docs in sync.\n")

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
