import ast

def test_binds_to_localhost():
    """Verify that run.py binds to 127.0.0.1 by default."""
    with open("run.py", "r") as f:
        tree = ast.parse(f.read())
    
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "run":
            for kw in node.keywords:
                if kw.arg == "host" and kw.value.value == "127.0.0.1":
                    found = True
                    break
    assert found, "run.py must bind to 127.0.0.1 by default"
