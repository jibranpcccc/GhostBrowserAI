import ast
import sys
import os
import pytest


def test_worker_network_boundary():
    filepath = "backend/browser_manager.py"
    assert os.path.exists(filepath), f"{filepath} does not exist."

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
    except Exception as e:
        raise AssertionError(f"Error reading {filepath}: {e}")

    try:
        root = ast.parse(source, filename=filepath)
    except Exception as e:
        raise AssertionError(f"Error parsing {filepath} with ast: {e}")

    route_funcs = []
    for node in ast.walk(root):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "block_tracker_route":
            route_funcs.append(node)

    assert len(route_funcs) == 1, f"Expected exactly 1 block_tracker_route, found {len(route_funcs)}"

    try:
        func_source = ast.unparse(route_funcs[0])
    except Exception as e:
        raise AssertionError(f"Error unparsing block_tracker_route node: {e}")

    forbidden = ["urllib", "urlopen", "httpx", "requests.", "socket", "route.fulfill"]
    required = ["route.continue_", "route.abort"]

    for term in forbidden:
        assert term not in func_source, f"block_tracker_route contains forbidden term '{term}'"

    for term in required:
        assert term in func_source, f"block_tracker_route missing required term '{term}'"

    assert "FAIL-CLOSED" in source, "No FAIL-CLOSED guard found in browser_manager.py"

    assert "--force-webrtc-ip-handling-policy" in source, "Missing WebRTC IP leak prevention"
    assert "--enforce-webrtc-ip-permission-check" in source, "Missing WebRTC permission enforcement"


if __name__ == "__main__":
    try:
        test_worker_network_boundary()
        sys.exit(0)
    except (AssertionError, Exception):
        sys.exit(1)
