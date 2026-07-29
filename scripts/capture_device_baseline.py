from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.baseline_lab import capture_native_baseline, compare_baseline_to_cohorts


async def run(output: Path) -> int:
    baseline = await capture_native_baseline(output)
    comparison = compare_baseline_to_cohorts(baseline)
    print(json.dumps(comparison, indent=2))
    return 0 if comparison.get("best_match") else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("baselines/local/native-device.json"),
    )
    args = parser.parse_args()
    return asyncio.run(run(args.output))


if __name__ == "__main__":
    raise SystemExit(main())
