import ast
import sys
import os

def run_test():
    filepath = "backend/browser_manager.py"
    if not os.path.exists(filepath):
        print(f"[FAIL] {filepath} does not exist.")
        sys.exit(1)

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(f"[FAIL] Error reading {filepath}: {e}")
        sys.exit(1)

    try:
        root = ast.parse(content, filename=filepath)
    except Exception as e:
        print(f"[FAIL] Error parsing {filepath} with ast: {e}")
        sys.exit(1)

    # Walk ast to find AsyncFunctionDef named global_routing_barrier
    barriers = []
    for node in ast.walk(root):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "global_routing_barrier":
            barriers.append(node)

    if len(barriers) != 1:
        print(f"[FAIL] Expected exactly 1 global_routing_barrier, found {len(barriers)}")
        sys.exit(1)
    else:
        print("[PASS] Exactly one global_routing_barrier function found.")

    barrier_node = barriers[0]

    try:
        func_source = ast.unparse(barrier_node)
    except Exception as e:
        print(f"[FAIL] Error unparsing global_routing_barrier node: {e}")
        sys.exit(1)

    forbidden = ["urllib", "urlopen", "httpx", "requests.", "socket", "route.fulfill"]
    required = ["route.continue_", "route.abort", "fail_closed_profile"]

    all_passed = True

    # Inspect forbidden terms
    for term in forbidden:
        if term in func_source:
            print(f"[FAIL] global_routing_barrier source code contains forbidden term '{term}'.")
            all_passed = False
        else:
            print(f"[PASS] global_routing_barrier source code does not contain forbidden term '{term}'.")

    # Inspect required terms
    for term in required:
        if term in func_source:
            print(f"[PASS] global_routing_barrier source code contains required term '{term}'.")
        else:
            print(f"[FAIL] global_routing_barrier source code does not contain required term '{term}'.")
            all_passed = False

    if all_passed:
        print("ALL REGRESSION CHECKS PASSED.")
        sys.exit(0)
    else:
        print("SOME REGRESSION CHECKS FAILED.")
        sys.exit(1)

if __name__ == "__main__":
    run_test()
