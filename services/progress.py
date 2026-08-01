"""Lesson progress roll-ups.

`user_progress` rows point at a lesson, and a lesson only knows its module, so
working out "how far through course X is student Y" needs the
lesson -> module -> course chain rebuilt in Python.
"""

from collections import defaultdict

from extensions import supabase
from services.db import fetch_all


def lesson_index():
    """Return (lesson_id -> course_id, course_id -> [lesson_id]) for the whole academy."""
    modules = fetch_all("modules", select="id, course_id")
    lessons = fetch_all("lessons", select="id, module_id, title, order_index")
    module_course = {m["id"]: m.get("course_id") for m in modules}

    lesson_course = {}
    course_lessons = defaultdict(list)
    for lesson in lessons:
        course_id = module_course.get(lesson.get("module_id"))
        if course_id:
            lesson_course[lesson["id"]] = course_id
            course_lessons[course_id].append(lesson["id"])
    return lesson_course, course_lessons, {l["id"]: l for l in lessons}


def _completed_rows(rows):
    return [r for r in rows if r.get("completed")]


def student_progress(student_id, enrolled_course_ids=()):
    """Per-course progress for one student, including courses not yet started."""
    lesson_course, course_lessons, lessons = lesson_index()
    rows = fetch_all("user_progress", select="*", filters={"student_id": student_id})

    done_per_course = defaultdict(set)
    last_activity = {}
    for row in _completed_rows(rows):
        course_id = lesson_course.get(row.get("lesson_id"))
        if not course_id:
            continue
        done_per_course[course_id].add(row["lesson_id"])
        stamp = row.get("completed_at")
        if stamp and stamp > last_activity.get(course_id, ""):
            last_activity[course_id] = stamp

    course_ids = set(enrolled_course_ids) | set(done_per_course)
    titles = {c["id"]: c["title"] for c in fetch_all("courses", select="id, title")}

    summary = []
    for course_id in course_ids:
        total = len(course_lessons.get(course_id, []))
        done = len(done_per_course.get(course_id, set()))
        summary.append(
            {
                "course_id": course_id,
                "title": titles.get(course_id, "Deleted course"),
                "completed": done,
                "total": total,
                "percent": round(done / total * 100) if total else 0,
                "last_activity": last_activity.get(course_id),
            }
        )
    summary.sort(key=lambda s: (-s["percent"], s["title"]))

    return {
        "courses": summary,
        "rows": rows,
        "lessons": lessons,
        "total_completed": len(_completed_rows(rows)),
        "xp_awarded": len([r for r in rows if r.get("xp_awarded")]),
    }


def course_completion(course_id, student_ids):
    """student_id -> {completed, total, percent} for a single course."""
    _, course_lessons, _ = lesson_index()
    lesson_ids = set(course_lessons.get(course_id, []))
    total = len(lesson_ids)
    result = {sid: {"completed": 0, "total": total, "percent": 0} for sid in student_ids}
    if not lesson_ids or not student_ids:
        return result

    rows = (
        supabase.table("user_progress")
        .select("student_id, lesson_id, completed")
        .in_("student_id", list(student_ids))
        .execute()
        .data
        or []
    )
    for row in rows:
        if row.get("completed") and row.get("lesson_id") in lesson_ids:
            bucket = result.get(row.get("student_id"))
            if bucket:
                bucket["completed"] += 1
    for bucket in result.values():
        bucket["percent"] = round(bucket["completed"] / total * 100) if total else 0
    return result
