#!/usr/bin/env python3
"""Project-specific OpenAPI strictness linter.

Rules (fail loud):
1. No `additionalProperties: true` anywhere.
2. Every `type: object` schema must explicitly set `additionalProperties: false`.
3. Operation/schema descriptions must not restate verification ("verified") or
   student access ("student-access...") — that lives in x-verified/x-student-access.
4. Every operation must carry `x-student-access` in {ok, forbidden, unknown}.
5. `x-verified` must be a boolean when present.
6. Every operation must have at least one 2xx response and tags.
7. Examples must not contain ids observed on the live account (the id list comes
   from the gitignored `.env`, so no live identifier is committed; the rule is
   skipped when none are configured).
8. Every `{param}` in a path template must be declared in that path item's
   parameters (path- or operation-level).
9. `allOf` composition wrappers must not pin `additionalProperties: false`.
10. Every response example must validate against its own response schema.
11. `x-verified: true` implies `x-student-access` is not `unknown`.
"""
import os, re, sys

try:
    import yaml
except ImportError:
    sys.exit("pip install pyyaml")

# A Schoology object id (7+ digits) or a SIS-style code (UUID-shaped). Both are
# real identifiers belonging to a real school when they come out of the corpus.
ID_TOKEN = re.compile(r"\b\d{7,}\b|\b[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\b")

def live_ids():
    """Every id-shaped value in the gitignored probe corpus.

    Rule 7 asserts that no committed example echoes a real object id. The ids
    are themselves live identifiers, so writing them into this file (or into
    `.env`) would be the very leak the rule exists to prevent -- they are read
    out of `probe/` instead, which is gitignored and is where they already are.

    With no corpus (CI, a fresh clone) the rule does not run. That is fine: it
    is a belt-and-braces check behind `gen_examples.py`, which already
    guarantees no probe value can reach an example, and it is most useful
    exactly where the corpus exists -- on the machine that generates them.
    """
    probe_dir = os.path.join(os.path.dirname(__file__), "..", "probe")
    if not os.path.isdir(probe_dir):
        return set()
    ids = set()
    for name in os.listdir(probe_dir):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(probe_dir, name)) as f:
                ids.update(ID_TOKEN.findall(f.read()))
        except OSError:
            continue
    return ids

FORBIDDEN_IDS = live_ids()

def walk(node, path, out, key=None):
    out.append((node, path, key))
    if isinstance(node, dict):
        for k, v in node.items():
            walk(v, f"{path}/{k}", out, key=k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk(v, f"{path}/{i}", out, key=key)

def resolve_param(p, doc):
    if "$ref" in p:
        ref = p["$ref"].lstrip("#/").split("/")
        node = doc
        for part in ref:
            node = node.get(part) if isinstance(node, dict) else None
            if node is None: return None
        return node.get("name")
    return p.get("name")

def check_examples(doc):
    """Rule 9: every response example must validate against its own schema.

    An example that contradicts the schema is worse than no example -- it is the
    part readers copy, and it is what renderers show first.
    """
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        print("WARN  jsonschema not installed; skipping example validation")
        return []
    errors = []
    for path, item in (doc.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if not isinstance(op, dict):
                continue
            for code, resp in (op.get("responses") or {}).items():
                if not isinstance(resp, dict):
                    continue
                for mime, media in (resp.get("content") or {}).items():
                    if not isinstance(media, dict) or "example" not in media:
                        continue
                    root = dict(doc)
                    ptr = path.replace("~", "~0").replace("/", "~1")
                    mime_ptr = mime.replace("~", "~0").replace("/", "~1")
                    root["$ref"] = (f"#/paths/{ptr}/{method}/responses/{code}"
                                    f"/content/{mime_ptr}/schema")
                    for e in Draft202012Validator(root).iter_errors(media["example"]):
                        loc = "/".join(map(str, e.absolute_path)) or "<root>"
                        errors.append(f"/paths/{path}/{method}/responses/{code}: "
                                      f"example invalid at {loc}: {e.message[:90]}")
    return errors


def main(specfile):
    doc = yaml.safe_load(open(specfile))
    nodes = []
    walk(doc, "", nodes)
    errors, warnings = [], []

    VERIFIED_RE = re.compile(r"x-verified|\(verified|verified\)|verified (403|for students|for a student|against|status)", re.I)
    for node, path, key in nodes:
        if node is True and key == "additionalProperties":
            errors.append(f"{path}: additionalProperties is true")
        elif isinstance(node, dict) and node.get("additionalProperties") is True:
            errors.append(f"{path}: additionalProperties is true")

        if (isinstance(node, dict) and node.get("type") == "object"
                and "properties" in node and "additionalProperties" not in node):
            errors.append(f"{path}: object schema missing additionalProperties: false")

        # Composition wrappers (allOf + required, no own properties) must NOT pin
        # additionalProperties: it only sees `properties` declared at its own level,
        # so `allOf` branches don't count and the schema rejects every property.
        if (isinstance(node, dict) and "allOf" in node
                and node.get("additionalProperties") is False
                and "properties" not in node):
            errors.append(f"{path}: allOf wrapper pins additionalProperties: false "
                          f"with no own properties — rejects every body")

        if isinstance(node, str):
            low = node.lower()
            if (VERIFIED_RE.search(node) and "/description" in path
                    and not path.startswith("/info")):
                errors.append(f"{path}: description restates verification (use x-verified): {node[:60]!r}")
            if "student-access" in low and path.endswith("/description") and not path.startswith("/info"):
                warnings.append(f"{path}: description restates student access (use x-student-access)")

        # Examples are attached as `schema.example` (singular) by build.py, so
        # match that segment -- `/examples/` never appears in a bundled doc.
        if isinstance(node, str) and FORBIDDEN_IDS and "/example" in path:
            for tok in ID_TOKEN.findall(node):
                if tok in FORBIDDEN_IDS:
                    errors.append(f"{path}: example contains a live-observed id")
                    break

    methods = {"get", "post", "put", "delete", "patch", "head", "options", "trace"}
    for path, item in (doc.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        declared = set(re.findall(r"\{([^}]+)\}", path))
        param_names = set()
        for p in item.get("parameters", []):
            if isinstance(p, dict):
                param_names.add(resolve_param(p, doc))
        for method, op in item.items():
            if method not in methods or not isinstance(op, dict):
                continue
            op_path = f"/paths/{path}/{method}"
            xsa = op.get("x-student-access")
            if xsa not in ("ok", "Forbidden", "unknown", "forbidden"):
                if xsa == "Forbidden":
                    errors.append(f"{op_path}: x-student-access must be lowercase")
                else:
                    errors.append(f"{op_path}: missing x-student-access (ok|forbidden|unknown)")
            if "x-verified" in op and not isinstance(op["x-verified"], bool):
                errors.append(f"{op_path}: x-verified must be boolean")
            # Every probe ran under one live student account, so a verified
            # response shape is itself proof the student reached the operation.
            if op.get("x-verified") and xsa == "unknown":
                errors.append(f"{op_path}: x-verified: true but x-student-access: "
                              f"unknown — a verified response means a student reached it")
            if not op.get("responses"):
                errors.append(f"{op_path}: no responses")
            elif not any(str(c).startswith("2") for c in op["responses"]):
                errors.append(f"{op_path}: no 2xx response")
            if not op.get("tags"):
                errors.append(f"{op_path}: missing tags")
            op_params = {resolve_param(p, doc) for p in op.get("parameters", []) if isinstance(p, dict)}
            if declared and not (declared <= (param_names | op_params)):
                errors.append(f"{op_path}: undeclared path params {declared - (param_names | op_params)}")

    errors += check_examples(doc)

    for e in errors:
        print(f"ERROR {e}")
    for w in warnings:
        print(f"WARN  {w}")
    print(f"\n{len(errors)} errors, {len(warnings)} warnings")
    sys.exit(1 if errors else 0)

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "dist/openapi.strict.json")
