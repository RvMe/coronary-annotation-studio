"""Reader completion measures real CPR samples without creating diagnoses."""
from copy import deepcopy
import csv
import json
from pathlib import Path
import tempfile
import unittest

from annotation_app.domain import (
    apply_annotation, coverage_report, delete_annotation, empty_state,
    mark_case_complete, validate_label,
)
from annotation_app.storage import AnnotationStore


def record(start, end, identity="a", path="LAD", scope=None):
    return {
        "annotation_id": identity, "label_group_id": identity, "path_id": path,
        "canonical_anatomy_id": scope or path, "anatomical_segment": scope or path,
        "s_start_mm": start, "s_end_mm": end, "native_anchors": {},
        "provenance": {}, "review_required": False,
        "label": {"finding_status": "negative", "plaque_composition": None,
                  "stenosis_grade": "0", "confidence": "high", "reason": ""},
    }


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.state = empty_state("synthetic", "reader-A", {"geometry_id": "synthetic-1"})

    def report(self, state, end=99):
        return coverage_report(state, {"LAD": end}, path_samples={"LAD": list(range(100))})

    def test_full_interval_covers_terminal_sample(self):
        state = apply_annotation(self.state, record(0, 99))
        report = self.report(state)
        self.assertTrue(report["complete"])
        self.assertEqual((report["covered_slices"], report["total_slices"]), (100, 100))
        self.assertEqual(report["coverage_basis"], "unique_cpr_samples")
        completed = mark_case_complete(state, report)
        self.assertEqual(completed["case_status"], "complete")
        self.assertFalse(completed["completion"]["confirmed_with_gaps"])
        self.assertEqual(completed["annotations"], state["annotations"])

    def test_exactly_ninety_percent_cannot_override(self):
        state = apply_annotation(self.state, record(0, 90))
        report = self.report(state)
        self.assertEqual(report["coverage_percent"], 90)
        self.assertFalse(report["can_override"])
        with self.assertRaisesRegex(ValueError, "above 90%"):
            mark_case_complete(state, report, allow_incomplete=True)

    def test_above_ninety_requires_explicit_confirmation_and_keeps_gap(self):
        state = apply_annotation(self.state, record(0, 91))
        before = deepcopy(state)
        report = self.report(state)
        self.assertEqual(report["covered_slices"], 91)
        self.assertTrue(report["can_override"])
        with self.assertRaises(ValueError):
            mark_case_complete(state, report)
        completed = mark_case_complete(state, report, allow_incomplete=True)
        self.assertEqual(state, before)
        self.assertEqual(completed["case_status"], "complete_with_gaps")
        self.assertEqual(completed["completion"]["coverage"]["gaps"], report["gaps"])
        self.assertEqual(completed["annotations"], before["annotations"])
        self.assertEqual(len(completed["annotations"]), 1)

    def test_sample_coverage_is_not_length_weighted_or_per_vessel_averaged(self):
        state = apply_annotation(self.state, record(0, 90))
        state = apply_annotation(state, record(0, 99, "b", "RCA"))
        report = coverage_report(state, {"LAD": 99, "RCA": 99},
                                 path_samples={"LAD": list(range(100)), "RCA": [0, 99]})
        self.assertEqual(report["covered_slices"], 92)
        self.assertEqual(report["total_slices"], 102)
        self.assertAlmostEqual(report["coverage_ratio"], 92 / 102)
        self.assertAlmostEqual(report["by_path"]["LAD"]["coverage_ratio"], .9)

    def test_shared_lm_samples_and_boundary_are_counted_once(self):
        state = apply_annotation(self.state, record(0, 10, "lm", "LAD", "LM"))
        state = apply_annotation(state, record(10, 20, "lad", "LAD"))
        state = apply_annotation(state, record(10, 20, "lcx", "LCX"))
        samples = {path: list(range(21)) for path in ("LAD", "LCX")}
        lm = {"status": "verified", "owner": "LAD", "verified_end_mm": 10}
        report = coverage_report(state, {"LAD": 20, "LCX": 20}, path_samples=samples, canonical_lm=lm)
        self.assertTrue(report["complete"])
        self.assertEqual(report["total_slices"], 31)
        self.assertEqual(report["covered_slices"], 31)
        self.assertEqual(report["target_mm"], 30)
        self.assertEqual(report["by_path"]["LCX"]["total_slices"], 10)
        self.assertTrue(report["shared_lm_excluded_from_lcx"])
        # A duplicate legacy LCX LM record must not inflate coverage.
        duplicate = apply_annotation(state, record(0, 10, "legacy-lm", "LCX", "LM"))
        other = coverage_report(duplicate, {"LAD": 20, "LCX": 20}, path_samples=samples, canonical_lm=lm)
        self.assertEqual(other["covered_slices"], 31)

    def test_lcx_shared_lm_annotation_cannot_cover_missing_owner(self):
        state = apply_annotation(self.state, record(0, 20, "lcx", "LCX"))
        state = apply_annotation(state, record(10, 20, "lad", "LAD"))
        report = coverage_report(state, {"LAD": 20, "LCX": 20},
                                 path_samples={path: list(range(21)) for path in ("LAD", "LCX")},
                                 canonical_lm={"status": "verified", "verified_end_mm": 10})
        self.assertFalse(report["complete"])
        self.assertEqual(report["gaps"], [{"path_id": "LAD", "s_start_mm": 0, "s_end_mm": 10}])

    def test_distinct_nonshared_vessels_are_not_deduplicated(self):
        state = apply_annotation(self.state, record(0, 20))
        state = apply_annotation(state, record(0, 20, "lcx", "LCX"))
        report = coverage_report(state, {"LAD": 20, "LCX": 20},
                                 path_samples={path: list(range(21)) for path in ("LAD", "LCX")},
                                 canonical_lm={"status": "not_present"})
        self.assertEqual(report["total_slices"], 42)

    def test_float_terminal_roundoff_is_tolerated_without_rewriting_annotation(self):
        state = apply_annotation(self.state, record(0, 99 - 1e-8))
        report = self.report(state)
        self.assertTrue(report["complete"])
        self.assertEqual(report["covered_slices"], 100)
        self.assertEqual(state["annotations"][0]["s_end_mm"], 99 - 1e-8)

    def test_real_subsample_gap_is_reported_even_when_all_samples_have_labels(self):
        state = apply_annotation(self.state, record(0, 50.001, "left"))
        state = apply_annotation(state, record(50.002, 99, "right"))
        report = self.report(state)
        self.assertEqual(report["covered_slices"], 100)
        self.assertFalse(report["complete"])
        self.assertEqual(len(report["gaps"]), 1)
        self.assertAlmostEqual(report["gaps"][0]["s_end_mm"] - report["gaps"][0]["s_start_mm"], .001)
        self.assertEqual(mark_case_complete(state, report, allow_incomplete=True)["case_status"], "complete_with_gaps")

    def test_overlap_between_legacy_canonical_scopes_does_not_inflate_coverage(self):
        state = apply_annotation(self.state, record(0, 60, "lm", scope="LM"))
        state = apply_annotation(state, record(50, 91, "lad"))
        report = self.report(state)
        self.assertEqual(report["covered_slices"], 91)
        self.assertEqual(report["covered_mm"], 91)

    def test_review_flags_are_counted_as_labels_but_not_cleared_by_override(self):
        state = apply_annotation(self.state, record(0, 99))
        state["annotations"][0]["review_required"] = True
        state["annotations"][0]["provenance"]["training_geometry_eligible"] = False
        report = self.report(state)
        self.assertEqual(report["coverage_ratio"], 1)
        self.assertEqual(report["review_count"], 1)
        self.assertFalse(report["complete"])
        completed = mark_case_complete(state, report, allow_incomplete=True)
        self.assertTrue(completed["annotations"][0]["review_required"])
        self.assertFalse(completed["annotations"][0]["provenance"]["training_geometry_eligible"])

    def test_pending_rereview_is_unprocessed_even_if_it_overlaps_a_label(self):
        state = apply_annotation(self.state, record(0, 99))
        state["rereview_intervals"] = [{"path_id": "LAD", "s_start_mm": 91, "s_end_mm": 99,
                                       "reason": "edited_coverage_removed"}]
        report = self.report(state)
        self.assertEqual(report["covered_slices"], 91)
        self.assertEqual(report["gaps"], [{"path_id": "LAD", "s_start_mm": 91, "s_end_mm": 99}])
        completed = mark_case_complete(state, report, allow_incomplete=True)
        self.assertEqual(completed["rereview_intervals"], state["rereview_intervals"])

    def test_ambiguous_geometry_is_preserved_separately_from_reader_completion(self):
        state = apply_annotation(self.state, record(0, 99))
        report = coverage_report(state, {"LAD": 99}, path_samples={"LAD": list(range(100))},
                                 canonical_lm={"status": "ambiguous"})
        self.assertTrue(report["complete"])
        self.assertTrue(report["geometry_warnings"])
        completed = mark_case_complete(state, report)
        self.assertEqual(completed["case_status"], "complete")
        self.assertEqual(completed["completion"]["coverage"]["geometry_warnings"], ["shared_lm_mapping_ambiguous"])

    def test_empty_case_and_invalid_samples_do_not_qualify(self):
        report = self.report(self.state)
        self.assertEqual(report["covered_slices"], 0)
        self.assertFalse(report["can_override"])
        for samples in ([], [0, 0, 99], [0, float("nan"), 99], [-1, 99], [0, 100]):
            with self.subTest(samples=samples), self.assertRaises(ValueError):
                coverage_report(self.state, {"LAD": 99}, path_samples={"LAD": samples})

    def test_complete_lcx_duplicate_prefix_can_have_zero_unique_extent(self):
        state = apply_annotation(self.state, record(0, 20, scope="LM"))
        report = coverage_report(state, {"LAD": 20, "LCX": 20},
                                 path_samples={path: list(range(21)) for path in ("LAD", "LCX")},
                                 canonical_lm={"status": "verified", "verified_end_mm": 20})
        self.assertTrue(report["complete"])
        self.assertEqual(report["by_path"]["LCX"]["total_slices"], 0)
        self.assertEqual(report["total_slices"], 21)

    def test_apply_and_delete_invalidate_only_live_completion_metadata(self):
        state = apply_annotation(self.state, record(0, 99))
        completed = mark_case_complete(state, self.report(state))
        changed = apply_annotation(completed, record(0, 50, "b"))
        self.assertEqual(changed["case_status"], "in_progress")
        self.assertNotIn("completion", changed)
        deleted = delete_annotation(completed, "a")
        self.assertEqual(deleted["case_status"], "in_progress")
        self.assertNotIn("completion", deleted)
        self.assertIn("completion", completed)

    def test_legacy_length_report_and_explicit_lcx_window_remain_compatible(self):
        state = apply_annotation(self.state, record(8, 20, "lcx", "LCX"))
        report = coverage_report(state, {"LCX": {"start_mm": 8, "end_mm": 20}})
        self.assertTrue(report["complete"])
        self.assertEqual(report["coverage_basis"], "physical_length_mm")
        self.assertIsNone(report["total_slices"])

    def test_non_evaluable_can_omit_confidence_without_rewriting_legacy(self):
        label = {"finding_status": "non_evaluable", "plaque_composition": None,
                 "stenosis_grade": "unable", "reason": "", "reason_codes": []}
        original = deepcopy(label)
        validate_label(label)
        self.assertEqual(label, original)
        label["confidence"] = None
        validate_label(label)
        label["confidence"] = "high"
        validate_label(label)
        label.update(finding_status="negative", stenosis_grade="0", confidence=None)
        with self.assertRaises(ValueError):
            validate_label(label)

    def test_completion_survives_sqlite_restart_export_import_and_history(self):
        with tempfile.TemporaryDirectory(prefix="completion-v012-") as temporary:
            folder = Path(temporary)
            with AnnotationStore(folder / "reader.sqlite") as store:
                state = store.commit(apply_annotation(self.state, record(0, 91)), "apply")
                done = store.commit(mark_case_complete(state, self.report(state), allow_incomplete=True), "mark_complete_with_gaps")
                export = store.export_case("synthetic", "reader-A", folder / "exports")
                self.assertEqual(json.loads(export.read_text(encoding="utf-8"))["completion"], done["completion"])
                store.commit(delete_annotation(done, "a"), "delete")
                restored = store.undo("synthetic", "reader-A")
                self.assertEqual(restored["completion"], done["completion"])
            with AnnotationStore(folder / "reader.sqlite") as reopened:
                self.assertEqual(reopened.load("synthetic", "reader-A")["case_status"], "complete_with_gaps")
                self.assertEqual(reopened.load("synthetic", "reader-B")["case_status"], "in_progress")
            with AnnotationStore(folder / "received.sqlite") as receiver:
                imported = receiver.import_case(export)
                self.assertEqual(imported["completion"], done["completion"])
                self.assertEqual(imported["annotations"], done["annotations"])

    def test_csv_discloses_completion_scope_and_does_not_reuse_invalidated_percent(self):
        with tempfile.TemporaryDirectory(prefix="completion-csv-v012-") as temporary:
            folder = Path(temporary)
            with AnnotationStore(folder / "reader.sqlite") as store:
                state = store.commit(apply_annotation(self.state, record(0, 91)), "apply")
                done = store.commit(mark_case_complete(state, self.report(state), allow_incomplete=True), "mark_complete_with_gaps")
                export = store.export_case("synthetic", "reader-A", folder / "exports")
                with export.with_name("annotations.csv").open(encoding="utf-8-sig", newline="") as handle:
                    row = next(csv.DictReader(handle))
                self.assertEqual(row["case_status"], "complete_with_gaps")
                self.assertEqual(row["case_completion_mode"], "reader_confirmed_with_gaps")
                self.assertEqual(float(row["case_coverage_percent"]), 91)
                self.assertEqual(row["case_coverage_basis"], "unique_cpr_samples")
                changed = store.commit(apply_annotation(done, record(0, 99, "b")), "apply")
                self.assertNotIn("completion", changed)
                newer = store.export_case("synthetic", "reader-A", folder / "exports")
                with newer.with_name("annotations.csv").open(encoding="utf-8-sig", newline="") as handle:
                    updated = next(csv.DictReader(handle))
                self.assertEqual(updated["case_status"], "in_progress")
                self.assertEqual(updated["case_completion_mode"], "")
                self.assertEqual(updated["case_coverage_percent"], "")
                self.assertEqual(updated["case_coverage_basis"], "")

    def test_legacy_complete_csv_does_not_invent_a_coverage_assessment(self):
        with tempfile.TemporaryDirectory(prefix="completion-legacy-v012-") as temporary:
            folder = Path(temporary)
            with AnnotationStore(folder / "reader.sqlite") as store:
                state = apply_annotation(self.state, record(0, 91))
                state["case_status"] = "complete"
                store.commit(state, "legacy_complete")
                export = store.export_case("synthetic", "reader-A", folder / "exports")
                with export.with_name("annotations.csv").open(encoding="utf-8-sig", newline="") as handle:
                    row = next(csv.DictReader(handle))
                self.assertEqual(row["case_status"], "complete")
                self.assertEqual(row["case_coverage_percent"], "")
                self.assertEqual(row["case_coverage_basis"], "")


if __name__ == "__main__":
    unittest.main()
