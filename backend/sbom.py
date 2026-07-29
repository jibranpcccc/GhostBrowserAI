"""
Software Bill of Materials (SBOM) generator.

This module reads the project's requirements.txt and produces a minimal
CycloneDX 1.4-style JSON SBOM. It does not perform deep dependency
resolution or reach out to package indexes.
"""

import datetime
import json
import os
import re
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from backend.auth import require_admin_token

router = APIRouter(prefix="/api/sbom", tags=["sbom"])

REQUIREMENTS_FILE = "requirements.txt"


def _requirements_path() -> str:
    """Return the absolute path to the project's requirements.txt."""
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", REQUIREMENTS_FILE)
    )


# Match dependency specifiers: name ==|>=|~= version, ignoring markers.
_SPEC_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(==|>=|~=)\s*([^\s;#]+)"
)


def _build_component(name: str, version: str) -> Dict[str, Any]:
    """Build a CycloneDX component dict for a package."""
    normalized_name = name.lower()
    return {
        "name": name,
        "version": version,
        "purl": f"pkg:pypi/{normalized_name}@{version}",
        "type": "library",
        "licenses": [],
    }


def parse_requirements(path: str) -> List[Dict[str, Any]]:
    """Parse a requirements.txt into SBOM component dicts.

    Supported formats:
        package==version
        package>=version
        package~=version

    Ignored:
        - blank lines and comments
        - pip options (lines starting with '-')
        - editable installs (e.g., -e ...)
        - VCS URLs (git+, hg+, svn+, bzr+)

    Args:
        path: Path to the requirements file.

    Returns:
        list: SBOM component dicts.
    """
    components: List[Dict[str, Any]] = []
    if not os.path.exists(path):
        return components

    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            # Strip inline comments.
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue

            # Skip pip options and editable installs.
            if line.startswith("-"):
                continue

            # Skip VCS URLs.
            if re.match(r"^(git|hg|svn|bzr)\+", line, re.IGNORECASE):
                continue

            # Strip extras (e.g., package[extra]==1.0).
            line = re.sub(r"\[.*?\]", "", line)

            match = _SPEC_RE.match(line)
            if not match:
                continue

            name, _operator, version = match.groups()
            name = name.strip()
            version = version.strip()

            if not name or not version:
                continue

            components.append(_build_component(name, version))

    return components


def get_sbom() -> Dict[str, Any]:
    """Return a CycloneDX 1.4-ish SBOM dict sourced from requirements.txt.

    If requirements.txt is missing, the components list is empty and a
    note is included.
    """
    components = parse_requirements(_requirements_path())
    result: Dict[str, Any] = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.4",
        "metadata": {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
        "components": components,
    }

    if not components:
        result["notes"] = [
            f"{REQUIREMENTS_FILE} not found; no components could be determined."
        ]

    return result


@router.get("")
def sbom_endpoint(_auth: None = Depends(require_admin_token)) -> Dict[str, Any]:
    """GET /api/sbom — return the generated SBOM."""
    return get_sbom()


# Convenience export for other modules.
sbom_data = get_sbom()


if __name__ == "__main__":
    # Smoke test: print a JSON summary of the generated SBOM.
    sbom = get_sbom()
    summary = {
        "bomFormat": sbom.get("bomFormat"),
        "specVersion": sbom.get("specVersion"),
        "metadata": sbom.get("metadata"),
        "componentCount": len(sbom.get("components", [])),
        "firstThreeComponents": sbom.get("components", [])[:3],
        "notes": sbom.get("notes"),
    }
    print(json.dumps(summary, indent=2))
