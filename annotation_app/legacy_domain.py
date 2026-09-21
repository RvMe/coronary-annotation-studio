"""Pure, reader-local annotation editing. Missing coverage is never negative."""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any
from uuid import uuid4

from .label_catalog import REASON_CODES

EPS = 1e-6
SCHEMA_VERSION = "imagecasx-annotations-1.0"
PATHS = {"LAD", "LCX", "RCA"}
FINDINGS = {"negative", "positive", "uncertain", "non_evaluable"}
COMPOSITIONS = {"calcified", "non_calcified", "partially_calcified", "uncertain"}
STENOSES = {"0", "1_24", "25_49", "50_69", "70_99", "100", "unable"}
CANONICAL_GROUP_REVIEW_REASON = "canonical_split_group_summary_requires_subsegment_review"


def technical_qa_reasons(annotation: dict) -> list[str]:
    """Research-side QA, never a demand for a physician to repair geometry.

    This is an interpretation of a snapshot, not a migration. In particular,
    a legacy ``review_required`` flag and its provenance remain untouched.
    Reader completion does not clear these reasons or grant training eligibility.
    """
    provenance = annotation.get("provenance", {})
    reasons = []
    if (provenance.get("geometry_mapping_status") == "path_local_ambiguous"
            or provenance.get("geometry_ambiguous_ranges")):
        reasons.append("path_local_geometry_ambiguous")
    if provenance.get("training_geometry_eligible") is False and not reasons:
        reasons.append("training_geometry_ineligible")
    if provenance.get("native_anchors_require_recompute"):
        reasons.append("native_anchors_require_recompute")
    if (provenance.get("review_reason") == CANONICAL_GROUP_REVIEW_REASON
            or provenance.get("label_scope") == "group_summary_only"):
        reasons.append("canonical_group_summary_not_local_truth")
    if (provenance.get("training_segment_label_eligible") is False
            and "canonical_group_summary_not_local_truth" not in reasons):
        reasons.append("training_segment_label_ineligible")
    return reasons


def reader_review_reasons(annotation: dict) -> list[str]:
    """Return unresolved reader work, separating old overloaded review flags.

    A normal interval remains normal when merely shortened by an overlapping
    new label. Positive/uncertain (and conservatively non-evaluable) inherited
    summaries still need local confirmation. Explicit or unknown independent
    review reasons are retained, including when geometry QA is also present.
    """
    if not annotation.get("review_required"):
        return []
    provenance = annotation.get("provenance", {})
    explicit = provenance.get("reader_review_reasons", [])
    if not isinstance(explicit, list):
        explicit = []
    reasons = [reason for reason in explicit if isinstance(reason, str) and reason]
    reason = provenance.get("review_reason")
    if reason and reason != CANONICAL_GROUP_REVIEW_REASON:
        reasons.append(reason if isinstance(reason, str) else "unspecified_reader_review_reason")
    clipped = provenance.get("derivation") == "newest_wins_clip"
    if clipped and annotation.get("label", {}).get("finding_status") != "negative":
        reasons.append("clipped_summary_requires_local_confirmation")
    elif provenance.get("summary_requires_reassessment") and not clipped:
        reasons.append("summary_requires_reassessment")
    if reasons:
        return list(dict.fromkeys(reasons))
    # Only known, explainable legacy flags may be demoted to technical QA or
    # normal inheritance. An unknown flag is not silently dismissed.
    known_technical = (provenance.get("geometry_mapping_status") == "path_local_ambiguous"
                       or bool(provenance.get("geometry_ambiguous_ranges"))
                       or reason == CANONICAL_GROUP_REVIEW_REASON
                       or provenance.get("label_scope") == "group_summary_only")
    if clipped or known_technical:
        return []
    return ["legacy_unspecified_reader_review"]


def effective_reader_review_required(annotation: dict) -> bool:
    """Whether a live record still needs an explicit reader Apply action."""
    return bool(reader_review_reasons(annotation))


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _interval(start: Any, end: Any) -> tuple[float, float]:
    start, end = _number(start, "s_start_mm"), _number(end, "s_end_mm")
    if start < 0 or end - start <= EPS:
        raise ValueError("Interval must have nonnegative start and end > start")
    return start, end


def validate_label(label: dict, start: float | None = None, end: float | None = None) -> None:
    """Validate explicit reader choices; never supply a normal default."""
    if not isinstance(label, dict):
        raise ValueError("label must be an object")
    finding = label.get("finding_status")
    composition = label.get("plaque_composition")
    if finding not in FINDINGS:
        raise ValueError("Choose a finding status")
    if composition is not None and composition not in COMPOSITIONS:
        raise ValueError("Invalid plaque composition")
    if label.get("stenosis_grade") not in STENOSES:
        raise ValueError("Choose a stenosis grade")
    if label.get("confidence") not in {"high", "medium", "low"} and not (
        finding == "non_evaluable" and label.get("confidence") is None
    ):
        raise ValueError("Choose a confidence")
    if not isinstance(label.get("reason", ""), str):
        raise ValueError("reason must be text")
    # Optional additive field: validation must not add it to legacy snapshots,
    # whose exact source/audit identity remains part of their import contract.
    if "reason_codes" in label:
        codes = label["reason_codes"]
        if not isinstance(codes, list) or any(not isinstance(code, str) for code in codes):
            raise ValueError("reason_codes must be a list of reason codes")
        if len(codes) != len(set(codes)):
            raise ValueError("reason_codes must not contain duplicates")
        if any(code not in REASON_CODES for code in codes):
            raise ValueError("Unknown reason_codes value")
    if finding == "negative" and (composition is not None or label["stenosis_grade"] != "0"):
        raise ValueError("Explicit negative requires no plaque composition and stenosis 0")
    if finding == "positive" and composition is None:
        raise ValueError("Positive finding requires a composition, including uncertain")
    if finding == "non_evaluable" and label["stenosis_grade"] != "unable":
        raise ValueError("Non-evaluable finding requires unable stenosis")
    if label.get("s_peak_stenosis_mm") is not None:
        peak = _number(label["s_peak_stenosis_mm"], "s_peak_stenosis_mm")
        if peak < 0 or (start is not None and peak < start - EPS) or (end is not None and peak > end + EPS):
            raise ValueError("Peak stenosis location must lie in the selected interval")


def _normalise_annotation(annotation: dict) -> dict:
    if not isinstance(annotation, dict):
        raise ValueError("annotation must be an object")
    result = deepcopy(annotation)
    if result.get("path_id") not in PATHS:
        raise ValueError("Invalid path_id")
    for field in ("canonical_anatomy_id", "anatomical_segment"):
        _text(result.get(field), field)
    start, end = _interval(result.get("s_start_mm"), result.get("s_end_mm"))
    validate_label(result.get("label"), start, end)
    result.setdefault("annotation_id", str(uuid4()))
    result.setdefault("label_group_id", result["annotation_id"])
    for field in ("annotation_id", "label_group_id"):
        _text(result[field], field)
    result.setdefault("native_anchors", {})
    result.setdefault("provenance", {})
    if not isinstance(result["provenance"], dict):
        raise ValueError("provenance must be an object")
    result.setdefault("review_required", False)
    if not isinstance(result["review_required"], bool):
        raise ValueError("review_required must be boolean")
    result.setdefault("review_status", "reader_annotated")
    return result


def empty_state(case_id, reader_id, source=None) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": _text(str(case_id), "case_id"),
        "reader_id": _text(reader_id, "reader_id"),
        "revision": 0, "annotations": [], "markers": [], "rereview_intervals": [],
        "view_state": {}, "case_status": "in_progress", "source": deepcopy(source or {}),
    }


def _same_scope(a: dict, b: dict) -> bool:
    return a["path_id"] == b["path_id"] and a["canonical_anatomy_id"] == b["canonical_anatomy_id"]


def _overlap(a: float, b: float, c: float, d: float) -> bool:
    return min(b, d) - max(a, c) > EPS


def validate_state(state: dict) -> None:
    """Validation for the storage boundary and imported snapshots."""
    if not isinstance(state, dict) or state.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported annotation schema")
    for key in ("case_id", "reader_id"):
        _text(state.get(key), key)
    revision = state.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise ValueError("revision must be a nonnegative integer")
    if not isinstance(state.get("source"), dict) or not isinstance(state.get("view_state"), dict):
        raise ValueError("source and view_state must be objects")
    for key in ("annotations", "markers", "rereview_intervals"):
        if not isinstance(state.get(key), list):
            raise ValueError(f"{key} must be a list")
    required = {"annotation_id", "label_group_id", "path_id", "canonical_anatomy_id",
                "anatomical_segment", "s_start_mm", "s_end_mm", "label", "native_anchors",
                "provenance", "review_required"}
    for annotation in state["annotations"]:
        if not isinstance(annotation, dict) or not required.issubset(annotation):
            raise ValueError("Live annotation is missing required fields")
        for identity in ("case_id", "reader_id"):
            if identity in annotation and annotation[identity] != state[identity]:
                raise ValueError("Annotation identity disagrees with its reader/case snapshot")
    annotations = [_normalise_annotation(a) for a in state["annotations"]]
    ids = [a["annotation_id"] for a in annotations]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate annotation ID")
    for index, annotation in enumerate(annotations):
        for other in annotations[index + 1:]:
            if _same_scope(annotation, other) and _overlap(annotation["s_start_mm"], annotation["s_end_mm"], other["s_start_mm"], other["s_end_mm"]):
                raise ValueError("Snapshot contains overlapping live annotations in one canonical scope")
    marker_ids = []
    for marker in state["markers"]:
        marker_ids.append(_text(marker.get("marker_id"), "marker_id"))
        if marker.get("path_id") not in PATHS or _number(marker.get("s_mm"), "s_mm") < 0:
            raise ValueError("Invalid marker")
    if len(marker_ids) != len(set(marker_ids)):
        raise ValueError("Duplicate marker ID")
    for interval in state["rereview_intervals"]:
        if interval.get("path_id") not in PATHS:
            raise ValueError("Invalid rereview path")
        _interval(interval.get("s_start_mm"), interval.get("s_end_mm"))


def _subtract(start: float, end: float, cut_start: float, cut_end: float) -> list[tuple[float, float]]:
    if not _overlap(start, end, cut_start, cut_end):
        return [(start, end)]
    pieces = []
    if cut_start - start > EPS:
        pieces.append((start, cut_start))
    if end - cut_end > EPS:
        pieces.append((cut_end, end))
    return pieces


def _clear_rereview(state: dict, annotation: dict) -> None:
    result = []
    for item in state["rereview_intervals"]:
        if item["path_id"] != annotation["path_id"]:
            result.append(item)
            continue
        for start, end in _subtract(item["s_start_mm"], item["s_end_mm"], annotation["s_start_mm"], annotation["s_end_mm"]):
            result.append({**item, "s_start_mm": start, "s_end_mm": end})
    state["rereview_intervals"] = result


def _rereview(annotation: dict, reason: str) -> dict:
    return {"path_id": annotation["path_id"], "s_start_mm": annotation["s_start_mm"],
            "s_end_mm": annotation["s_end_mm"], "reason": reason}


def _fragment(original: dict, start: float, end: float) -> dict:
    fragment = deepcopy(original)
    fragment["annotation_id"] = str(uuid4())
    fragment["s_start_mm"], fragment["s_end_mm"] = start, end
    inherited_reader_reasons = reader_review_reasons(original)
    summary_needs_review = original["label"]["finding_status"] != "negative"
    fragment["review_required"] = summary_needs_review or bool(inherited_reader_reasons)
    fragment["review_status"] = "needs_rereview" if fragment["review_required"] else "reader_annotated"
    fragment.setdefault("provenance", {}).update({
        "derived_from_annotation_id": original["annotation_id"],
        "original_interval_mm": [original["s_start_mm"], original["s_end_mm"]],
        "derivation": "newest_wins_clip",
        "summary_requires_reassessment": summary_needs_review,
        "reader_review_reasons": inherited_reader_reasons,
        "normal_subset_inherited": not summary_needs_review,
        "source_label": deepcopy(original["label"]),
        "source_native_anchors": deepcopy(original.get("native_anchors", {})),
        "native_anchors_require_recompute": True,
    })
    if summary_needs_review:
        # Recomputing endpoint geometry cannot validate an inherited maximum
        # stenosis or plaque composition for this smaller interval.
        fragment["provenance"]["training_segment_label_eligible"] = False
    fragment["native_anchors"] = {}
    # An old segment maximum is not a proven maximum of a clipped remainder.
    fragment["label"].pop("s_peak_stenosis_mm", None)
    return fragment


def apply_annotation(state, annotation, editing_id=None) -> dict:
    validate_state(state)
    result, new = deepcopy(state), _normalise_annotation(annotation)
    old_edit = None
    if editing_id is not None:
        old_edit = next((a for a in result["annotations"] if a["annotation_id"] == editing_id), None)
        if old_edit is None:
            raise ValueError("The selected annotation is no longer live")
        result["annotations"] = [a for a in result["annotations"] if a["annotation_id"] != editing_id]
        result["rereview_intervals"].append(_rereview(old_edit, "edited_coverage_removed"))
        new["provenance"].setdefault("edited_annotation_id", editing_id)
        new["provenance"].setdefault("previous_interval_mm", [old_edit["s_start_mm"], old_edit["s_end_mm"]])
    if any(a["annotation_id"] == new["annotation_id"] for a in result["annotations"]):
        raise ValueError("annotation_id already exists; select it explicitly to edit")
    kept = []
    overwritten = []
    for old in result["annotations"]:
        if not _same_scope(old, new) or not _overlap(old["s_start_mm"], old["s_end_mm"], new["s_start_mm"], new["s_end_mm"]):
            kept.append(old)
            continue
        overwritten.append(old["annotation_id"])
        kept.extend(_fragment(old, start, end) for start, end in _subtract(old["s_start_mm"], old["s_end_mm"], new["s_start_mm"], new["s_end_mm"]))
    new["provenance"]["overwritten_annotation_ids"] = overwritten
    result["annotations"] = sorted(kept + [new], key=lambda a: (a["path_id"], a["s_start_mm"], a["s_end_mm"], a["annotation_id"]))
    _clear_rereview(result, new)
    result["case_status"] = "in_progress"
    result.pop("completion", None)
    validate_state(result)
    return result


def delete_annotation(state, annotation_id) -> dict:
    validate_state(state)
    result = deepcopy(state)
    old = next((a for a in result["annotations"] if a["annotation_id"] == annotation_id), None)
    if old is None:
        raise ValueError("The selected annotation is no longer live")
    result["annotations"] = [a for a in result["annotations"] if a["annotation_id"] != annotation_id]
    result["rereview_intervals"].append(_rereview(old, "annotation_deleted"))
    result["case_status"] = "in_progress"
    result.pop("completion", None)
    return result


def add_marker(state, path_id, s_mm) -> dict:
    validate_state(state)
    s_mm = _number(s_mm, "s_mm")
    if path_id not in PATHS or s_mm < 0:
        raise ValueError("Invalid marker path/position")
    result = deepcopy(state)
    if not any(m["path_id"] == path_id and abs(m["s_mm"] - s_mm) <= EPS for m in result["markers"]):
        result["markers"].append({"marker_id": str(uuid4()), "path_id": path_id, "s_mm": s_mm})
    return result


def delete_marker(state, marker_id) -> dict:
    validate_state(state)
    result = deepcopy(state)
    if not any(m["marker_id"] == marker_id for m in result["markers"]):
        raise ValueError("Marker no longer exists")
    result["markers"] = [m for m in result["markers"] if m["marker_id"] != marker_id]
    return result


def snap_endpoint(value, candidates, mm_per_pixel, held=None, disabled=False):
    value = _number(value, "value")
    mm_per_pixel = _number(mm_per_pixel, "mm_per_pixel")
    if mm_per_pixel <= 0:
        raise ValueError("mm_per_pixel must be positive")
    if disabled:
        return value, None
    points = sorted(set(_number(candidate, "candidate") for candidate in candidates))
    if held is not None:
        held = _number(held, "held")
        if any(abs(point - held) <= EPS for point in points) and abs(value - held) <= min(12 * mm_per_pixel, 0.75) + EPS:
            return held, held
    if points:
        best = min(points, key=lambda point: (abs(point - value), point))
        if abs(value - best) <= min(8 * mm_per_pixel, 0.5) + EPS:
            return best, best
    return value, None


def _merge_coverage(pieces):
    """Union intervals without rounding their physical coordinates."""
    merged = []
    for left, right in sorted(pieces):
        if right - left <= EPS:
            continue
        if merged and left <= merged[-1][1] + EPS:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
        else:
            merged.append((left, right))
    return merged


def coverage_report(state, path_lengths: dict, *, path_samples=None, canonical_lm=None) -> dict:
    """Report actual labels, gaps and reviews without supplying implicit negatives.

    The legacy two-argument call remains valid and uses physical length. Pass
    ``path_samples={path: CPRPath.distances.tolist()}`` for the reader's actual
    CPR-row coverage criterion. Verified shared LM belongs to LAD only; LCX's
    duplicate prefix, including an exactly shared boundary sample, is excluded.
    Intervals are half-open except that an annotation reaching the path terminal
    also covers its last sample. Sub-sample gaps remain explicit in ``gaps``.
    """
    validate_state(state)
    if not isinstance(path_lengths, dict):
        raise ValueError("Coverage targets must be an object")
    windows = {}
    for path, specification in path_lengths.items():
        if path not in PATHS:
            raise ValueError("Invalid path in coverage target")
        start, end = (_interval(specification.get("start_mm", 0), specification.get("end_mm"))
                      if isinstance(specification, dict) else _interval(0, specification))
        windows[path] = (start, end)
    lm = canonical_lm or {}
    geometry_warnings = []
    lm_end = 0.0
    if lm.get("status") == "verified":
        lm_end = _number(lm.get("verified_end_mm"), "LM endpoint")
        if lm.get("owner", "LAD") != "LAD" or lm_end < 0:
            raise ValueError("Verified LM must have LAD ownership and a nonnegative endpoint")
        if "LAD" not in windows or "LCX" not in windows:
            raise ValueError("Verified LM coverage requires both LAD and LCX targets")
        if lm_end > min(windows["LAD"][1], windows["LCX"][1]) + EPS:
            raise ValueError("Verified LM endpoint exceeds a coverage target")
        windows["LCX"] = (max(windows["LCX"][0], lm_end), windows["LCX"][1])
    elif lm.get("status") == "ambiguous":
        geometry_warnings.append("shared_lm_mapping_ambiguous")
    if path_samples is not None and (not isinstance(path_samples, dict) or set(windows) - set(path_samples)):
        raise ValueError("Provide CPR sample positions for every target path")

    gaps, reviews, technical_qa, by_path = [], [], [], {}
    total_mm = covered_mm = 0.0
    total_slices = covered_slices = 0
    for path, (start, end) in windows.items():
        annotations = [a for a in state["annotations"] if a["path_id"] == path
                       and _overlap(start, end, a["s_start_mm"], a["s_end_mm"])]
        rereview = [a for a in state["rereview_intervals"] if a["path_id"] == path
                    and _overlap(start, end, a["s_start_mm"], a["s_end_mm"])]
        pieces = _merge_coverage((max(start, a["s_start_mm"]), min(end, a["s_end_mm"])) for a in annotations)
        # A stale rereview interval never becomes covered merely because it
        # overlaps a live record. Review-required summaries still count as
        # labelled rows, but remain separate, unresolved review work.
        for pending in rereview:
            pieces = [piece for left, right in pieces for piece in
                      _subtract(left, right, pending["s_start_mm"], pending["s_end_mm"])]
        path_gaps, position = [], start
        for left, right in pieces:
            if left > position + EPS:
                path_gaps.append({"path_id": path, "s_start_mm": position, "s_end_mm": left})
            position = max(position, right)
        if end > position + EPS:
            path_gaps.append({"path_id": path, "s_start_mm": position, "s_end_mm": end})
        path_reviews = deepcopy([a for a in annotations if effective_reader_review_required(a)] + rereview)
        path_technical = []
        for annotation in annotations:
            reasons = technical_qa_reasons(annotation)
            if reasons:
                path_technical.append({
                    "annotation_id": annotation["annotation_id"], "path_id": path,
                    "s_start_mm": annotation["s_start_mm"], "s_end_mm": annotation["s_end_mm"],
                    "reasons": reasons,
                })
                for reason in reasons:
                    if reason in {"path_local_geometry_ambiguous", "training_geometry_ineligible",
                                  "native_anchors_require_recompute"} and reason not in geometry_warnings:
                        geometry_warnings.append(reason)
        path_total_mm = max(0.0, end - start)
        path_covered_mm = min(path_total_mm, sum(right - left for left, right in pieces))
        item = {"start_mm": start, "end_mm": end, "target_mm": path_total_mm,
                "covered_mm": path_covered_mm, "gaps": path_gaps,
                "review_required": path_reviews, "review_count": len(path_reviews),
                "technical_qa": path_technical, "technical_qa_count": len(path_technical)}
        if path_samples is not None:
            try:
                samples = [_number(value, "CPR sample position") for value in path_samples[path]]
            except TypeError as exc:
                raise ValueError("CPR sample positions must be an iterable of numbers") from exc
            if not samples or any(right <= left for left, right in zip(samples, samples[1:])):
                raise ValueError("CPR sample positions must be nonempty and strictly increasing")
            if samples[0] < -EPS or samples[-1] > end + EPS:
                raise ValueError("CPR sample positions exceed the native path extent")
            samples = [s for s in samples if start - EPS <= s <= end + EPS
                       and not (path == "LCX" and lm_end > 0 and s <= lm_end + EPS)]
            def has_label(s):
                return any(left - EPS <= s and (s < right - EPS or
                           (abs(s - end) <= EPS and right >= end - EPS)) for left, right in pieces)
            count = sum(has_label(s) for s in samples)
            item.update(total_slices=len(samples), covered_slices=count,
                        coverage_ratio=count / len(samples) if samples else 1.0)
            total_slices += len(samples)
            covered_slices += count
        else:
            item.update(total_slices=None, covered_slices=None,
                        coverage_ratio=path_covered_mm / path_total_mm if path_total_mm else 1.0)
        item["coverage_percent"] = item["coverage_ratio"] * 100
        by_path[path] = item
        gaps.extend(path_gaps)
        reviews.extend(path_reviews)
        technical_qa.extend(path_technical)
        total_mm += path_total_mm
        covered_mm += path_covered_mm
    ratio = (covered_slices / total_slices if total_slices else 0.0) if path_samples is not None else (covered_mm / total_mm if total_mm else 0.0)
    # This is reader completion, not a geometry or training-readiness gate.
    complete = bool(windows) and not gaps and not reviews
    return {"complete": complete, "gaps": gaps, "review_required": reviews,
            "review_count": len(reviews), "geometry_warnings": geometry_warnings,
            "technical_qa": technical_qa, "technical_qa_count": len(technical_qa),
            "coverage_basis": "unique_cpr_samples" if path_samples is not None else "physical_length_mm",
            "coverage_ratio": ratio, "coverage_percent": ratio * 100,
            "total_slices": total_slices if path_samples is not None else None,
            "covered_slices": covered_slices if path_samples is not None else None,
            "target_mm": total_mm, "covered_mm": covered_mm, "by_path": by_path,
            # Integer comparison makes exactly 90% unambiguously ineligible.
            "can_override": (covered_slices * 10 > total_slices * 9 and total_slices > 0)
                            if path_samples is not None else ratio > .9 + EPS,
            "shared_lm_excluded_from_lcx": lm_end > 0}


def mark_case_complete(state, report, *, allow_incomplete=False):
    """Record explicit reader completion without altering labels or their flags.

    An override is only valid above 90% of target CPR samples. The report and
    gaps are retained in the JSON/audit stream; completion is not adjudication.
    """
    validate_state(state)
    if not isinstance(report, dict) or not isinstance(report.get("complete"), bool):
        raise ValueError("A current coverage report is required")
    complete = report["complete"]
    if not complete and (not allow_incomplete or not report.get("can_override")):
        raise ValueError("Incomplete completion requires explicit confirmation and coverage above 90%")
    result = deepcopy(state)
    result["case_status"] = "complete" if complete else "complete_with_gaps"
    result["completion"] = {
        "schema_version": "imagecasx-completion-1.0",
        "mode": "strict_complete" if complete else "reader_confirmed_with_gaps",
        "confirmed_with_gaps": not complete,
        "checked_revision": state["revision"], "coverage": deepcopy(report),
    }
    return result
