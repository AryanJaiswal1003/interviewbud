"""Turn the dataset's `check(candidate)` asserts into individually runnable test cases."""

import ast
import re
from dataclasses import dataclass

NODE_BUILDERS = {"list_node", "tree_node"}
NODE_COMPARATORS = {"is_same_list", "is_same_tree"}


@dataclass
class ParsedAssert:
    source: str
    actual_expr: str  # expression producing the candidate's output
    expected_expr: str  # expression producing the expected output
    cmp: str  # eq | same_list | same_tree
    call_args: str  # human-readable args, e.g. "nums = [3, 3], target = 6"
    args_key: str  # normalised literal args, used to match description examples


def _unwrap_builder(node: ast.expr) -> ast.expr:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in NODE_BUILDERS:
        return node.args[0] if node.args else ast.List(elts=[])
    return node


def _args_key(call: ast.Call) -> str:
    parts = []
    for kw in call.keywords:
        try:
            val = ast.literal_eval(_unwrap_builder(kw.value))
        except Exception:
            val = ast.unparse(kw.value)
        parts.append(f"{kw.arg}={val!r}")
    return ";".join(parts)


def _human_args(call: ast.Call) -> str:
    return ", ".join(f"{kw.arg} = {ast.unparse(_unwrap_builder(kw.value))}" for kw in call.keywords)


def parse_check(test_src: str) -> list[ParsedAssert]:
    tree = ast.parse(test_src)
    check = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "check")
    out: list[ParsedAssert] = []
    for node in check.body:
        if not isinstance(node, ast.Assert):
            continue
        src = ast.get_source_segment(test_src, node) or ast.unparse(node)
        t = node.test
        if isinstance(t, ast.Compare) and len(t.ops) == 1 and isinstance(t.ops[0], ast.Eq):
            actual, expected, cmp = t.left, t.comparators[0], "eq"
        elif isinstance(t, ast.Call) and isinstance(t.func, ast.Name) and t.func.id in NODE_COMPARATORS:
            actual, expected = t.args[0], t.args[1]
            cmp = "same_list" if t.func.id == "is_same_list" else "same_tree"
        else:
            actual, expected, cmp = t, ast.Constant(True), "eq"
        call = actual if isinstance(actual, ast.Call) else None
        out.append(
            ParsedAssert(
                source=src,
                actual_expr=ast.unparse(actual),
                expected_expr=ast.unparse(expected),
                cmp=cmp,
                call_args=_human_args(call) if call else ast.unparse(actual),
                args_key=_args_key(call) if call else "",
            )
        )
    return out


def example_args_key(example_input: str) -> str | None:
    """'nums = [2,7,11,15], target = 9' -> normalised key comparable with ParsedAssert.args_key."""
    try:
        call = ast.parse(f"f({example_input})", mode="eval").body
    except SyntaxError:
        return None
    if not isinstance(call, ast.Call):
        return None
    return _args_key(call)


_EXAMPLE_RE = re.compile(
    r"Example\s*\d*:\s*\n+\s*Input:\s*(?P<input>.+?)\s*\n\s*Output:\s*(?P<output>.+?)\s*\n"
    r"(?:\s*Explanation:\s*(?P<expl>.*?))?(?=\n\s*Example\s*\d*:|\n\s*Constraints:|\Z)",
    re.S,
)


def parse_examples(description: str) -> list[dict]:
    out = []
    for m in _EXAMPLE_RE.finditer(description):
        out.append(
            {
                "input": m.group("input").strip(),
                "output": m.group("output").strip(),
                "explanation": (m.group("expl") or "").strip(),
            }
        )
    return out


def parse_constraints(description: str) -> list[str]:
    if "Constraints:" not in description:
        return []
    tail = description.split("Constraints:", 1)[1]
    tail = re.split(r"\n\s*(?:Follow[- ]?up|Note)\b", tail, maxsplit=1)[0]
    return [ln.strip() for ln in tail.splitlines() if ln.strip()]


def statement_only(description: str) -> str:
    """Problem statement without the examples/constraints blocks (those are rendered separately)."""
    return re.split(r"\n\s*Example\s*1\s*:", description, maxsplit=1)[0].strip()


def follow_up(description: str) -> str:
    m = re.search(r"\n\s*(Follow[- ]?up.*)", description, re.S)
    return m.group(1).strip() if m else ""


def title_from_slug(slug: str) -> str:
    small = {"a", "an", "and", "the", "of", "in", "on", "to", "ii", "iii", "iv"}
    words = slug.split("-")
    out = []
    for i, w in enumerate(words):
        if w in {"ii", "iii", "iv"}:
            out.append(w.upper())
        elif i and w in small:
            out.append(w)
        else:
            out.append(w.capitalize())
    return " ".join(out)


def entry_signature(starter_code: str) -> tuple[list[tuple[str, str]], str]:
    """Return ([(param, annotation)], return_annotation) for the first method in the starter code."""
    tree = ast.parse(starter_code + "\n        pass")
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            params = [
                (a.arg, ast.unparse(a.annotation) if a.annotation else "")
                for a in node.args.args
                if a.arg != "self"
            ]
            return params, ast.unparse(node.returns) if node.returns else ""
    return [], ""


def build_call(entry_args: str, starter_code: str) -> str:
    """Build `candidate(...)` from human args, wrapping ListNode/TreeNode params with the dataset helpers."""
    params, _ = entry_signature(starter_code)
    ann = dict(params)
    call = ast.parse(f"f({entry_args})", mode="eval").body
    if not isinstance(call, ast.Call) or call.args:
        raise ValueError("Test input must be keyword arguments, e.g. `nums = [1,2], target = 3`")
    parts = []
    for kw in call.keywords:
        if kw.arg not in ann:
            raise ValueError(f"Unknown parameter {kw.arg!r}; expected one of {list(ann)}")
        ast.literal_eval(kw.value)  # inputs must be plain literals
        val = ast.unparse(kw.value)
        a = ann[kw.arg]
        if "ListNode" in a:
            val = f"list_node({val})"
        elif "TreeNode" in a:
            val = f"tree_node({val})"
        parts.append(f"{kw.arg} = {val}")
    missing = [p for p in ann if p not in {kw.arg for kw in call.keywords}]
    if missing:
        raise ValueError(f"Missing parameters: {missing}")
    return f"candidate({', '.join(parts)})"
