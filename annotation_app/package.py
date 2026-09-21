"""Strict, path-specific validation for neutral, hash-bound CPR data packages."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import numpy as np
import SimpleITK as sitk
from .imaging import safe_relative_path, NativeVolume, CPRPath, CaseImages, GeometryError, _read_image
from .coordinate_field import CoordinateFieldPath
from .projects import validate_id, project_for_package


def file_record(path, root):
    path, root = Path(path), Path(root)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8*1024*1024), b""):
            digest.update(chunk)
    return {"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def checked_assets(manifest, base, verify=True):
    records = manifest.get("files")
    if not isinstance(records, list) or not records:
        raise GeometryError("files: expected nonempty checksum list")
    found = {}
    for item in records:
        name = item["path"]
        path = safe_relative_path(base, name)
        if path in found:
            raise GeometryError(f"files/{name}: duplicate file")
        if not path.is_file() or type(item.get("bytes")) is not int or item["bytes"] != path.stat().st_size:
            raise GeometryError(f"files/{name}: missing file or byte-count mismatch")
        digest = item.get("sha256", "")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise GeometryError(f"files/{name}: invalid SHA-256")
        if verify and file_record(path, base)["sha256"] != digest:
            raise GeometryError(f"files/{name}: SHA-256 mismatch")
        found[path] = item
    def asset(relative):
        path = safe_relative_path(base, relative)
        if path not in found:
            raise GeometryError(f"files/{relative}: required asset absent from checksum list")
        return path
    return asset


def load_neutral_case(path, *, verify_checksums=True):
    path = Path(path).resolve()
    m = json.loads(path.read_text(encoding="utf-8-sig"))
    if m.get("schema_version") != "cas-case-1.0":
        raise GeometryError("schema_version: expected cas-case-1.0; old formats require explicit import")
    for key in ("project_id", "case_id", "geometry_id"):
        validate_id(m.get(key), key)
    for key, expected in (("units", "mm"), ("coordinate_system", "LPS"), ("intensity_units", "HU")):
        if m.get(key) != expected:
            raise GeometryError(f"{key}: must explicitly declare {expected}")
    asset = checked_assets(m, path.parent, verify_checksums)
    def image(relative):
        target = asset(relative)
        if not str(target).lower().endswith((".nii", ".nii.gz", ".nrrd")):
            raise GeometryError(f"{relative}: only NIfTI and NRRD are supported")
        im = _read_image(target)
        if im.GetDimension()!=3 or im.GetNumberOfComponentsPerPixel()!=1 or min(im.GetSize())<2:
            raise GeometryError(f"{relative}: expected scalar 3D image with each dimension >=2")
        direction = np.asarray(im.GetDirection()).reshape(3,3)
        if not np.allclose(direction.T@direction, np.eye(3), atol=1e-5) or not np.isfinite(im.GetSpacing()).all() or min(im.GetSpacing())<=0:
            raise GeometryError(f"{relative}: invalid direction or spacing")
        return im
    native = NativeVolume(image(m["native"]))
    if not np.isfinite(native.array_zyx).all():
        raise GeometryError("native: nonfinite HU values")
    records = m.get("paths")
    if not isinstance(records, dict) or not records:
        raise GeometryError("paths: expected at least one supplied coronary path")
    scope = m.get("annotation_scope")
    if not isinstance(scope, list) or not scope or len(set(scope)) != len(scope) or not set(scope).issubset(records):
        raise GeometryError("annotation_scope: must explicitly list unique supplied path IDs")
    paths = {}
    for pid, record in records.items():
        try:
            validate_id(pid, "path_id")
            cpr = image(record["image"])
            volume = sitk.GetArrayFromImage(cpr)
            if not np.isfinite(volume).all():
                raise GeometryError("image: nonfinite HU values")
            mapping = record["mapping"]
            if mapping.get("axis_order") != "s,v,u":
                raise GeometryError("mapping/axis_order: expected s,v,u (array Z,Y,X)")
            spacing = np.asarray(mapping["inplane_spacing_mm"], dtype=float)
            if spacing.shape != (2,) or not np.isfinite(spacing).all() or np.any(spacing<=0):
                raise GeometryError("mapping/inplane_spacing_mm: expected [u,v] positive millimetres")
            if not np.allclose(cpr.GetSpacing()[:2], spacing, atol=1e-6, rtol=0):
                raise GeometryError("mapping/inplane_spacing_mm: differs from CPR image grid")
            with np.load(asset(mapping["file"]), allow_pickle=False) as archive:
                fields = {key: archive[key] for key in archive.files}
            distances = np.asarray(fields["distance_mm"], float)
            if distances.shape != (len(volume),) or not np.isfinite(distances).all() or abs(distances[0])>1e-8 or np.any(np.diff(distances)<=0):
                raise GeometryError("mapping/distance_mm: expected strictly increasing samples starting at zero")
            segments = fields.get("segment_id", np.zeros(len(volume), dtype=np.int32))
            if mapping["type"] == "frames":
                if not np.isclose(spacing[0], spacing[1], rtol=0, atol=1e-8):
                    raise GeometryError("mapping/frames: equal in-plane sampling required; use coordinate_field for unequal sampling")
                paths[pid] = CPRPath(volume, distances, fields["centers"], fields["normals"], fields["binormals"], fields["tangents"], float(distances[-1]), float(spacing[0]), segments, native, pid)
            elif mapping["type"] == "coordinate_field":
                paths[pid] = CoordinateFieldPath(volume, distances, fields["native_lps"], spacing, native, pid, segments)
            else:
                raise GeometryError("mapping/type: expected frames or coordinate_field")
            indices = native.world_to_index(paths[pid].centers)
            if np.any(indices < -.5) or np.any(indices > np.array(native.image.GetSize())-.5):
                raise GeometryError("mapping: path center lies outside native CT")
        except (ValueError, KeyError, TypeError) as exc:
            raise GeometryError(f"paths/{pid}: {exc}") from exc
    canonical = m.get("canonical_lm", {"status": "not_present"})
    if canonical.get("status") not in ("verified", "not_present", "ambiguous"):
        raise GeometryError("canonical_lm/status: unsupported value")
    if canonical["status"] == "verified":
        if canonical.get("owner") != "LAD" or set(canonical.get("reference_paths", [])) != {"LAD", "LCX"} or not {"LAD", "LCX"}.issubset(scope) or not canonical.get("relationship_id") or not canonical.get("evidence"):
            raise GeometryError("canonical_lm: explicit LAD/LCX relationship, scope and evidence required")
        end = float(canonical["verified_end_mm"])
        if not np.isfinite(end) or not 0<end<=min(paths[p].length_mm for p in ("LAD","LCX")):
            raise GeometryError("canonical_lm/verified_end_mm: invalid range")
        samples = np.unique(np.r_[0,end,paths["LAD"].distances,paths["LCX"].distances])
        samples = samples[samples<=end]
        samples = np.unique(np.r_[samples,(samples[:-1]+samples[1:])/2])
        error = max(np.linalg.norm(paths["LAD"].point(s)-paths["LCX"].point(s)) for s in samples)
        if error>1e-5:
            raise GeometryError(f"canonical_lm: geometry disagreement {error:g} mm")
    return CaseImages(m["case_id"], m, native, paths, canonical)


def validate_package(path):
    project = project_for_package(path)
    results = []
    for entry in project["cases"]:
        case = load_neutral_case(safe_relative_path(project["root"], entry["case_manifest"]))
        results.append({"case_id": case.case_id, "paths": list(case.paths), "annotation_scope": case.manifest["annotation_scope"], "geometry_id": case.manifest["geometry_id"]})
    return {"status": "PASS", "project_id": project["project_id"], "cases": results, "meaning": "Format, checksum and geometry validation only; not reader or clinical acceptance."}


def source_for_case(case, case_path=None):
    """Stable provenance shared by GUI, legacy conversion and offline exports."""
    m = case.manifest if hasattr(case, "manifest") else case
    source = {"project_id": m["project_id"], "geometry_id": m["geometry_id"],
              "physical_coordinate_system": "LPS", "coordinate_units": "mm",
              "interval_convention": "half-open-start-inclusive-end-exclusive",
              "annotation_scope": m["annotation_scope"]}
    for key in ("dataset", "split"):
        if key in m.get("metadata", {}):
            source[key] = m["metadata"][key]
    return source
