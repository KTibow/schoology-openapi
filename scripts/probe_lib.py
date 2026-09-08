"""Derive probe target ids from the saved corpus instead of hardcoding them.

Every id the probe scripts need is already reachable from `/users/me` and the
collections that hang off it, so none of them has to be written down. That
matters twice over: hardcoded ids are real identifiers belonging to a real
school, and they rot the moment the account's enrollment changes.

`probe/` is gitignored, so nothing here puts live data in the repo.
"""
import json, os

PROBE_DIR = "probe"


def load(key, default=None):
    """A saved probe body, or `default` if that probe has not run."""
    p = os.path.join(PROBE_DIR, f"{key}.json")
    if not os.path.exists(p):
        return default
    try:
        with open(p) as f:
            return json.load(f)
    except (ValueError, OSError):
        return default


def records(body, wrapper):
    """The list under `wrapper` in a collection body (`[]` when absent)."""
    if not isinstance(body, dict):
        return []
    items = body.get(wrapper)
    if isinstance(items, dict):          # single record, unwrapped by the API
        return [items]
    return items if isinstance(items, list) else []


def first(body, wrapper, field="id", where=None):
    """`field` of the first record under `wrapper` matching `where`.

    Returns a string (ids arrive as ints or numeric strings depending on the
    endpoint), or None when there is nothing to pick.
    """
    for rec in records(body, wrapper):
        if not isinstance(rec, dict):
            continue
        if where and not where(rec):
            continue
        val = rec.get(field)
        if val not in (None, "", 0, "0"):
            return str(val)
    return None


def me(field):
    """A field of the signed-in user, from the saved `/users/me` probe."""
    body = load("users_me")
    val = body.get(field) if isinstance(body, dict) else None
    return str(val) if val not in (None, "") else None


def require(name, value):
    """Fail loudly rather than probing a `None` into the URL."""
    if not value:
        raise SystemExit(
            f"{name} could not be derived from the probe corpus in {PROBE_DIR}/ "
            f"— run scripts/probe.py first."
        )
    return value
