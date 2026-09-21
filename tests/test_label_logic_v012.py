"""Reader-form dependency contract independent of Qt and clinical image data."""
from copy import deepcopy
import unittest

from annotation_app.label_logic import (
    label_applicability, prepare_label_for_submission, transition_label, validate_new_label,
)


class LabelLogicV012Tests(unittest.TestCase):
    def positive(self, **kwargs):
        result = {"finding_status": "positive", "plaque_composition": "calcified",
                  "stenosis_grade": "50_69", "confidence": "high", "reason_codes": []}
        result.update(kwargs)
        return result

    def test_empty_finding_disables_all_following_controls(self):
        flags = label_applicability({})
        self.assertFalse(any(flags.values()))
        for key, value in [("plaque_composition", "calcified"), ("stenosis_grade", "0"),
                           ("confidence", "high"), ("reason_codes", [])]:
            with self.assertRaises(ValueError):
                transition_label({}, key, value)

    def test_normal_has_fixed_zero_but_requires_explicit_confidence(self):
        label = transition_label({}, "finding_status", "negative")
        self.assertIsNone(label["plaque_composition"])
        self.assertEqual(label["stenosis_grade"], "0")
        self.assertIsNone(label["confidence"])
        flags = label_applicability(label)
        self.assertFalse(flags["composition_enabled"])
        self.assertFalse(flags["stenosis_enabled"])
        self.assertTrue(flags["confidence_required"])
        self.assertFalse(flags["reasons_enabled"])
        with self.assertRaises(ValueError):
            validate_new_label(label)
        validate_new_label(transition_label(label, "confidence", "high"))

    def test_positive_zero_stenosis_is_permitted(self):
        label = self.positive(stenosis_grade="0")
        validate_new_label(label)
        self.assertFalse(label_applicability(label)["peak_enabled"])

    def test_positive_and_suspected_both_require_explicit_composition(self):
        for finding in ("positive", "uncertain"):
            label = self.positive(finding_status=finding, plaque_composition=None)
            with self.assertRaisesRegex(ValueError, "Plaque composition"):
                validate_new_label(label)
            label["plaque_composition"] = "uncertain"
            validate_new_label(label)

    def test_non_evaluable_skips_diagnostic_controls_without_fake_low_confidence(self):
        label = transition_label(self.positive(), "finding_status", "non_evaluable")
        self.assertIsNone(label["plaque_composition"])
        self.assertEqual(label["stenosis_grade"], "unable")
        self.assertIsNone(label["confidence"])
        flags = label_applicability(label)
        self.assertFalse(any(flags[key] for key in ("composition_enabled", "stenosis_enabled", "confidence_enabled", "peak_enabled")))
        self.assertTrue(flags["reasons_enabled"])
        validate_new_label(label)
        for key, value in [("plaque_composition", "uncertain"), ("stenosis_grade", "0"), ("confidence", "low")]:
            with self.assertRaises(ValueError):
                transition_label(label, key, value)

    def test_reason_codes_are_optional_and_only_editable_when_non_evaluable(self):
        label = transition_label({}, "finding_status", "non_evaluable")
        validate_new_label(label)
        codes = ["other", "motion_artifact"]
        updated = transition_label(label, "reason_codes", codes)
        self.assertEqual(updated["reason_codes"], ["motion_artifact", "other"])
        self.assertEqual(codes, ["other", "motion_artifact"])
        validate_new_label(updated)
        for malformed in (None, "motion_artifact", ["unknown"], ["other", "other"], [False]):
            with self.assertRaises(ValueError):
                transition_label(label, "reason_codes", malformed)
        with self.assertRaises(ValueError):
            transition_label(self.positive(), "reason_codes", ["other"])

    def test_finding_change_clears_downstream_and_preserves_legacy_text(self):
        previous = self.positive(reason="历史备注", reason_codes=["motion_artifact"],
                                 entry_method="typical_normal_preset", s_peak_stenosis_mm=5.0)
        source = deepcopy(previous)
        result = transition_label(previous, "finding_status", "uncertain")
        self.assertEqual(previous, source)
        self.assertIsNone(result["plaque_composition"])
        self.assertIsNone(result["stenosis_grade"])
        self.assertIsNone(result["confidence"])
        self.assertEqual(result["reason_codes"], [])
        self.assertEqual(result["reason"], "历史备注")
        self.assertNotIn("entry_method", result)
        self.assertNotIn("s_peak_stenosis_mm", result)

    def test_non_evaluable_to_evaluable_does_not_inherit_unable_or_old_reasons(self):
        old = transition_label({}, "finding_status", "non_evaluable")
        old = transition_label(old, "reason_codes", ["noise_blur"])
        for finding in ("positive", "uncertain"):
            label = transition_label(old, "finding_status", finding)
            self.assertIsNone(label["stenosis_grade"])
            self.assertIsNone(label["confidence"])
            self.assertEqual(label["reason_codes"], [])

    def test_repeated_selected_value_is_idempotent_and_does_not_clear_confidence(self):
        label = self.positive(reason_codes=["motion_artifact"])
        for key in ("finding_status", "plaque_composition", "stenosis_grade", "confidence"):
            result = transition_label(label, key, label[key])
            self.assertEqual(result, label)
            self.assertIsNot(result, label)

    def test_changed_composition_or_stenosis_requires_confidence_reconfirmation(self):
        label = self.positive()
        for key, value in (("plaque_composition", "non_calcified"), ("stenosis_grade", "25_49")):
            changed = transition_label(label, key, value)
            self.assertIsNone(changed["confidence"])
            with self.assertRaisesRegex(ValueError, "Confidence"):
                validate_new_label(changed)
            validate_new_label(transition_label(changed, "confidence", "medium"))

    def test_peak_only_for_positive_or_suspected_numeric_nonzero_stenosis(self):
        for finding in ("positive", "uncertain"):
            for stenosis in ("1_24", "25_49", "50_69", "70_99", "100"):
                label = self.positive(finding_status=finding, stenosis_grade=stenosis, s_peak_stenosis_mm=5.0)
                self.assertTrue(label_applicability(label)["peak_enabled"])
                validate_new_label(label)
        for stenosis in ("0", "unable"):
            label = self.positive(s_peak_stenosis_mm=5.0)
            changed = transition_label(label, "stenosis_grade", stenosis)
            self.assertNotIn("s_peak_stenosis_mm", changed)
            self.assertFalse(label_applicability(changed)["peak_enabled"])

    def test_legacy_non_evaluable_preparation_is_explicit_copy_without_mutation(self):
        legacy = self.positive(finding_status="non_evaluable", reason="旧备注", reason_codes=["other"],
                               s_peak_stenosis_mm=4)
        before = deepcopy(legacy)
        label_applicability(legacy)
        prepared = prepare_label_for_submission(legacy)
        self.assertEqual(legacy, before)
        self.assertIsNone(prepared["confidence"])
        self.assertIsNone(prepared["plaque_composition"])
        self.assertEqual(prepared["stenosis_grade"], "unable")
        self.assertEqual(prepared["reason"], "旧备注")
        self.assertEqual(prepared["reason_codes"], ["other"])
        self.assertNotIn("s_peak_stenosis_mm", prepared)
        validate_new_label(prepared)
        with self.assertRaises(ValueError):
            validate_new_label(legacy)

    def test_old_non_evaluable_missing_confidence_can_be_prepared(self):
        label = {"finding_status": "non_evaluable", "plaque_composition": None, "stenosis_grade": "unable"}
        prepared = prepare_label_for_submission(label)
        self.assertIsNone(prepared["confidence"])
        validate_new_label(prepared)

    def test_old_positive_reason_is_preserved_without_reopening_reason_selector(self):
        legacy = self.positive(reason="旧文字", reason_codes=["noise_blur"])
        prepared = prepare_label_for_submission(legacy)
        self.assertEqual(prepared, legacy)
        self.assertFalse(label_applicability(prepared)["reasons_enabled"])
        validate_new_label(prepared)

    def test_invalid_new_submission_rejects_hidden_values_and_invalid_peak(self):
        labels = [None, {}, self.positive(finding_status="missing"),
                  self.positive(finding_status="negative"),
                  self.positive(finding_status="non_evaluable"),
                  self.positive(stenosis_grade="0", s_peak_stenosis_mm=1),
                  self.positive(stenosis_grade="unable", s_peak_stenosis_mm=1),
                  self.positive(s_peak_stenosis_mm=float("nan")),
                  self.positive(s_peak_stenosis_mm=True),
                  self.positive(reason_codes=["missing"])]
        for label in labels:
            with self.subTest(label=label), self.assertRaises(ValueError):
                validate_new_label(label)


if __name__ == "__main__":
    unittest.main()
