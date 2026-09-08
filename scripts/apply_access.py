#!/usr/bin/env python3
"""Write x-student-access into spec/paths/*.yaml from probe evidence only.

The annotation means one thing: "this exact request was made with a live student
account and this is what came back". Anything not observed is `unknown`. In
particular every non-GET operation is `unknown`, because the probe policy is
read-only and there is no way to learn whether a student may POST/PUT/DELETE
without actually doing it to a real school's data.

Mapping from probe/_access.json:
  2xx / 3xx  -> ok
  403        -> forbidden
  400 / 404  -> unknown   (the fixture id was wrong; says nothing about access)
  not probed -> unknown

Run: python3 scripts/probe.py && python3 scripts/apply_access.py
"""
import glob
import json
import os
import re
import sys

ACCESS = "probe/_access.json"     # spec-driven sweep: operationId -> status
INDEX = "probe/_index.json"       # older hand-written corpus: concrete paths
BUNDLE = "dist/openapi.strict.json"
METHODS = ("get", "post", "put", "delete", "patch")


def verdict(status):
    if status == 403:
        return "forbidden"
    if 200 <= status < 400:
        return "ok"
    return "unknown"


def match_template(concrete, paths):
    """Spec path template for a concrete probed path."""
    concrete = re.sub(r"^/v1", "", concrete.split("?")[0])
    parts = concrete.split("/")
    best, score = None, -1
    for tmpl in paths:
        tp = tmpl.split("/")
        if len(tp) != len(parts):
            continue
        if not re.match("^" + re.sub(r"\{[^}]+\}", r"[^/]+", tmpl) + "$", concrete):
            continue
        s = sum(1 for a, b in zip(tp, parts) if not a.startswith("{") and a == b)
        if s > score:
            best, score = tmpl, s
    return best


def main():
    if not os.path.exists(ACCESS):
        sys.exit(f"{ACCESS} not found — run scripts/probe.py first "
                 f"(needs live credentials).")
    doc = json.load(open(BUNDLE))
    tmpl_of = {o["operationId"]: p
               for p, it in doc["paths"].items() for m, o in it.items()
               if m in METHODS}

    # Evidence source 1: the spec-driven sweep, keyed by operationId. Each
    # record holds one status per scope (realm, reminder type, ...) probed.
    statuses, by_scope = {}, {}
    for oid, rec in json.load(open(ACCESS)).items():
        for scope, status in rec["outcomes"].items():
            statuses.setdefault(oid, set()).add(status)
            if scope != "-":
                by_scope.setdefault(oid, {})[scope] = status

    # Source 2: the older hand-written corpus. It reached a few item endpoints
    # the sweep cannot (it has no id for a discussion post, say), so merging the
    # two covers strictly more than either alone.
    verified = set()
    if os.path.exists(INDEX):
        by_tmpl = {t: oid for oid, t in tmpl_of.items()}
        for rec in json.load(open(INDEX)).values():
            t = match_template(rec["path"], doc["paths"])
            if not t or "get" not in doc["paths"][t]:
                continue
            oid = doc["paths"][t]["get"]["operationId"]
            statuses.setdefault(oid, set()).add(rec["status"])
            if rec["status"] == 200:
                verified.add(oid)

    # A 2xx anywhere proves reachability and outranks a 403 seen in another
    # scope or with a different fixture id. An operation offered in five realms
    # is not "forbidden" because one realm refused it -- `listPosts` answers 403
    # for sections, groups and schools but 200 for users, and calling that
    # forbidden would be simply wrong.
    observed = {}
    for oid, st in statuses.items():
        observed[oid] = "ok" if any(200 <= s < 400 for s in st) else \
                        ("forbidden" if 403 in st else "unknown")

    def scope_note(oid):
        """`realm=ok` map, but only when the scopes actually disagree."""
        seen = by_scope.get(oid) or {}
        verdicts = {k: verdict(v) for k, v in seen.items()}
        if len(set(verdicts.values())) < 2:
            return None
        return verdicts

    changes, unverified, mixed = [], [], []
    counts = {"ok": 0, "forbidden": 0, "unknown": 0}
    for path in sorted(glob.glob("spec/paths/*.yaml")):
        lines = open(path).read().splitlines(keepends=True)
        drop = set()

        # Operation blocks start at a method key nested under a path item.
        starts = [i for i, l in enumerate(lines)
                  if re.match(r"^  (" + "|".join(METHODS) + r"):\s*$", l)]
        for n, start in enumerate(starts):
            end = starts[n + 1] if n + 1 < len(starts) else len(lines)
            block = range(start, end)
            oid_i = next((i for i in block
                          if re.match(r"^    operationId: ", lines[i])), None)
            acc_i = next((i for i in block
                          if re.match(r"^    x-student-access: ", lines[i])), None)
            # This script is re-run after every sweep, and it replaces the
            # single annotation line with a two-line block. Drop any by-realm
            # line already there, or the second run duplicates the mapping key
            # and the bundle stops resolving.
            for i in block:
                if re.match(r"^    x-student-access-by-realm: ", lines[i]):
                    drop.add(i)
            if oid_i is None or acc_i is None:
                continue
            oid = lines[oid_i].split(":", 1)[1].strip()
            method = re.match(r"^  (\w+):", lines[start]).group(1)

            # Non-GET is unknowable under a read-only probe policy, full stop.
            want = observed.get(oid, "unknown") if method == "get" else "unknown"
            have = lines[acc_i].split(":", 1)[1].strip()
            counts[want] += 1
            note = scope_note(oid) if method == "get" else None
            block_text = f"    x-student-access: {want}\n"
            if note:
                rendered = ", ".join(f"{k}: {v}" for k, v in sorted(note.items()))
                block_text += f"    x-student-access-by-realm: {{ {rendered} }}\n"
            if have != want or note:
                lines[acc_i] = block_text
                if have != want:
                    changes.append((oid, method.upper(), have, want))
                if note:
                    mixed.append((oid, note))

            # x-verified means "a live response was validated against this
            # schema", which only verify_live.py can establish, and only for a
            # saved 200 body. Anything else claiming it is unbacked.
            ver_i = next((i for i in block
                          if re.match(r"^    x-verified: ", lines[i])), None)
            if ver_i is not None and not (oid in verified and method == "get"):
                # Defer the delete: `starts` indexes this list, so removing a
                # line now would shift every later block.
                drop.add(ver_i)
                unverified.append((oid, method.upper()))
        for i in sorted(drop, reverse=True):
            del lines[i]
        open(path, "w").write("".join(lines))

    print(f"x-student-access now: ok={counts['ok']} forbidden={counts['forbidden']} "
          f"unknown={counts['unknown']}")
    print(f"changed {len(changes)} annotations\n")
    demoted = [c for c in changes if c[3] == "unknown"]
    flipped = [c for c in changes if c[3] != "unknown"]
    print(f"  {len(flipped)} corrected against a live observation:")
    for oid, m, a, b in sorted(flipped):
        print(f"    {oid:32} {m:6} {a} -> {b}")
    print(f"\n  {len(demoted)} demoted to unknown (never observed):")
    for oid, m, a, b in sorted(demoted)[:12]:
        print(f"    {oid:32} {m:6} {a} -> unknown")
    if len(demoted) > 12:
        print(f"    ... and {len(demoted) - 12} more")
    print(f"\n  {len(mixed)} operations whose access differs by realm:")
    for oid, note in sorted(mixed):
        print(f"    {oid:28} " + "  ".join(f"{k}={v}" for k, v in sorted(note.items())))
    print(f"\n  {len(unverified)} unbacked x-verified removed:")
    for oid, m in sorted(unverified):
        print(f"    {oid:32} {m}")


if __name__ == "__main__":
    main()
