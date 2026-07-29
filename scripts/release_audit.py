from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath


SECRET_PATTERNS = (
    re.compile(rb"cfut_[A-Za-z0-9]{20,}"),
    re.compile(rb"(?:api[_-]?token|authorization)\s*[:=]\s*[\"']?[A-Za-z0-9_-]{24,}", re.I),
)
FORBIDDEN_NAMES = {
    "cloudflare_accounts.txt",
    "cloudflare_accounts.priority.txt",
    ".env",
    "profiles_meta.json",
    "proxies.db",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit_distribution(root: Path) -> dict:
    """Audit a built distribution directory for forbidden files and content."""
    problems = []
    files = []
    if not root.exists():
        return {"passed": False, "problems": [f"Distribution does not exist: {root}"], "files": []}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if path.name.lower() in FORBIDDEN_NAMES:
            problems.append(f"Forbidden private file: {relative}")
        if path.stat().st_size <= 5 * 1024 * 1024:
            content = path.read_bytes()
            if any(pattern.search(content) for pattern in SECRET_PATTERNS):
                problems.append(f"Possible credential material: {relative}")
        files.append({"path": relative, "size": path.stat().st_size, "sha256": sha256(path)})
    required = ["GhostBrowser.exe", "playwright-browsers/chrome-win64/chrome.exe"]
    observed = {item["path"] for item in files}
    for name in required:
        if name not in observed:
            problems.append(f"Required release file missing: {name}")
    return {"passed": not problems, "problems": problems, "files": files}


def _git_staged_files(root: Path) -> list[str]:
    """Return paths in the index: the tree that would be released if staged."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            capture_output=True,
            check=False,
            cwd=root,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"git is not available: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace").strip() or "git ls-files failed")
    raw = result.stdout.decode("utf-8", errors="replace")
    if not raw:
        return []
    return [p for p in raw.split("\x00") if p]


def _read_staged_file(root: Path, relative: str) -> bytes:
    """Read a file from the git index rather than from the working tree."""
    result = subprocess.run(
        ["git", "show", f":{relative}"],
        capture_output=True,
        check=False,
        cwd=root,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(message or f"Unable to read staged file: {relative}")
    return result.stdout


def audit_release_tree(root: Path) -> list[str]:
    """Audit the staged release candidate, including its staged file content."""
    try:
        staged = _git_staged_files(root)
    except RuntimeError as exc:
        return [str(exc)]

    problems = []
    for relative in staged:
        lower = relative.lower()
        name = PurePosixPath(relative).name.lower()
        if lower.endswith(".db"):
            problems.append(f"Release candidate contains runtime artifact (.db): {relative}")
        if name == ".env":
            problems.append(f"Release candidate contains .env file: {relative}")
        if name == "cloudflare_accounts.txt":
            problems.append(f"Release candidate contains cloudflare_accounts.txt: {relative}")
        try:
            content = _read_staged_file(root, relative)
        except RuntimeError as exc:
            problems.append(str(exc))
            continue
        if len(content) <= 5 * 1024 * 1024 and any(pattern.search(content) for pattern in SECRET_PATTERNS):
            problems.append(f"Possible credential material in release candidate: {relative}")
    return problems


def working_tree_problems(root: Path) -> list[str]:
    """Require both the index and working tree to be clean before release."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain=v1"],
            capture_output=True,
            check=False,
            cwd=root,
        )
    except FileNotFoundError as exc:
        return [f"git is not available: {exc}"]
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        return [message or "git status failed"]
    if result.stdout:
        return ["Working tree is not clean; commit, stash, or remove all changes before release."]
    return []


def verify_build(root: Path) -> list[str]:
    """Compile-check the backend and tests, then import the FastAPI app."""
    problems = []

    compile_proc = subprocess.run(
        [sys.executable, "-m", "compileall", "-q", "backend", "tests"],
        capture_output=True,
        text=True,
        cwd=root,
    )
    if compile_proc.returncode != 0:
        problems.append("Build verification failed: python -m compileall -q backend tests")
        if compile_proc.stdout.strip():
            problems.append(compile_proc.stdout.strip())
        if compile_proc.stderr.strip():
            problems.append(compile_proc.stderr.strip())

    import_proc = subprocess.run(
        [sys.executable, "-c", "from backend.main import app"],
        capture_output=True,
        text=True,
        cwd=root,
    )
    if import_proc.returncode != 0:
        problems.append("Build verification failed: from backend.main import app")
        if import_proc.stdout.strip():
            problems.append(import_proc.stdout.strip())
        if import_proc.stderr.strip():
            problems.append(import_proc.stderr.strip())

    return problems


def _run_credential_scan(root: Path) -> list[str]:
    """Run the credential heuristics over the staged release candidate."""
    import importlib.util

    scanner_path = Path(__file__).with_name("check_credentials.py")
    spec = importlib.util.spec_from_file_location("_check_credentials", scanner_path)
    if spec is None or spec.loader is None:
        return [f"Credential scanner not found: {scanner_path}"]
    check_credentials = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check_credentials)

    try:
        staged = _git_staged_files(root)
    except RuntimeError as exc:
        return [str(exc)]

    problems = []
    for relative in staged:
        candidate_path = PurePosixPath(relative)
        if check_credentials._is_allowed_path(candidate_path):
            continue
        try:
            content = _read_staged_file(root, relative)
        except RuntimeError as exc:
            problems.append(str(exc))
            continue
        if b"\x00" in content:
            continue
        for line, snippet in check_credentials._scan_text(content.decode("utf-8", errors="replace")):
            problems.append(f"Potential credential in release candidate: {relative}:{line}: {snippet}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="GhostBrowser release audit")
    parser.add_argument("--dist", type=Path, default=Path("dist/GhostBrowser"))
    parser.add_argument("--manifest", type=Path, default=Path("dist/release-manifest.json"))
    parser.add_argument("--skip-dist", action="store_true", help="Skip the built distribution audit")
    parser.add_argument("--skip-credentials", action="store_true", help="Skip the credential pattern scan")
    args = parser.parse_args()

    root = Path.cwd()
    problems: list[str] = []
    dist_report = None

    problems += working_tree_problems(root)
    problems += audit_release_tree(root)
    problems += verify_build(root)
    if not args.skip_credentials:
        problems += _run_credential_scan(root)

    if not args.skip_dist:
        if args.dist.exists():
            dist_report = audit_distribution(args.dist)
            problems += dist_report["problems"]
        else:
            problems.append(f"Distribution directory does not exist: {args.dist}")

    passed = not problems

    if passed and dist_report is not None:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(json.dumps(dist_report, indent=2), encoding="utf-8")

    summary = {
        "passed": passed,
        "file_count": len(dist_report["files"]) if dist_report else 0,
        "problems": problems,
        "manifest": str(args.manifest) if (passed and dist_report is not None) else None,
    }
    print(json.dumps(summary, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
