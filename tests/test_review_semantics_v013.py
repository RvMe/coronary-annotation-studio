"""Separate physician reassessment from technical QA without rewriting history."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from annotation_app.domain import (
    CANONICAL_GROUP_REVIEW_REASON, apply_annotation, coverage_report,
    effective_reader_review_required, empty_state, mark_case_complete,
    reader_review_reasons, technical_qa_reasons,
)
from annotation_app.storage import AnnotationStore
from tests.test_domain import annotation


class ReviewSemanticsTests(unittest.TestCase):
    def clipped(self, finding="negative", **provenance):
        state = empty_state("synthetic-review", "reader-A")
        old = annotation(0, 100, "old", finding=finding)
        old["provenance"].update(provenance)
        state = apply_annotation(state, old)
        return apply_annotation(state, annotation(40, 60, "replacement", finding="negative"))

    def geometry_record(self):
        rec = annotation(0, 100, finding="negative")
        rec["review_required"] = True
        rec["provenance"].update(
            geometry_mapping_status="path_local_ambiguous",
            geometry_ambiguous_ranges=[{"path_id": "LAD", "s_start_mm": 0, "s_end_mm": 8}],
            training_geometry_eligible=False,
        )
        return rec

    def test_normal_clipping_inherits_only_explicit_normal_without_reader_gate(self):
        state = self.clipped()
        self.assertEqual(len(state["annotations"]), 3)
        for rec in (state["annotations"][0], state["annotations"][2]):
            self.assertFalse(rec["review_required"])
            self.assertFalse(effective_reader_review_required(rec))
            self.assertEqual(rec["review_status"], "reader_annotated")
            self.assertTrue(rec["provenance"]["normal_subset_inherited"])
            self.assertFalse(rec["provenance"]["summary_requires_reassessment"])
            self.assertEqual(rec["provenance"]["source_label"]["finding_status"], "negative")
            self.assertEqual(rec["native_anchors"], {})
            self.assertIn("native_anchors_require_recompute", technical_qa_reasons(rec))
        report = coverage_report(state, {"LAD": 100})
        self.assertTrue(report["complete"])
        self.assertEqual(report["review_count"], 0)
        self.assertEqual(report["technical_qa_count"], 2)

    def test_positive_and_uncertain_clipped_summaries_still_need_reader(self):
        for finding in ("positive", "uncertain"):
            with self.subTest(finding=finding):
                state = self.clipped(finding)
                for rec in (state["annotations"][0], state["annotations"][2]):
                    self.assertTrue(rec["review_required"])
                    self.assertTrue(effective_reader_review_required(rec))
                    self.assertIn("clipped_summary_requires_local_confirmation", reader_review_reasons(rec))
                    self.assertEqual(rec["provenance"]["source_label"]["stenosis_grade"], "50_69")
                    self.assertIs(rec["provenance"]["training_segment_label_eligible"], False)
                report = coverage_report(state, {"LAD": 100})
                self.assertEqual(report["gaps"], [])
                self.assertEqual(report["review_count"], 2)
                self.assertFalse(report["complete"])

    def test_legacy_normal_clip_flag_is_interpreted_without_rewriting(self):
        record = annotation(finding="negative")
        record["review_required"] = True
        record["review_status"] = "needs_rereview"
        record["provenance"].update(derivation="newest_wins_clip", summary_requires_reassessment=True,
                                    native_anchors_require_recompute=True)
        before = deepcopy(record)
        self.assertFalse(effective_reader_review_required(record))
        self.assertEqual(technical_qa_reasons(record), ["native_anchors_require_recompute"])
        self.assertEqual(record, before)

    def test_independent_reader_review_survives_normal_clipping(self):
        for reason in ("reader_requested_second_look", None):
            with self.subTest(reason=reason):
                state = empty_state("synthetic-review", "reader-A")
                rec = annotation(0, 100, finding="negative")
                rec["review_required"] = True
                if reason:
                    rec["provenance"]["review_reason"] = reason
                state = apply_annotation(state, rec)
                result = apply_annotation(state, annotation(40, 60, "replacement", finding="negative"))
                for fragment in (result["annotations"][0], result["annotations"][2]):
                    self.assertTrue(effective_reader_review_required(fragment))
                    self.assertIn(reason or "legacy_unspecified_reader_review", reader_review_reasons(fragment))

    def test_geometry_only_flag_does_not_create_physician_work_or_fix_geometry(self):
        rec = self.geometry_record()
        before = deepcopy(rec)
        self.assertFalse(effective_reader_review_required(rec))
        self.assertEqual(technical_qa_reasons(rec), ["path_local_geometry_ambiguous"])
        state = apply_annotation(empty_state("synthetic-review", "reader-A"), rec)
        report = coverage_report(state, {"LAD": 100})
        self.assertTrue(report["complete"])
        self.assertEqual(report["review_count"], 0)
        self.assertEqual(report["technical_qa_count"], 1)
        self.assertEqual(report["geometry_warnings"], ["path_local_geometry_ambiguous"])
        done = mark_case_complete(state, report)
        self.assertEqual(done["case_status"], "complete")
        self.assertFalse(done["annotations"][0]["provenance"]["training_geometry_eligible"])
        self.assertTrue(done["annotations"][0]["review_required"], "Legacy source flags are not mutated")
        self.assertEqual(rec, before)

    def test_known_geometry_does_not_swallow_independent_reader_reason(self):
        rec = self.geometry_record()
        rec["provenance"]["review_reason"] = "reader_requested_second_look"
        self.assertEqual(reader_review_reasons(rec), ["reader_requested_second_look"])
        self.assertIn("path_local_geometry_ambiguous", technical_qa_reasons(rec))
        rec["provenance"].pop("review_reason")
        rec["provenance"].update(derivation="newest_wins_clip", summary_requires_reassessment=True)
        rec["label"] = annotation()["label"]
        self.assertTrue(effective_reader_review_required(rec))

    def test_unknown_review_flag_is_not_discarded_due_to_training_ineligibility(self):
        rec = annotation(finding="negative")
        rec["review_required"] = True
        rec["provenance"]["training_geometry_eligible"] = False
        self.assertEqual(reader_review_reasons(rec), ["legacy_unspecified_reader_review"])
        self.assertEqual(technical_qa_reasons(rec), ["training_geometry_ineligible"])

    def test_legacy_canonical_group_summary_is_research_qa_not_fake_local_truth(self):
        rec = annotation()
        rec["review_required"] = True
        rec["label"].update(plaque_composition="uncertain", stenosis_grade="unable")
        rec["provenance"]["review_reason"] = CANONICAL_GROUP_REVIEW_REASON
        before = deepcopy(rec)
        self.assertFalse(effective_reader_review_required(rec))
        self.assertEqual(technical_qa_reasons(rec), ["canonical_group_summary_not_local_truth"])
        self.assertEqual(rec, before)
        rec["provenance"]["review_reason"] = "reader_requested_second_look"
        rec["provenance"].update(label_scope="group_summary_only", training_segment_label_eligible=False)
        self.assertTrue(effective_reader_review_required(rec))
        self.assertIn("canonical_group_summary_not_local_truth", technical_qa_reasons(rec))

    def test_new_canonical_group_summary_metadata_is_technical_even_without_legacy_flag(self):
        rec = annotation()
        rec["provenance"].update(label_scope="group_summary_only", training_segment_label_eligible=False)
        self.assertFalse(effective_reader_review_required(rec))
        self.assertEqual(technical_qa_reasons(rec), ["canonical_group_summary_not_local_truth"])

    def test_repeated_normal_clips_do_not_regenerate_review_work(self):
        state = self.clipped()
        for index, (left, right) in enumerate(((10, 20), (70, 80), (5, 15))):
            state = apply_annotation(state, annotation(left, right, f"normal-{index}", finding="negative"))
        self.assertTrue(all(not effective_reader_review_required(rec) for rec in state["annotations"]))
        self.assertTrue(coverage_report(state, {"LAD": 100})["complete"])

    def test_geometry_only_legacy_normal_clip_does_not_regenerate_physician_review(self):
        rec = self.geometry_record()
        rec["provenance"].update(derivation="newest_wins_clip", summary_requires_reassessment=True)
        state = apply_annotation(empty_state("synthetic-review", "reader-A"), rec)
        result = apply_annotation(state, annotation(40, 60, "replacement", finding="negative"))
        for fragment in (result["annotations"][0], result["annotations"][2]):
            self.assertFalse(effective_reader_review_required(fragment))
            self.assertFalse(fragment["review_required"])
            self.assertIs(fragment["provenance"]["training_geometry_eligible"], False)
            self.assertIn("path_local_geometry_ambiguous", technical_qa_reasons(fragment))

    def test_explicit_same_interval_confirmation_clears_local_clipped_summary_review(self):
        state = self.clipped("positive")
        old = state["annotations"][0]
        confirmed = annotation(old["s_start_mm"], old["s_end_mm"], "confirmed")
        confirmed["provenance"].update(label_scope="interval", training_segment_label_eligible=True)
        result = apply_annotation(state, confirmed, editing_id=old["annotation_id"])
        replacement = next(rec for rec in result["annotations"] if rec["annotation_id"] == "confirmed")
        self.assertFalse(effective_reader_review_required(replacement))
        self.assertEqual(technical_qa_reasons(replacement), [])
        self.assertEqual(result["rereview_intervals"], [])
        self.assertTrue(replacement["provenance"]["training_segment_label_eligible"])

    def test_global_ambiguous_lm_is_retained_but_not_a_completion_gate(self):
        state = apply_annotation(empty_state("synthetic-review", "reader-A"), annotation(0, 100))
        report = coverage_report(state, {"LAD": 100}, canonical_lm={"status": "ambiguous"})
        self.assertTrue(report["complete"])
        self.assertEqual(report["geometry_warnings"], ["shared_lm_mapping_ambiguous"])
        self.assertEqual(mark_case_complete(state, report)["completion"]["coverage"], report)

    def test_technical_reasons_do_not_change_strictly_above_ninety_override(self):
        state = empty_state("synthetic-review", "reader-A")
        rec = self.geometry_record()
        rec["s_end_mm"] = 90
        state = apply_annotation(state, rec)
        report = coverage_report(state, {"LAD": 99}, path_samples={"LAD": list(range(100))})
        self.assertEqual(report["coverage_percent"], 90)
        self.assertFalse(report["can_override"])
        with self.assertRaises(ValueError):
            mark_case_complete(state, report, allow_incomplete=True)

    def test_classification_and_completion_roundtrip_preserve_legacy_evidence_and_reader_isolation(self):
        with tempfile.TemporaryDirectory(prefix="review-semantics-v013-") as temporary:
            folder = Path(temporary)
            with AnnotationStore(folder / "reader.sqlite") as store:
                state = apply_annotation(empty_state("synthetic-review", "reader-A"), self.geometry_record())
                state = store.commit(state, "apply")
                source_annotations = deepcopy(state["annotations"])
                done = store.commit(mark_case_complete(state, coverage_report(state, {"LAD": 100})), "mark_complete")
                export = store.export_case("synthetic-review", "reader-A", folder / "exports")
                payload = json.loads(export.read_text(encoding="utf-8"))
                self.assertEqual(payload["annotations"], source_annotations)
                self.assertEqual(payload["completion"]["coverage"]["technical_qa_count"], 1)
                self.assertEqual(store.undo("synthetic-review", "reader-A")["case_status"], "in_progress")
                self.assertEqual(store.redo("synthetic-review", "reader-A")["completion"], done["completion"])
                self.assertEqual(store.load("synthetic-review", "reader-B")["annotations"], [])
            with AnnotationStore(folder / "received.sqlite") as received:
                loaded = received.import_case(export)
                self.assertEqual(loaded["annotations"], source_annotations)
                self.assertEqual(loaded["completion"], done["completion"])
                self.assertTrue(coverage_report(loaded, {"LAD": 100})["complete"])


if __name__ == "__main__":
    unittest.main()
