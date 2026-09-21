"""Assemble a new, non-overwriting Windows onedir test release.

Run with the exact Python environment used for PyInstaller. This script never
rebuilds the app or edits its input directory. --self-test uses temporary fixtures;
--audit-only is read-only. A normal assembly fetches versioned Qt source archives.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata as metadata
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import urllib.request
import uuid
import zipfile

RUNTIME = {"pyside6", "pyside6-essentials", "shiboken6", "simpleitk", "numpy", "scipy", "pynrrd", "typing-extensions"}
BUILD = {"pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "pefile", "pywin32-ctypes", "packaging", "setuptools", "pip"}
QT_ALLOWED = {"qt6core.dll", "qt6gui.dll", "qt6widgets.dll", "qt6network.dll", "qt6opengl.dll"}
SOURCE_SUFFIXES = {".py", ".md", ".txt", ".ps1", ".spec", ".json", ".toml", ".ini", ".cfg"}
SKIP_DIRS = {"__pycache__", ".venv", "venv", "build", "dist", "node_modules", ".pytest_cache", ".git"}
DATA_SUFFIXES = {".nii", ".nrrd", ".dcm", ".npz", ".npy", ".sqlite", ".sqlite3", ".db", ".pt", ".pth", ".onnx"}


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def normalize_name(value):
    return re.sub(r"[-_.]+", "-", value).lower()


def relative_safe(value):
    path = PurePosixPath(str(value).replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or re.match(r"^[A-Za-z]:", str(path)):
        raise ValueError(f"Unsafe archive-relative path: {value}")
    return path


def file_record(path, root):
    return {"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size, "sha256": digest(path)}


def copy_checked(source, destination):
    source, destination = Path(source), Path(destination)
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"Only regular source files are accepted: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(destination)
    before = source.stat()
    original_hash = digest(source)
    shutil.copyfile(source, destination)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or digest(destination) != original_hash:
        raise ValueError(f"Source changed during assembly or copy mismatch: {source}")
    return original_hash


def is_license(path):
    name = Path(path).name.lower()
    return name.startswith(("license", "copying", "copyright", "notice", "authors")) or name == "qt_attribution.json"


def audit_qt_binaries(app_dir):
    app_dir = Path(app_dir)
    items = [file_record(path, app_dir) for path in sorted(app_dir.rglob("*"))
             if path.is_file() and path.name.lower().startswith("qt6") and path.suffix.lower() == ".dll"]
    names = {Path(item["path"]).name.lower() for item in items}
    unexpected = sorted(names - QT_ALLOWED)
    if unexpected:
        raise ValueError("Unapproved Qt modules in onedir build; review/filter build first: " + ", ".join(unexpected))
    if not {"qt6core.dll", "qt6gui.dll", "qt6widgets.dll"}.issubset(names):
        raise ValueError("Missing required QtCore/Gui/Widgets dynamic libraries")
    return {"approved_modules": sorted(QT_ALLOWED), "binaries": items,
            "plugins": [file_record(p, app_dir) for p in sorted(app_dir.rglob("*.dll")) if "plugins" in p.parts]}


def environment_inventory():
    from PySide6.QtCore import qVersion
    required = {"PySide6_Essentials", "shiboken6", "SimpleITK", "numpy", "scipy", "pynrrd", "PyInstaller"}
    for name in required:
        metadata.distribution(name)
    distributions = []
    for dist in sorted(metadata.distributions(), key=lambda item: item.metadata["Name"].lower()):
        name = dist.metadata["Name"]
        normalized = normalize_name(name)
        distributions.append({"name": name, "version": dist.version,
            "role": "runtime_dependency" if normalized in RUNTIME else "build_tool" if normalized in BUILD else "installed_environment_only_not_claimed_bundled",
            "license_expression": dist.metadata.get("License-Expression") or dist.metadata.get("License"),
            "project_urls": dist.metadata.get_all("Project-URL") or [], "requires": dist.requires or []})
    return {"schema_version": "cas-software-dependencies-1.0", "python_version": sys.version,
            "python_implementation": sys.implementation.name, "architecture": "64bit" if sys.maxsize > 2**32 else "32bit",
            "qt_version": qVersion(), "pyside_version": metadata.version("PySide6_Essentials"),
            "distributions": distributions}


def collect_wheel_licenses(payload, inventory):
    records = []
    for item in inventory["distributions"]:
        dist = metadata.distribution(item["name"])
        destination = payload / "third_party_licenses" / f"{normalize_name(item['name'])}-{item['version']}"
        destination.mkdir(parents=True)
        original_metadata = dist.read_text("METADATA")
        if original_metadata:
            (destination / "METADATA.txt").write_text(original_metadata, encoding="utf-8")
        found = []
        for entry in dist.files or []:
            if not is_license(str(entry)):
                continue
            source = Path(dist.locate_file(entry))
            if source.is_file():
                relative = relative_safe(entry)
                # Preserve bytes and original package path, but avoid nested
                # dist-info/vendor paths overflowing an ordinary Windows unzip.
                source_hash = digest(source)
                target = destination / (source_hash[:20] + ".txt")
                if target.exists():
                    if digest(target) != source_hash:
                        raise ValueError("Wheel license content-addressed filename collision")
                else:
                    copy_checked(source, target)
                found.append({"original_distribution_path": str(relative), **file_record(target, payload)})
        records.append({"distribution": item["name"], "version": item["version"], "role": item["role"], "files": found})
    python_license = next((Path(sys.base_prefix) / name for name in ("LICENSE_PYTHON.txt", "LICENSE.txt", "LICENSE")
                           if (Path(sys.base_prefix) / name).is_file()), None)
    if python_license is None:
        raise FileNotFoundError("Actual base Python license is missing; do not substitute a guessed version")
    target = payload / "third_party_licenses/Python" / python_license.name
    copy_checked(python_license, target)
    records.append({"distribution": "Python interpreter", "version": sys.version, "role": "runtime_dependency", "files": [file_record(target, payload)]})
    return records


def bind_qt_to_build_environment(app_dir, qt_audit):
    """Fail if audited Qt binaries did not come from the active wheel environment."""
    wheel = metadata.distribution("PySide6_Essentials")
    candidates = {}
    for entry in wheel.files or []:
        if Path(str(entry)).name.lower() in QT_ALLOWED:
            source = Path(wheel.locate_file(entry))
            if source.is_file():
                candidates[source.name.lower()] = digest(source)
    for binary in qt_audit["binaries"]:
        name = Path(binary["path"]).name.lower()
        if candidates.get(name) != binary["sha256"]:
            raise ValueError(f"App Qt DLL differs from active build environment: {name}")
    python_dlls = list(app_dir.rglob(f"python{sys.version_info.major}{sys.version_info.minor}.dll"))
    reference = Path(sys.base_prefix) / f"python{sys.version_info.major}{sys.version_info.minor}.dll"
    if len(python_dlls) != 1 or not reference.is_file() or digest(python_dlls[0]) != digest(reference):
        raise ValueError("App Python DLL does not match active build interpreter")


def collect_sqlite_public_domain(info, matching, payload):
    """Recover SQLite's declaration from the exact hash-bound conda artifact.

    SQLite uses a public-domain blessing rather than a conventional LICENSE file.
    Do not apply this exception to other packages or accept recipe-script licenses.
    """
    if info.get("name") != "libsqlite" or info.get("license") != "blessing":
        return None
    source_root = Path(info.get("link", {}).get("source", ""))
    package = source_root.parent / (source_root.name + ".conda")
    expected_package_hash = info.get("sha256", "")
    if (not package.is_file() or not re.fullmatch(r"[0-9a-f]{64}", expected_package_hash)
            or digest(package) != expected_package_hash):
        raise ValueError("SQLite public-domain fallback requires the exact SHA-verified conda artifact")
    paths_file, about_file = source_root / "info/paths.json", source_root / "info/about.json"
    paths = {p["_path"]: p for p in json.loads(paths_file.read_text(encoding="utf-8"))["paths"]}
    about = json.loads(about_file.read_text(encoding="utf-8"))
    if (about.get("license") != "blessing" or about.get("license_url") not in
            {"http://www.sqlite.org/copyright.html", "https://www.sqlite.org/copyright.html"}):
        raise ValueError("SQLite artifact lacks its authoritative public-domain attribution")
    header_relative, dll_relative = "Library/include/sqlite3.h", "Library/bin/sqlite3.dll"
    header, dll = source_root / header_relative, source_root / dll_relative
    for relative, path in ((header_relative, header), (dll_relative, dll)):
        expected = paths.get(relative, {}).get("sha256")
        if not expected or not path.is_file() or digest(path) != expected:
            raise ValueError(f"SQLite artifact member SHA mismatch: {relative}")
    if not any(Path(m["path"]).name.lower() == "sqlite3.dll" and m["sha256"] == digest(dll) for m in matching):
        raise ValueError("SQLite declaration is not bound to the shipped sqlite3.dll")
    contents = header.read_text(encoding="utf-8")
    version = re.search(r'^#define SQLITE_VERSION\s+"([^"]+)"', contents, re.MULTILINE)
    source_id = re.search(r'^#define SQLITE_SOURCE_ID\s+"([^"]+)"', contents, re.MULTILINE)
    declaration = re.match(r"/\*.*?\*/", contents, re.DOTALL)
    if (not version or version.group(1) != info["version"] or not source_id or not declaration
            or "The author disclaims copyright to this source code." not in declaration.group()
            or "May you do good and not evil." not in declaration.group()):
        raise ValueError("Version-bound SQLite public-domain declaration not found")
    provenance = {"kind": "public_domain_declaration_from_exact_package_header",
                  "package_sha256_verified": expected_package_hash, "package_url": info.get("url"),
                  "copyright_url": "https://www.sqlite.org/copyright.html",
                  "sqlite_version": version.group(1), "sqlite_source_id": source_id.group(1),
                  "header": file_record(header, source_root), "dll": file_record(dll, source_root),
                  "paths_manifest": file_record(paths_file, source_root)}
    if payload is None:
        return [file_record(header, source_root)], provenance
    destination = payload / "third_party_licenses/conda-runtime" / f"{info['name']}-{info['version']}"
    copy_checked(header, destination / "sqlite3.h")
    notice = destination / "SQLITE_PUBLIC_DOMAIN.txt"
    notice.write_text("SQLite public-domain declaration from the exact shipped dependency package.\n"
                      "The following is the original opening comment of the verified sqlite3.h.\n"
                      "It is not the license of this annotation application or of conda recipe scripts.\n\n"
                      + declaration.group() + "\n\nVerification provenance:\n"
                      + json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return [file_record(destination / "sqlite3.h", payload), file_record(notice, payload)], provenance


def collect_conda_runtime_licenses(app_dir, payload):
    """Identify shipped base-runtime DLLs by exact hash, not by name alone."""
    app_files = {}
    for path in app_dir.rglob("*.dll"):
        app_files.setdefault(path.name.lower(), []).append(path)
    records = []
    conda_meta = Path(sys.base_prefix) / "conda-meta"
    if not conda_meta.is_dir():
        return {"source": "not_conda", "packages": [], "manual_review": "Review non-wheel system runtimes independently"}
    for metadata_file in sorted(conda_meta.glob("*.json")):
        info = json.loads(metadata_file.read_text(encoding="utf-8"))
        matching = []
        for relative in info.get("files", []):
            name = PurePosixPath(relative.replace("\\", "/")).name.lower()
            if name not in app_files or not name.endswith(".dll"):
                continue
            source = Path(sys.base_prefix) / relative
            if not source.is_file():
                continue
            expected = digest(source)
            for path in app_files[name]:
                if digest(path) == expected:
                    matching.append({"path": path.relative_to(app_dir).as_posix(), "sha256": expected})
        if not matching:
            continue
        license_root = Path(info.get("link", {}).get("source", "")) / "info/licenses"
        copies = []
        if license_root.is_dir():
            for source in sorted(license_root.rglob("*")):
                if source.is_file():
                    if payload is None:
                        copies.append(file_record(source, license_root))
                    else:
                        target = payload / "third_party_licenses/conda-runtime" / f"{info['name']}-{info['version']}" / source.relative_to(license_root)
                        copy_checked(source, target)
                        copies.append(file_record(target, payload))
        declaration_provenance = None
        if not copies:
            sqlite_evidence = collect_sqlite_public_domain(info, matching, payload)
            if sqlite_evidence is not None:
                copies, declaration_provenance = sqlite_evidence
        records.append({"name": info["name"], "version": info["version"], "build": info.get("build"),
                        "license": info.get("license"), "package_url": info.get("url"), "package_sha256": info.get("sha256"),
                        "matched_binaries": matching, "license_files": copies,
                        "public_domain_declaration_provenance": declaration_provenance,
                        "license_text_status": declaration_provenance["kind"] if declaration_provenance else
                        (("available_on_build_host" if payload is None else "collected") if copies else "manual_review_missing_local_text")})
    matched = {entry["path"] for record in records for entry in record["matched_binaries"]}
    wheel_hashes = {}
    for dist in metadata.distributions():
        for entry in dist.files or []:
            name = Path(str(entry)).name.lower()
            if name not in app_files or not name.endswith(".dll"):
                continue
            source = Path(dist.locate_file(entry))
            if source.is_file():
                wheel_hashes.setdefault(name, set()).add(digest(source))
    unmatched = []
    for name, binaries in app_files.items():
        for binary in binaries:
            relative = binary.relative_to(app_dir).as_posix()
            if relative not in matched and digest(binary) not in wheel_hashes.get(name, set()):
                unmatched.append(file_record(binary, app_dir))
    return {"source": "actual_base_python_conda_records_with_binary_hash_match", "packages": records,
            "unmatched_nonwheel_dlls_require_review": unmatched,
            "missing_local_license_text_packages": [p["name"] for p in records if not p["license_files"]]}


def download_official_source(url, destination):
    request = urllib.request.Request(url + ".sha256", headers={"User-Agent": "CoronaryAnnotationStudio-Release-Assembler/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        official = response.read().decode("utf-8").strip()
    hashes = re.findall(r"\b[0-9a-fA-F]{64}\b", official)
    if len(hashes) != 1:
        raise ValueError(f"Could not identify unique official SHA256 for {url}")
    expected = hashes[0].lower()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if digest(destination) != expected:
            raise ValueError("Existing source cache hash mismatch")
    else:
        partial = destination.with_name(destination.name + ".partial")
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "CoronaryAnnotationStudio-Release-Assembler/1.0"}), timeout=60) as response, partial.open("xb") as output:
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                output.write(chunk)
        if digest(partial) != expected:
            raise ValueError(f"Official source SHA mismatch: {url}")
        partial.replace(destination)
    return {"url": url, "official_sha256_url": url + ".sha256", "sha256": expected, "bytes": destination.stat().st_size}


def extract_source_license_texts(archive_path, payload):
    """Keep original text with short names; original archive paths remain indexed.

    Long SPDX filenames under a versioned release root exceed Windows MAX_PATH.
    The complete original source archive is separately shipped unchanged.
    """
    records = []
    with tarfile.open(archive_path, "r:xz") as archive:
        for member in archive:
            relative = relative_safe(member.name)
            if not member.isfile() or not ("LICENSES" in relative.parts or is_license(str(relative))):
                continue
            if member.size > 4 * 1024 * 1024:
                raise ValueError("Unexpected oversized license/attribution member")
            with archive.extractfile(member) as stream:
                content = stream.read()
            sha = hashlib.sha256(content).hexdigest()
            destination = payload / "third_party_licenses/qt-source" / (sha[:20] + ".txt")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                if digest(destination) != sha:
                    raise ValueError("License content-addressed filename collision")
            else:
                destination.write_bytes(content)
            records.append({"archive_member": str(relative), **file_record(destination, payload)})
    return records


def collect_qt_sources(payload, inventory, cache):
    qt, pyside = inventory["qt_version"], inventory["pyside_version"]
    qt_minor = ".".join(qt.split(".")[:2])
    urls = [f"https://download.qt.io/archive/qt/{qt_minor}/{qt}/submodules/qtbase-everywhere-src-{qt}.tar.xz",
            f"https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-{pyside}-src/pyside-setup-everywhere-src-{pyside}.tar.xz"]
    records = []
    for url in urls:
        print("QT_SOURCE_FETCH " + url, flush=True)
        cached = cache / url.rsplit("/", 1)[1]
        record = download_official_source(url, cached)
        target = payload / "third_party_sources" / cached.name
        copy_checked(cached, target)
        record["path"] = target.relative_to(payload).as_posix()
        license_records = extract_source_license_texts(cached, payload)
        record["license_and_attribution_files"] = len(license_records)
        record["license_and_attribution_index"] = license_records
        records.append(record)
        print(f"QT_SOURCE_VERIFIED {cached.name} licenses={len(license_records)}", flush=True)
    original_names = {PurePosixPath(item["archive_member"]).name for record in records
                      for item in record["license_and_attribution_index"]}
    for name in ("LGPL-3.0-only.txt", "GPL-3.0-only.txt"):
        if name not in original_names:
            raise ValueError(f"Missing required Qt license text: {name}")
    return records
