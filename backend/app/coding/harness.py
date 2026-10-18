# Executed INSIDE the sandbox container (python -I -). Stdlib only.
# The host appends: main(<payload>)
#
# Trust boundary: everything that happens in this process is controlled by the candidate's code (it can patch
# builtins, walk gc, rewrite stdout). So this harness never sees expected answers and never decides pass/fail. It
# only reports what the candidate's function returned, converted to plain JSON data; the host compares that with
# expected values it computed separately (see coding/service.py).
import contextlib
import io
import json
import signal
import sys
import time
import traceback

MAX_ITEMS = 1_000_000  # total values serialised per result, so a huge return can't exhaust memory
MAX_DEPTH = 60


class _Timeout(Exception):
    pass


def _alarm(signum, frame):
    raise _Timeout()


class Unsupported(TypeError):
    pass


def _plain(x, node_types, budget, depth=0):
    """Return value -> plain JSON data. Only exact built-in types are accepted (no subclasses), so a candidate can't
    smuggle out an object with a rigged __eq__; ListNode/TreeNode (the classes the problem's preamble defined, captured
    before the candidate's code ran) are flattened to lists the way LeetCode prints them."""
    budget[0] -= 1
    if budget[0] < 0:
        raise Unsupported("return value is too large")
    if depth > MAX_DEPTH:
        raise Unsupported("return value is nested too deeply")
    t = type(x)
    if x is None or t is bool or t is int or t is str:
        return x
    if t is float:
        return x if x == x and x not in (float("inf"), float("-inf")) else repr(x)
    if t is list or t is tuple:
        return [_plain(v, node_types, budget, depth + 1) for v in x]
    if t is dict:
        out = {}
        for k, v in x.items():
            if type(k) not in (str, int, bool) and k is not None:
                raise Unsupported(f"dict keys of type {type(k).__name__} aren't supported")
            out[str(k)] = _plain(v, node_types, budget, depth + 1)
        return out
    list_node, tree_node = node_types
    if list_node is not None and t is list_node:
        out, n = [], x
        while n is not None:
            if type(n) is not list_node:
                raise Unsupported("a linked list node points at something that isn't a ListNode")
            budget[0] -= 1
            if budget[0] < 0:
                raise Unsupported("linked list is too long (or has a cycle)")
            out.append(_plain(n.val, node_types, budget, depth + 1))
            n = n.next
        return out
    if tree_node is not None and t is tree_node:
        out, q, i = [], [x], 0
        while i < len(q):
            n = q[i]
            i += 1
            budget[0] -= 1
            if budget[0] < 0:
                raise Unsupported("tree is too large (or has a cycle)")
            if n is None:
                out.append(None)
                continue
            if type(n) is not tree_node:
                raise Unsupported("a tree node points at something that isn't a TreeNode")
            out.append(_plain(n.val, node_types, budget, depth + 1))
            q.append(n.left)
            q.append(n.right)
        while out and out[-1] is None:
            out.pop()
        return out
    raise Unsupported(f"returned a {t.__name__}; return plain values (numbers, strings, lists, dicts, None)")


def _to_plain(x, node_types):
    return _plain(x, node_types, [MAX_ITEMS])


def run_tests(g, payload, node_types):
    results = []
    per_case = payload.get("per_case_timeout", 2.0)
    signal.signal(signal.SIGALRM, _alarm)
    timeouts = 0
    for t in payload["tests"]:
        if timeouts >= payload.get("max_timeouts", 3):
            results.append({"id": t["id"], "status": "skipped", "ms": 0, "value": None, "stdout": "",
                            "error": "skipped after repeated timeouts"})
            continue
        buf = io.StringIO()
        status, value, err = "error", None, ""
        start = time.perf_counter()
        try:
            g["candidate"] = eval(payload["entry_point"], g)
            signal.setitimer(signal.ITIMER_REAL, per_case)
            with contextlib.redirect_stdout(buf):
                actual = eval(t["actual_expr"], g)
                value = _to_plain(actual, node_types)
            signal.setitimer(signal.ITIMER_REAL, 0)
            status = "ok"
        except _Timeout:
            status, err = "timeout", f"exceeded {per_case:.1f}s"
            timeouts += 1
        except RecursionError:
            signal.setitimer(signal.ITIMER_REAL, 0)
            err = "RecursionError: maximum recursion depth exceeded"
        except Unsupported as e:
            signal.setitimer(signal.ITIMER_REAL, 0)
            err = str(e)
        except BaseException as e:  # noqa: BLE001 - candidate code may raise anything, even SystemExit
            signal.setitimer(signal.ITIMER_REAL, 0)
            tb = traceback.extract_tb(e.__traceback__)
            line = next((f.lineno for f in reversed(tb) if f.filename == "<solution>"), None)
            err = f"{type(e).__name__}: {e}" + (f" (line {line})" if line else "")
        results.append({"id": t["id"], "status": status, "ms": round((time.perf_counter() - start) * 1000, 2),
                        "value": value, "stdout": buf.getvalue()[-2000:], "error": err[:500]})
    return results


def main(payload):
    g = {"__name__": "__solution__"}
    marker = payload.pop("marker")
    out = {"compile_error": None, "results": [], "value": None}
    node_types = (None, None)
    try:
        exec(compile(payload["preamble"], "<preamble>", "exec"), g)
        node_types = (g.get("ListNode"), g.get("TreeNode"))  # before the candidate's code can replace them
        sys.setrecursionlimit(20000)
        exec(compile(payload["code"], "<solution>", "exec"), g)
    except SyntaxError as e:
        out["compile_error"] = f"SyntaxError: {e.msg} (line {e.lineno})"
    except BaseException as e:  # noqa: BLE001
        out["compile_error"] = f"{type(e).__name__}: {e}"[:500]
    if out["compile_error"] is None:
        if payload.get("mode") == "eval":
            # Trusted runs only (no candidate code): expected values, and the reference solution on injected inputs.
            vals = []
            signal.signal(signal.SIGALRM, _alarm)
            for expr in payload["exprs"]:
                try:
                    g["candidate"] = eval(payload["entry_point"], g)
                    signal.setitimer(signal.ITIMER_REAL, payload.get("per_case_timeout", 2.0))
                    with contextlib.redirect_stdout(io.StringIO()):
                        v = _to_plain(eval(expr, g), node_types)
                    signal.setitimer(signal.ITIMER_REAL, 0)
                    vals.append({"ok": True, "value": v})
                except _Timeout:
                    vals.append({"ok": False, "error": "timeout"})
                except BaseException as e:  # noqa: BLE001
                    signal.setitimer(signal.ITIMER_REAL, 0)
                    vals.append({"ok": False, "error": f"{type(e).__name__}: {e}"[:300]})
            out["value"] = vals
        else:
            out["results"] = run_tests(g, payload, node_types)
    sys.stdout.write("\n" + marker + json.dumps(out) + "\n")
    sys.stdout.flush()
