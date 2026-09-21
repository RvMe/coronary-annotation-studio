"""Explicit, lossless legacy import. Never connect to the predecessor database."""
from __future__ import annotations
from copy import deepcopy
import base64
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from .legacy_imaging import load_case as load_legacy_case
from .legacy_storage import AnnotationStore as LegacyStore
from .imaging import safe_relative_path
from .domain import SCHEMA_VERSION
from .package import file_record, source_for_case, validate_package
from .projects import validate_id


def import_package(source, destination, project_id):
    validate_id(project_id)
    source, destination = Path(source).resolve(), Path(destination).resolve()
    manifest = source / "manifest.json" if source.is_dir() else source
    old = json.loads(manifest.read_text(encoding="utf-8-sig"))
    if old.get("schema_version") != "imagecasx-package-1.0":
        raise ValueError("Expected legacy imagecasx-package-1.0 manifest")
    if destination.exists() or destination.is_relative_to(manifest.parent):
        raise ValueError("Choose a new destination outside the source package")
    # Validate every case before creating a destination. Original files are read-only.
    cases = []
    seen = set()
    for entry in old["cases"]:
        path = safe_relative_path(manifest.parent, entry["case_manifest"])
        case = load_legacy_case(path)
        validate_id(case.case_id, "case_id")
        if case.case_id != str(entry["case_id"]) or case.case_id in seen:
            raise ValueError("Legacy case identity mismatch or duplicate")
        seen.add(case.case_id)
        cases.append((path, case))
    if not cases:
        raise ValueError("Legacy package contains no cases")
    destination.mkdir(parents=True)
    entries = []
    for source_path, case in cases:
        root = destination / case.case_id
        root.mkdir()
        m = case.manifest
        for record in m["files"]:
            old_path = safe_relative_path(source_path.parent, record["path"])
            new_path = safe_relative_path(root, record["path"])
            new_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(old_path, new_path)
        shutil.copyfile(source_path, root / "legacy-case.json")
        canonical = deepcopy(case.canonical_lm)
        if canonical.get("status") == "verified":
            canonical.update(reference_paths=["LAD","LCX"], relationship_id="legacy-explicit-LM",
                             evidence={"original_sidecar": m["canonical_lm"], "verification": "Legacy declared identity and exact centerline comparison"})
        paths = {pid: {"image": record["image"], "display_name": pid,
                      "mapping": {"type": "frames", "file": record["frames"], "axis_order": "s,v,u",
                                  "inplane_spacing_mm": [case.paths[pid].spacing_mm]*2}}
                 for pid, record in m["paths"].items()}
        new = {"schema_version": "cas-case-1.0", "project_id": project_id, "case_id": case.case_id,
               "geometry_id": m["geometry_id"], "units": "mm", "coordinate_system": "LPS", "intensity_units": "HU",
               "native": m["native"], "paths": paths, "annotation_scope": list(paths), "canonical_lm": canonical,
               "metadata": {"dataset": "ImageCAS-X", "split": m["official_split"]},
               "legacy_provenance": {"original_schema": m["schema_version"], "original_manifest": "legacy-case.json",
                                      "original_manifest_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest()},
               "files": [file_record(p, root) for p in sorted(root.rglob("*")) if p.is_file()]}
        (root / "case.json").write_text(json.dumps(new, indent=2, ensure_ascii=False), encoding="utf-8")
        entries.append({"case_id":case.case_id,"case_manifest":f"{case.case_id}/case.json"})
    shutil.copyfile(manifest, destination / "legacy-package.json")
    (destination / "manifest.json").write_text(json.dumps({"schema_version":"cas-package-1.0","project_id":project_id,
        "name": project_id, "cases": entries, "legacy_provenance":{"original_manifest":"legacy-package.json",
        "sha256":hashlib.sha256(manifest.read_bytes()).hexdigest()}},indent=2),encoding="utf-8")
    return validate_package(destination)


def import_annotations(export_path, store, case):
    """Validate original checksums/full audit, retain original evidence in import event."""
    path = Path(export_path).resolve()
    with tempfile.TemporaryDirectory(prefix="cas-legacy-validation-") as directory:
        with LegacyStore(Path(directory)/"validation.sqlite") as validator:
            validator.import_case(path)
    original = json.loads(path.read_text(encoding="utf-8"))
    if original["case_id"] != case.case_id or original["source"].get("geometry_id") != case.manifest["geometry_id"]:
        raise ValueError("Legacy labels do not belong to this case geometry")
    if "legacy_provenance" not in case.manifest:
        raise ValueError("Import legacy labels into an explicitly converted legacy package")
    source = source_for_case(case)
    current = store.load(original["case_id"], original["reader_id"], source)
    if current["revision"] or current["annotations"] or current["markers"]:
        raise ValueError("Import conflict: this reader already has records; use a separate project")
    converted = deepcopy(original)
    converted["schema_version"] = SCHEMA_VERSION
    converted["source"] = source
    converted["revision"] = current["revision"]
    # Keep original completion metadata and all diagnosis/provenance fields.
    details = {"original_schema": original["schema_version"], "original_snapshot": original,
               "original_export": {name: (path.parent/name).read_text(encoding="utf-8-sig")
                                   for name in ("annotations.json","annotations.csv","audit.jsonl","checksums.json")},
               "original_files": [file_record(path.parent/name,path.parent) for name in
                                  ("annotations.json","annotations.csv","audit.jsonl","checksums.json")],
               "original_files_base64": {name:base64.b64encode((path.parent/name).read_bytes()).decode("ascii") for name in
                                         ("annotations.json","annotations.csv","audit.jsonl","checksums.json")}}
    return store.commit(converted,"legacy_import",details)
