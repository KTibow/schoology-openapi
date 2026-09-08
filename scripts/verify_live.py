#!/usr/bin/env python3
"""E2E verification of dist/openapi.strict.json, in two halves.

1. Responses: every probe/*.json body must validate against the GET 200 schema
   for its path. Fails loud on undocumented fields, wrong types and missing
   required fields -- this is what surfaced Schoology's undocumented fields.
   Needs the probe corpus (live credentials); skipped without it.

2. Requests: hand-written bodies checked against the *Create / *Writable
   schemas, positive and negative. Needs nothing, so it runs in CI too.
"""
import json, os, re, sys, yaml
from jsonschema import Draft202012Validator

BUNDLE = json.load(open("dist/openapi.strict.json"))


def escape_pointer(tmpl):
    return tmpl.replace("~", "~0").replace("/", "~1")


def make_validator(tmpl):
    """Validator rooted at the whole bundle, targeting the GET 200 schema of tmpl."""
    ref = f"#/paths/{escape_pointer(tmpl)}/get/responses/200/content/application~1json/schema"
    root = dict(BUNDLE)
    root["$ref"] = ref
    return Draft202012Validator(root)

def match_spec_path(concrete):
    """Find the OpenAPI path template matching a concrete probed path.
    Prefer templates whose literal segments match exactly (so /messages/recipients
    wins over /messages/{message_id})."""
    concrete = re.sub(r"^/v1", "", concrete.split("?")[0])
    parts = concrete.split("/")
    best, best_score = None, -1
    for tmpl in BUNDLE["paths"]:
        tparts = tmpl.split("/")
        if len(tparts) != len(parts):
            continue
        pattern = "^" + re.sub(r"\{[^}]+\}", r"[^/]+", tmpl) + "$"
        if not re.match(pattern, concrete):
            continue
        score = sum(1 for tp, cp in zip(tparts, parts) if not tp.startswith("{") and tp == cp)
        if score > best_score:
            best, best_score = tmpl, score
    return best

def is_empty_collection(body):
    """True when the body is a collection wrapper holding no records."""
    if not isinstance(body, dict):
        return False
    lists = {k: v for k, v in body.items() if isinstance(v, list) and k != "links"}
    scalars = [k for k, v in body.items() if not isinstance(v, (list, dict))]
    if not lists or scalars:
        return False
    return not any(lists.values())


def response_schema_names(bundle, op):
    """Every component schema named anywhere under the operation's 200."""
    out = set()

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                out.add(node["$ref"].split("/")[-1])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk((op.get("responses") or {}).get("200") or {})
    return out


def main():
    # The request fixtures need no credentials, so they run everywhere (CI
    # included). The response half needs the probe corpus and is skipped
    # without it rather than failing the build.
    if not os.path.exists("probe/_index.json"):
        print("probe/_index.json absent — skipping live response validation "
              "(needs credentials); checking request fixtures only.\n")
        sys.exit(1 if verify_requests() else 0)

    index = json.load(open("probe/_index.json"))
    ok, fail, skip, empty = 0, 0, 0, 0
    schemas_hit, schemas_empty, schemas_seen = set(), set(), {}
    failures = []
    for key, info in sorted(index.items()):
        if info["status"] != 200:
            skip += 1
            continue
        try:
            body = json.load(open(f"probe/{key}.json"))
        except FileNotFoundError:
            skip += 1
            continue
        tmpl = match_spec_path(info["path"])
        if not tmpl:
            print(f"SKIP (no spec path) {key}: {info['path']}")
            skip += 1
            continue
        op = BUNDLE["paths"][tmpl].get("get")
        if not op:
            print(f"SKIP (no GET op) {key}: {tmpl}")
            skip += 1
            continue
        resp = op.get("responses", {}).get("200") or op.get("responses", {}).get("201")
        schema = None
        if resp:
            media = (resp.get("content") or {}).get("application/json")
            if media:
                schema = media.get("schema")
        if not schema:
            print(f"SKIP (no schema) {key}: {tmpl}")
            skip += 1
            continue
        v = make_validator(tmpl)
        errors = sorted(v.iter_errors(body), key=lambda e: list(e.absolute_path))
        if not errors:
            ok += 1
            if is_empty_collection(body):
                empty += 1
                schemas_empty.add(op["operationId"])
                schemas_seen.setdefault(op["operationId"], False)
                print(f"EMPTY {key} <- {tmpl} (wrapper only, no records)")
            else:
                schemas_seen[op["operationId"]] = True
                schemas_hit |= response_schema_names(BUNDLE, op)
                print(f"OK    {key} <- {tmpl}")
        else:
            fail += 1
            print(f"FAIL  {key} <- {tmpl} ({len(errors)} errors)")
            for e in errors[:8]:
                loc = "/".join(str(p) for p in e.absolute_path) or "(root)"
                print(f"        at {loc}: {e.message[:160]}")
            failures.append((key, tmpl, errors))
    # A schema is only exercised by a body that carries a record. An empty
    # collection validates its wrapper and nothing else -- `{"album": []}`
    # satisfies AlbumCollection without ever touching the Album schema. Counting
    # those as passes makes the corpus look far better than it is, and hides
    # exactly the endpoints where the account has no content to check against.
    reachable = set()
    for path, item in BUNDLE["paths"].items():
        op = item.get("get")
        if op and (op.get("responses") or {}).get("200"):
            reachable |= response_schema_names(BUNDLE, op)
    print(f"\n{ok} validated ({empty} of them an empty wrapper only), "
          f"{fail} failed, {skip} skipped")
    print(f"response schemas exercised by a real record: "
          f"{len(schemas_hit & reachable)} of {len(reachable)}")
    # Only report an operation as recordless when *no* probe of it found data;
    # one empty section says nothing when another had rows.
    never = sorted(o for o in schemas_empty if not schemas_seen.get(o))
    if never:
        print("no records to check against: " + ", ".join(never))

    print("\n--- request body fixtures ---")
    req_fail = verify_requests()
    sys.exit(1 if (fail or req_fail) else 0)


# ---------------------------------------------------------------------------
# Request-body fixtures.
#
# Probing is read-only by policy, so nothing here comes from the live API: these
# are hand-written bodies that the docs say are valid, checked against the
# *Create / *Writable schemas. Without this the request half of the spec was
# never exercised at all, which is how 15 `*Create` schemas shipped composing
# `allOf` with a sibling `additionalProperties: false` -- a combination that
# rejects every property, since `additionalProperties` cannot see into `allOf`.
#
# Each entry is (schema name, should-validate, body).
REQUEST_FIXTURES = [
    ("UserCreate", True, {"name_first": "Robin", "name_last": "Vasquez", "role_id": 100020}),
    ("UserCreate", True, {"name_first": "Robin", "name_last": "Vasquez", "role_id": 100020,
                          "primary_email": "robin.vasquez@example.edu", "grad_year": "2027"}),
    ("UserCreate", False, {"name_first": "Robin", "name_last": "Vasquez"}),          # missing role_id
    ("UserCreate", False, {"name_first": "Robin", "name_last": "Vasquez",
                           "role_id": 100020, "not_a_field": 1}),                    # unknown key
    ("UserWritable", True, {"bio": "Updated bio."}),
    ("AssignmentCreate", True, {"title": "Chapter 3 Reading Response", "max_points": 100}),
    ("AssignmentCreate", False, {"max_points": 100}),                                # missing title
    ("DiscussionCreate", True, {"title": "Chapter 3 discussion"}),
    ("EventCreate", True, {"title": "Field trip", "start": "2026-10-14 09:00:00"}),
    ("EventCreate", False, {"title": "Field trip"}),                                 # missing start
    ("SectionCreate", True, {"title": "Period 3", "grading_periods": [100050]}),
    ("EnrollmentCreate", True, {"uid": 100001, "admin": 0, "status": 1}),
    ("GroupCreate", True, {"title": "Robotics Club"}),
    ("SchoolCreate", True, {"title": "Example High School"}),
    ("MessageCreate", True, {"recipient_ids": "100001", "subject": "Question about the essay",
                             "message": "Sent from the API."}),
    ("GradingPeriodCreate", True, {"title": "Fall 2026", "start": "2026-08-24", "end": "2026-12-19"}),
    ("CollectionCreate", True, {"title": "Shared resources"}),
    ("PageCreate", True, {"title": "Syllabus"}),
    ("MediaAlbumCreate", True, {"title": "Field trip photos"}),
    ("BlogPostCreate", True, {"title": "Week 3 recap"}),
    ("CourseCreate", True, {"title": "English 9", "course_code": "ENG-9"}),
    ("SubscriptionInput", True, {"subscription": [
        {"target_id": 100500, "trigger": "grades", "subscribed": 1, "include_object": 1}]}),
    ("SubscriptionInput", False, {"subscription": [{"trigger": "grades"}]}),         # missing target_id
    ("UploadRequest", True, {"filename": "reading-response.pdf", "filesize": 184320}),
]


def verify_requests():
    """Check the hand-written request fixtures against their schemas."""
    ok = fail = 0
    for name, should_pass, body in REQUEST_FIXTURES:
        if name not in BUNDLE["components"]["schemas"]:
            print(f"FAIL  request fixture references unknown schema {name}")
            fail += 1
            continue
        root = dict(BUNDLE)
        root["$ref"] = f"#/components/schemas/{name}"
        errors = list(Draft202012Validator(root).iter_errors(body))
        passed = not errors
        if passed == should_pass:
            ok += 1
            verdict = "accepts" if should_pass else "rejects"
            print(f"OK    {name} {verdict} {json.dumps(body)[:58]}")
        else:
            fail += 1
            if should_pass:
                print(f"FAIL  {name} should accept {json.dumps(body)[:58]}")
                for e in errors[:3]:
                    loc = "/".join(str(p) for p in e.absolute_path) or "(root)"
                    print(f"        at {loc}: {e.message[:140]}")
            else:
                print(f"FAIL  {name} should reject {json.dumps(body)[:58]} but accepted it")
    print(f"\n{ok} request fixtures behaved correctly, {fail} did not")
    return fail


if __name__ == "__main__":
    main()
