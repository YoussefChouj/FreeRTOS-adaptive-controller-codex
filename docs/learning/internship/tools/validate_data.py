#!/usr/bin/env python3
"""Validate the internship learning data. SPEC section 6.

Standard library only. Exits 0 when everything passes and prints every
problem it found otherwise.

Usage:
    python tools/validate_data.py                # validate the shipped data/
    python tools/validate_data.py --data DIR     # validate another data dir
"""

import argparse
import json
import os
import sys

MODULES = {"M0", "M1", "M2", "M3", "M4", "M5", "M6"}
SIM_IDS = {"tilt", "zloop"}
LESSON_STATUSES = {"planned", "taught", "quizzed", "passed"}
BREAKPOINT_STATUSES = {"open", "retested", "fixed"}
GLOSSARY_KINDS = {"term", "phrase"}

GLOSSARY_REQUIRED = ("id", "kind", "zh", "pinyin", "en", "module", "lesson")
LESSON_REQUIRED = ("id", "module", "en", "zh", "status", "scores")
MODULE_REQUIRED = ("id", "en", "zh", "goal", "jd")
BREAKPOINT_REQUIRED = ("date", "lesson", "topic", "gap", "status")
PROGRESS_REQUIRED = ("updated", "target", "modules", "lessons", "breakpoints")


class Problems(object):
    """Collects problems and prints them as they are found."""

    def __init__(self):
        self.items = []

    def add(self, msg):
        self.items.append(msg)
        print("PROBLEM: %s" % msg)

    def count(self):
        return len(self.items)


def load_json(path, label, problems):
    if not os.path.isfile(path):
        problems.add("%s file is missing: %s" % (label, path))
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as exc:
        problems.add("%s does not parse as JSON: %s (%s)" % (label, path, exc))
        return None
    except OSError as exc:
        problems.add("%s cannot be read: %s (%s)" % (label, path, exc))
        return None


def check_glossary(glossary, problems):
    if glossary is None:
        return set()
    if not isinstance(glossary, list):
        problems.add("glossary.json must be a JSON array")
        return set()

    seen = set()
    lesson_ids = set()
    for index, entry in enumerate(glossary):
        where = "glossary.json[%d]" % index
        if not isinstance(entry, dict):
            problems.add("%s must be an object" % where)
            continue

        for field in GLOSSARY_REQUIRED:
            if field not in entry:
                problems.add("%s is missing required field '%s'" % (where, field))

        gid = entry.get("id")
        if gid is not None:
            if gid in seen:
                problems.add("duplicate glossary id '%s' at %s" % (gid, where))
            seen.add(gid)
            lesson_ids.add(entry.get("lesson"))

        kind = entry.get("kind")
        if kind is not None and kind not in GLOSSARY_KINDS:
            problems.add("%s has kind '%s', expected one of %s"
                         % (where, kind, sorted(GLOSSARY_KINDS)))

        module = entry.get("module")
        if module is not None and module not in MODULES:
            problems.add("%s has module '%s', expected M0-M6" % (where, module))

    return seen


def check_progress(progress, data_dir, problems):
    if progress is None:
        return set(), set()
    if not isinstance(progress, dict):
        problems.add("progress.json must be a JSON object")
        return set(), set()

    for field in PROGRESS_REQUIRED:
        if field not in progress:
            problems.add("progress.json is missing required field '%s'" % field)

    # --- modules ---
    modules = progress.get("modules")
    if modules is not None and not isinstance(modules, list):
        problems.add("progress.json 'modules' must be an array")
        modules = []
    module_ids = set()
    for index, module in enumerate(modules or []):
        where = "progress.json modules[%d]" % index
        if not isinstance(module, dict):
            problems.add("%s must be an object" % where)
            continue
        for field in MODULE_REQUIRED:
            if field not in module:
                problems.add("%s is missing required field '%s'" % (where, field))
        mid = module.get("id")
        if mid is not None:
            if mid not in MODULES:
                problems.add("%s has id '%s', expected M0-M6" % (where, mid))
            if mid in module_ids:
                problems.add("duplicate module id '%s' at %s" % (mid, where))
            module_ids.add(mid)

    # --- lessons ---
    lessons = progress.get("lessons")
    if lessons is not None and not isinstance(lessons, list):
        problems.add("progress.json 'lessons' must be an array")
        lessons = []
    lesson_ids = set()
    for index, lesson in enumerate(lessons or []):
        where = "progress.json lessons[%d]" % index
        if not isinstance(lesson, dict):
            problems.add("%s must be an object" % where)
            continue
        for field in LESSON_REQUIRED:
            if field not in lesson:
                problems.add("%s is missing required field '%s'" % (where, field))

        lid = lesson.get("id")
        if lid is not None:
            if lid in lesson_ids:
                problems.add("duplicate lesson id '%s' at %s" % (lid, where))
            lesson_ids.add(lid)

        module = lesson.get("module")
        if module is not None and module not in MODULES:
            problems.add("%s has module '%s', expected M0-M6" % (where, module))

        status = lesson.get("status")
        if status is not None and status not in LESSON_STATUSES:
            problems.add("%s has status '%s', expected one of %s"
                         % (where, status, sorted(LESSON_STATUSES)))

        scores = lesson.get("scores")
        if scores is not None and not isinstance(scores, dict):
            problems.add("%s has 'scores' that is not an object" % where)

        sims = lesson.get("sims")
        if sims is not None:
            if not isinstance(sims, list):
                problems.add("%s has 'sims' that is not an array" % where)
            else:
                for sim in sims:
                    if sim not in SIM_IDS:
                        problems.add("%s has sim id '%s', expected one of %s"
                                     % (where, sim, sorted(SIM_IDS)))

        lesson_file = lesson.get("file")
        if lesson_file is not None:
            path = os.path.join(data_dir, *str(lesson_file).split("/"))
            if not os.path.isfile(path):
                problems.add("%s lesson file does not exist: data/%s" % (where, lesson_file))

    # --- breakpoints ---
    breakpoints = progress.get("breakpoints")
    if breakpoints is not None and not isinstance(breakpoints, list):
        problems.add("progress.json 'breakpoints' must be an array")
        breakpoints = []
    for index, bp in enumerate(breakpoints or []):
        where = "progress.json breakpoints[%d]" % index
        if not isinstance(bp, dict):
            problems.add("%s must be an object" % where)
            continue
        for field in BREAKPOINT_REQUIRED:
            if field not in bp:
                problems.add("%s is missing required field '%s'" % (where, field))

        status = bp.get("status")
        if status is not None and status not in BREAKPOINT_STATUSES:
            problems.add("%s has status '%s', expected one of %s"
                         % (where, status, sorted(BREAKPOINT_STATUSES)))

        bp_lesson = bp.get("lesson")
        if bp_lesson is not None and bp_lesson not in lesson_ids:
            problems.add("%s references unknown lesson '%s'" % (where, bp_lesson))

    return lesson_ids, module_ids


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    default_data = os.path.normpath(os.path.join(here, os.pardir, "data"))

    parser = argparse.ArgumentParser(description="Validate the internship learning data.")
    parser.add_argument("--data", default=default_data,
                        help="data directory to validate (default: the shipped data/)")
    args = parser.parse_args(argv)

    data_dir = os.path.abspath(args.data)
    if not os.path.isdir(data_dir):
        print("PROBLEM: data directory does not exist: %s" % data_dir)
        print("FAILED: 1 problem")
        return 1

    problems = Problems()

    glossary = load_json(os.path.join(data_dir, "glossary.json"), "glossary.json", problems)
    progress = load_json(os.path.join(data_dir, "progress.json"), "progress.json", problems)

    glossary_ids = check_glossary(glossary, problems)
    lesson_ids, _module_ids = check_progress(progress, data_dir, problems)

    # Every glossary entry must point at a lesson that exists.
    for index, entry in enumerate(glossary or []):
        if not isinstance(entry, dict):
            continue
        gl_lesson = entry.get("lesson")
        if gl_lesson is not None and gl_lesson not in lesson_ids:
            problems.add("glossary.json[%d] (id '%s') references unknown lesson '%s'"
                         % (index, entry.get("id"), gl_lesson))

    count = problems.count()
    if count == 0:
        print("OK: data is valid (%d glossary entries, %d lessons) in %s"
              % (len(glossary_ids), len(lesson_ids), data_dir))
        return 0

    print("FAILED: %d problem(s) in %s" % (count, data_dir))
    return 1


if __name__ == "__main__":
    sys.exit(main())
