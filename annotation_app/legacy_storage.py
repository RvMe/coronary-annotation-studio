"""Local SQLite snapshots, durable undo/redo ledger, and verified reader exports."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from .legacy_domain import (
    CANONICAL_GROUP_REVIEW_REASON, effective_reader_review_required, empty_state, reader_review_reasons,
    technical_qa_reasons, validate_state,
)

EXPORT_SCHEMA = "imagecasx-export-1.0"
MAX_IMPORT_BYTES = 100 * 1024 * 1024


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", value)[:40] or "id"
    return cleaned + "_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def _atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _identity_content(state: dict) -> dict:
    """Revision and camera state are local; diagnostic contents must be identical."""
    return {key: deepcopy(value) for key, value in state.items() if key not in {"revision", "view_state"}}


def _verify_source(existing: dict, incoming: dict) -> None:
    if existing and incoming != existing:
        raise ValueError("Package provenance mismatch: use the original case geometry/source")


class AnnotationStore:
    """One local database; streams are keyed by (case_id, reader_id)."""

    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.db_path), timeout=15, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        if self.connection.execute("PRAGMA user_version").fetchone()[0] not in (0, 1):
            self.connection.close()
            raise ValueError("Unsupported annotation database version; do not downgrade it")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS streams (
                case_id TEXT NOT NULL, reader_id TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision >= 0),
                state_json TEXT NOT NULL, undo_json TEXT NOT NULL DEFAULT '[]',
                redo_json TEXT NOT NULL DEFAULT '[]',
                PRIMARY KEY(case_id, reader_id)
            );
            CREATE TABLE IF NOT EXISTS audit (
                case_id TEXT NOT NULL, reader_id TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision > 0),
                action TEXT NOT NULL, utc TEXT NOT NULL,
                details_json TEXT NOT NULL, state_json TEXT NOT NULL,
                PRIMARY KEY(case_id, reader_id, revision)
            );
            PRAGMA user_version=1;
        """)
        self._sources = {}

    @contextmanager
    def _transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.connection.execute("COMMIT")
        except BaseException:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise

    def _row(self, case_id, reader_id):
        return self.connection.execute("SELECT * FROM streams WHERE case_id=? AND reader_id=?", (str(case_id), reader_id)).fetchone()

    def list_case_ids(self, reader_id: str) -> list[str]:
        """List this reader's persisted streams, even without mounted images.

        The deterministic binary-text order is independent of imported package
        order. This includes draft-only streams; callers choose which saved
        content to export. A source cached by ``load`` alone is not persisted.
        """
        rows = self.connection.execute(
            "SELECT case_id FROM streams WHERE reader_id=? ORDER BY case_id COLLATE BINARY",
            (reader_id,),
        )
        return [row["case_id"] for row in rows]

    def load(self, case_id, reader_id, source=None) -> dict:
        key = (str(case_id), reader_id)
        row = self._row(*key)
        # Queue/status reads without an explicit source must not erase the binding
        # established by a just-loaded image before its first autosave or commit.
        bound_source = source if source is not None else self._sources.get(key)
        state = json.loads(row["state_json"]) if row else empty_state(*key, bound_source)
        validate_state(state)
        if source is None and bound_source:
            _verify_source(state["source"], bound_source)
            if not state["source"]:
                if state["annotations"]:
                    raise ValueError("Cannot rebind annotations that lack package provenance")
                state["source"] = deepcopy(bound_source)
        if source is not None:
            _verify_source(state["source"], source)
            if not state["source"]:
                if state["annotations"]:
                    raise ValueError("Cannot rebind annotations that lack package provenance")
                state["source"] = deepcopy(source)
            self._sources[key] = deepcopy(source)
        else:
            self._sources[key] = deepcopy(state["source"])
        return state

    def _write(self, state, action, details, undo, redo, revision):
        saved = deepcopy(state)
        saved["revision"] = revision
        validate_state(saved)
        serialised = _json(saved)
        self.connection.execute("""INSERT INTO streams(case_id,reader_id,revision,state_json,undo_json,redo_json)
            VALUES(?,?,?,?,?,?) ON CONFLICT(case_id,reader_id) DO UPDATE SET
            revision=excluded.revision,state_json=excluded.state_json,undo_json=excluded.undo_json,redo_json=excluded.redo_json""",
            (saved["case_id"], saved["reader_id"], revision, serialised, _json(undo), _json(redo)))
        self.connection.execute("INSERT INTO audit VALUES(?,?,?,?,?,?,?)",
            (saved["case_id"], saved["reader_id"], revision, action, _utc(), _json(details or {}), serialised))
        return saved

    def commit(self, state, action, details=None) -> dict:
        validate_state(state)
        _json(state)
        if not isinstance(action, str) or not action.strip():
            raise ValueError("Commit action is required")
        with self._transaction():
            row = self._row(state["case_id"], state["reader_id"])
            prior = json.loads(row["state_json"]) if row else empty_state(state["case_id"], state["reader_id"], state["source"])
            if state["revision"] != prior["revision"]:
                raise ValueError("Stale annotation revision: reload before applying changes")
            _verify_source(prior["source"], state["source"])
            undo = json.loads(row["undo_json"]) if row else []
            undo.append(prior)
            result = self._write(state, action, details, undo, [], prior["revision"] + 1)
        return result

    def _history(self, case_id, reader_id, redo: bool):
        with self._transaction():
            row = self._row(case_id, reader_id)
            if row is None:
                return None
            undo_stack, redo_stack = json.loads(row["undo_json"]), json.loads(row["redo_json"])
            source_stack, destination_stack = (redo_stack, undo_stack) if redo else (undo_stack, redo_stack)
            if not source_stack:
                return None
            current = json.loads(row["state_json"])
            target = source_stack.pop()
            destination_stack.append(current)
            # Metadata source cannot be changed by history operations.
            target["source"] = deepcopy(current["source"])
            return self._write(target, "redo" if redo else "undo", {"restored_snapshot_revision": target["revision"]}, undo_stack, redo_stack, current["revision"] + 1)

    def undo(self, case_id, reader_id):
        return self._history(case_id, reader_id, False)

    def redo(self, case_id, reader_id):
        return self._history(case_id, reader_id, True)

    def save_view(self, case_id, reader_id, view_state):
        if not isinstance(view_state, dict):
            raise ValueError("view_state must be an object")
        _json(view_state)
        with self._transaction():
            row = self._row(case_id, reader_id)
            cached_source = self._sources.get((str(case_id), reader_id))
            state = json.loads(row["state_json"]) if row else empty_state(case_id, reader_id, cached_source)
            if cached_source is not None:
                _verify_source(state["source"], cached_source)
                if not state["source"]:
                    if state["annotations"]:
                        raise ValueError("Cannot rebind annotations that lack package provenance")
                    state["source"] = deepcopy(cached_source)
            state["view_state"] = deepcopy(view_state)
            self.connection.execute("""INSERT INTO streams(case_id,reader_id,revision,state_json,undo_json,redo_json)
                VALUES(?,?,?,?,?,?) ON CONFLICT(case_id,reader_id) DO UPDATE SET state_json=excluded.state_json""",
                (state["case_id"], state["reader_id"], state["revision"], _json(state), "[]", "[]"))

    def _audit_records(self, case_id, reader_id) -> list:
        rows = self.connection.execute("SELECT * FROM audit WHERE case_id=? AND reader_id=? ORDER BY revision", (str(case_id), reader_id))
        return [{"case_id": row["case_id"], "reader_id": row["reader_id"], "revision": row["revision"],
                 "action": row["action"], "utc": row["utc"], "details": json.loads(row["details_json"]),
                 "state": json.loads(row["state_json"])} for row in rows]

    def export_case(self, case_id, reader_id, directory) -> Path:
        """Return annotations.json inside a fresh immutable export directory."""
        # One read transaction prevents snapshot/audit revisions disagreeing under another connection.
        with self._transaction():
            state = self.load(case_id, reader_id)
            audit = self._audit_records(case_id, reader_id)
        target = Path(directory).resolve() / f"case_{_safe_name(str(case_id))}__reader_{_safe_name(reader_id)}__r{state['revision']}_{uuid4().hex[:8]}"
        target.mkdir(parents=True, exist_ok=False)
        csv_buffer = io.StringIO(newline="")
        fields = ["case_id", "reader_id", "revision", "case_status", "case_completion_mode", "case_coverage_percent", "case_coverage_basis", "annotation_id", "label_group_id", "path_id", "canonical_anatomy_id", "anatomical_segment", "s_start_mm", "s_end_mm", "finding_status", "plaque_composition", "stenosis_grade", "confidence", "reason", "reason_codes_json", "s_peak_stenosis_mm", "review_required", "review_status", "effective_reader_review_required", "reader_review_reasons", "technical_qa_reasons", "label_scope", "training_segment_label_eligible", "native_anchors_json", "provenance_json"]
        # These are an explicit completion-time assessment, not a guessed live
        # denominator. Legacy completion without a report stays blank. Editing
        # invalidates completion; never carry an old percentage into in-progress
        # exports even if an external snapshot retained stale metadata.
        completion = state.get("completion", {}) if state.get("case_status") in {"complete", "complete_with_gaps"} else {}
        completion = completion if isinstance(completion, dict) else {}
        coverage = completion.get("coverage", {})
        coverage = coverage if isinstance(coverage, dict) else {}
        writer = csv.DictWriter(csv_buffer, fieldnames=fields)
        writer.writeheader()
        for annotation in state["annotations"]:
            record = {key: annotation.get(key, "") for key in fields}
            record.update({key: state[key] for key in ("case_id", "reader_id", "revision")})
            record.update(case_status=state.get("case_status", "in_progress"),
                          case_completion_mode=completion.get("mode", ""),
                          case_coverage_percent=coverage.get("coverage_percent", ""),
                          case_coverage_basis=coverage.get("coverage_basis", ""))
            record.update({key: annotation["label"].get(key, "") for key in ("finding_status", "plaque_composition", "stenosis_grade", "confidence", "reason", "s_peak_stenosis_mm")})
            record["reason_codes_json"] = _json(annotation["label"].get("reason_codes", []))
            # Legacy review_required conflated reader confirmation and geometry
            # QA. Expose the current interpretation without rewriting the JSON
            # snapshot or the audit ledger; reason columns contain JSON arrays.
            record["effective_reader_review_required"] = effective_reader_review_required(annotation)
            record["reader_review_reasons"] = _json(reader_review_reasons(annotation))
            record["technical_qa_reasons"] = _json(technical_qa_reasons(annotation))
            provenance = annotation.get("provenance", {})
            record["label_scope"] = provenance.get("label_scope", "group_summary_only" if provenance.get("review_reason") == CANONICAL_GROUP_REVIEW_REASON else "interval")
            # Blank is deliberately unknown, never an implicit training GO.
            record["training_segment_label_eligible"] = provenance.get("training_segment_label_eligible", "")
            record["native_anchors_json"] = _json(annotation.get("native_anchors", {}))
            record["provenance_json"] = _json(annotation.get("provenance", {}))
            # Prevent spreadsheet formula execution from user-entered free text.
            for key, value in record.items():
                if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
                    record[key] = "'" + value
            writer.writerow(record)
        files = {
            "annotations.json": (_json(state) + "\n").encode("utf-8"),
            "annotations.csv": csv_buffer.getvalue().encode("utf-8-sig"),
            "audit.jsonl": ("".join(_json(row) + "\n" for row in audit)).encode("utf-8"),
        }
        manifest = {"schema_version": EXPORT_SCHEMA, "case_id": state["case_id"], "reader_id": reader_id,
                    "revision": state["revision"], "files": []}
        for name, payload in files.items():
            _atomic_write(target / name, payload)
            manifest["files"].append({"path": name, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
        # This final file is the completion marker; interrupted exports lack a valid manifest.
        _atomic_write(target / "checksums.json", (_json(manifest) + "\n").encode("utf-8"))
        return target / "annotations.json"

    def import_case(self, json_path, reader_id=None) -> dict:
        source_path = Path(json_path).resolve(strict=True)
        if source_path.name != "annotations.json":
            raise ValueError("Import an exported annotations.json with its checksum and audit files")
        manifest_path = source_path.parent / "checksums.json"
        if not manifest_path.is_file() or manifest_path.stat().st_size > MAX_IMPORT_BYTES:
            raise ValueError("Missing or invalid export checksum manifest")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != EXPORT_SCHEMA:
            raise ValueError("Unsupported export schema")
        entries = manifest.get("files", [])
        if not isinstance(entries, list) or {item.get("path") for item in entries if isinstance(item, dict)} != {"annotations.json", "annotations.csv", "audit.jsonl"} or len(entries) != 3:
            raise ValueError("Export manifest must list exactly the three expected files")
        checked = {}
        for entry in entries:
            path = (source_path.parent / entry["path"]).resolve(strict=True)
            if path.parent != source_path.parent or path.stat().st_size > MAX_IMPORT_BYTES:
                raise ValueError("Unsafe export file path or oversized import")
            content = path.read_bytes()
            if len(content) != entry.get("bytes") or hashlib.sha256(content).hexdigest() != entry.get("sha256"):
                raise ValueError(f"Export checksum mismatch: {path.name}")
            checked[path.name] = content
        imported = json.loads(checked["annotations.json"].decode("utf-8"))
        validate_state(imported)
        if reader_id is not None and reader_id != imported["reader_id"]:
            raise ValueError("Cannot relabel one reader's annotations as another reader")
        for key in ("case_id", "reader_id", "revision"):
            if manifest.get(key) != imported[key]:
                raise ValueError("Export manifest identity mismatch")
        imported_audit = [json.loads(line) for line in checked["audit.jsonl"].decode("utf-8").splitlines() if line.strip()]
        for sequence, event in enumerate(imported_audit, start=1):
            if event.get("case_id") != imported["case_id"] or event.get("reader_id") != imported["reader_id"]:
                raise ValueError("Audit reader/case identity mismatch")
            if event.get("revision") != sequence or event.get("state", {}).get("revision") != sequence:
                raise ValueError("Audit revisions must be contiguous and agree with their snapshots")
            validate_state(event["state"])
            for identity in ("case_id", "reader_id"):
                if event["state"][identity] != imported[identity]:
                    raise ValueError("Audit snapshot identity mismatch")
        if len(imported_audit) != imported["revision"]:
            raise ValueError("Export audit does not contain every committed revision")
        if imported_audit and _identity_content(imported_audit[-1]["state"]) != _identity_content(imported):
            raise ValueError("Export snapshot does not match the final audit event")
        with self._transaction():
            row = self._row(imported["case_id"], imported["reader_id"])
            current = json.loads(row["state_json"]) if row else empty_state(imported["case_id"], imported["reader_id"], imported["source"])
            _verify_source(current["source"], imported["source"])
            if row and (current["revision"] > 0 or current["annotations"] or current["markers"]):
                if _identity_content(current) == _identity_content(imported):
                    return current
                raise ValueError("Divergent annotations already exist for this reader; import into a separate review database")
            candidate = deepcopy(imported)
            details = {"source_snapshot_sha256": hashlib.sha256(checked["annotations.json"]).hexdigest(), "imported_revision": imported["revision"], "imported_audit": imported_audit}
            return self._write(candidate, "import", details, [current], [], current["revision"] + 1)

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
