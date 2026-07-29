import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DETECTION = SCRIPTS / "detection_check.py"
ISOLATION = SCRIPTS / "isolation_check.py"


def _status(summary):
    return summary.get("status", "failed") if isinstance(summary, dict) else "failed"


def _reason(summary):
    if not isinstance(summary, dict):
        return str(summary)
    if summary.get("reason"):
        return summary["reason"]
    leaks = summary.get("leaks")
    if leaks:
        if isinstance(leaks, list):
            return "; ".join(leaks)
        return str(leaks)
    return "no details"


def _annotation(name, summary):
    title = f"{name} failed"
    msg = _reason(summary)
    return f"::error title={title}::{msg}"


def run_scanner(path, args, env):
    cmd = [sys.executable, str(path)] + args
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=env,
    )
    try:
        summary = json.loads(proc.stdout)
    except Exception:
        summary = {
            "status": "failed",
            "reason": proc.stderr.strip() or f"invalid JSON output: {proc.stdout[:200]!r}",
        }
    passed = proc.returncode == 0 and _status(summary) == "passed"
    return passed, summary


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run GhostBrowser scanner scripts."
    )
    parser.add_argument(
        "--detection-only",
        action="store_true",
        help="Run only detection_check.py.",
    )
    parser.add_argument(
        "--isolation-only",
        action="store_true",
        help="Run only isolation_check.py.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run detection_check.py with --live (requires GHOSTBROWSER_ALLOW_LIVE_NETWORK=1).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print full JSON summaries.",
    )
    parser.add_argument(
        "--ci",
        action="store_true",
        help="Convert JSON summary to GitHub Actions annotations on failure.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.detection_only and args.isolation_only:
        print("error: --detection-only and --isolation-only are mutually exclusive", file=sys.stderr)
        sys.exit(1)

    if args.live:
        allowed = {"1", "true", "yes"}
        if os.getenv("GHOSTBROWSER_ALLOW_LIVE_NETWORK", "").strip().lower() not in allowed:
            print("error: GHOSTBROWSER_ALLOW_LIVE_NETWORK=1 is required for --live", file=sys.stderr)
            sys.exit(1)
        if args.isolation_only:
            print("error: --live cannot be used with --isolation-only", file=sys.stderr)
            sys.exit(1)

    scanners = []
    env = os.environ.copy()

    if not args.isolation_only:
        if args.live:
            scanners.append(("detection_check", DETECTION, ["--temp", "--live"]))
        else:
            scanners.append(("detection_check", DETECTION, ["--temp", "--skip-network"]))

    if not args.detection_only:
        scanners.append(("isolation_check", ISOLATION, ["--headless"]))

    all_passed = True
    summaries = {}

    for name, path, sargs in scanners:
        passed, summary = run_scanner(path, sargs, env)
        summaries[name] = summary
        if args.verbose:
            print(f"--- {name} summary ---")
            print(json.dumps(summary, indent=2))
        if not passed:
            all_passed = False
            if args.ci:
                print(_annotation(name, summary))
            else:
                print(f"{name}: {_status(summary)} - {_reason(summary)}", file=sys.stderr)

    if all_passed:
        print("All scanners passed.")
        sys.exit(0)

    if not args.ci:
        print("Scanners failed.", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
