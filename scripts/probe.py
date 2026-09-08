import json, os, re, sys
sys.path.insert(0, "scripts")
from sgy import get, json_body
import probe_lib

os.makedirs("probe", exist_ok=True)
results = {}

def probe(key, path, save=True):
    s, h, b = get(path)
    results[key] = {"path": path, "status": s}
    print(f"{s}  GET {path}")
    if save and s == 200:
        body = json_body(b)
        with open(f"probe/{key}.json", "w") as f:
            json.dump(body, f, indent=1)
    return s, (json_body(b) if s == 200 else b[:200].decode("utf-8","replace"))

# discover self
s, b = probe("users_me", "/v1/users/me")
uid = b.get("id") if isinstance(b, dict) else None
school_id = b.get("school_id") if isinstance(b, dict) else None
print("UID:", uid, "school:", school_id)

probe("users_uid", f"/v1/users/{uid}")
probe("users_me_sections", f"/v1/users/{uid}/sections")
probe("users_me_groups", f"/v1/users/{uid}/groups")
probe("users_list", "/v1/users")
probe("users_me_grades", f"/v1/users/{uid}/grades")
probe("users_me_network", f"/v1/users/{uid}/network")
probe("users_me_requests_friends", f"/v1/users/{uid}/requests/friends")
probe("users_me_invites_sent", f"/v1/users/{uid}/invites/sent")
probe("users_me_invites_received", f"/v1/users/{uid}/invites/received")
probe("users_me_updates", f"/v1/users/{uid}/updates")
probe("users_me_events", f"/v1/users/{uid}/events")
probe("users_me_sections_grades", f"/v1/users/{uid}/grades?section_id=0")  # will 404/403 probably
probe("app_user_info", "/v1/app-user-info")
probe("users_languages", "/v1/users/languages")
probe("roles", "/v1/roles")
probe("search", "/v1/search?keywords=math")
probe("messages_inbox", "/v1/messages/inbox")
probe("messages_sent", "/v1/messages/sent")
probe("messages_recipients", "/v1/messages/recipients")
probe("collections", "/v1/collections")
probe("gradingperiods", "/v1/gradingperiods")

# A course to exercise the course/folder endpoints with. Derived from the
# user's own enrollments rather than written down: enrollment changes every
# year, and a literal id here would be a real school's identifier in the repo.
# Override with PROBE_COURSE_ID to aim at a specific (e.g. past-year) course.
COURSE = os.environ.get("PROBE_COURSE_ID") or probe_lib.first(
    probe_lib.load("users_me_sections", {}), "section", "course_id")
if COURSE:
    probe("course", f"/v1/courses/{COURSE}")
    probe("course_sections", f"/v1/courses/{COURSE}/sections")
    probe("course_folder_root", f"/v1/courses/{COURSE}/folder/0")
else:
    # An account with no sections (or a 403 on the listing) is not an error --
    # the rest of the corpus is still worth collecting.
    print("skip course probes: no enrolled section to derive a course from")

# find a section id
secs = json.load(open("probe/course_sections.json")) if os.path.exists("probe/course_sections.json") else {}
sid = None
if isinstance(secs, dict) and secs.get("section"):
    sid = str(secs["section"][0]["id"])
print("SECTION:", sid)
if sid:
    for key, p in [
        ("section", f"/v1/sections/{sid}"),
        ("section_assignments", f"/v1/sections/{sid}/assignments"),
        ("section_enrollments", f"/v1/sections/{sid}/enrollments"),
        ("section_grades", f"/v1/sections/{sid}/grades"),
        ("section_grading_periods", f"/v1/sections/{sid}/grading_periods"),
        ("section_grading_categories", f"/v1/sections/{sid}/grading_categories"),
        ("section_grading_scales", f"/v1/sections/{sid}/grading_scales"),
        ("section_grading_rubrics", f"/v1/sections/{sid}/grading_rubrics"),
        ("section_grading_groups", f"/v1/sections/{sid}/grading_groups"),
        ("section_attendance", f"/v1/sections/{sid}/attendance"),
        ("section_completion", f"/v1/sections/{sid}/completion"),
        ("section_discussions", f"/v1/sections/{sid}/discussions"),
        ("section_documents", f"/v1/sections/{sid}/documents"),
        ("section_events", f"/v1/sections/{sid}/events"),
        ("section_updates", f"/v1/sections/{sid}/updates"),
        ("section_albums", f"/v1/sections/{sid}/albums"),
        ("section_pages", f"/v1/sections/{sid}/pages"),
        ("section_packages", f"/v1/sections/{sid}/packages"),
        ("section_web_packages", f"/v1/sections/{sid}/web_packages"),
        ("section_reminders_ungraded", f"/v1/sections/{sid}/reminders/ungraded"),
        ("section_grade_items", f"/v1/sections/{sid}/grade_items"),
        ("section_media", f"/v1/sections/{sid}/media"),
    ]:
        probe(key, p)
    # drill into an assignment + comments + submission
    a = json.load(open("probe/section_assignments.json"))
    if a.get("assignment"):
        aid = str(a["assignment"][0]["id"])
        probe("assignment", f"/v1/sections/{sid}/assignments/{aid}")
        probe("assignment_comments", f"/v1/sections/{sid}/assignments/{aid}/comments")
        probe("assignment_with_attachments", f"/v1/sections/{sid}/assignments/{aid}?with_attachments=1")
        probe("submissions", f"/v1/sections/{sid}/submissions/{aid}/{uid}")
        probe("submissions_comments", f"/v1/sections/{sid}/submissions/{aid}/{uid}/comments")
    d = json.load(open("probe/section_discussions.json"))
    if d.get("discussion"):
        did = str(d["discussion"][0]["id"])
        probe("discussion", f"/v1/sections/{sid}/discussions/{did}")
        probe("discussion_comments", f"/v1/sections/{sid}/discussions/{did}/comments")

json.dump(results, open("probe/_index.json", "w"), indent=1)
print(json.dumps(results, indent=0))
