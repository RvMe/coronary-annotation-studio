"""Shared, non-diagnostic display colors and optional reader reason vocabulary."""

PROTOCOL_VERSION = "imagecasx-reader-1.3"
REASON_CATALOG_VERSION = "imagecasx-reasons-1.0"
REASON_OPTIONS = (
    ("motion_artifact", 'Motion / misalignment', 'Motion blur, misalignment or discontinuity along the path'),
    ("blooming_metal_artifact", 'Blooming / metal artifact', 'Calcium blooming, metal or streaks obscure the lumen. This does not assign plaque composition.'),
    ("poor_contrast", 'Poor contrast', 'Weak vessel enhancement makes boundaries difficult to identify.'),
    ("noise_blur", 'Noise / blur', 'Noise or blur obscures detail or small vessels.'),
    ("cpr_geometry_coverage", 'CPR geometry / coverage', 'Off-center geometry, reconstruction distortion or incomplete coverage. This does not imply that native CT is not evaluable.'),
    ("interpretive_uncertainty", 'Interpretive uncertainty', 'The target is visible but composition or stenosis grade remains uncertain.'),
    ("other", 'Other', 'Other reading difficulty; a text explanation is not required.'),
)
REASON_CODES = frozenset(code for code, _, _ in REASON_OPTIONS)

# Composition buttons and interval overlays must use one palette.
ANNOTATION_COLORS = {
    "negative": "#7fa98d", "positive": "#ba858f", "uncertain": "#b2a477",
    "non_evaluable": "#9a93ba", "calcified": "#a9c0df",
    "non_calcified": "#c8999c", "partially_calcified": "#b5a0cf",
}
SELECTION_COLORS = {
    "finding_status": {key: ANNOTATION_COLORS[key] for key in ("negative", "positive", "uncertain", "non_evaluable")},
    "plaque_composition": {key: ANNOTATION_COLORS[key] for key in ("calcified", "non_calcified", "partially_calcified", "uncertain")},
    "stenosis_grade": {"0": "#7fa98d", "1_24": "#b7be82", "25_49": "#d2b56f", "50_69": "#d19869", "70_99": "#c87979", "100": "#aa7897", "unable": "#9babc0"},
    "confidence": {"high": "#83b3a4", "medium": "#c9b47c", "low": "#aa99bd"},
}
