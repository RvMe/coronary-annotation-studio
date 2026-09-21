"""Native LPS geometry and HU-preserving CPR reconstruction.

Source evidence: the completed pipeline's scripts/cprlib.py, sample_cpr(),
physical_to_continuous_indices(), write_cpr() and resample_polyline(), plus
scripts/generate_reference_cpr.py, process_vessel(). Arrays are (N,H,W), with
columns along normal and rows along binormal. Frames already contain SimpleITK
physical LPS coordinates. The NRRD identity affine is only a local CPR grid.

Reformats sample the *native* HU array through the released frames. This avoids
double interpolation, cropped rotated corners, and ambiguous off-grid frames.
The released CPR array is retained unchanged for reference/QA. The upstream
sampler rounded integer inputs before its float32 write; comparison with our
float32 interpolation therefore has an expected <= 0.5 HU quantization error.
"""

from __future__ import annotations

import csv
import ctypes
import hashlib
import json
import math
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import NamedTuple

import numpy as np
import SimpleITK as sitk
from scipy.ndimage import map_coordinates
from .imaging import _assert_self_contained_image


class GeometryError(ValueError):
    """Input geometry cannot safely identify native physical positions."""


def _ascii_short_path(path: Path) -> str | None:
    """ITK's Windows NIfTI backend may still use narrow fopen, unlike Python."""
    if str(path).isascii():
        return str(path)
    if os.name != "nt":
        return None
    function = ctypes.windll.kernel32.GetShortPathNameW
    function.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint)
    function.restype = ctypes.c_uint
    size = function(str(path), None, 0)
    if not size:
        return None
    buffer = ctypes.create_unicode_buffer(size)
    if not function(str(path), buffer, size):
        return None
    return buffer.value if buffer.value.isascii() else None


def _read_image(path: str | Path) -> sitk.Image:
    """Read Unicode Windows filenames without modifying the source.

    Use the existing NTFS short name where available. If 8.3 names are disabled,
    copy only the currently decoded compressed file into a private temporary
    directory, read it, then remove that temporary copy. Never cache raw CCTA in
    the source repository, and never rename the user's data to work around ITK.
    """
    path = Path(path).resolve()
    _assert_self_contained_image(path)
    if os.name != "nt" or str(path).isascii():
        return sitk.ReadImage(str(path))
    short = _ascii_short_path(path)
    if short is not None:
        return sitk.ReadImage(short)
    with tempfile.TemporaryDirectory(prefix="ImageCASX-decode-") as directory:
        readable_root = _ascii_short_path(Path(directory))
        if readable_root is None:
            raise GeometryError("Windows ITK requires an ASCII temporary path on this computer. Set TEMP to a writable ASCII directory and restart; data filenames may remain Chinese.")
        staged = Path(directory) / ("volume" + "".join(path.suffixes))
        shutil.copyfile(path, staged)
        return sitk.ReadImage(str(Path(readable_root) / staged.name))


def safe_relative_path(root: Path | str, relative: str) -> Path:
    """Resolve a portable asset path, rejecting traversal, drives and symlinks."""
    if not isinstance(relative, str) or not relative or "\x00" in relative:
        raise ValueError("Asset path must be a nonempty relative string")
    portable = relative.replace("\\", "/")
    win = PureWindowsPath(relative)
    if Path(portable).is_absolute() or win.is_absolute() or win.drive or ":" in portable:
        raise ValueError(f"Absolute/drive asset path is forbidden: {relative!r}")
    if ".." in portable.split("/"):
        raise ValueError(f"Parent traversal is forbidden: {relative!r}")
    base = Path(root).resolve()
    target = (base / portable).resolve()
    if target == base or not target.is_relative_to(base):
        raise ValueError(f"Asset escapes case directory: {relative!r}")
    return target


def _finite(value: float, name: str) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise GeometryError(f"{name} must be finite")
    return value


def _unit(vector: np.ndarray) -> np.ndarray:
    length = np.linalg.norm(vector)
    if not np.isfinite(length) or length < 1e-10:
        raise GeometryError("Degenerate interpolated frame")
    return vector / length


class CPRFrame(NamedTuple):
    center: np.ndarray
    tangent: np.ndarray
    normal: np.ndarray
    binormal: np.ndarray


class NativeVolume:
    """One resident SimpleITK volume and a read-only, normally zero-copy HU view."""

    def __init__(self, image: sitk.Image):
        if image.GetDimension() != 3 or image.GetNumberOfComponentsPerPixel() != 1:
            raise GeometryError("Native CCTA must be a scalar 3-D volume")
        self.image = image
        self.origin = np.asarray(image.GetOrigin(), dtype=np.float64)
        self.spacing = np.asarray(image.GetSpacing(), dtype=np.float64)
        self.direction = np.asarray(image.GetDirection(), dtype=np.float64).reshape(3, 3)
        if not (np.isfinite(self.origin).all() and np.isfinite(self.spacing).all()
                and np.isfinite(self.direction).all() and (self.spacing > 0).all()):
            raise GeometryError("Invalid native affine")
        if abs(np.linalg.det(self.direction)) < 1e-8:
            raise GeometryError("Singular native direction matrix")
        self._inverse_direction = np.linalg.inv(self.direction)
        array = sitk.GetArrayViewFromImage(image)
        self.array_zyx = (array if array.dtype in (np.dtype("int16"), np.dtype("float32"))
                          else array.astype(np.float32))
        self.array_zyx.flags.writeable = False

    @classmethod
    def read(cls, path: str | Path) -> "NativeVolume":
        return cls(_read_image(path))

    def world_to_index(self, point_lps) -> np.ndarray:
        """LPS xyz -> continuous native xyz index; accepts (...,3)."""
        point = np.asarray(point_lps, dtype=np.float64)
        if point.shape[-1:] != (3,) or not np.isfinite(point).all():
            raise GeometryError("Physical points must be finite (...,3) LPS")
        return ((point - self.origin) @ self._inverse_direction.T) / self.spacing

    def index_to_world(self, index_xyz) -> np.ndarray:
        index = np.asarray(index_xyz, dtype=np.float64)
        if index.shape[-1:] != (3,) or not np.isfinite(index).all():
            raise GeometryError("Indices must be finite (...,3)")
        return (index * self.spacing) @ self.direction.T + self.origin

    def sample(self, points_lps, outside_hu: float = -1024.0) -> np.ndarray:
        points = np.asarray(points_lps, dtype=np.float64)
        indices = self.world_to_index(points)
        values = map_coordinates(
            self.array_zyx, indices.reshape(-1, 3)[:, ::-1].T,
            order=1, mode="constant", cval=float(outside_hu),
            output=np.float32, prefilter=False,
        )
        return values.reshape(points.shape[:-1])

    def axial(self, z_index: float) -> np.ndarray:
        """Nearest acquired native axial plane, rows=y, columns=x (no flip)."""
        index = int(np.clip(round(_finite(z_index, "z index")), 0, self.array_zyx.shape[0] - 1))
        return self.array_zyx[index]

    def orientation_labels(self) -> dict[str, str]:
        """Patient directions at unflipped axial image edges (supports obliquity)."""
        def label(vector):
            positive, negative = "LPS", "RAI"
            # Retain meaningful oblique components, ordered by contribution.
            order = np.argsort(-np.abs(vector))
            return "".join((positive[i] if vector[i] > 0 else negative[i])
                           for i in order if abs(vector[i]) >= .2)
        horizontal, vertical = self.direction[:, 0], self.direction[:, 1]
        return {"left": label(-horizontal), "right": label(horizontal),
                "top": label(-vertical), "bottom": label(vertical)}


@dataclass
class CPRPath:
    volume: np.ndarray
    distances: np.ndarray
    centers: np.ndarray
    normals: np.ndarray
    binormals: np.ndarray
    tangents: np.ndarray
    length_mm: float
    spacing_mm: float
    segment_ids: np.ndarray
    native: NativeVolume | None = None
    path_id: str = ""

    def __post_init__(self):
        self.volume = np.asarray(self.volume)
        if self.volume.ndim != 3 or min(self.volume.shape) < 2:
            raise GeometryError("CPR volume must have (N,H,W) dimensions >= 2")
        if not np.issubdtype(self.volume.dtype, np.number):
            raise GeometryError("CPR must contain numeric HU values")
        self.length_mm = _finite(self.length_mm, "length")
        self.spacing_mm = _finite(self.spacing_mm, "spacing")
        if self.length_mm <= 0 or self.spacing_mm <= 0:
            raise GeometryError("CPR length and spacing must be positive")
        count = len(self.volume)
        self.distances = np.array(self.distances, dtype=np.float64, copy=True)
        if self.distances.shape != (count,) or not np.isfinite(self.distances).all():
            raise GeometryError("CPR distances do not match rows")
        # Upstream arange can overshoot by <= one quarter of a sample. The last
        # center is already interpolated to the actual polyline endpoint.
        if abs(self.distances[0]) > 1e-6 or abs(self.distances[-1] - self.length_mm) > self.spacing_mm * .251 + 1e-5:
            raise GeometryError("CPR distances disagree with source polyline length")
        self.distances[-1] = self.length_mm
        if np.any(np.diff(self.distances) <= 0):
            raise GeometryError("CPR distances must be strictly increasing")
        for name in ("centers", "normals", "binormals", "tangents"):
            value = np.asarray(getattr(self, name), dtype=np.float64)
            if value.shape != (count, 3) or not np.isfinite(value).all():
                raise GeometryError(f"Invalid {name} shape/values")
            setattr(self, name, value)
        basis = np.stack((self.tangents, self.normals, self.binormals), axis=2)
        gram = np.swapaxes(basis, 1, 2) @ basis
        if np.max(np.abs(gram - np.eye(3))) > 1e-4 or np.min(np.linalg.det(basis)) < .999:
            raise GeometryError("Released frames are not right-handed orthonormal bases")
        self.segment_ids = np.asarray(self.segment_ids, dtype=np.int32)
        if self.segment_ids.shape != (count,):
            raise GeometryError("Segment IDs do not match CPR rows")
        for array in (self.volume, self.distances, self.centers, self.normals,
                      self.binormals, self.tangents, self.segment_ids):
            array.flags.writeable = False

    @classmethod
    def read(cls, image_path, frames_path, qa_path, native=None, path_id="", path_csv=None):
        image = _read_image(image_path)
        if image.GetDimension() != 3 or image.GetNumberOfComponentsPerPixel() != 1:
            raise GeometryError("CPR NRRD must be a scalar 3-D image")
        spacing = np.asarray(image.GetSpacing(), dtype=float)
        if not np.isclose(spacing[0], spacing[1], atol=1e-6):
            raise GeometryError("Expected equal CPR in-plane sampling")
        qa = json.loads(Path(qa_path).read_text(encoding="utf-8-sig"))
        with np.load(str(frames_path), allow_pickle=False) as archive:
            required = ("centers", "normals", "binormals", "tangents", "distance_mm", "segment_id")
            if not set(required).issubset(archive.files):
                raise GeometryError("Frame archive is missing required arrays")
            frames = {key: archive[key] for key in required}
        result = cls(
            volume=sitk.GetArrayFromImage(image), distances=frames["distance_mm"],
            centers=frames["centers"], normals=frames["normals"],
            binormals=frames["binormals"], tangents=frames["tangents"],
            length_mm=qa["length_mm"], spacing_mm=spacing[0],
            segment_ids=frames["segment_id"], native=native, path_id=path_id,
        )
        if path_csv is not None:
            with Path(path_csv).open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            if len(rows) != len(result.distances):
                raise GeometryError("Path CSV/frame count differs")
            csv_centers = np.array([[float(row[key]) for key in ("x_mm", "y_mm", "z_mm")] for row in rows])
            csv_distances = np.array([float(row["distance_mm"]) for row in rows])
            csv_segments = np.array([int(row["segment_id"]) for row in rows])
            if (not np.allclose(csv_centers, result.centers, rtol=0, atol=1e-5)
                    or not np.allclose(csv_distances, frames["distance_mm"], rtol=0, atol=1e-6)
                    or not np.array_equal(csv_segments, result.segment_ids)):
                raise GeometryError("Path CSV and frames disagree")
        return result

    def clamp_s(self, s_mm: float) -> float:
        return float(np.clip(_finite(s_mm, "s"), 0, self.length_mm))

    def row_at(self, s_mm: float) -> float:
        """Physical s -> fractional row, including the shorter final interval."""
        return float(np.interp(self.clamp_s(s_mm), self.distances, np.arange(len(self.distances))))

    def s_at_row(self, row: float) -> float:
        return float(np.interp(_finite(row, "row"), np.arange(len(self.distances)), self.distances))

    def frame(self, s_mm: float) -> CPRFrame:
        s = self.clamp_s(s_mm)
        high = int(np.searchsorted(self.distances, s, side="right"))
        if high == 0:
            high = 1
        if high >= len(self.distances):
            high = len(self.distances) - 1
        low = high - 1
        weight = (s - self.distances[low]) / (self.distances[high] - self.distances[low])
        def blend(array):
            return array[low] * (1 - weight) + array[high] * weight
        center = blend(self.centers)
        tangent = _unit(blend(self.tangents))
        normal = blend(self.normals)
        normal = _unit(normal - np.dot(normal, tangent) * tangent)
        binormal = _unit(np.cross(tangent, normal))
        return CPRFrame(center, tangent, normal, binormal)

    def point(self, s_mm, u_mm=0, v_mm=0) -> np.ndarray:
        frame = self.frame(s_mm)
        return frame.center + _finite(u_mm, "u") * frame.normal + _finite(v_mm, "v") * frame.binormal

    def display_point(self, s_mm, u_mm=0, v_mm=0, angle_deg=0) -> np.ndarray:
        frame = self.frame(s_mm)
        angle = math.radians(_finite(angle_deg, "angle"))
        normal = math.cos(angle) * frame.normal + math.sin(angle) * frame.binormal
        binormal = -math.sin(angle) * frame.normal + math.cos(angle) * frame.binormal
        return frame.center + _finite(u_mm, "u") * normal + _finite(v_mm, "v") * binormal

    def longitudinal(self, angle_deg=0, offset_mm=0) -> np.ndarray:
        """Rows follow distances; columns are rotated normal, offset is rotated v."""
        angle = math.radians(_finite(angle_deg, "angle"))
        offset = _finite(offset_mm, "offset")
        width = self.volume.shape[2]
        u = (np.arange(width) - (width - 1) / 2) * self.spacing_mm
        cos, sin = math.cos(angle), math.sin(angle)
        if self.native is not None:
            normal = cos * self.normals + sin * self.binormals
            binormal = -sin * self.normals + cos * self.binormals
            points = self.centers[:, None, :] + u[None, :, None] * normal[:, None, :] + offset * binormal[:, None, :]
            return self.native.sample(points)
        # Explicit fallback for synthetic/unit use; production load_case always
        # supplies native, so a rotated square never gains false black corners.
        x = (u * cos - offset * sin) / self.spacing_mm + (width - 1) / 2
        y = (u * sin + offset * cos) / self.spacing_mm + (self.volume.shape[1] - 1) / 2
        rows = np.broadcast_to(np.arange(len(self.distances))[:, None], (len(self.distances), width))
        coords = np.stack((rows, np.broadcast_to(y, rows.shape), np.broadcast_to(x, rows.shape)))
        return map_coordinates(self.volume, coords, order=1, mode="constant", cval=-1024, output=np.float32, prefilter=False)

    def cross_section(self, s_mm, angle_deg=0) -> np.ndarray:
        height, width = self.volume.shape[1:]
        u = (np.arange(width) - (width - 1) / 2) * self.spacing_mm
        v = (np.arange(height) - (height - 1) / 2) * self.spacing_mm
        vv, uu = np.meshgrid(v, u, indexing="ij")
        angle = math.radians(_finite(angle_deg, "angle"))
        cos, sin = math.cos(angle), math.sin(angle)
        if self.native is not None:
            frame = self.frame(s_mm)
            normal = cos * frame.normal + sin * frame.binormal
            binormal = -sin * frame.normal + cos * frame.binormal
            points = frame.center + uu[:, :, None] * normal + vv[:, :, None] * binormal
            return self.native.sample(points)
        x = (uu * cos - vv * sin) / self.spacing_mm + (width - 1) / 2
        y = (uu * sin + vv * cos) / self.spacing_mm + (height - 1) / 2
        coords = np.stack((np.full_like(x, self.row_at(s_mm)), y, x))
        return map_coordinates(self.volume, coords, order=1, mode="constant", cval=-1024, output=np.float32, prefilter=False)

    def nearest_s(self, point_lps, max_distance_mm=10, *, hint_s_mm=None, search_radius_mm=None):
        """Nearest polyline projection within THIS path and an optional local s window.

        This is not a global-tree inverse. With folded paths, pass a current s
        hint/window; ambiguity between distant equally near portions returns None.
        """
        point = np.asarray(point_lps, dtype=float)
        if point.shape != (3,) or not np.isfinite(point).all():
            raise GeometryError("Expected one finite LPS point")
        max_distance = _finite(max_distance_mm, "max distance")
        if max_distance < 0:
            raise GeometryError("max distance must be nonnegative")
        a, b = self.centers[:-1], self.centers[1:]
        delta = b - a
        squared_length = np.einsum("ij,ij->i", delta, delta)
        fraction = np.clip(np.einsum("ij,ij->i", point - a, delta) / np.maximum(squared_length, 1e-20), 0, 1)
        projection = a + fraction[:, None] * delta
        distance = np.linalg.norm(projection - point, axis=1)
        positions = self.distances[:-1] + fraction * np.diff(self.distances)
        eligible = (distance <= max_distance) & (squared_length > 1e-20)
        if hint_s_mm is not None:
            hint = self.clamp_s(hint_s_mm)
            radius = 10.0 if search_radius_mm is None else _finite(search_radius_mm, "search radius")
            if radius < 0:
                raise GeometryError("search radius must be nonnegative")
            eligible &= np.abs(positions - hint) <= radius
        candidates = np.flatnonzero(eligible)
        if not len(candidates):
            return None
        best = candidates[np.argmin(distance[candidates])]
        # Do not invent a unique inverse for crossing/folded parts of one path.
        ambiguous = eligible & (distance <= distance[best] + 1e-5) & (np.abs(positions - positions[best]) > 1.0)
        if np.any(ambiguous):
            return None
        return float(positions[best])


@dataclass
class CaseImages:
    case_id: str
    manifest: dict
    native: NativeVolume
    paths: dict[str, CPRPath]
    canonical_lm: dict


def load_case(case_manifest_path, *, verify_checksums=True) -> CaseImages:
    """Load one portable, hash-bound case. No source paths or metadata are edited."""
    manifest_path = Path(case_manifest_path).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if manifest.get("schema_version") != "imagecasx-case-1.0":
        raise ValueError("Unsupported case manifest schema")
    if not manifest.get("case_id") or not manifest.get("geometry_id"):
        raise ValueError("Case/geometry identity is required")
    if manifest.get("official_split") not in {"train", "validation", "test"}:
        raise ValueError("Missing or unrecognized official patient split")
    base = manifest_path.parent
    files = {}
    for entry in manifest.get("files", []):
        resolved = safe_relative_path(base, entry["path"])
        if resolved in files:
            raise ValueError("Duplicate manifest file")
        if not resolved.is_file() or resolved.stat().st_size != int(entry["bytes"]):
            raise ValueError(f"Missing asset or byte-count mismatch: {entry['path']}")
        digest = entry.get("sha256", "")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Invalid file SHA-256")
        if verify_checksums:
            sha = hashlib.sha256()
            with resolved.open("rb") as stream:
                for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                    sha.update(chunk)
            if sha.hexdigest() != digest:
                raise ValueError(f"Asset SHA-256 mismatch: {entry['path']}")
        files[resolved] = entry
    def asset(relative):
        resolved = safe_relative_path(base, relative)
        if resolved not in files:
            raise ValueError(f"Required asset is absent from hash manifest: {relative}")
        return resolved
    native_path = asset(manifest["native"])
    records = manifest.get("paths", {})
    if set(records) != {"LAD", "LCX", "RCA"}:
        raise ValueError("Case must identify LAD, LCX and RCA")
    path_assets = {name: {key: asset(record[key]) for key in ("image", "frames", "qa", "path")}
                   for name, record in records.items()}
    canonical = json.loads(asset(manifest["canonical_lm"]).read_text(encoding="utf-8-sig"))
    if canonical.get("schema_version") != "imagecasx-canonical-lm-1.0" or canonical.get("status") not in {"verified", "not_present", "ambiguous"}:
        raise GeometryError("Unsupported canonical LM sidecar")
    native = NativeVolume.read(native_path)
    paths = {name: CPRPath.read(files_["image"], files_["frames"], files_["qa"], native, name, files_["path"])
             for name, files_ in path_assets.items()}
    if canonical.get("status") == "verified":
        end = _finite(canonical.get("verified_end_mm"), "LM endpoint")
        if canonical.get("owner") != "LAD" or not 0 < end <= min(paths["LAD"].length_mm, paths["LCX"].length_mm):
            raise GeometryError("Invalid verified LM range or owner")
        sample = np.unique(np.r_[0, end, paths["LAD"].distances[paths["LAD"].distances <= end]])
        # Source graph identity is certified by the packager; loader additionally
        # rejects a sidecar that would mirror unequal physical centers.
        if max(np.linalg.norm(paths["LAD"].point(s) - paths["LCX"].point(s)) for s in sample) > 1e-5:
            raise GeometryError("Verified LM does not share equal native centers")
    return CaseImages(str(manifest["case_id"]), manifest, native, paths, canonical)


def window_uint8(hu, width, level) -> np.ndarray:
    """Continuous linear research display window; leaves source HU unchanged."""
    width = max(_finite(width, "window width"), 1.0)
    level = _finite(level, "window level")
    data = np.asarray(hu, dtype=np.float32)
    result = (data - (level - width / 2)) * (255.0 / width)
    result = np.nan_to_num(result, nan=0.0, posinf=255.0, neginf=0.0)
    return np.ascontiguousarray(np.clip(result, 0, 255).astype(np.uint8))
