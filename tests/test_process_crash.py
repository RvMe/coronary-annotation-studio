"""Actual abrupt-process SQLite recovery, without GUI or clinical data.

The child calls os._exit from inside a SQLite trigger: Python finally blocks,
AnnotationStore rollback handlers and connection close/checkpoint cannot run.
Large uncommitted snapshots force dirty WAL pages beyond the tiny cache. These
tests cover process death, not physical power-loss/filesystem durability claims.
"""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from annotation_app.domain import apply_annotation
from annotation_app.storage import AnnotationStore
from tests.test_domain import annotation


REPO = Path(__file__).resolve().parents[1]
CRASH_EXIT = 73
CHILD = r'''
import os, sys
from annotation_app.domain import add_marker
from annotation_app.storage import AnnotationStore

database, mode = sys.argv[1:]
store = AnnotationStore(database)
state = store.load('synthetic-case', 'A', {'geometry_id': 'synthetic-geometry'})
store.connection.execute('PRAGMA cache_size=8')
store.connection.execute('PRAGMA cache_spill=ON')

def abruptly_exit():
    print('ABRUPT_PROCESS_EXIT:' + mode, flush=True)
    os._exit(73)

store.connection.create_function('abrupt_process_exit', 0, abruptly_exit)
if mode == 'mid_annotation_commit':
    # _write updates streams first. Killing BEFORE audit insert interrupts the
    # actual production commit between snapshot write and ledger write.
    store.connection.executescript(
        'CREATE TEMP TRIGGER die_before_audit BEFORE INSERT ON audit '
        'BEGIN SELECT abrupt_process_exit(); END;')
    changed = add_marker(state, 'LAD', 19.75)
    changed['view_state'] = {'draft': {'not_committed': 'UNCOMMITTED_SENTINEL_' * 65536}}
    store.commit(changed, 'must_not_be_committed')
elif mode == 'mid_view_autosave':
    store.connection.executescript(
        'CREATE TEMP TRIGGER die_after_view AFTER UPDATE OF state_json ON streams '
        'BEGIN SELECT abrupt_process_exit(); END;')
    store.save_view('synthetic-case', 'A', {'s_mm': 999, 'draft': {'not_committed': 'UNCOMMITTED_SENTINEL_' * 65536}})
elif mode == 'after_committed_annotation_and_draft':
    store.commit(add_marker(state, 'LAD', 24.75), 'durable_marker')
    store.save_view('synthetic-case', 'A', {'s_mm': 26.75, 'draft': {'a': 30, 'b': 40, 'label': {}, 'reason': 'recover me, not a diagnosis'}})
    abruptly_exit()
else:
    raise ValueError(mode)
raise AssertionError('Crash injection was not reached')
'''


class ActualProcessCrashTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="imagecasx-process-crash-")
        self.database = Path(self.temporary.name) / "测试 崩溃" / "annotations.sqlite"
        self.source = {"geometry_id": "synthetic-geometry"}
        self.view = {"path_id": "LAD", "s_mm": 15.125,
                     "draft": {"a": 12, "b": 18, "label": {}, "reason": "saved draft only"}}
        with AnnotationStore(self.database) as store:
            state = store.load("synthetic-case", "A", self.source)
            item = annotation(10, 30, "committed-synthetic", finding="negative")
            item["provenance"]["geometry_id"] = self.source["geometry_id"]
            store.commit(apply_annotation(state, item), "durable_annotation")
            store.save_view("synthetic-case", "A", self.view)
            self.expected = store.load("synthetic-case", "A", self.source)
        # A normal initial close checkpoints the baseline. Any remaining WAL
        # after the child exits is therefore from the deliberately killed child.
        self.assertFalse(Path(str(self.database) + "-wal").exists())

    def tearDown(self):
        self.temporary.cleanup()

    def crash(self, mode):
        result = subprocess.run(
            [sys.executable, "-c", CHILD, str(self.database), mode],
            cwd=REPO, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", timeout=15,
        )
        self.assertEqual(result.returncode, CRASH_EXIT, result.stdout + result.stderr)
        self.assertIn("ABRUPT_PROCESS_EXIT:" + mode, result.stdout)
        wal = Path(str(self.database) + "-wal")
        self.assertTrue(wal.exists(), "Expected unclosed child WAL")
        self.assertGreater(wal.stat().st_size, 32, "Expected actual WAL frames, not only an in-memory interrupted transaction")

    def verify_baseline_recovered(self):
        with AnnotationStore(self.database) as reopened:
            recovered = reopened.load("synthetic-case", "A", self.source)
            self.assertEqual(recovered, self.expected)
            self.assertEqual(recovered["view_state"], self.view)
            self.assertEqual(len(recovered["annotations"]), 1)
            self.assertEqual(recovered["markers"], [])
            self.assertEqual(recovered["revision"], 1)
            audit = reopened._audit_records("synthetic-case", "A")
            self.assertEqual([record["action"] for record in audit], ["durable_annotation"])
            raw = reopened.connection.execute("SELECT state_json FROM streams").fetchone()[0]
            self.assertNotIn("UNCOMMITTED_SENTINEL", raw)
            self.assertEqual(reopened.connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(reopened.load("synthetic-case", "B")["annotations"], [])
            # The committed undo ledger also survives; the killed operation is
            # not an extra undo entry or a new revision.
            undone = reopened.undo("synthetic-case", "A")
            self.assertEqual(undone["annotations"], [])
            self.assertEqual(undone["revision"], 2)
            redone = reopened.redo("synthetic-case", "A")
            self.assertEqual(redone["annotations"], self.expected["annotations"])

    def test_actual_process_exit_between_snapshot_and_audit_rolls_back(self):
        self.crash("mid_annotation_commit")
        self.verify_baseline_recovered()

    def test_actual_process_exit_during_draft_autosave_recovers_previous_draft(self):
        self.crash("mid_view_autosave")
        self.verify_baseline_recovered()

    def test_actual_process_exit_after_commits_keeps_annotation_marker_and_draft(self):
        self.crash("after_committed_annotation_and_draft")
        with AnnotationStore(self.database) as reopened:
            state = reopened.load("synthetic-case", "A", self.source)
            self.assertEqual(state["annotations"], self.expected["annotations"])
            self.assertEqual(state["revision"], 2)
            self.assertEqual(len(state["markers"]), 1)
            self.assertEqual(state["markers"][0]["s_mm"], 24.75)
            self.assertEqual(state["view_state"], {"s_mm": 26.75, "draft": {
                "a": 30, "b": 40, "label": {}, "reason": "recover me, not a diagnosis"}})
            self.assertEqual([item["action"] for item in reopened._audit_records("synthetic-case", "A")],
                             ["durable_annotation", "durable_marker"])
            self.assertEqual(reopened.connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")


if __name__ == "__main__":
    unittest.main()
