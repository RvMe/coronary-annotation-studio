# Known limitations

- Existing 3D CPR + native CT + mapping is required. There is no centerline or CPR
  generation, screenshot/MIP import, DICOM/PACS, automatic diagnosis or contour segmentation.
- Native CT display follows the acquired image grid; a rotated or oblique input
  retains its declared orientation. Inspect orientation labels and mapped points.
- Nonlinear fields have finite coverage. Off-grid mappings are rejected; arbitrary
  global inverse transforms are not assumed.
- Frame input requires equal in-plane spacing. Dense coordinate fields support
  unequal spacing. Slicer input expects along-path K and explicitly handled parents.
- First-release shared-segment deduplication supports only explicitly declared,
  geometrically verified LAD-owned LM. It is not automatic anatomy recognition.
- Coverage refers to the declared scope, not the whole coronary tree. Reader
  completion does not imply adjudication, clinical approval or training readiness.
- Each reader has a local stream. The initial release does not implement a full
  two-reader adjudication workflow, network synchronization or simultaneous editing.
- A source release and deterministic synthetic checks do not establish clinical
  validity. Actual tested systems, Rosetta checks and missing physical-device tests
  are listed in the [acceptance report](acceptance.md).
