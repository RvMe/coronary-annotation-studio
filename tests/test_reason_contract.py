"""Optional structured reasons preserve legacy reader records and audit identity."""
from copy import deepcopy
import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from annotation_app.domain import apply_annotation, validate_label, validate_state
from annotation_app.label_catalog import REASON_CODES
from annotation_app.storage import AnnotationStore
from tests.test_domain import annotation


class ReasonValidationTests(unittest.TestCase):
    def label(self):
        return {"finding_status": "non_evaluable", "plaque_composition": None,
                "stenosis_grade": "unable", "confidence": "low"}

    def test_reason_and_codes_are_optional_for_non_evaluable(self):
        label = self.label()
        before = deepcopy(label)
        validate_label(label)
        self.assertEqual(label, before)
        validate_label({**label, "reason": "", "reason_codes": []})

    def test_every_catalog_code_is_accepted_without_free_text(self):
        self.assertEqual(REASON_CODES, frozenset({"motion_artifact", "blooming_metal_artifact",
            "poor_contrast", "noise_blur", "cpr_geometry_coverage", "interpretive_uncertainty", "other"}))
        for code in sorted(REASON_CODES):
            with self.subTest(code=code):
                validate_label({**self.label(), "reason_codes": [code]})

    def test_multiple_reasons_are_accepted_without_reordering(self):
        label = {**self.label(), "reason_codes": ["poor_contrast", "motion_artifact"]}
        before = deepcopy(label)
        validate_label(label)
        self.assertEqual(label, before)

    def test_invalid_structured_reason_shapes_are_rejected(self):
        for codes in ("motion_artifact", None, {}, ("motion_artifact",), [None], [{}], [True], [1]):
            with self.subTest(codes=codes), self.assertRaisesRegex(ValueError, "reason_codes"):
                validate_label({**self.label(), "reason_codes": codes})

    def test_duplicate_and_unknown_codes_are_rejected(self):
        for codes in (["motion_artifact", "motion_artifact"], ["unknown"], [""], ["Motion_Artifact"]):
            with self.subTest(codes=codes), self.assertRaisesRegex(ValueError, "reason_codes"):
                validate_label({**self.label(), "reason_codes": codes})

    def test_legacy_reason_is_optional_text_and_lossless(self):
        label = {**self.label(), "reason": "  原始备注\n细节 = 不改写  "}
        before = deepcopy(label)
        validate_label(label)
        self.assertEqual(label, before)
        for reason in (None, [], 1):
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, "reason must be text"):
                validate_label({**self.label(), "reason": reason})


class ReasonPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="imagecasx-reason-contract-")
        self.root = Path(self.temp.name)
        self.store = AnnotationStore(self.root / "annotations.sqlite")
        self.source = {"geometry_id": "fixture-v1"}

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def commit_label(self, label):
        item = annotation()
        item["label"] = label
        state = self.store.load("case1", "A", self.source)
        return self.store.commit(apply_annotation(state, item), "apply")

    def label(self):
        return {"finding_status": "non_evaluable", "plaque_composition": None,
                "stenosis_grade": "unable", "confidence": "low"}

    def csv_row(self, json_path):
        with json_path.with_suffix(".csv").open(encoding="utf-8-sig", newline="") as handle:
            return next(csv.DictReader(handle))

    def test_legacy_snapshot_load_export_and_validation_do_not_insert_codes(self):
        state = self.commit_label({**self.label(), "reason": "原始旧版备注"})
        raw_before = self.store._row("case1", "A")["state_json"]
        audit_before = self.store._audit_records("case1", "A")
        validate_state(state)
        self.assertEqual(self.store.load("case1", "A"), state)
        path = self.store.export_case("case1", "A", self.root / "exports")
        exported = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(exported, state)
        self.assertNotIn("reason_codes", exported["annotations"][0]["label"])
        self.assertEqual(self.csv_row(path)["reason_codes_json"], "[]")
        self.assertEqual(self.store._row("case1", "A")["state_json"], raw_before)
        self.assertEqual(self.store._audit_records("case1", "A"), audit_before)

    def test_empty_reason_selection_survives_export_import(self):
        state = self.commit_label({**self.label(), "reason": "", "reason_codes": []})
        path = self.store.export_case("case1", "A", self.root / "exports")
        self.assertEqual(self.csv_row(path)["reason_codes_json"], "[]")
        with AnnotationStore(self.root / "import.sqlite") as imported_store:
            result = imported_store.import_case(path)
            self.assertEqual(result["annotations"], state["annotations"])
            self.assertEqual(result["annotations"][0]["label"]["reason_codes"], [])

    def test_codes_and_legacy_text_are_lossless_across_export_import_reexport(self):
        label = {**self.label(), "reason_codes": ["noise_blur", "poor_contrast"],
                 "reason": "原有备注\n保留，含分隔符", "custom_legacy_field": "keep me"}
        state = self.commit_label(label)
        path = self.store.export_case("case1", "A", self.root / "exports")
        self.assertEqual(json.loads(self.csv_row(path)["reason_codes_json"]), label["reason_codes"])
        self.assertEqual(self.csv_row(path)["reason"], label["reason"])
        with AnnotationStore(self.root / "middle.sqlite") as middle:
            imported = middle.import_case(path)
            self.assertEqual(imported["annotations"], state["annotations"])
            second = middle.export_case("case1", "A", self.root / "exports")
        with AnnotationStore(self.root / "last.sqlite") as last:
            final = last.import_case(second)
            self.assertEqual(final["annotations"][0]["label"], label)

    def test_old_export_without_csv_reason_column_remains_importable(self):
        state = self.commit_label({**self.label(), "reason": "旧版自由填写"})
        path = self.store.export_case("case1", "A", self.root / "legacy_exports")
        csv_path = path.with_suffix(".csv")
        with csv_path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = [name for name in reader.fieldnames if name != "reason_codes_json"]
            records = [{name: row[name] for name in fields} for row in reader]
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)
        csv_bytes = buffer.getvalue().encode("utf-8-sig")
        csv_path.write_bytes(csv_bytes)
        checksums_path = path.parent / "checksums.json"
        checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
        csv_entry = next(entry for entry in checksums["files"] if entry["path"] == "annotations.csv")
        csv_entry.update(bytes=len(csv_bytes), sha256=hashlib.sha256(csv_bytes).hexdigest())
        checksums_path.write_text(json.dumps(checksums), encoding="utf-8")
        old_files = {item.name: item.read_bytes() for item in path.parent.iterdir()}
        with AnnotationStore(self.root / "legacy_import.sqlite") as target:
            imported = target.import_case(path)
            self.assertEqual(imported["annotations"], state["annotations"])
            self.assertNotIn("reason_codes", imported["annotations"][0]["label"])
            upgraded_export = target.export_case("case1", "A", self.root / "exports")
            self.assertEqual(self.csv_row(upgraded_export)["reason_codes_json"], "[]")
        self.assertEqual({item.name: item.read_bytes() for item in path.parent.iterdir()}, old_files)

    def test_undo_redo_restores_original_reason_codes_as_one_record(self):
        first = self.commit_label({**self.label(), "reason_codes": ["motion_artifact"]})
        item = annotation(10, 30, "edited")
        item["label"] = {**self.label(), "reason_codes": ["poor_contrast", "noise_blur"]}
        original_id = first["annotations"][0]["annotation_id"]
        second = self.store.commit(apply_annotation(first, item, editing_id=original_id), "apply")
        self.assertEqual(self.store.undo("case1", "A")["annotations"], first["annotations"])
        self.assertEqual(self.store.redo("case1", "A")["annotations"], second["annotations"])

    def test_invalid_reason_codes_do_not_change_saved_state_or_audit(self):
        state = self.commit_label({**self.label(), "reason_codes": []})
        invalid = deepcopy(state)
        invalid["annotations"][0]["label"]["reason_codes"] = ["not-a-supported-reason"]
        audit_before = self.store._audit_records("case1", "A")
        with self.assertRaisesRegex(ValueError, "reason_codes"):
            self.store.commit(invalid, "apply")
        self.assertEqual(self.store.load("case1", "A"), state)
        self.assertEqual(self.store._audit_records("case1", "A"), audit_before)


if __name__ == "__main__":
    unittest.main()
