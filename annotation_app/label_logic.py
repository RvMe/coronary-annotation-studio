"""Pure current-protocol reader-form dependencies, separate from legacy imports.

Loading an existing annotation must not call these normalizers: old snapshots and
their audit hashes remain unchanged. The UI calls transitions for explicit reader
actions, and prepares a copy only when submitting an edited/new annotation.
"""
from __future__ import annotations

from copy import deepcopy
import math

from .label_catalog import REASON_CODES, REASON_OPTIONS

FINDINGS = frozenset(("negative", "positive", "uncertain", "non_evaluable"))
COMPOSITIONS = frozenset(("calcified", "non_calcified", "partially_calcified", "uncertain"))
STENOSES = frozenset(("0", "1_24", "25_49", "50_69", "70_99", "100", "unable"))
CONFIDENCES = frozenset(("high", "medium", "low"))
NUMERIC_POSITIVE_STENOSES = STENOSES - {"0", "unable"}


def label_applicability(label: dict) -> dict[str, bool]:
    """Describe applicable controls without changing a current or legacy label.

    `negative` denotes the explicit normal preset/status, not merely an absence
    of visible plaque. A plaque-positive label may validly have 0% stenosis.
    The difficulty selector belongs only to non-evaluable in reader protocol 1.2;
    already-saved reasons on other findings may be shown separately read-only.
    """
    finding = label.get("finding_status")
    plaque = finding in {"positive", "uncertain"}
    confidence = finding in {"negative", "positive", "uncertain"}
    return {
        "finding_selected": finding in FINDINGS,
        "composition_enabled": plaque,
        "composition_required": plaque,
        "stenosis_enabled": plaque,
        "stenosis_required": plaque,
        "confidence_enabled": confidence,
        "confidence_required": confidence,
        "reasons_enabled": finding == "non_evaluable",
        "peak_enabled": plaque and label.get("stenosis_grade") in NUMERIC_POSITIVE_STENOSES,
    }


def _validate_reason_codes(codes: object) -> None:
    if not isinstance(codes, list) or any(not isinstance(code, str) for code in codes):
        raise ValueError('Reasons must be a list of optional reason codes.')
    if len(codes) != len(set(codes)) or any(code not in REASON_CODES for code in codes):
        raise ValueError('Reasons contain an unknown or duplicate option.')


def transition_label(label: dict, key: str, value: object) -> dict:
    """Apply one reader choice and clear obsolete dependent choices atomically.

    Switching the finding resets all downstream diagnostic choices. Changing
    composition or stenosis asks for confidence again, rather than inheriting an
    earlier confidence in a different diagnosis. Re-clicking a selected value is
    idempotent. Legacy free-text reason stays available as a read-only old note;
    its source snapshot is never mutated by this function.
    """
    if key not in {"finding_status", "plaque_composition", "stenosis_grade", "confidence", "reason_codes"}:
        raise ValueError('Unknown annotation field.')
    result = deepcopy(label)
    applicability = label_applicability(label)
    if key == "finding_status":
        if value not in FINDINGS:
            raise ValueError('Select an interval finding.')
    elif key == "reason_codes":
        if not applicability["reasons_enabled"]:
            raise ValueError('Reasons can be selected only for a non-evaluable interval.')
        _validate_reason_codes(value)
    else:
        control = {"plaque_composition": "composition_enabled", "stenosis_grade": "stenosis_enabled", "confidence": "confidence_enabled"}[key]
        if not applicability[control]:
            raise ValueError('This field does not apply to the current finding. Select a finding first.')
        valid = {"plaque_composition": COMPOSITIONS, "stenosis_grade": STENOSES, "confidence": CONFIDENCES}[key]
        if value not in valid:
            raise ValueError('Unknown annotation option.')
    if label.get(key) == value:
        return result
    if key == "finding_status":
        result.update({"finding_status": value, "plaque_composition": None,
                       "stenosis_grade": None, "confidence": None, "reason_codes": []})
        result.pop("s_peak_stenosis_mm", None)
        result.pop("entry_method", None)
        if value == "negative":
            result["stenosis_grade"] = "0"
        elif value == "non_evaluable":
            result["stenosis_grade"] = "unable"
    elif key == "reason_codes":
        selected = set(value)
        result[key] = [code for code, _, _ in REASON_OPTIONS if code in selected]
    else:
        result[key] = value
        if key in {"plaque_composition", "stenosis_grade"}:
            result["confidence"] = None
        if key == "stenosis_grade" and value not in NUMERIC_POSITIVE_STENOSES:
            result.pop("s_peak_stenosis_mm", None)
    return result


def prepare_label_for_submission(label: dict) -> dict:
    """Canonicalize fixed/skipped fields on a copy for an explicit new commit.

    The caller must preserve superseded legacy choices in edit provenance. This
    is not an import migration and must never be written back merely on loading.
    Legacy reasons are preserved; changing finding through transition_label is
    the explicit action that clears selectable reason codes.
    """
    result = deepcopy(label)
    finding = result.get("finding_status")
    if finding == "negative":
        result["plaque_composition"] = None
        result["stenosis_grade"] = "0"
    elif finding == "non_evaluable":
        result["plaque_composition"] = None
        result["stenosis_grade"] = "unable"
        result["confidence"] = None
    if not label_applicability(result)["peak_enabled"]:
        result.pop("s_peak_stenosis_mm", None)
    return result


def validate_new_label(label: dict) -> None:
    """Validate an explicit protocol-1.2 submission without legacy mutations.

    Persistence retains the less strict historical validator, so old annotations
    remain importable. A skipped confidence is null, never an invented low value.
    """
    if not isinstance(label, dict) or label.get("finding_status") not in FINDINGS:
        raise ValueError('Select 1 · Interval finding first.')
    finding = label["finding_status"]
    composition = label.get("plaque_composition")
    stenosis = label.get("stenosis_grade")
    confidence = label.get("confidence")
    applicability = label_applicability(label)
    if applicability["composition_required"] and composition not in COMPOSITIONS:
        raise ValueError('Select 2 · Plaque composition; choose Uncertain type if needed.')
    if not applicability["composition_enabled"] and composition is not None:
        raise ValueError('Normal and non-evaluable findings cannot retain plaque composition.')
    if stenosis not in STENOSES:
        raise ValueError('Select 3 · Maximum diameter stenosis.')
    if finding == "negative" and stenosis != "0":
        raise ValueError('Normal means no plaque and 0% stenosis. Non-plaque stenosis cannot be labeled normal.')
    if finding == "non_evaluable":
        if stenosis != "unable" or confidence is not None:
            raise ValueError('Not evaluable skips stenosis grading and confidence; these are saved as unable and not applicable.')
    elif confidence not in CONFIDENCES:
        raise ValueError('Select 4 · Confidence; confirm it again after changing the finding.')
    if not isinstance(label.get("reason", ""), str):
        raise ValueError('The legacy note must be text.')
    if "reason_codes" in label:
        _validate_reason_codes(label["reason_codes"])
    peak = label.get("s_peak_stenosis_mm")
    if peak is not None:
        if not applicability["peak_enabled"]:
            raise ValueError('Peak stenosis requires an explicit nonzero stenosis grade.')
        if isinstance(peak, bool) or not isinstance(peak, (int, float)) or not math.isfinite(peak) or peak < 0:
            raise ValueError('Peak stenosis must be a valid nonnegative distance in millimeters.')
