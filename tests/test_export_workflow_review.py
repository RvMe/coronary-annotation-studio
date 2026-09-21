"""Independent result-only handoff checks using synthetic labels, never images."""
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from annotation_app.domain import apply_annotation, delete_annotation
from annotation_app.storage import AnnotationStore
from tests.test_domain import annotation


class ExportWorkflowReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="imagecasx-result-handoff-")
        self.root = Path(self.temp.name)
        self.store = AnnotationStore(self.root / "doctor.sqlite")
        # References deliberately do not exist on this computer. Label-only
        # export/import must not resolve or copy a CCTA/CPR image to work.
        self.source = {"geometry_id": "synthetic-reviewed-geometry-v1",
                       "native_sha256": "a" * 64,
                       "native_reference": "unmounted_dataset/case/native.nii.gz"}

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def save_case(self, case_id="1", reader="DOCTOR-A", finding="positive"):
        state = self.store.load(case_id, reader, self.source)
        item = annotation(annotation_id=f"{case_id}-{reader}", finding=finding)
        return self.store.commit(apply_annotation(state, item), "apply")

    def test_applied_labels_visible_to_other_connection_before_exit_or_export(self):
        saved = self.save_case()
        with AnnotationStore(self.root / "doctor.sqlite") as second_connection:
            self.assertEqual(second_connection.load("1", "DOCTOR-A"), saved)
        self.assertFalse((self.root / "exports").exists())

    def test_saved_draft_is_recoverable_but_not_an_applied_label(self):
        saved = self.save_case()
        draft = {"draft": {"label": {"finding_status": "negative"},
                           "a": 40, "b": 50, "dirty": True}}
        self.store.save_view("1", "DOCTOR-A", draft)
        exported = self.store.export_case("1", "DOCTOR-A", self.root / "exports")
        payload = json.loads(exported.read_text(encoding="utf-8"))
        self.assertEqual(payload["annotations"], saved["annotations"])
        self.assertEqual(payload["view_state"], draft)
        self.assertEqual(payload["revision"], saved["revision"])

    def test_result_only_zip_preserves_per_case_files_and_reader_isolation(self):
        for case_id in ("1", "2"):
            self.save_case(case_id, "DOCTOR-A", "negative")
            self.save_case(case_id, "DOCTOR-B", "positive")
        exports = [self.store.export_case(case_id, "DOCTOR-A", self.root / "exports")
                   for case_id in ("1", "2")]
        archive_path = self.root / "doctor-A-results.zip"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for exported in exports:
                self.assertEqual({p.name for p in exported.parent.iterdir()},
                                 {"annotations.json", "annotations.csv", "audit.jsonl", "checksums.json"})
                for path in exported.parent.iterdir():
                    archive.write(path, path.relative_to((self.root / "exports").resolve()))
        with zipfile.ZipFile(archive_path) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(len(archive.namelist()), 8)
            self.assertFalse(any(name.endswith((".nii", ".nii.gz", ".nrrd", ".npz", ".dcm"))
                                 for name in archive.namelist()))
            for name in archive.namelist():
                if name.endswith("annotations.json"):
                    state = json.loads(archive.read(name))
                    self.assertEqual(state["reader_id"], "DOCTOR-A")
                    self.assertEqual(state["annotations"][0]["label"]["finding_status"], "negative")
        self.assertEqual(len(self.store.load("1", "DOCTOR-B")["annotations"]), 1)

    def test_researcher_import_needs_no_image_copy_and_retains_geometry_anchors(self):
        saved = self.save_case()
        exported = self.store.export_case("1", "DOCTOR-A", self.root / "exports")
        with AnnotationStore(self.root / "researcher.sqlite") as researcher:
            imported = researcher.import_case(exported)
            self.assertEqual(imported["annotations"], saved["annotations"])
            self.assertEqual(imported["source"], self.source)
            self.assertEqual(researcher.load("1", "DOCTOR-A", self.source)["annotations"],
                             saved["annotations"])
            with self.assertRaisesRegex(ValueError, "provenance"):
                researcher.load("1", "DOCTOR-A", {"geometry_id": "different-reconstruction"})

    def test_deleted_region_and_history_survive_result_only_handoff(self):
        saved = self.save_case()
        deleted = delete_annotation(saved, saved["annotations"][0]["annotation_id"])
        self.store.commit(deleted, "delete_annotation")
        exported = self.store.export_case("1", "DOCTOR-A", self.root / "exports")
        with AnnotationStore(self.root / "researcher.sqlite") as researcher:
            imported = researcher.import_case(exported)
            self.assertEqual(imported["annotations"], [])
            self.assertTrue(imported["rereview_intervals"])
            provenance = researcher._audit_records("1", "DOCTOR-A")[0]["details"]
            self.assertEqual([event["action"] for event in provenance["imported_audit"]],
                             ["apply", "delete_annotation"])

    def test_annotation_json_alone_is_not_a_valid_handoff(self):
        self.save_case()
        exported = self.store.export_case("1", "DOCTOR-A", self.root / "exports")
        incomplete = self.root / "incomplete"
        incomplete.mkdir()
        (incomplete / "annotations.json").write_bytes(exported.read_bytes())
        with AnnotationStore(self.root / "researcher.sqlite") as researcher:
            with self.assertRaisesRegex(ValueError, "checksum"):
                researcher.import_case(incomplete / "annotations.json")

    def test_importing_reader_b_does_not_overwrite_existing_reader_a(self):
        state_a = self.save_case(reader="DOCTOR-A", finding="negative")
        state_b = self.save_case(reader="DOCTOR-B", finding="positive")
        exported_a = self.store.export_case("1", "DOCTOR-A", self.root / "exports")
        exported_b = self.store.export_case("1", "DOCTOR-B", self.root / "exports")
        with AnnotationStore(self.root / "researcher.sqlite") as researcher:
            researcher.import_case(exported_a)
            researcher.import_case(exported_b)
            self.assertEqual(researcher.load("1", "DOCTOR-A")["annotations"], state_a["annotations"])
            self.assertEqual(researcher.load("1", "DOCTOR-B")["annotations"], state_b["annotations"])


if __name__ == "__main__":
    unittest.main()
