#!/usr/bin/env python3
"""Walk every GET in the spec against a live account. GETs only.

One pass, answering two questions from the same request:

  may a student call this?  the status, into probe/_access.json, which
                            apply_access.py turns into `x-student-access`
  does the response match?  the body, into probe/<key>.json and an index at
                            probe/_index.json, which verify_live.py checks
                            against the schema and gen_examples.py turns into
                            examples

Keeping the body of a request already made is what lets one pass answer both.
It also has to: a status alone cannot tell a working endpoint from a typo here,
because `/sections/{id}/…` answers `200` with the section object for a
subresource Schoology does not recognise. Access and shape have to be judged
together or neither is trustworthy.

Endpoints are not listed anywhere: the walk covers whatever the spec declares,
so an operation added tomorrow is probed without touching this file. The few
ids that cannot be derived from a path's shape are declared in FOREIGN_IDS and
PARAM_SOURCE.

Every id is derived from `/users/me` and the collections hanging off it, so no
real school's identifiers are written down here. `probe/` is gitignored; keep
it that way.

Usage:
  python3 scripts/probe.py
"""
import json
import os
import re
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sgy  # noqa: E402

# Progress goes to a redirected file as often as to a terminal, and block
# buffering hides a long run's output until it exits.
sys.stdout.reconfigure(line_buffering=True)

BUNDLE = "dist/openapi.strict.json"
PROBE_DIR = "probe"
INDEX = f"{PROBE_DIR}/_index.json"
ACCESS = f"{PROBE_DIR}/_access.json"
DELAY = 0.25  # be a polite guest on someone else's school's API

# Enrolled section ids, filled by discover(). resolve() falls back through them
# when the section in scope has none of the objects it is looking for.
SECTION_IDS = []

# How many sections to draw fixtures from. Every lookup stops at the first
# section that has records, so a wider pool only costs requests for material
# this account does not have anywhere.
SECTION_POOL = 20

# Ids consulted only through FOREIGN_IDS. Kept out of the main pool because a
# path like `/{realm}/{realm_id}/updates/{update_id}` needs an update from the
# realm in scope, while `/like/{id}` takes any update at all.
FOREIGN_POOL = {}

# Parameters listed by a sibling collection rather than by the path they sit in:
# `/sections/{id}/submissions/{grade_item_id}` cannot yield a grade item, because
# the listing is `/sections/{id}/grade_items`. Looking there keeps the id and the
# section that owns it together, which seeding a global value does not.
PARAM_SOURCE = {"grade_item_id": "grade_items"}

# Concrete collection URLs already fetched that held no records. Without this the
# same fruitless hunt runs again for every operation needing that id -- ~40
# requests each, since it walks the whole section pool before giving up.
_EMPTY = set()

# The parameter resolve() actually gave up on, for reporting. Listing every
# parameter that was not pre-seeded names the wrong ones: `grade_item_id` is
# resolved from a sibling collection at request time and is never seeded, so it
# looked like the blocker on paths whose real problem was a missing comment id.
_UNRESOLVED = []

# Index keys are named after the operation they came from. They become
# filenames, so the prefix avoids characters Windows rejects.
SWEEP_PREFIX = "op_"

# Path parameters whose value belongs to a different resource, so no amount of
# walking the path shape can find them. `/like/{id}` lists who liked an *update*;
# `/users/ext/{id}` takes a school_uid. Each entry maps a parameter to the key it
# borrows from the discovered id pool. This is the irreducible remainder that
# has to be written down -- everything else is derived.
FOREIGN_IDS = {
    "listUpdateLikes":          {"id": "update_id"},
    "listCommentLikes":         {"id": "update_id", "comment_id": "update_comment_id"},
    "getUserByExtId":           {"id": "school_uid"},
    "getSectionByExtId":        {"id": "section_school_codes"},
    # The trailing {id} repeats the section id rather than naming a new object.
    "getSectionCompletionUser": {"id": "section_id"},
    # `/gradingperiods` is admin-only, so the id has to come from a section the
    # student is in; the endpoint itself is global and takes any of them.
    "getGradingPeriod":         {"grading_period_id": "grading_period_id"},
}

# Which discovered id fills {realm_id} for each {realm} value.
REALM_ID = {
    # A course is only a container; its materials belong to its sections, and a
    # real course id answers 403. The `courses` realm wants a *section* id --
    # see the getRealmFolder description.
    "courses": "section_id",
    "sections": "section_id",
    "groups": "group_id",
    "users": "user_id",
    "schools": "school_id",
    "districts": "district_id",
}


# --------------------------------------------------------------------------
# shared plumbing
# --------------------------------------------------------------------------

def fetch(path):
    """GET, retrying a burst rate-limit rather than recording it as a result.

    Schoology throttles short bursts, and `resolve()` fires several requests
    back to back while hunting for a fixture id. A 429 is a failed measurement,
    not an answer: recorded as-is it demotes an operation to `unknown` and wipes
    out a real observation from an earlier run.
    """
    for attempt in range(4):
        status, headers, raw = sgy.get(path)
        if status != 429:
            return status, headers, raw
        wait = float((headers or {}).get("Retry-After") or 0) or 2 ** attempt
        print(f"[429, waiting {wait:g}s] ", end="")
        time.sleep(wait)
    return status, headers, raw


def get_json(path):
    """GET following redirects (/users/me answers 303 and must be re-signed)."""
    s, _, b = fetch(path)
    if s != 200:
        return s, None
    try:
        return s, json.loads(b)
    except Exception:
        return s, None


class Corpus:
    """Records every probe into probe/_index.json and saves the body.

    Every probe goes through here, so a response cannot be saved without being
    registered or vice versa. Splitting those two jobs is what once left
    `getSectionCompletionUser` at `unknown` for months with a 200 response
    sitting on disk that nothing read.

    `owns` says which index keys this pass is responsible for. Saving keeps the
    other pass's entries and drops this pass's stale ones, so a probe that is
    removed or renamed does not leave a record pointing at a body that no longer
    matches it. A stale entry is worse than a missing one: verify_live.py skips
    what it cannot match to a spec path, so the check quietly disappears instead
    of failing.
    """

    def __init__(self):
        os.makedirs(PROBE_DIR, exist_ok=True)
        self.index = {}

    def record(self, key, path, status, raw):
        """Register one response and keep its body when there is one."""
        self.index[key] = {"path": path, "status": status}
        body = None
        if status == 200:
            try:
                body = sgy.json_body(raw)
            except Exception:
                body = None
            if body is not None:
                with open(f"{PROBE_DIR}/{key}.json", "w") as f:
                    json.dump(body, f, indent=1)
        else:
            with open(f"{PROBE_DIR}/{key}.{status}.txt", "w") as f:
                f.write(raw[:500].decode("utf-8", "replace"))
        return body

    def save(self):
        """Rewrite the index from this run alone.

        Merging would leave records from probes that have since been removed or
        renamed, pointing at bodies that no longer match them. verify_live.py
        skips what it cannot match to a spec path, so a stale record does not
        fail -- the check just quietly disappears.
        """
        with open(INDEX, "w") as f:
            json.dump(self.index, f, indent=1, sort_keys=True)
        print(f"\n{len(self.index)} responses kept -> {INDEX}")


# --------------------------------------------------------------------------
# sweep pass: every GET in the spec, status only
# --------------------------------------------------------------------------

def records(body, wrapper):
    """The list under `wrapper` in a collection body (`[]` when absent)."""
    if not isinstance(body, dict):
        return []
    items = body.get(wrapper)
    if isinstance(items, dict):          # single record, unwrapped by the API
        return [items]
    return items if isinstance(items, list) else []


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


def is_empty_collection(body):
    """True when the body is a collection wrapper holding no records."""
    if not isinstance(body, dict):
        return False
    lists = {k: v for k, v in body.items() if isinstance(v, list) and k != "links"}
    scalars = [k for k, v in body.items() if not isinstance(v, (list, dict))]
    if not lists or scalars:
        return False
    return not any(lists.values())


def first_item_id(body):
    """Id of the first object in a Schoology collection wrapper.

    Collections wrap their rows under a singular key (`event`, `discussion`,
    `role`, ...). Rather than maintain a list of those names, take the first
    key whose value is a non-empty list of objects -- that is the wrapper by
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


def discover():
    """Build a pool of real ids to fill path parameters with."""
    ids = {}
    s, me = get_json("/v1/users/me")
    if not isinstance(me, dict):
        sys.exit(f"could not resolve /users/me (status {s}); check .env credentials")
    ids["user_id"] = me.get("id")
    ids["school_id"] = me.get("school_id")
    ids["building_id"] = me.get("building_id")
    ids["school_uid"] = me.get("school_uid")
    uid = ids.get("user_id")

    # Past enrollments too: a current section can be empty of pages, albums or
    # updates while last year's is full of them, and an expired section stays
    # readable. Current sections come first so a live one is preferred when
    # both have records.
    _, secs = get_json(f"/v1/users/{uid}/sections")
    _, allsecs = get_json(f"/v1/users/{uid}/sections?include_past=1")
    section_ids = [str(r["id"]) for r in records(secs, "section")
                   if isinstance(r, dict) and r.get("id")]
    for r in records(allsecs, "section"):
        if isinstance(r, dict) and r.get("id") and str(r["id"]) not in section_ids:
            section_ids.append(str(r["id"]))
    sid = section_ids[0] if section_ids else None
    SECTION_IDS[:] = section_ids[:SECTION_POOL]
    ids["section_id"] = sid
    ids["realm_id"] = sid
    ids["realm"] = "sections"
    if isinstance(secs, dict):
        s0 = (secs.get("section") or [{}])[0]
        ids["course_id"] = s0.get("course_id")

    _, grps = get_json(f"/v1/users/{uid}/groups")
    ids["group_id"] = first_id(grps, "group")

    # Item ids are deliberately NOT seeded here. resolve() derives each one from
    # the collection that lists it, which keeps the item and the realm it lives
    # in together. Seeding them globally pairs an id discovered in one section
    # with whichever section happens to be in scope, and Schoology answers that
    # mismatch with 403 -- indistinguishable from a permission failure, and it
    # silently demoted four operations to `forbidden` when the fixture pool grew
    # to include past sections that hold content the current ones do not.

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

    # An update from wherever the account has one, for /like/{id}.
    FOREIGN_POOL.clear()
    for realm, rid in (("groups", ids.get("group_id")), ("users", uid)):
        if not rid or "update_id" in FOREIGN_POOL:
            continue
        _, body = get_json(f"/v1/{realm}/{rid}/updates")
        time.sleep(DELAY)
        upd = first_id(body, "update")
        if upd is None:
            continue
        FOREIGN_POOL["update_id"] = upd
        _, cbody = get_json(f"/v1/{realm}/{rid}/updates/{upd}/comments")
        time.sleep(DELAY)
        c = first_id(cbody, "comment")
        if c is not None:
            FOREIGN_POOL["update_comment_id"] = c

    # A grading period id from a readable section, for the global endpoint.
    for candidate in section_ids[:SECTION_POOL]:
        _, body = get_json(f"/v1/sections/{candidate}/grading_periods")
        time.sleep(DELAY)
        gp = first_id(body, "grading_period")
        if gp is not None:
            FOREIGN_POOL["grading_period_id"] = gp
            break

    ids["folder_id"] = 0          # documented root of the materials tree
    ids["type"] = "ungraded"      # reminders
    ids["realm2"] = "sections"
    return {k: v for k, v in ids.items()
            if v is not None and not k.startswith("_")}


def fill(template, ids):
    """Substitute path params; return None if any is unavailable."""
    out = template
    for name in re.findall(r"\{([^}]+)\}", template):
        if name not in ids:
            return None
        out = out.replace("{" + name + "}", str(ids[name]))
    return out


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
    if depth == 0:
        _UNRESOLVED.clear()
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
        candidates = []
        if name in PARAM_SOURCE:
            candidates.append(parent.rsplit("/", 1)[0] + "/" + PARAM_SOURCE[name])
        candidates.append(parent)
        # `/package/{id}` and `/page/{id}` are listed at the plural path.
        tail = parent.rsplit("/", 1)[-1]
        if tail and not tail.startswith("{"):
            candidates.append(parent + "s")
        # Try the parent collection in each enrolled section, not just the one
        # the scope happens to point at. Content is unevenly distributed: this
        # account's first section holds zero pages, so a single-section lookup
        # reports `getPage` unprobed while another section has one. When an
        # alternate section supplies the id, the rest of the template has to
        # follow it there -- an id from section B against section A is a
        # cross-realm mismatch, which answers 403 and reads as a permission
        # failure.
        found = None
        for cand in candidates:
            # Only at the top level: nesting the fallback multiplies out to
            # hundreds of requests for a template with three unknown ids.
            for alt in (_section_variants(scope) if depth == 0 else [scope]):
                parent_path = resolve(cand, alt, depth + 1)
                if parent_path is None or parent_path in _EMPTY:
                    continue
                status, body = get_json("/v1" + parent_path)
                time.sleep(DELAY)
                if first_item_id(body) is None:
                    _EMPTY.add(parent_path)
                item = first_item_id(body)
                if item is not None:
                    found = dict(alt, **{name: item})
                    break
            if found:
                break
        if not found:
            if not _UNRESOLVED:
                _UNRESOLVED.append(name)
            return None
        scope = found
    return None


def _section_variants(scope):
    """`scope`, then the same scope aimed at each other enrolled section."""
    yield scope
    known = {str(s) for s in SECTION_IDS}
    current = {k: str(scope.get(k)) for k in ("section_id", "realm_id")}
    for sid in SECTION_IDS:
        if all(v == str(sid) for v in current.values() if v in known):
            continue
        alt = dict(scope)
        for key in ("section_id", "realm_id"):
            if key in alt and str(alt[key]) in known:
                alt[key] = sid
        if alt != scope:
            yield alt


def resolve_candidates(tmpl, scope, limit=4):
    """Concrete paths for `tmpl`, one per enrolled section, best-effort.

    The caller walks these until a response carries records, so an operation is
    judged on an instance that has content rather than on whichever one happened
    to sort first.
    """
    seen = []
    for alt in _section_variants(scope):
        concrete = resolve(tmpl, alt)
        if concrete and concrete not in seen:
            seen.append(concrete)
        if len(seen) >= limit:
            break
    return seen or [resolve(tmpl, scope)]


def optional_query_probes(doc, path, op, limit=3):
    """(name, value) pairs for optional query parameters the spec constrains.

    Only parameters with an enum or a boolean type, because those are the ones
    whose accepted values are written down. Anything free-form would need a
    fixture and is left to FOREIGN_IDS.
    """
    out = []
    for q in _params(doc, path, op):
        if q.get("in") != "query" or q.get("required"):
            continue
        schema = q.get("schema") or {}
        if schema.get("enum"):
            out.append((q["name"], schema["enum"][0]))
        elif schema.get("type") == "boolean":
            out.append((q["name"], "1"))
        if len(out) >= limit:
            break
    return out


def _params(doc, path, op):
    """Path-item and operation parameters, with $refs resolved."""
    raw = list(doc["paths"][path].get("parameters") or []) + list(op.get("parameters") or [])
    out = []
    for q in raw:
        if "$ref" in q:
            node = doc
            for part in q["$ref"].lstrip("#/").split("/"):
                node = node[part]
            q = node
        out.append(q)
    return out


def enum_path_params(doc, path, op):
    """Enum-constrained path parameters, as {name: [values]}."""
    return {q["name"]: list(q["schema"]["enum"]) for q in _params(doc, path, op)
            if q.get("in") == "path" and (q.get("schema") or {}).get("enum")}


def required_query(doc, path, op):
    """Names of query parameters the operation marks required."""
    return [q["name"] for q in _params(doc, path, op)
            if q.get("in") == "query" and q.get("required")]


def sweep():
    doc = json.load(open(BUNDLE))
    c = Corpus()
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

        print(f"  GET {tmpl:56} ", end="")
        outcomes, skipped = {}, []
        for var in variants:
            scope = dict(ids, **var)
            # Parameters whose value belongs to another resource entirely.
            pool = dict(FOREIGN_POOL, **ids)
            for param, source in FOREIGN_IDS.get(oid, {}).items():
                if source in pool:
                    scope[param] = pool[source]
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
            # An empty collection validates its wrapper and nothing else, so
            # prefer an instance that actually holds records: the first section
            # in the pool may have no comments while the third has plenty.
            attempt = None
            for candidate in ([concrete] if needed else resolve_candidates(tmpl, scope)):
                if needed:
                    candidate += "?" + "&".join(
                        f"{q}={urllib.parse.quote(str(ids[q]))}" for q in needed)
                status, _, raw = fetch("/v1" + candidate)
                time.sleep(DELAY)
                if attempt is None or status == 200:
                    attempt = (candidate, status, raw)
                if status != 200:
                    break
                try:
                    if not is_empty_collection(json.loads(raw)):
                        break
                except Exception:
                    break
            concrete, status, raw = attempt
            scope_name = "+".join(var.values()) or "-"
            outcomes[scope_name] = status
            # Keep the body: it cost a request already, and the status on its
            # own does not establish that the endpoint works.
            key = SWEEP_PREFIX + oid + ("" if scope_name == "-" else "+" + scope_name)
            c.record(key, "/v1" + concrete, status, raw)

            # Declared query parameters change the response shape, so probe each
            # one the spec constrains to a known value. Derived from the document
            # rather than a hand-kept list, so a new parameter is covered as soon
            # as it is written down.
            if status == 200:
                for qname, qval in optional_query_probes(doc, tmpl, op):
                    qs = ("&" if "?" in concrete else "?") + f"{qname}={qval}"
                    qstatus, _, qraw = fetch("/v1" + concrete + qs)
                    c.record(f"{key}+{qname}", "/v1" + concrete + qs, qstatus, qraw)
                    time.sleep(DELAY)

        if not outcomes:
            missing = list(_UNRESOLVED) or [n for n in re.findall(r"\{([^}]+)\}", tmpl)
                                            if n not in ids]
            unprobed.append((oid, tmpl, missing or skipped))
            print(f"not probed (no {', '.join(str(m) for m in (missing or skipped))})")
            continue

        results[oid] = {"path": tmpl, "outcomes": outcomes}
        print(" ".join(f"{k}={v}" for k, v in outcomes.items()))

    os.makedirs(PROBE_DIR, exist_ok=True)
    json.dump(results, open(ACCESS, "w"), indent=1, sort_keys=True)
    c.save()

    print(f"\nprobed {len(results)} of {len(gets)} GET operations -> {ACCESS}")
    if unprobed:
        print(f"\n{len(unprobed)} not probed (no id available for a path parameter).")
        print("Either the collection that lists the id is forbidden, or the")
        print("account has no such object: a section with zero albums cannot")
        print("answer whether getAlbum is allowed.")
        for oid, tmpl, missing in unprobed:
            print(f"  {oid:34} {tmpl:52} missing {missing}")


# --------------------------------------------------------------------------

def main():
    if sys.argv[1:]:
        sys.exit("usage: python3 scripts/probe.py   (no arguments)")
    sweep()


if __name__ == "__main__":
    main()
