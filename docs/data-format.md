# Neutral package format 1.0

[简体中文](zh/data-format.md)

A package contains `manifest.json`, one or more case manifests, and explicitly
hashed image/mapping assets. Use relative portable paths; absolute paths, `..`
traversal and paths escaping the case directory are rejected. Validate before
opening:

```console
python -m annotation_app validate /path/to/package
```

## Package and case identity

```json
{
  "schema_version": "cas-package-1.0",
  "project_id": "study-a-v1",
  "name": "Example study",
  "cases": [{"case_id": "case-001", "case_manifest": "case-001/case.json"}]
}
```

IDs use 1–96 letters, digits, dots, underscores or hyphens, starting with a letter
or digit. Every case must repeat its enclosing project identity. Project IDs are
stable namespaces: unrelated data must use different project IDs. Each project
receives its own local database; reader IDs create independent streams inside it.

Required case fields:

| Field | Contract |
| --- | --- |
| `schema_version` | `cas-case-1.0` |
| `project_id`, `case_id`, `geometry_id` | Explicit stable identities |
| `units`, `coordinate_system`, `intensity_units` | `mm`, `LPS`, `HU` |
| `native` | Scalar native CT NIfTI/NRRD relative filename |
| `paths` | Nonempty dictionary keyed by supplied path ID |
| `annotation_scope` | Unique IDs of supplied paths to annotate; coverage uses only these |
| `files` | Every required asset's relative path, bytes and lowercase SHA-256 |
| `metadata` | Optional dataset, split and other provenance; no split is invented |

Path names are identifiers, not an inferred anatomical classification. One path
is valid. No fixed LAD/LCX/RCA set is required. The complete synthetic manifests
are runnable examples of the format.

## Spatial mapping

Each path has `image`, optional `display_name`, and `mapping` with `type`, `file`,
`axis_order: "s,v,u"` and `inplane_spacing_mm: [u,v]`. Image arrays are `(N,H,W)`:
along-path row, cross-section row, cross-section column. The image grid's first
two spacings must agree with the mapping. All three image dimensions must be at
least two. The CPR image affine describes its local grid, not the native CT.

Mapping archives are NPZ without Python objects. `distance_mm` is a finite,
strictly increasing `(N,)` array beginning at zero and reaching the true supplied
endpoint. Nonuniform samples are supported. Optional `segment_id` has `(N,)`
values and defaults to unknown.

- `frames`: `centers`, `normals`, `binormals`, `tangents` each have `(N,3)` LPS
  values. Bases must be right-handed and orthonormal. Equal in-plane spacing is
  required. A position maps as center + u × normal + v × binormal; interpolation
  follows `distance_mm` and normalized interpolated bases.
- `coordinate_field`: `native_lps` has shape `(N,H,W,3)`, containing absolute
  native LPS millimetres at every CPR grid location. Trilinear interpolation
  supplies subvoxel mappings. Unequal u/v spacing is supported. Mapping outside
  the supplied field is rejected instead of extrapolated. A nonlinear field is
  not converted into an orthonormal affine or assumed globally invertible.

Native inverse navigation is path-local. A folded or crossing path can have
multiple nearest positions. Use path and local-position context; ambiguous
solutions are not reported as uniquely determined.

## Explicit shared LM

Absent `canonical_lm` means no declared shared segment. A verified relation must
state `owner: "LAD"`, `reference_paths: ["LAD","LCX"]`, `verified_end_mm`,
`relationship_id` and evidence. Both paths must be in annotation scope. The
loader additionally verifies equal LPS centerline geometry at all proximal
samples and interval midpoints, within 0.00001 mm. Only then is the common
prefix counted once. First-release deduplication supports this explicit LM
relation; arbitrary shared graphs are not inferred.

## Annotation and export schema

`cas-annotations-1.0` stores case, reader, revision, source, formal annotations,
markers, re-review intervals, view/draft state and completion state. An interval
stores IDs, path, anatomical segment, along-path bounds, native anchors,
composition, stenosis, confidence, reader/version provenance and review status.
Field names and enum values are language-independent.

Intervals use start-inclusive/end-exclusive bounds; coverage additionally treats
the physical final endpoint consistently. Source geometry and project identity
must agree when records are reopened. Revisions and edit snapshots are audited.

An export contains `annotations.json`, `annotations.csv`, `audit.jsonl` and a
`cas-export-1.0` checksum manifest. Import requires all four files. CSV escapes
spreadsheet formula prefixes while JSON retains exact values. CSV rows explicitly
include project and geometry IDs, and checksum manifests retain project identity. A batch adds a
`cas-batch-export-1.0` manifest hashing each exported file. Source images are never
part of annotation exports.
