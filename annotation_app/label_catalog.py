"""Shared, non-diagnostic display colors and optional reader reason vocabulary."""

PROTOCOL_VERSION = "imagecasx-reader-1.3"
REASON_CATALOG_VERSION = "imagecasx-reasons-1.0"
REASON_OPTIONS = (
    ("motion_artifact", "运动/错层", "血管运动模糊、错位或阶梯状断续"),
    ("blooming_metal_artifact", "钙化/金属伪影", "钙化膨胀、金属或条纹伪影遮挡管腔；不自动判定斑块类型"),
    ("poor_contrast", "造影偏弱", "血管显影不足，边界不易辨认"),
    ("noise_blur", "噪声/模糊", "噪声较大、细节或细小血管看不清"),
    ("cpr_geometry_coverage", "CPR偏轴/不完整", "中心线偏离、重建畸变或目标未完整显示；不等同于原始CT不可评估"),
    ("interpretive_uncertainty", "类型/程度难定", "可以看到目标，但组成或狭窄等级仍难确定"),
    ("other", "其他", "其他阅片困难；选择后仍不要求输入文字"),
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
