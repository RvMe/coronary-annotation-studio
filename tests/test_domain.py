from copy import deepcopy
import math
import random
import unittest

from annotation_app.domain import (
    EPS, add_marker, apply_annotation, coverage_report, delete_annotation,
    delete_marker, empty_state, snap_endpoint, validate_label, validate_state,
)


def annotation(start=10, end=30, annotation_id="old", path="LAD", finding="positive", scope=None):
    label = {"finding_status": finding, "plaque_composition": "partially_calcified",
             "stenosis_grade": "50_69", "confidence": "high", "reason": ""}
    if finding == "negative":
        label.update(plaque_composition=None, stenosis_grade="0")
    return {"annotation_id": annotation_id, "label_group_id": annotation_id, "path_id": path,
            "canonical_anatomy_id": scope or path, "anatomical_segment": scope or path,
            "s_start_mm": start, "s_end_mm": end, "label": label,
            "native_anchors": {"start_lps": [1, 2, 3], "end_lps": [4, 5, 6]},
            "provenance": {"geometry_id": "test-geometry"}, "review_required": False}


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.state = empty_state("1", "A", {"geometry_id": "g1"})

    def with_old(self):
        return apply_annotation(self.state, annotation())

    def test_empty_is_unreviewed_not_negative(self):
        report = coverage_report(self.state, {"LAD": 30})
        self.assertFalse(report["complete"])
        self.assertEqual(report["gaps"], [{"path_id": "LAD", "s_start_mm": 0.0, "s_end_mm": 30.0}])
        self.assertEqual(self.state["annotations"], [])

    def test_middle_overwrite_splits_and_marks_summary(self):
        old = self.with_old()
        before = deepcopy(old)
        result = apply_annotation(old, annotation(18, 24, "new", finding="negative"))
        self.assertEqual(old, before)
        self.assertEqual([(a["s_start_mm"], a["s_end_mm"]) for a in result["annotations"]], [(10, 18.0), (18, 24), (24.0, 30)])
        for fragment in (result["annotations"][0], result["annotations"][2]):
            self.assertTrue(fragment["review_required"])
            self.assertEqual(fragment["label_group_id"], "old")
            self.assertEqual(fragment["provenance"]["derived_from_annotation_id"], "old")
            self.assertEqual(fragment["native_anchors"], {})
            self.assertTrue(fragment["provenance"]["native_anchors_require_recompute"])

    def test_full_overwrite_removes_old(self):
        result = apply_annotation(self.with_old(), annotation(0, 40, "new"))
        self.assertEqual([a["annotation_id"] for a in result["annotations"]], ["new"])

    def test_multiple_intervals_overwritten_atomically_in_domain(self):
        state = apply_annotation(self.state, annotation(0, 10, "a"))
        state = apply_annotation(state, annotation(10, 20, "b"))
        result = apply_annotation(state, annotation(5, 15, "new"))
        self.assertEqual([(a["s_start_mm"], a["s_end_mm"]) for a in result["annotations"]], [(0, 5.0), (5, 15), (15.0, 20)])
        self.assertEqual(result["annotations"][1]["provenance"]["overwritten_annotation_ids"], ["a", "b"])

    def test_half_open_adjacency_does_not_clip(self):
        result = apply_annotation(self.with_old(), annotation(30, 40, "new"))
        self.assertEqual(result["annotations"][0]["annotation_id"], "old")
        self.assertFalse(result["annotations"][0]["review_required"])

    def test_edit_shrink_tails_become_rereview(self):
        result = apply_annotation(self.with_old(), annotation(18, 24, "old"), editing_id="old")
        self.assertEqual(len(result["annotations"]), 1)
        self.assertEqual([(r["s_start_mm"], r["s_end_mm"]) for r in result["rereview_intervals"]], [(10, 18), (24, 30)])

    def test_edit_move_leaves_old_unreviewed(self):
        result = apply_annotation(self.with_old(), annotation(40, 50, "moved"), editing_id="old")
        self.assertEqual([(a["s_start_mm"], a["s_end_mm"]) for a in result["annotations"]], [(40, 50)])
        self.assertEqual(result["rereview_intervals"][0]["s_start_mm"], 10)
        self.assertEqual(result["rereview_intervals"][0]["s_end_mm"], 30)

    def test_delete_does_not_resurrect_overwritten_record(self):
        state = apply_annotation(self.with_old(), annotation(18, 24, "new"))
        result = delete_annotation(state, "new")
        self.assertEqual([(a["s_start_mm"], a["s_end_mm"]) for a in result["annotations"]], [(10, 18.0), (24.0, 30)])
        self.assertEqual(result["rereview_intervals"][-1]["reason"], "annotation_deleted")

    def test_reapply_clears_only_covered_rereview(self):
        state = delete_annotation(self.with_old(), "old")
        state = apply_annotation(state, annotation(15, 25, "next"))
        self.assertEqual([(r["s_start_mm"], r["s_end_mm"]) for r in state["rereview_intervals"]], [(10, 15), (25, 30)])

    def test_other_path_and_canonical_scope_not_overwritten(self):
        state = apply_annotation(self.with_old(), annotation(10, 30, "lcx", "LCX"))
        state = apply_annotation(state, annotation(10, 30, "lm", "LAD", scope="LM"))
        self.assertEqual(len(state["annotations"]), 3)

    def test_clipped_peak_is_retained_only_as_source_evidence(self):
        old = annotation()
        old["label"]["s_peak_stenosis_mm"] = 25.123456789
        state = apply_annotation(self.state, old)
        result = apply_annotation(state, annotation(18, 24, "new"))
        for fragment in (result["annotations"][0], result["annotations"][2]):
            self.assertNotIn("s_peak_stenosis_mm", fragment["label"])
            self.assertEqual(fragment["provenance"]["source_label"]["s_peak_stenosis_mm"], 25.123456789)

    def test_positions_are_not_rounded(self):
        a = annotation(0.1234567890123, 1.1234567890123)
        state = apply_annotation(self.state, a)
        self.assertEqual(state["annotations"][0]["s_start_mm"], a["s_start_mm"])

    def test_extremely_short_piece_does_not_create_zero_interval(self):
        result = apply_annotation(self.with_old(), annotation(10 + EPS / 2, 20, "new"))
        self.assertEqual(len(result["annotations"]), 2)
        self.assertTrue(all(a["s_end_mm"] - a["s_start_mm"] > EPS for a in result["annotations"]))

    def test_input_validation(self):
        for start, end in ((-1, 3), (0, 0), (math.nan, 2), (0, math.inf), (True, 3)):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                apply_annotation(self.state, annotation(start, end))
        with self.assertRaises(ValueError):
            apply_annotation(self.with_old(), annotation())
        with self.assertRaises(ValueError):
            apply_annotation(self.state, annotation(), editing_id="absent")

    def test_explicit_normal_and_diagnostic_consistency(self):
        validate_label(annotation(finding="negative")["label"])
        label = annotation(finding="negative")["label"]
        label["plaque_composition"] = "calcified"
        with self.assertRaises(ValueError):
            validate_label(label)
        label = annotation()["label"]
        label["plaque_composition"] = None
        with self.assertRaises(ValueError):
            validate_label(label)
        with self.assertRaises(ValueError):
            validate_label({})

    def test_markers_are_idempotent_and_not_annotations(self):
        state = add_marker(self.state, "LAD", 12.345678)
        state = add_marker(state, "LAD", 12.345678)
        state = add_marker(state, "LCX", 12.345678)
        self.assertEqual(len(state["markers"]), 2)
        self.assertEqual(state["annotations"], [])
        result = delete_marker(state, state["markers"][0]["marker_id"])
        self.assertEqual(len(result["markers"]), 1)
        self.assertEqual(len(state["markers"]), 2)

    def test_snap_pixel_and_physical_limits(self):
        self.assertEqual(snap_endpoint(10.4, [10], 0.1), (10, 10))
        self.assertEqual(snap_endpoint(10.51, [10], 1), (10.51, None))
        self.assertEqual(snap_endpoint(10.09, [10], 0.01), (10.09, None))
        self.assertEqual(snap_endpoint(10.1, [10], 1, disabled=True), (10.1, None))

    def test_snap_hysteresis_and_removed_target(self):
        self.assertEqual(snap_endpoint(10.7, [10], 1, held=10), (10, 10))
        self.assertEqual(snap_endpoint(10.76, [10], 1, held=10), (10.76, None))
        self.assertEqual(snap_endpoint(10.6, [], 1, held=10), (10.6, None))
        self.assertEqual(snap_endpoint(10, [10.2, 9.8], 1), (9.8, 9.8))

    def test_coverage_includes_review_flags_and_explicit_lcx_start(self):
        state = apply_annotation(self.state, annotation(0, 20, "a", finding="negative"))
        self.assertTrue(coverage_report(state, {"LAD": 20})["complete"])
        state = apply_annotation(state, annotation(5, 10, "new"))
        report = coverage_report(state, {"LAD": 20})
        self.assertEqual(report["gaps"], [])
        self.assertTrue(report["complete"])
        self.assertEqual(report["review_count"], 0)
        self.assertEqual(report["technical_qa_count"], 2)
        state = apply_annotation(self.state, annotation(8, 20, "lcx", "LCX", finding="negative"))
        self.assertTrue(coverage_report(state, {"LCX": {"start_mm": 8, "end_mm": 20}})["complete"])

    def test_validate_state_rejects_overlapping_live_same_scope(self):
        state = self.with_old()
        state["annotations"].append(annotation(15, 40, "another"))
        with self.assertRaises(ValueError):
            validate_state(state)

    def test_imported_live_annotation_cannot_omit_id_or_claim_another_reader(self):
        state = self.with_old()
        state["annotations"][0].pop("annotation_id")
        with self.assertRaisesRegex(ValueError, "required"):
            validate_state(state)
        state = self.with_old()
        state["annotations"][0]["reader_id"] = "B"
        with self.assertRaisesRegex(ValueError, "identity"):
            validate_state(state)

    def test_randomised_newest_wins_matches_independent_point_oracle(self):
        randomizer = random.Random(20260910)
        state = self.state
        oracle = {}
        for sequence in range(100):
            start = randomizer.randrange(0, 99)
            end = randomizer.randrange(start + 1, 101)
            incoming = annotation(start, end, f"a{sequence}")
            incoming["label"]["reason"] = str(sequence)
            state = apply_annotation(state, incoming)
            for integer in range(start, end):
                oracle[integer + 0.5] = str(sequence)
            for point, expected in oracle.items():
                covering = [a for a in state["annotations"] if a["s_start_mm"] <= point < a["s_end_mm"]]
                self.assertEqual(len(covering), 1)
                self.assertEqual(covering[0]["label"]["reason"], expected)


if __name__ == "__main__":
    unittest.main()
