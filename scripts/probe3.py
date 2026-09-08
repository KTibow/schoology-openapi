"""Third probe pass: singles, folders, resources and group content.

Every target is discovered from `/users/me` and the collections that hang off
it — nothing is hardcoded, so this file holds no real school's identifiers and
keeps working when the account's enrollment changes. Run probe.py and probe2.py
first; this pass reads their saved corpus. See scripts/probe_lib.py.
"""
import json, os, sys
sys.path.insert(0, "scripts")
from sgy import get, json_body
import probe_lib

index = json.load(open("probe/_index.json"))
R = probe_lib.require


def probe(key, path):
    s, h, b = get(path)
    print(f"{s}  GET {path}")
    index[key] = {"path": path, "status": s}
    if s == 200:
        try: body = json_body(b)
        except Exception: body = b[:200].decode("utf-8","replace")
        json.dump(body, open(f"probe/{key}.json","w"), indent=1)
        return body
    return None


# --- who and where -------------------------------------------------------
UID = R("UID", probe_lib.me("id"))
SCHOOL = R("SCHOOL", probe_lib.me("school_id"))
SCHOOL_UID = probe_lib.me("school_uid")

_sections = probe_lib.load("users_me_sections", {})
SID = R("SID", probe_lib.first(_sections, "section", "id"))
CID = R("CID", probe_lib.first(_sections, "section", "course_id"))
GID = probe_lib.first(probe_lib.load("users_me_groups", {}), "group", "id")

# --- folders -------------------------------------------------------------
# A child folder of the materials root, when the root has one.
FOLDER = probe_lib.first(probe_lib.load("course_folder_root", {}), "folder-item",
                         "id", where=lambda r: r.get("type") == "folder")
if FOLDER:
    probe("course_folder_child", f"/v1/courses/{CID}/folder/{FOLDER}")
probe("course_folder_root2", f"/v1/courses/{CID}/folder/0")

# --- collections + resources --------------------------------------------
_collections = probe_lib.load("collections", {})
COL_HOME = probe_lib.first(_collections, "collection", "id",
                           where=lambda r: str(r.get("is_default")) == "1")
COL_REALM = probe_lib.first(_collections, "collection", "id",
                            where=lambda r: bool(r.get("realm")))
if COL_HOME:
    probe("collection_resources_home", f"/v1/collections/{COL_HOME}/resources")
if COL_REALM:
    body = probe("collection_resources_group", f"/v1/collections/{COL_REALM}/resources")
    # Drill into a resource folder, when the collection root has one.
    res_folder = probe_lib.first(body or {}, "resources", "id",
                                 where=lambda r: r.get("type") == "folder")
    if res_folder:
        probe("collection_resources_group_f",
              f"/v1/collections/{COL_REALM}/resources?f={res_folder}")
        if GID:
            probe("group_resources_f", f"/v1/groups/{GID}/resources?f={res_folder}")
if GID:
    probe("group_resources_att", f"/v1/groups/{GID}/resources?with_attachments=1")

# --- messages ------------------------------------------------------------
MSG = probe_lib.first(probe_lib.load("messages_inbox", {}), "message", "id")
if MSG:
    probe("message_single", f"/v1/messages/{MSG}?keep_unread=TRUE")
probe("message_inbox_att", "/v1/messages/inbox?with_attachments=TRUE&limit=2")

# --- users extras --------------------------------------------------------
if SCHOOL_UID:
    probe("users_ext_me", f"/v1/users/ext/{SCHOOL_UID}")
probe("users_inactive", "/v1/users/inactive")
probe("users_me_grades_final", f"/v1/users/{UID}/grades?grading_period_ids=final")
probe("users_me_grades_all", f"/v1/users/{UID}/grades?include_all_enrollments=1")
# The search term only has to exercise the parameter; a bare letter matches
# broadly without putting anyone's name in the repo.
probe("users_me_network_search", f"/v1/users/{UID}/network?search=a&page=0")
EVENT = probe_lib.first(probe_lib.load("users_me_events", {}), "event", "id")
if EVENT:
    probe("user_event_single", f"/v1/users/{UID}/events/{EVENT}")

# --- roles/groups misc ---------------------------------------------------
probe("groups_categories", "/v1/groups/categories")
probe("schools_list", "/v1/schools")
GP = probe_lib.first(probe_lib.load("gradingperiods", {}), "gradingperiods", "id")
if GP:
    probe("gradingperiods_single", f"/v1/gradingperiods/{GP}")
probe("reminders_global", "/v1/reminders/ungraded")

# --- section extras ------------------------------------------------------
GRADE_ITEM = probe_lib.first(probe_lib.load("section_grade_items", {}),
                             "assignment", "id")
if GRADE_ITEM:
    probe("section_grade_item", f"/v1/sections/{SID}/grade_items/{GRADE_ITEM}")
probe("section_assignments_tags", f"/v1/sections/{SID}/assignments?with_tags=1&limit=2")
probe("section_assignments_dropbox",
      f"/v1/sections/{SID}/assignments?with_dropbox_stats=TRUE&limit=2")
probe("section_posts", f"/v1/sections/{SID}/posts")

# --- group content singles ----------------------------------------------
if GID:
    did = probe_lib.first(probe("group_discussions", f"/v1/groups/{GID}/discussions") or {},
                          "discussion", "id")
    if did:
        probe("group_discussion_single", f"/v1/groups/{GID}/discussions/{did}")
        probe("group_discussion_comments", f"/v1/groups/{GID}/discussions/{did}/comments")
    upd = probe_lib.first(probe("group_updates", f"/v1/groups/{GID}/updates") or {},
                          "update", "id")
    if upd:
        probe("group_update_single", f"/v1/groups/{GID}/updates/{upd}")
        probe("group_update_comments", f"/v1/groups/{GID}/updates/{upd}/comments")
        probe("like_group_update", f"/v1/like/{upd}")

# --- other sections from the user's enrollment ---------------------------
for i, sec in enumerate(probe_lib.records(_sections, "section")[1:4]):
    sid2 = str(sec["id"])
    probe(f"section{i+2}", f"/v1/sections/{sid2}")
    probe(f"section{i+2}_assignments", f"/v1/sections/{sid2}/assignments")
    probe(f"section{i+2}_events",
          f"/v1/sections/{sid2}/events?start_date=2026-01-01&end_date=2027-01-01")
    probe(f"section{i+2}_pages", f"/v1/sections/{sid2}/pages")
    probe(f"section{i+2}_documents", f"/v1/sections/{sid2}/documents")

# --- school realm materials ---------------------------------------------
probe("school_documents", f"/v1/schools/{SCHOOL}/documents")
probe("school_events", f"/v1/schools/{SCHOOL}/events")

json.dump(index, open("probe/_index.json","w"), indent=1)
print("index updated")
