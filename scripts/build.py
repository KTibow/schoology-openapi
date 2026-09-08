#!/usr/bin/env python3
"""Bundle the multi-file spec into dist/openapi.json.

operationIds must be unique *in the source*: realm-generic operations live at
a single `/{realm}/{realm_id}/...` path whose `realm` enum names the realms
that support them, so there is nothing to disambiguate. A duplicate is a real
authoring mistake (two operations that should have been one path, or a typo),
and this script fails on it rather than inventing a suffix -- a generated
suffix would make every operationId depend on key order in paths/_index.yaml,
which silently renames CLI commands and SDK methods when the file is reordered.
"""
import json, os, subprocess, sys, re

BUNDLE = "dist/openapi.json"
STRICT = "dist/openapi.strict.json"
EXAMPLES = "spec/examples.json"

r = subprocess.run(["pnpm", "exec", "redocly", "bundle", "spec/openapi.yaml", "-o", BUNDLE],
                   capture_output=True, text=True)
print(r.stdout.strip() or r.stderr.strip())
if r.returncode != 0:
    sys.exit(r.returncode)

import glob as _glob

# A path-item node that nothing in paths/_index.yaml points at is invisible:
# the bundle omits it, lint sees nothing, and the endpoint silently does not
# exist. That is a easy mistake to make when adding endpoints by hand.
_index_src = open("spec/paths/_index.yaml").read()
_orphans = []
for _f in sorted(_glob.glob("spec/paths/*.yaml")):
    if _f.endswith("_index.yaml"):
        continue
    _base = os.path.basename(_f)
    for _node in re.findall(r"^([A-Za-z]\w*):\s*$", open(_f).read(), re.M):
        if f"{_base}#/{_node}'" not in _index_src:
            _orphans.append(f"{_base}#/{_node}")
if _orphans:
    for _o in _orphans:
        print(f"ERROR path item defined but never referenced by paths/_index.yaml: {_o}",
              file=sys.stderr)
    sys.exit("unreferenced path items; wire them up or delete them")

doc = json.load(open(BUNDLE))
seen, clashes, missing = {}, [], []
for path, item in doc.get("paths", {}).items():
    if not isinstance(item, dict):
        continue
    for method, op in item.items():
        if method not in {"get", "post", "put", "delete", "patch"} or not isinstance(op, dict):
            continue
        oid = op.get("operationId")
        if not oid:
            missing.append(f"{method.upper()} {path}")
            continue
        if oid in seen:
            clashes.append(f"{oid!r}: {seen[oid]} and {method.upper()} {path}")
        else:
            seen[oid] = f"{method.upper()} {path}"

if missing or clashes:
    for m in missing:
        print(f"ERROR missing operationId: {m}", file=sys.stderr)
    for c in clashes:
        print(f"ERROR duplicate operationId {c}", file=sys.stderr)
    sys.exit("operationIds must be unique and present; fix the source spec")

# GitHub-style alert syntax; Scalar renders it as a red callout. (Its `:::`
# directive form is not supported and comes out as literal text.)
FORBIDDEN_CALLOUT = (
    "> [!CAUTION]\n"
    "> Not available to student accounts — returns `403` with an empty body.\n"
)


def realm_callout(by_realm):
    """Amber callout naming the realms that refuse an otherwise-usable operation."""
    refused = sorted(k for k, v in by_realm.items() if v == "forbidden")
    allowed = sorted(k for k, v in by_realm.items() if v == "ok")
    if not refused or not allowed:
        return None
    def listed(names):
        q = [f"`{n}`" for n in names]
        return q[0] if len(q) == 1 else ", ".join(q[:-1]) + " and " + q[-1]

    return ("> [!WARNING]\n"
            f"> Student accounts can use this in {listed(allowed)}, but "
            f"{listed(refused)} return{'s' if len(refused) == 1 else ''} `403`.\n")


def annotate(doc):
    """Render *only* `x-student-access: forbidden` into the description.

    The extensions are the source of truth and anything consuming this
    programmatically reads them directly. A description is prose a human reads
    before calling the endpoint, so the only thing worth spending it on is the
    one fact that changes whether they can call it at all. `ok`, `unknown` and
    `x-verified` are provenance -- interesting when auditing the spec, noise on
    every one of 225 operation pages.
    """
    annotated = 0
    for item in (doc.get("paths") or {}).values():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method not in {"get", "post", "put", "delete", "patch"} or not isinstance(op, dict):
                continue
            by_realm = op.get("x-student-access-by-realm")
            if op.get("x-student-access") == "forbidden":
                callout = FORBIDDEN_CALLOUT
            elif by_realm:
                callout = realm_callout(by_realm)
            else:
                callout = None
            if not callout:
                continue
            desc = op.get("description")
            op["description"] = callout + (f"\n{desc}" if desc else "")
            annotated += 1
    return doc, annotated


def schema_refs(node, acc):
    if isinstance(node, dict):
        r = node.get("$ref")
        if isinstance(r, str) and r.startswith("#/components/schemas/"):
            acc.add(r.rsplit("/", 1)[-1])
        for v in node.values():
            schema_refs(v, acc)
    elif isinstance(node, list):
        for v in node:
            schema_refs(v, acc)


def closure(doc, seed):
    """Every component schema reachable from `seed`, following $refs."""
    schemas = doc["components"]["schemas"]
    seen, stack = set(seed), list(seed)
    while stack:
        acc = set()
        schema_refs(schemas.get(stack.pop(), {}), acc)
        for name in acc - seen:
            seen.add(name)
            stack.append(name)
    return seen


def open_response_schemas(doc):
    """Relax `additionalProperties: false` on schemas only ever used in responses.

    Closed response shapes are the right tool while *developing* the spec -- they
    are how the probe harness surfaced Schoology's undocumented fields. They are
    the wrong contract to publish: only a fraction of operations have been probed,
    so for the rest we would be asserting a closed shape nobody has observed, and
    any field Schoology adds later breaks every consumer that validates. Requests
    stay closed -- there the API really does reject unknown keys.

    Schemas reachable from *both* a request and a response stay closed, so
    relaxing a response never weakens request validation.
    """
    resp_seed, req_seed = set(), set()
    for item in (doc.get("paths") or {}).values():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method not in {"get", "post", "put", "delete", "patch"} or not isinstance(op, dict):
                continue
            schema_refs(op.get("responses", {}), resp_seed)
            schema_refs(op.get("requestBody", {}), req_seed)
    response_only = closure(doc, resp_seed) - closure(doc, req_seed)

    opened = 0
    for name in response_only:
        stack = [doc["components"]["schemas"][name]]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                # Don't descend into $refs: each target is visited on its own.
                if node.pop("additionalProperties", None) is False:
                    opened += 1
                stack.extend(v for k, v in node.items() if k != "$ref")
            elif isinstance(node, list):
                stack.extend(node)
    return opened, len(response_only)


def attach_examples(doc):
    """Attach the fictional response examples from spec/examples.json.

    Generated by scripts/gen_examples.py from the probe corpus: structure and
    JSON types are real, every value is invented. Without these, renderers show
    `"string"` / `0` placeholders that hide the thing you actually need to know
    (which optional keys appear, and whether an id came back as 5 or "5").
    """
    if not os.path.exists(EXAMPLES):
        print(f"note: {EXAMPLES} absent — publishing without response examples")
        return 0
    examples = json.load(open(EXAMPLES))
    attached, orphans = 0, []
    for path, body in examples.items():
        op = (doc.get("paths") or {}).get(path, {}).get("get")
        if not op:
            orphans.append(path)
            continue
        schema = (op.get("responses", {}).get("200", {})
                    .get("content", {}).get("application/json"))
        if schema is None:
            orphans.append(path)
            continue
        schema["example"] = body
        attached += 1
    if orphans:
        for p in orphans:
            print(f"ERROR example for unknown GET 200: {p}", file=sys.stderr)
        sys.exit("spec/examples.json is stale; re-run scripts/gen_examples.py")
    return attached


doc, n_callouts = annotate(doc)
n_examples = attach_examples(doc)

# Strict build: exactly as authored. lint_spec.py and verify_live.py read this.
json.dump(doc, open(STRICT, "w"), indent=1)

opened, n_schemas = open_response_schemas(doc)
json.dump(doc, open(BUNDLE, "w"), indent=1)

print(f"bundled {len(seen)} unique operationIds, {n_examples} response examples, "
      f"{n_callouts} student-forbidden callouts")
print(f"  {STRICT}: strict (closed response shapes) — for lint + live verification")
print(f"  {BUNDLE}: published "
      f"(opened {opened} response objects across {n_schemas} response-only schemas)")

