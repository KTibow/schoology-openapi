#!/usr/bin/env python3
"""Generate fictional response examples into spec/examples.json.

Why not just scrub the probe responses? Because `probe/` holds real data from a
live student account -- the token owner and, via network/search endpoints, other
real people. A scrubber is a denylist: anything it fails to anticipate leaks. So
nothing here is safe-by-review; it is safe *by construction*.

The probe corpus contributes only **structure**, never values:

  * which optional keys an endpoint actually returns (the docs don't say, and
    this is the single most useful thing the probes know),
  * what JSON type each key arrived as (Schoology returns ids as `5` in one
    place and `"5"` in another -- an example should show the real one),
  * how deeply arrays nest.

Every scalar that reaches the output comes from FICTION below, keyed by field
name, or from a deterministic generator for that JSON type. No string from
`probe/` is ever copied.

Run after probing:  python3 scripts/gen_examples.py
Then:               python3 scripts/build.py
"""
import json
import os
import re
import sys

PROBE_INDEX = "probe/_index.json"
OUT = "spec/examples.json"
MAX_ARRAY = 2  # keep examples readable; real pages return up to `limit`

# Fictional values, keyed by exact field name. Everything else falls back to
# FALLBACK by JSON type. Ids are drawn from an obviously-fake 100xxx range.
FICTION = {
    "uid": "100001", "id": 100001, "school_id": 100010, "building_id": 100011,
    "school_uid": "S1000001", "role_id": 100020,
    "name_title": "Ms.", "name_first": "Robin", "name_first_preferred": "Robin",
    "name_middle": "Q", "name_last": "Vasquez", "name_display": "Robin Vasquez",
    "username": "rvasquez", "primary_email": "robin.vasquez@example.edu",
    "grad_year": "2027", "birthday_date": "2009-04-15",
    "tz_name": "America/New_York", "tz_offset": -5, "language": "en",
    "password": "", "additional_buildings": "", "position": "Student",
    "gender": "F", "bio": "", "phone": "", "website": "", "address": "",
    "interests": "", "activities": "", "department": "",
    "subjects_taught": "", "grades_taught": "",
    "picture_url": "https://asset-cdn.schoology.com/system/files/imagecache/profile_reg/pictures/example.jpg",
    "profile_url": "https://asset-cdn.schoology.com/system/files/imagecache/profile_reg/pictures/example.jpg",
    "title": "Chapter 3 Reading Response",
    "description": "Read the chapter and answer the three prompts.",
    "body": "Posted to the class stream.",
    "comment": "Thanks -- resubmitted with the corrected chart.",
    "due": "2026-10-14 23:59:00", "start": "2026-10-14 09:00:00",
    "end": "2026-10-14 10:00:00",
    "created": 1760000000, "last_updated": 1760086400, "timestamp": 1760000000,
    "max_points": 100, "factor": 1, "grade": "92", "weight": 1,
    "section_id": 100100, "course_id": 100200, "group_id": 100300,
    "assignment_id": 100400, "grade_item_id": 100400, "realm_id": 100100,
    "user_id": 100001, "target_id": 100500, "comment_id": 100600,
    "album_id": 100700, "content_id": 100701, "update_id": 100800,
    "post_id": 100900, "collection_id": 101000, "template_id": 101001,
    "folder_id": 0, "revision_id": 1, "message_id": 101100,
    "course_code": "ENG-9", "section_code": "ENG-9-P3",
    "section_school_code": "ENG-9-P3-F26", "section_title": "Period 3",
    "course_title": "English 9", "subject_area": 4,
    "filename": "reading-response.pdf", "filesize": 184320,
    "md5_checksum": "9f86d081884c7d659a2feaa0c55ad015",
    "filemime": "application/pdf", "extension": "pdf",
    "url": "https://example.edu/resource",
    "self": "https://api.schoology.com/v1/users/100001",
    "next": "https://api.schoology.com/v1/users?start=20&limit=20",
    "download_path": "https://api.schoology.com/v1/attachment/100002/source",
    "target": "https://example.edu/schoology-webhook",
    "trigger": "grades", "realm": "section", "type": "assignment",
    "status": 1, "total": 2, "response_code": 200,
    "message": "Success.", "location": "https://api.schoology.com/v1/users/100001",
    "subject": "Question about the essay",
    "trigger_name": "grades", "version": "v1",
}

FALLBACK = {
    int: 1,
    float: 1.0,
    bool: True,
    str: "example",
    type(None): None,
}


# Legal stand-ins for `format`-constrained placeholders, by format name.
FORMAT_FILLER = {
    "uri": "https://example.edu/resource",
    "uri-reference": "https://example.edu/resource",
    "email": "robin.vasquez@example.edu",
    "date": "2026-10-14",
    "date-time": "2026-10-14T09:00:00Z",
}


def formatted_fields(bundle):
    """Map property name -> `format` for every formatted string in the spec.

    The type-only fallback would emit `"example"` for `web_url`, which is a
    legal string but not a legal `uri`, and renderers (rightly) flag it. Reading
    the constraint off the spec keeps FICTION from having to enumerate every
    URL-ish field by hand.
    """
    found = {}

    def walk(node):
        if isinstance(node, dict):
            for name, sub in (node.get("properties") or {}).items():
                if isinstance(sub, dict) and isinstance(sub.get("format"), str):
                    found.setdefault(name, sub["format"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(bundle.get("components", {}).get("schemas", {}))
    return found


FORMATTED = {}  # populated in main() from the bundle


def fictionalize(node, key=None):
    """Rebuild `node` with the same shape and types but invented values."""
    if isinstance(node, dict):
        return {k: fictionalize(v, k) for k, v in node.items()}
    if isinstance(node, list):
        return [fictionalize(v, key) for v in node[:MAX_ARRAY]]

    if key in FICTION:
        want = FICTION[key]
        # Preserve the JSON type the API actually used for this key: Schoology
        # returns the same id as 5 here and "5" there, and the example should
        # show whichever this endpoint really sent.
        if isinstance(node, str) and not isinstance(want, str):
            return str(want)
        if isinstance(node, bool):
            return bool(want) if not isinstance(want, str) else True
        if isinstance(node, int) and isinstance(want, str):
            return int(want) if want.lstrip("-").isdigit() else 1
        return want

    # No entry for this key: emit a type-correct placeholder rather than
    # anything derived from the probe.
    if isinstance(node, bool):
        return True
    if isinstance(node, str) and key in FORMATTED:
        filler = FORMAT_FILLER.get(FORMATTED[key])
        if filler is not None:
            return filler
    for t, v in FALLBACK.items():
        if isinstance(node, t):
            return v
    return None


def repair(bundle, path, example):
    """Fix values that violate an `enum` or a `format` in their own schema.

    The generic placeholders can't know that `synced` is `0|1`, that
    `message_status` is `read|unread`, or that `web_url` must parse as a URI.
    Rather than teach FICTION every constraint by hand (which silently rots as
    the spec grows), validate the example and let the schema supply a legal
    value. Enum members and format fillers both come from the spec side, never
    from probe data.
    """
    from jsonschema import Draft202012Validator, FormatChecker

    root = dict(bundle)
    ptr = path.replace("~", "~0").replace("/", "~1")
    root["$ref"] = f"#/paths/{ptr}/get/responses/200/content/application~1json/schema"
    validator = Draft202012Validator(root, format_checker=FormatChecker())

    used = set()
    for _ in range(10):  # a repair can expose a nested one; converges quickly
        errors = [e for e in validator.iter_errors(example)
                  if e.validator in ("enum", "format")]
        if not errors:
            break
        progressed = False
        for err in errors:
            if err.validator == "enum":
                choices = [c for c in err.validator_value if c is not None]
                if not choices:
                    continue
                # Prefer a member matching the type the API actually returned.
                same = [c for c in choices if isinstance(c, type(err.instance))]
                pick = (same or choices)[0]
            else:
                pick = FORMAT_FILLER.get(err.validator_value)
                if pick is None:
                    continue
            if not err.absolute_path:
                continue
            target = example
            for key in list(err.absolute_path)[:-1]:
                target = target[key]
            target[list(err.absolute_path)[-1]] = pick
            progressed = True
            if isinstance(pick, str):
                used.add(pick)
        if not progressed:
            break
    return used


def collect_strings(node, acc=None):
    acc = set() if acc is None else acc
    if isinstance(node, dict):
        for v in node.values():
            collect_strings(v, acc)
    elif isinstance(node, list):
        for v in node:
            collect_strings(v, acc)
    elif isinstance(node, str):
        acc.add(node)
    return acc


def main():
    if not os.path.exists(PROBE_INDEX):
        sys.exit(f"{PROBE_INDEX} not found — run scripts/probe.py first "
                 f"(needs live credentials). spec/examples.json left untouched.")

    bundle = json.load(open("dist/openapi.strict.json"))
    FORMATTED.update(formatted_fields(bundle))
    paths = bundle["paths"]

    def match(concrete):
        concrete = re.sub(r"^/v1", "", concrete.split("?")[0])
        parts = concrete.split("/")
        best, best_score = None, -1
        for tmpl in paths:
            tparts = tmpl.split("/")
            if len(tparts) != len(parts):
                continue
            if not re.match("^" + re.sub(r"\{[^}]+\}", r"[^/]+", tmpl) + "$", concrete):
                continue
            score = sum(1 for tp, cp in zip(tparts, parts)
                        if not tp.startswith("{") and tp == cp)
            if score > best_score:
                best, best_score = tmpl, score
        return best

    index = json.load(open(PROBE_INDEX))
    out, skipped = {}, 0
    for key, info in sorted(index.items()):
        if info["status"] != 200:
            continue
        try:
            body = json.load(open(f"probe/{key}.json"))
        except (FileNotFoundError, json.JSONDecodeError):
            skipped += 1
            continue
        tmpl = match(info["path"])
        if not tmpl or "get" not in paths[tmpl]:
            skipped += 1
            continue
        # First probe for a path wins; later ones are usually the same endpoint
        # with different query parameters.
        out.setdefault(tmpl, fictionalize(body))

    from_enums = set()
    for tmpl, example in out.items():
        from_enums |= repair(bundle, tmpl, example)

    # Safety net for the invariant this whole script exists to hold: every
    # string in the output must trace back to FICTION, a literal placeholder,
    # or an enum member taken from the spec. If fictionalize() ever grows a
    # path that copies a probe value through, this fails before anything is
    # written.
    allowed = ({str(v) for v in FICTION.values()} | {FALLBACK[str], ""}
               | set(FORMAT_FILLER.values()) | from_enums)
    escaped = sorted(s for s in collect_strings(out) if s not in allowed)
    if escaped:
        for s in escaped[:20]:
            print(f"ERROR value not from FICTION: {s!r}", file=sys.stderr)
        sys.exit(f"{len(escaped)} example value(s) may derive from probe data; "
                 f"refusing to write {OUT}")

    json.dump(out, open(OUT, "w"), indent=1, sort_keys=True)
    print(f"wrote {len(out)} response examples -> {OUT} ({skipped} probes skipped)")
    print(f"  all {len(collect_strings(out))} distinct strings trace to FICTION")


if __name__ == "__main__":
    main()
