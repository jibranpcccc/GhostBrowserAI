from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath


# Matches likely credential assignments for common secret-bearing keys.
# It intentionally requires an assignment-like separator (: or =) and a value
# that does not look like a variable reference or function call.
CREDENTIAL_RE = re.compile(
    r"""
    (?ix)                         # ignore case, verbose
    (?<![A-Za-z0-9_-])            # keyword starts at word boundary
    (?:
        account[_-]id
      | api[_-]token
      | api[_-]key
      | password
      | secret
      | private[_-]key
    )
    \s*[:=]\s*                    # separator
    ["']?                          # optional opening quote
    (?P<value>[^\s"'<>[\](),.`{}]+)  # candidate value
    """,
    re.VERBOSE,
)

# Paths that are allowed to contain example / test credential shapes.
_ALLOWED_DIR_PARTS = {"tests", "test", "examples", "example", "docs"}
_ALLOWED_NAME_PARTS = {".example", ".template", "placeholder"}
_PLACEHOLDER = "<placeholder>"

# Values that are obviously not real secrets.
_NON_SECRET_VALUES = {"none", "null", "true", "false"}
# Format documentation patterns (e.g. "ACCOUNT_ID:API_TOKEN" in docstrings)
_FORMAT_DOC_PATTERN = re.compile(r"(?i)ACCOUNT_ID\s*[=:,]\s*(?:GATEWAY_NAME\s*[=:,]\s*)?API_TOKEN")


def _is_allowed_path(path: PurePosixPath) -> bool:
    """Return True if the file is a known test/example file."""
    parts = {p.lower() for p in path.parts}
    if parts & _ALLOWED_DIR_PARTS:
        return True
    name = path.name.lower()
    return any(fragment in name for fragment in _ALLOWED_NAME_PARTS)


def _is_decoy_value(value: str) -> bool:
    """Ignore values that are plain identifiers, booleans, or nulls."""
    if len(value) < 6:
        return True
    if value.lower() in _NON_SECRET_VALUES:
        return True
    # Skip variable names such as `account_id` on the RHS.
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        return True
    return False


def _scan_text(text: str) -> list[tuple[int, str]]:
    """Return (line_number, snippet) for each suspicious match."""
    findings: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _PLACEHOLDER in line.lower():
            continue
        if _FORMAT_DOC_PATTERN.search(line):
            continue
        for match in CREDENTIAL_RE.finditer(line):
            value = match.group("value")
            if _is_decoy_value(value):
                continue
            start = max(0, match.start() - 30)
            end = min(len(line), match.end() + 30)
            snippet = line[start:end].strip()
            findings.append((lineno, snippet))
    return findings


def get_tracked_files(cwd: Path | None = None) -> list[PurePosixPath]:
    """Return tracked file paths relative to the repo root using git ls-files."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            capture_output=True,
            check=True,
            cwd=cwd,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"Unable to list tracked files: {exc}", file=sys.stderr)
        raise SystemExit(2)
    raw = result.stdout.decode("utf-8", errors="replace")
    if not raw:
        return []
    return [PurePosixPath(p) for p in raw.split("\x00") if p]


def scan_tracked_files(cwd: Path | None = None) -> dict[str, list[tuple[int, str]]]:
    """Scan all tracked files and return findings grouped by path."""
    repo_root = cwd or Path.cwd()
    tracked = get_tracked_files(repo_root)
    findings: dict[str, list[tuple[int, str]]] = {}
    for relative in tracked:
        if _is_allowed_path(relative):
            continue
        path = repo_root / relative
        try:
            raw = path.read_bytes()
        except (OSError, UnicodeDecodeError):
            continue
        # Skip binary files based on a null-byte heuristic.
        if b"\x00" in raw:
            continue
        text = raw.decode("utf-8", errors="replace")
        matches = _scan_text(text)
        if matches:
            findings[str(relative)] = matches
    return findings


def main() -> int:
    findings = scan_tracked_files()
    if not findings:
        print("No suspicious credential patterns detected in tracked files.")
        return 0

    print("Potential credentials detected in tracked files:")
    for path, matches in sorted(findings.items()):
        for lineno, snippet in matches:
            print(f"  {path}:{lineno}: {snippet}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
