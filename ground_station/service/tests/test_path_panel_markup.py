"""Guard: every q('...') / getElementById('...') in path-panel.js must have a
matching id="..." in the panel's buildHTML() markup.

Catches bugs like "T18: the 3D view is unreachable because its markup was
never added" — the code referenced IDs that the template never emitted.

Run:  python -m pytest ground_station/service/tests/test_path_panel_markup.py -v
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

JS_PATH = Path(__file__).resolve().parents[3] / "docs" / "dashboard-platform" / "shell" / "plugins" / "path-panel.js"


def _get_js_source() -> str:
    if not JS_PATH.exists():
        raise unittest.SkipTest(f"path-panel.js not found at {JS_PATH}")
    return JS_PATH.read_text(encoding="utf-8")


class TestPathPanelMarkup(unittest.TestCase):
    """Verify that every DOM ID queried by the panel exists in its markup."""

    def test_q_ids_exist_in_markup(self):
        src = _get_js_source()

        # 1. Collect all literal IDs from q('...') calls.
        q_calls = re.findall(r"""q\(\s*['\"]([^'\"]+)['\"]\s*\)""", src)

        # 2. Collect all literal IDs from getElementById('...') calls.
        gei_calls = re.findall(
            r"""getElementById\(\s*['\"]([^'\"]+)['\"]\s*\)""", src
        )

        # Union of all queried IDs
        queried = sorted(set(q_calls + gei_calls))

        # 3. Extract all id="..." from the buildHTML function.
        #    Dynamically generated IDs (created inside render functions like
        #    renderWaypointTable) are intentionally excluded — they are added
        #    to the DOM imperatively and do not belong in the static markup.
        DYNAMIC_IDS = {"pp-add-wp"}  # created in renderWaypointTable() line ~477

        # 3. Extract all id="..." from the buildHTML function.
        #    Grab the function body by finding `function buildHTML()` and
        #    its return array (strings between '<' tags with id= attributes).
        build_match = re.search(
            r"function buildHTML\(\)\s*\{",
            src,
        )
        if not build_match:
            self.fail("Could not find buildHTML() in path-panel.js")

        # Find the return statement and collect all string literals
        body_start = build_match.end()
        # Get everything from function start to end of file, then find the return array
        body = src[body_start:]

        # Extract the return array: everything between `return [` and `].join(`
        ret_match = re.search(
            r"return\s*\[(.+?)\]\.join\(",
            body,
            re.DOTALL,
        )
        if not ret_match:
            self.fail("Could not find return array in buildHTML()")

        return_array = ret_match.group(1)

        # Find all id="..." in the markup strings (both single and double quoted)
        markup_ids = set(re.findall(r'''id\s*=\s*["']([^"']+)["']''', return_array))

        # 4. Assert every queried ID (except dynamic ones) is in the markup.
        missing = [qid for qid in queried if qid not in markup_ids and qid not in DYNAMIC_IDS]
        if missing:
            self.fail(
                "Queried IDs not found in markup:\n"
                + "\n".join(f"  - {m}" for m in missing)
                + f"\n\nAll queried IDs: {queried}\n"
                f"All markup IDs:   {sorted(markup_ids)}"
            )

    def test_no_undefined_q_calls(self):
        """Every q() call in event-listener code should reference an existing element."""
        src = _get_js_source()

        # Collect all q('id') calls
        q_calls = re.findall(r"""q\(\s*['\"]([^'\"]+)['\"]\s*\)""", src)
        all_queried = sorted(set(q_calls))

        # Extract markup IDs from buildHTML
        build_match = re.search(r"function buildHTML\(\)\s*\{", src)
        body = src[build_match.end():]
        ret_match = re.search(r"return\s*\[(.+?)\]\.join\(", body, re.DOTALL)
        return_array = ret_match.group(1)
        markup_ids = set(re.findall(r'''id\s*=\s*["']([^"']+)["']''', return_array))

        # Any ID not in markup (or dynamically generated) is a broken reference
        DYNAMIC_IDS = {"pp-add-wp"}
        broken = [qid for qid in all_queried if qid not in markup_ids and qid not in DYNAMIC_IDS]
        self.assertEqual(
            broken, [],
            f"Some q() references have no matching id=\"...\" in markup: {broken}",
        )


if __name__ == "__main__":
    unittest.main()
