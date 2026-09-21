"""Project identities isolate local databases, including offline sessions."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path
from .platform_support import user_data_dir
from .imaging import safe_relative_path


def validate_id(value, field="project_id"):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", value):
        raise ValueError(f"{field}: expected 1–96 portable letters, digits, dots, underscores or hyphens")
    return value


def database_for_project(project_id):
    validate_id(project_id)
    # Hash avoids case-insensitive filesystem collisions and reserved filenames.
    key = hashlib.sha256(project_id.encode("utf-8")).hexdigest()
    return user_data_dir() / "projects" / key / "annotations.sqlite"


def project_for_package(path):
    path = Path(path).resolve()
    manifest = path / "manifest.json" if path.is_dir() else path
    if manifest.name != "manifest.json":
        raise ValueError("Select a package directory containing manifest.json")
    data = json.loads(manifest.read_text(encoding="utf-8-sig"))
    if data.get("schema_version") != "cas-package-1.0":
        raise ValueError("Unsupported package schema. Use explicit legacy import for old packages.")
    pid = validate_id(data.get("project_id"))
    entries = data.get("cases")
    if not isinstance(entries, list) or not entries:
        raise ValueError("cases: expected a nonempty list")
    seen = set()
    for entry in entries:
        cid = validate_id(entry.get("case_id"), "case_id")
        if cid in seen:
            raise ValueError(f"cases/{cid}: duplicate case identity")
        seen.add(cid)
        case_path = safe_relative_path(manifest.parent, entry["case_manifest"])
        case = json.loads(case_path.read_text(encoding="utf-8-sig"))
        if case.get("project_id") != pid or case.get("case_id") != cid:
            raise ValueError(f"cases/{cid}: project or case identity differs from package manifest")
    return {**data, "manifest_path": str(manifest), "root": str(manifest.parent),
            "database": str(database_for_project(pid))}


def bind_database(path, project_id):
    """Immutable identity next to the local database; never silently rebind."""
    validate_id(project_id)
    target = Path(path).resolve().parent / "project.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    content = {"schema_version": "cas-local-project-1.0", "project_id": project_id}
    try:
        with target.open("x", encoding="utf-8") as stream:
            json.dump(content, stream, indent=2)
    except FileExistsError:
        if json.loads(target.read_text(encoding="utf-8")) != content:
            raise ValueError("Database already belongs to a different project")
    return Path(path)
