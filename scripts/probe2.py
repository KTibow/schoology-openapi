import json, os, sys
sys.path.insert(0, "scripts")
from sgy import get, json_body
import probe_lib

# Every target is derived from what scripts/probe.py already saved, so no real
# identifier is written down here. See scripts/probe_lib.py.
R = probe_lib.require
UID = R("UID", probe_lib.me("id"))
SCHOOL = R("SCHOOL", probe_lib.me("school_id"))
BUILDING = probe_lib.me("building_id") or SCHOOL

def probe(key, path):
    s, h, b = get(path)
    print(f"{s}  GET {path}")
    if s == 200:
        json.dump(json_body(b), open(f"probe/{key}.json","w"), indent=1)
    else:
        open(f"probe/{key}.{s}.txt","w").write(b[:500].decode("utf-8","replace"))
    return s, json_body(b) if s==200 else b[:300].decode("utf-8","replace")

_sections = json.load(open("probe/users_me_sections.json"))
sid = R("sid", probe_lib.first(_sections, "section", "id"))
cid = R("cid", probe_lib.first(_sections, "section", "course_id"))
# A section that actually carries a school (SIS) code, for the lookup-by-code path.
school_code = probe_lib.first(_sections, "section", "section_school_code")
print("using enrolled course", cid, "section", sid)

probe("course_enrolled", f"/v1/courses/{cid}")
probe("course_enrolled_sections", f"/v1/courses/{cid}/sections")
probe("section", f"/v1/sections/{sid}")
probe("section_assignments", f"/v1/sections/{sid}/assignments")
probe("section_grade_items", f"/v1/sections/{sid}/grade_items")
probe("section_enrollments", f"/v1/sections/{sid}/enrollments")
probe("section_grades", f"/v1/sections/{sid}/grades")
probe("section_grading_periods", f"/v1/sections/{sid}/grading_periods")
probe("section_grading_categories", f"/v1/sections/{sid}/grading_categories")
probe("section_grading_scales", f"/v1/sections/{sid}/grading_scales")
probe("section_grading_rubrics", f"/v1/sections/{sid}/grading_rubrics")
probe("section_grading_groups", f"/v1/sections/{sid}/grading_groups")
probe("section_attendance", f"/v1/sections/{sid}/attendance")
probe("section_completion", f"/v1/sections/{sid}/completion")
probe("section_discussions", f"/v1/sections/{sid}/discussions")
probe("section_documents", f"/v1/sections/{sid}/documents")
probe("section_events", f"/v1/sections/{sid}/events")
probe("section_updates", f"/v1/sections/{sid}/updates")
probe("section_albums", f"/v1/sections/{sid}/albums")
probe("section_pages", f"/v1/sections/{sid}/pages")
probe("section_packages", f"/v1/sections/{sid}/packages")
probe("section_web_packages", f"/v1/sections/{sid}/web_packages")
probe("section_reminders_ungraded", f"/v1/sections/{sid}/reminders/ungraded")
probe("user_grades_by_section", f"/v1/users/{UID}/grades?section_id={sid}")
probe("completion_user", f"/v1/sections/{sid}/completion/user/{UID}/{sid}")
probe("invites_sent", f"/v1/users/{UID}/invites/sent")
probe("courses_self_enrolled", "/v1/courses")
probe("groups_list", "/v1/groups")
probe("school", f"/v1/schools/{SCHOOL}")
probe("school_buildings", f"/v1/schools/{SCHOOL}/buildings")
probe("building", f"/v1/schools/{BUILDING}")
if school_code:
    probe("sections_by_code", f"/v1/sections?section_school_codes={school_code}")
else:
    print("skip sections_by_code: no section_school_code on any enrolled section")
