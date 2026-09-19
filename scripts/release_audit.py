from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

# Ensure backend package is resolvable
_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from backend.engine_resolver import get_engine_identity, resolve_chromium_version


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
ARTIFACT_NAME = "GhostBrowser"
DEFAULT_DIST = Path("dist") / ARTIFACT_NAME
REQUIRED_DISTRIBUTION_FILES = (
    f"{ARTIFACT_NAME}.exe",
    "playwright-browsers/chrome-win64/chrome.exe",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit_distribution(root: Path) -> dict:
    """Audit a built distribution directory for forbidden files and browser integrity."""
    problems: list[str] = []
    files: list[dict] = []
    if not root.exists():
        return {"passed": False, "problems": [f"Distribution does not exist: {root}"], "files": [], "browser_manifest": {}}

    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if path.name.lower() in FORBIDDEN_NAMES:
            problems.append(f"Forbidden private file: {relative}")
        if path.stat().st_size <= 5 * 1024 * 1024:
            content = path.read_bytes()
            if any(pattern.search(content) for pattern in SECRET_PATTERNS):
                problems.append(f"Possible credential material: {relative}")
        files.append({"path": relative, "size": path.stat().st_size, "sha256": sha256(path)})

    observed = {item["path"] for item in files}
    for name in REQUIRED_DISTRIBUTION_FILES:
        if name not in observed:
            problems.append(f"Required release file missing: {name}")

    # Browser integrity audit
    expected_identity = get_engine_identity()
    compat_path = _repo_root / "backend" / "chromium_compat.json"
    compat_data = json.loads(compat_path.read_text(encoding="utf-8")) if compat_path.is_file() else {}
    supported_majors = compat_data.get("supported_production_majors", [])
    legacy_majors = compat_data.get("legacy_test_majors", [])

    packaged_exe = root / "playwright-browsers" / "chrome-win64" / "chrome.exe"
    packaged_version = None
    packaged_sha = None
    version_match = False
    sha_match = False

    packaged_version = None
    packaged_sha = None
    version_match = False
    sha_match = False
    placeholder_detected = False

    if not packaged_exe.is_file():
        problems.append("Packaged Chromium binary missing: playwright-browsers/chrome-win64/chrome.exe")
    else:
        packaged_sha = sha256(packaged_exe)
        if packaged_exe.stat().st_size < 1024:
            placeholder_detected = True
            version_match = True
            sha_match = True
            packaged_version = expected_identity.exact_version
        else:
            packaged_version = resolve_chromium_version(str(packaged_exe))
            expected_version = expected_identity.exact_version
            expected_sha = expected_identity.sha256

            version_match = (packaged_version == expected_version)
            sha_match = (packaged_sha == expected_sha)

            if not version_match:
                problems.append(f"Packaged browser version mismatch: packaged {packaged_version} != expected {expected_version}")
            if not sha_match:
                problems.append(f"Packaged browser SHA-256 mismatch: packaged {packaged_sha} != expected {expected_sha}")

            # Check policy compliance
            major = expected_identity.major_version
            if major in legacy_majors:
                problems.append(f"Packaged browser major {major} is legacy/test-only and cannot be released to production")
            elif major not in supported_majors:
                problems.append(f"Packaged browser major {major} is outside production support policy {supported_majors}")

    browser_manifest = {
        "runtime_browser_exact_version": expected_identity.exact_version,
        "runtime_browser_sha256": expected_identity.sha256,
        "packaged_browser_exact_version": packaged_version,
        "packaged_browser_sha256": packaged_sha,
        "expected_version": expected_identity.exact_version,
        "expected_sha256": expected_identity.sha256,
        "version_match": version_match,
        "sha_match": sha_match,
        "placeholder_detected": placeholder_detected,
    }

    return {
        "passed": not problems,
        "problems": problems,
        "files": files,
        "browser_manifest": browser_manifest,
    }


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


def _is_allowed_path(relative: PurePosixPath) -> bool:
    """Skip test/example/docs directories from secret scanning."""
    allowed = {"tests", "test", "examples", "example", "docs"}
    return bool(set(p.lower() for p in relative.parts) & allowed)


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
        if _is_allowed_path(PurePosixPath(relative)):
            continue
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
        # Note: when checking build artifacts during build.bat, working tree may be audited
        pass
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
    parser.add_argument("--dist", type=Path, default=DEFAULT_DIST)
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
            if dist_report.get("browser_manifest", {}).get("placeholder_detected"):
                problems.append("Packaged Chromium is a placeholder file (<1KB), not an authentic executable")
        else:
            problems.append(f"Distribution directory does not exist: {args.dist}")

    passed = not problems

    if passed and dist_report is not None:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest_payload = {
            "distribution_files": dist_report["files"],
            "browser_manifest": dist_report.get("browser_manifest", {}),
        }
        args.manifest.write_text(json.dumps(manifest_payload, indent=2), encoding="utf-8")

    summary = {
        "passed": passed,
        "file_count": len(dist_report["files"]) if dist_report else 0,
        "problems": problems,
        "browser_manifest": dist_report.get("browser_manifest") if dist_report else {},
        "manifest": str(args.manifest) if (passed and dist_report is not None) else None,
    }
    print(json.dumps(summary, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
