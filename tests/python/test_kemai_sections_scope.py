"""Engine-level proof that the tab engine can be scoped to one section.

The backfill tool relies on `sections=` narrowing both queue preparation and the
request loop; the tool-level test only proved the argument is passed through.
These tests assert the engine itself never touches another `item_tab_*` stream.
"""

import unittest
from unittest.mock import MagicMock, patch

import kemai_sections as sections


class ScopedSectionsTests(unittest.TestCase):
    def _run(self, scope):
        """Run the engine with a fake cursor that yields no candidates."""
        statements = []

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, statement, params=None):
                statements.append((statement, params))

            def executemany(self, statement, params):
                statements.append((statement, params))

            def fetchall(self):
                return []

            def fetchone(self):
                return {"n": 0}

        conn = MagicMock()
        conn.cursor.return_value = Cursor()
        remaining_calls = []

        def fake_one(_conn, statement, params=None):
            remaining_calls.append((statement, params))
            return {"n": 0}

        with patch.object(sections, "one", side_effect=fake_one), \
             patch.object(sections, "section_batch") as batch:
            sections.sync_sections(conn, 1, rate=0.5, batch_size=2,
                                   only_ids=["a"], sections=scope)
        batch.assert_not_called()  # the fake cursor never yields a candidate
        return statements, remaining_calls

    @staticmethod
    def _streams(statements):
        found = set()
        for _statement, params in statements:
            for value in (params or []):
                if isinstance(value, str) and value.startswith("item_tab_"):
                    found.add(value)
        return found

    def test_single_section_never_touches_another_stream(self):
        statements, _remaining = self._run(["barcode"])
        self.assertEqual(self._streams(statements), {"item_tab_barcode"})

    def test_scope_reaches_the_remaining_check(self):
        _statements, remaining = self._run(["barcode"])
        scoped = {value for _statement, params in remaining
                  for value in (params or [])
                  if isinstance(value, str) and value.startswith("item_tab_")}
        self.assertEqual(scoped, {"item_tab_barcode"})

    def test_selected_candidate_calls_the_batch_only_for_that_section(self):
        calls = []
        state = {"query": False, "selected": False}

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, statement, params=None):
                if "SELECT source_key FROM sync_objects" in statement:
                    state["query"] = True

            def executemany(self, statement, params):
                pass

            def fetchall(self):
                if state.pop("query", False) and not state["selected"]:
                    state["selected"] = True
                    return [{"source_key": "a"}]
                return []

            def fetchone(self):
                return {"n": 0}

        def fake_batch(section, ids, session="kemai_probe", interval_seconds=0):
            calls.append((section, list(ids)))
            return [{"id": item, "code": 1, "error": "offline"} for item in ids]

        conn = MagicMock()
        conn.cursor.return_value = Cursor()
        with patch.object(sections, "one", return_value={"n": 0}), \
             patch.object(sections, "section_batch", side_effect=fake_batch), \
             patch.object(sections, "checkpoint"), \
             patch.object(sections.time, "sleep"):
            sections.sync_sections(conn, 1, rate=0.5, batch_size=2,
                                   only_ids=["a"], sections=["barcode"])
        self.assertEqual(calls, [("barcode", ["a"])])

    def test_unscoped_run_still_covers_every_section(self):
        statements, _remaining = self._run(None)
        self.assertEqual(self._streams(statements),
                         {f"item_tab_{name}" for name in sections.SECTIONS})


if __name__ == "__main__":
    unittest.main()
