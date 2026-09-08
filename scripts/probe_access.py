#!/usr/bin/env python3
"""Determine x-student-access by probing every GET operation in the spec.

Why this exists
---------------
`x-student-access` claimed to be "observed with a live student account", but the
old hand-written probe script covered whichever endpoints someone thought to add,
and the annotation was filled in for the rest by inference. An audit found only
18% of the annotations were backed by an actual observation.

So this walks the spec instead of a hand-maintained list: every GET operation is
probed, and the resulting status is the annotation. Operations whose path
parameters cannot be filled from discovered ids are reported as unprobed rather
than guessed.

Non-GET operations are deliberately NOT probed and are never annotated `ok` or
`forbidden` by this script. Determining whether a student may POST/PUT/DELETE
means actually doing it against a real school account. `OPTIONS` is not a
substitute -- it reports endpoint capability, not caller permission, and does so
inconsistently (`OPTIONS /v1/users` answers `Allow: DELETE, PUT` on an account
whose `GET /v1/users` is 403).

Records HTTP status only. No response body is written by this script.

Output: probe/_access.json  ->  {operationId: {"path": str, "outcomes": {scope: status}}}
where `scope` is the enum path-parameter combination probed ("-" if none).
"""
import json
import os
import re
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
import sgy  # noqa: E402

BUNDLE = "dist/openapi.strict.json"
OUT = "probe/_access.json"
DELAY = 0.25  # be a polite guest on someone else's school's API

# Which discovered id fills {realm_id} for each {realm} value.
REALM_ID = {
    "sections": "section_id",
    "groups": "group_id",
    "users": "user_id",
    "schools": "school_id",
    "courses": "course_id",
    "districts": "district_id",
}


def first_id(body, *keys):
    """First id under any of `keys` in a Schoology collection wrapper."""
    if not isinstance(body, dict):
        return None
    for k in keys:
        v = body.get(k)
        if isinstance(v, list) and v:
            v = v[0]
        if isinstance(v, dict):
            for idk in ("id", "uid", "nid"):
                if idk in v:
                    return v[idk]
    return None


def get_json(path):
    """GET following redirects (/users/me answers 303 and must be re-signed)."""
    s, _, b = sgy.get(path)
    if s != 200:
        return s, None
    try:
        return s, json.loads(b)
    except Exception:
        return s, None


def discover():
    """Build a pool of real ids to fill path parameters with."""
    ids = {}
    s, me = get_json("/v1/users/me")
    if not isinstance(me, dict):
        sys.exit(f"could not resolve /users/me (status {s}); check .env credentials")
    if isinstance(me, dict):
        ids["user_id"] = me.get("id")
        ids["school_id"] = me.get("school_id")
        ids["building_id"] = me.get("building_id")
    uid = ids.get("user_id")

    _, secs = get_json(f"/v1/users/{uid}/sections")
    sid = first_id(secs, "section")
    ids["section_id"] = sid
    ids["realm_id"] = sid
    ids["realm"] = "sections"
    if isinstance(secs, dict):
        s0 = (secs.get("section") or [{}])[0]
        ids["course_id"] = s0.get("course_id")

    _, grps = get_json(f"/v1/users/{uid}/groups")
    ids["group_id"] = first_id(grps, "group")

    # NB: never seed a bare `id`. It means a different object in every path,
    # and a stale one silently defeats the per-template discovery below --
    # `/{realm}/{realm_id}/events/{id}` probed with a *document* id answers 403,
    # which looks exactly like a permission failure.
    if sid:
        for path, key, param in [
            (f"/v1/sections/{sid}/assignments", "assignment", "assignment_id"),
            (f"/v1/sections/{sid}/discussions", "discussion", "post_id"),
            (f"/v1/sections/{sid}/updates", "update", "update_id"),
            (f"/v1/sections/{sid}/events", "event", "event_id"),
            (f"/v1/sections/{sid}/grading_categories", "grading_category", "grading_category_id"),
            (f"/v1/sections/{sid}/grading_periods", "grading_period", "grading_period_id"),
            (f"/v1/sections/{sid}/albums", "album", "album_id"),
            (f"/v1/sections/{sid}/pages", "page", "page_id"),
        ]:
            _, body = get_json(path)
            v = first_id(body, key)
            if v is not None:
                ids.setdefault(param, v)
            time.sleep(DELAY)
        ids.setdefault("grade_item_id", ids.get("assignment_id"))

    _, cols = get_json("/v1/collections")
    ids["collection_id"] = first_id(cols, "collection")
    _, inbox = get_json("/v1/messages/inbox")
    ids["message_id"] = first_id(inbox, "message")

    # Query-parameter fixtures, same idea as the path ones.
    if isinstance(secs, dict):
        s0 = (secs.get("section") or [{}])[0]
        code = s0.get("section_school_code") or s0.get("section_code")
        if code:
            ids["section_school_codes"] = code
    ids["keywords"] = "math"

    # A realm-generic path needs an id *of that realm*: `/groups/{id}/events`
    # wants a group id, not a section id. Without this the sweep can only ever
    # test one realm, which is how `listPosts` came to be called "forbidden" on
    # the strength of a single realm out of five.
    _, school = get_json(f"/v1/schools/{ids.get('school_id')}")
    if isinstance(school, dict) and school.get("district_id"):
        ids["district_id"] = school["district_id"]

    ids["folder_id"] = 0          # documented root of the materials tree
    ids["type"] = "ungraded"      # reminders
    ids["realm2"] = "sections"
    return {k: v for k, v in ids.items() if v is not None}


def fill(template, ids):
    """Substitute path params; return None if any is unavailable."""
    out = template
    for name in re.findall(r"\{([^}]+)\}", template):
        if name not in ids:
            return None
        out = out.replace("{" + name + "}", str(ids[name]))
    return out


def first_item_id(body):
    """Id of the first object in a Schoology collection wrapper.

    Collections wrap their rows under a singular key (`event`, `discussion`,
    `role`, ...). Rather than maintain a list of those names, take the first key
    whose value is a non-empty list of objects -- that is the wrapper by
    construction, and it keeps working for endpoints nobody has enumerated yet.
    """
    if not isinstance(body, dict):
        return None
    for key, value in body.items():
        if key in ("links", "tags", "attachments"):
            continue
        if isinstance(value, list) and value and isinstance(value[0], dict):
            for idk in ("id", "uid", "nid"):
                if idk in value[0]:
                    return value[0][idk]
    return None


def resolve(tmpl, scope, depth=0):
    """Fill a template, discovering each unknown id from its own collection.

    An item id must belong to the realm being probed: reusing a section's event
    id against `users/{uid}/events/{id}` returns 403, a cross-realm mismatch
    that looks exactly like a permission failure. For each missing parameter,
    take the prefix of the path ending at that parameter, drop it to get the
    collection that lists those objects, and read an id from there. Works for
    parameters in the middle of a path too, so `.../discussions/{post_id}/comments`
    resolves `post_id` from `.../discussions`.
    """
    scope = dict(scope)
    for _ in range(4):
        missing = [n for n in re.findall(r"\{([^}]+)\}", tmpl) if n not in scope]
        if not missing:
            return fill(tmpl, scope)
        if depth > 3:
            return None
        name = missing[0]
        token = "{" + name + "}"
        prefix = tmpl[: tmpl.index(token) + len(token)]
        parent = prefix[: prefix.rindex("/" + token)]
        candidates = [parent]
        # `/package/{id}` and `/page/{id}` are listed at the plural path.
        tail = parent.rsplit("/", 1)[-1]
        if tail and not tail.startswith("{"):
            candidates.append(parent + "s")
        for cand in candidates:
            parent_path = resolve(cand, scope, depth + 1)
            if parent_path is None:
                continue
            status, body = get_json("/v1" + parent_path)
            item = first_item_id(body)
            if item is not None:
                scope[name] = item
                break
        else:
            return None
    return None


def enum_path_params(doc, path, op):
    """Enum-constrained path parameters, as {name: [values]}."""
    raw = list(doc["paths"][path].get("parameters") or []) + list(op.get("parameters") or [])
    out = {}
    for q in raw:
        if "$ref" in q:
            node = doc
            for part in q["$ref"].lstrip("#/").split("/"):
                node = node[part]
            q = node
        if q.get("in") == "path" and (q.get("schema") or {}).get("enum"):
            out[q["name"]] = list(q["schema"]["enum"])
    return out


def required_query(doc, path, op):
    """Names of query parameters the operation marks required."""
    raw = list(doc["paths"][path].get("parameters") or []) + list(op.get("parameters") or [])
    names = []
    for q in raw:
        if "$ref" in q:
            node = doc
            for part in q["$ref"].lstrip("#/").split("/"):
                node = node[part]
            q = node
        if q.get("in") == "query" and q.get("required"):
            names.append(q["name"])
    return names


def main():
    doc = json.load(open(BUNDLE))
    ids = discover()
    print(f"discovered {len(ids)} path-parameter values: {sorted(ids)}\n")

    results, unprobed = {}, []
    gets = [(p, it["get"]) for p, it in doc["paths"].items() if "get" in it]
    for tmpl, op in sorted(gets):
        oid = op["operationId"]

        # An operation offered in several realms has to be tried in each: access
        # differs per realm, so one 403 says nothing about the other four.
        enums = enum_path_params(doc, tmpl, op)
        variants = [{}]
        for name, values in enums.items():
            variants = [dict(v, **{name: val}) for v in variants for val in values]

        needed = required_query(doc, tmpl, op)
        missing_q = [q for q in needed if q not in ids]
        if missing_q:
            # A lookup endpoint called without its required query parameter
            # answers 403, which is a fixture bug and not a permission signal.
            unprobed.append((oid, tmpl, [f"?{q}" for q in missing_q]))
            continue

        outcomes, skipped = {}, []
        for var in variants:
            scope = dict(ids, **var)
            # {realm_id} must be an id of whichever realm we are testing.
            if "realm" in var:
                key = REALM_ID.get(var["realm"])
                if key not in ids:
                    skipped.append(var["realm"])
                    continue
                scope["realm_id"] = ids[key]
            concrete = resolve(tmpl, scope)
            if concrete is None:
                skipped.append(",".join(var.values()) or "-")
                continue
            if needed:
                concrete += "?" + "&".join(
                    f"{q}={urllib.parse.quote(str(ids[q]))}" for q in needed)
            status, _, _ = sgy.get("/v1" + concrete)
            outcomes["+".join(var.values()) or "-"] = status
            time.sleep(DELAY)

        if not outcomes:
            missing = [n for n in re.findall(r"\{([^}]+)\}", tmpl) if n not in ids]
            unprobed.append((oid, tmpl, missing or skipped))
            continue

        results[oid] = {"path": tmpl, "outcomes": outcomes}
        shown = " ".join(f"{k}={v}" for k, v in outcomes.items())
        print(f"  GET {tmpl:56} {shown}")

    os.makedirs("probe", exist_ok=True)
    json.dump(results, open(OUT, "w"), indent=1, sort_keys=True)

    print(f"\nprobed {len(results)} of {len(gets)} GET operations -> {OUT}")
    if unprobed:
        print(f"\n{len(unprobed)} not probed (no id available for a path parameter):")
        for oid, tmpl, missing in unprobed:
            print(f"  {oid:34} {tmpl:52} missing {missing}")


if __name__ == "__main__":
    main()
