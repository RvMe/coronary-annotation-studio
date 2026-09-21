# Explicit legacy import

Legacy ImageCAS-X packages and full annotation exports can be copied into a new
neutral project. The original schema names and provenance are preserved in
import evidence. The application never connects to or upgrades the old database.

```console
python -m annotation_app import-legacy-package /old/package /new/package --project-id imported-study-v1
python -m annotation_app import-legacy-labels /old/export/annotations.json --case /new/package/case-id/case.json
```

Choose a new output directory outside the old package. Every original case asset
is validated before conversion. Source images are copied into that destination,
so ensure it is an appropriate local data location. Do not put patient data in a
source-code checkout.

Label import requires the original `annotations.json`, `annotations.csv`,
`audit.jsonl` and `checksums.json`. The importer checks hashes, case/reader
identity, contiguous audit revisions and geometry identity. Diagnostic fields,
intervals, reader IDs and historical snapshots are retained. Exact original file
bytes are retained in the import audit as base64 as well as parsed evidence.

Conflicting reader records are rejected; existing work is never overwritten by
an import. Exported annotations can be re-imported using the new format's normal
import action. Retain the original export as an independent archival copy.
