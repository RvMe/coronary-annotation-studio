from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from annotation_app.domain import add_marker, apply_annotation, delete_annotation
from annotation_app.storage import AnnotationStore
from tests.test_domain import annotation


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="imagecasx-store-test-")
        self.root = Path(self.temp.name)
        self.db = self.root / "测试 路径" / "annotations.sqlite"
        self.store = AnnotationStore(self.db)
        self.source = {"geometry_id": "test-g1", "native_sha256": "a" * 64}

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def initial(self, reader="A"):
        return self.store.load("1", reader, self.source)

    def committed(self):
        return self.store.commit(apply_annotation(self.initial(), annotation()), "apply")

    def test_commit_monotonic_and_roundtrip(self):
        first = self.committed()
        self.assertEqual(first["revision"], 1)
        self.assertEqual(self.store.load("1", "A", self.source), first)
        second = self.store.commit(add_marker(first, "LAD", 19.123456789), "marker")
        self.assertEqual(second["revision"], 2)

    def test_reader_and_case_isolation(self):
        self.committed()
        self.assertEqual(self.initial("B")["annotations"], [])
        self.assertEqual(self.store.load("2", "A")["annotations"], [])
        self.assertIsNone(self.store.undo("1", "B"))

    def test_stale_commit_rejected(self):
        state = self.initial()
        self.committed()
        with self.assertRaisesRegex(ValueError, "Stale"):
            self.store.commit(apply_annotation(state, annotation()), "apply")
        self.assertEqual(self.store.load("1", "A")["revision"], 1)

    def test_source_mismatch_fails_closed(self):
        saved = self.committed()
        with self.assertRaisesRegex(ValueError, "provenance"):
            self.store.load("1", "A", {"geometry_id": "different"})
        saved["source"] = {}
        with self.assertRaisesRegex(ValueError, "provenance"):
            self.store.commit(saved, "apply")

    def test_undo_overwrite_restores_original_as_one_transaction(self):
        first = self.committed()
        clipped = self.store.commit(apply_annotation(first, annotation(18, 24, "new")), "apply")
        self.assertEqual(len(clipped["annotations"]), 3)
        undone = self.store.undo("1", "A")
        self.assertEqual(undone["revision"], 3)
        self.assertEqual(undone["annotations"], first["annotations"])
        redone = self.store.redo("1", "A")
        self.assertEqual(redone["revision"], 4)
        self.assertEqual(redone["annotations"], clipped["annotations"])
        self.assertEqual(len(self.store._audit_records("1", "A")), 4)

    def test_delete_undo_and_redo_do_not_resurrect_old_on_delete(self):
        first = self.committed()
        overwrite = self.store.commit(apply_annotation(first, annotation(0, 40, "new")), "apply")
        deleted = self.store.commit(delete_annotation(overwrite, "new"), "delete")
        self.assertEqual(deleted["annotations"], [])
        restored = self.store.undo("1", "A")
        self.assertEqual([a["annotation_id"] for a in restored["annotations"]], ["new"])
        self.assertEqual(self.store.redo("1", "A")["annotations"], [])

    def test_new_commit_after_undo_clears_redo_but_keeps_ledger(self):
        first = self.committed()
        self.store.commit(add_marker(first, "LAD", 12), "marker")
        undone = self.store.undo("1", "A")
        self.store.commit(add_marker(undone, "LAD", 15), "marker")
        self.assertIsNone(self.store.redo("1", "A"))
        self.assertEqual(len(self.store._audit_records("1", "A")), 4)

    def test_save_view_preserves_revision_and_recovers_draft(self):
        self.initial()
        view = {"s_mm": 13.5, "draft": {"start": 10, "end": 20, "label": {}}}
        self.store.save_view("1", "A", view)
        state = self.store.load("1", "A", self.source)
        self.assertEqual(state["view_state"], view)
        self.assertEqual(state["source"], self.source)
        self.assertEqual(state["revision"], 0)
        self.assertEqual(state["annotations"], [])
        self.store.close()
        self.store = AnnotationStore(self.db)
        self.assertEqual(self.store.load("1", "A")["view_state"], view)

    def test_status_read_before_first_save_does_not_erase_source_binding(self):
        self.initial()
        self.store.load("1", "A")  # UI queue refresh, before any DB row exists.
        self.store.save_view("1", "A", {"draft": {"reason": "unsaved finding"}})
        self.assertEqual(self.store.load("1", "A")["source"], self.source)
        with self.assertRaisesRegex(ValueError, "provenance"):
            self.store.load("1", "A", {"geometry_id": "other-geometry"})

    def test_source_binding_is_written_on_existing_empty_view_row(self):
        self.store.save_view("1", "A", {"draft": {}})
        self.initial()  # Newly opened real image supplies its checked geometry.
        self.store.load("1", "A")  # Status refresh must also preserve this binding.
        self.store.save_view("1", "A", {"draft": {"reason": "bound"}})
        self.assertEqual(self.store.load("1", "A")["source"], self.source)

    def test_other_connection_source_change_rejects_stale_view_write(self):
        self.initial()
        with AnnotationStore(self.db) as other:
            other.load("1", "A", {"geometry_id": "other-geometry"})
            other.save_view("1", "A", {"draft": {}})
        with self.assertRaisesRegex(ValueError, "provenance"):
            self.store.save_view("1", "A", {"draft": {"reason": "do not attach to new geometry"}})

    def test_failed_audit_insert_rolls_back_snapshot(self):
        first = self.committed()
        self.store.connection.execute("CREATE TRIGGER fail_write BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT, 'simulated disk failure'); END")
        with self.assertRaises(sqlite3.DatabaseError):
            self.store.commit(add_marker(first, "LAD", 12), "marker")
        self.assertEqual(self.store.load("1", "A"), first)
        self.assertEqual(len(self.store._audit_records("1", "A")), 1)

    def test_nonfinite_view_and_state_not_persisted(self):
        self.committed()
        with self.assertRaises(ValueError):
            self.store.save_view("1", "A", {"bad": float("nan")})
        state = self.store.load("1", "A")
        state["source"]["bad"] = float("inf")
        with self.assertRaises(ValueError):
            self.store.commit(state, "bad")

    def test_export_full_roundtrip_and_duplicate_import(self):
        state = self.store.commit(add_marker(self.committed(), "LAD", 12.5), "marker")
        self.store.save_view("1", "A", {"s_mm": 12.5})
        path = self.store.export_case("1", "A", self.root / "exports")
        self.assertTrue(path.is_file())
        with AnnotationStore(self.root / "import.sqlite") as other:
            imported = other.import_case(path)
            self.assertEqual(imported["annotations"], state["annotations"])
            self.assertEqual(imported["markers"], state["markers"])
            self.assertEqual(imported["view_state"], {"s_mm": 12.5})
            self.assertEqual(imported["revision"], 1)
            self.assertEqual(other.import_case(path), imported)
            again = other.export_case("1", "A", self.root / "exports")
            self.assertEqual(json.loads(again.read_text(encoding="utf-8"))["annotations"], state["annotations"])

    def test_checksum_tampering_rejected(self):
        self.committed()
        path = self.store.export_case("1", "A", self.root / "exports")
        with path.open("ab") as handle:
            handle.write(b" ")
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.store.import_case(path)

    def test_import_reader_rebinding_rejected(self):
        self.committed()
        path = self.store.export_case("1", "A", self.root / "exports")
        with self.assertRaisesRegex(ValueError, "another reader"):
            self.store.import_case(path, reader_id="B")

    def test_divergent_import_rejected(self):
        first = self.committed()
        path = self.store.export_case("1", "A", self.root / "exports")
        self.store.commit(add_marker(first, "LAD", 14), "marker")
        with self.assertRaisesRegex(ValueError, "Divergent"):
            self.store.import_case(path)

    def test_missing_manifest_rejected(self):
        self.committed()
        path = self.store.export_case("1", "A", self.root / "exports")
        (path.parent / "checksums.json").unlink()
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.store.import_case(path)

    def test_export_manifest_paths_cannot_escape(self):
        self.committed()
        path = self.store.export_case("1", "A", self.root / "exports")
        manifest_path = path.parent / "checksums.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"][0]["path"] = "../annotations.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.import_case(path)

    def test_csv_formula_text_is_escaped_json_is_lossless(self):
        item = annotation()
        item["label"]["reason"] = "=HYPERLINK(\"bad\")"
        state = self.store.commit(apply_annotation(self.initial(), item), "apply")
        path = self.store.export_case("1", "A", self.root / "exports")
        with (path.parent / "annotations.csv").open(encoding="utf-8-sig", newline="") as handle:
            row = next(csv.DictReader(handle))
        self.assertTrue(row["reason"].startswith("'="))
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["annotations"][0]["label"]["reason"], item["label"]["reason"])

    def test_export_directories_are_new_and_not_overwritten(self):
        self.committed()
        first = self.store.export_case("1", "A", self.root / "exports")
        second = self.store.export_case("1", "A", self.root / "exports")
        self.assertNotEqual(first.parent, second.parent)
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_second_connection_stale_revision_is_detected(self):
        with AnnotationStore(self.db) as other:
            stale = other.load("1", "A", self.source)
            self.committed()
            with self.assertRaisesRegex(ValueError, "Stale"):
                other.commit(apply_annotation(stale, annotation()), "apply")

    def test_import_rejects_missing_audit_even_with_recomputed_checksum(self):
        self.committed()
        path = self.store.export_case("1", "A", self.root / "exports")
        audit_path = path.parent / "audit.jsonl"
        audit_path.write_bytes(b"")
        manifest_path = path.parent / "checksums.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest["files"]:
            if entry["path"] == "audit.jsonl":
                entry["bytes"] = 0
                entry["sha256"] = hashlib.sha256(b"").hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "every committed"):
            self.store.import_case(path)

    def test_reexport_imported_ledger_stays_valid(self):
        self.committed()
        original = self.store.export_case("1", "A", self.root / "exports")
        with AnnotationStore(self.root / "middle.sqlite") as middle:
            middle.import_case(original)
            repacked = middle.export_case("1", "A", self.root / "exports")
        with AnnotationStore(self.root / "last.sqlite") as last:
            final = last.import_case(repacked)
            self.assertEqual(final["annotations"], self.store.load("1", "A")["annotations"])

    def test_future_database_version_is_not_downgraded(self):
        future_path = self.root / "future.sqlite"
        with sqlite3.connect(future_path) as connection:
            connection.execute("PRAGMA user_version=99")
        connection.close()
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            AnnotationStore(future_path)


if __name__ == "__main__":
    unittest.main()
