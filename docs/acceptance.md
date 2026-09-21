# Release acceptance evidence

**Source and integration evidence captured on 2026-09-21.** This document is
included in the source snapshot used to assemble the programs. Final immutable
archive status and SHA-256 values are distributed separately as
`RELEASE-ACCEPTANCE.json` and `SHA256SUMS.txt` with the release. An archive is
accepted only after its own re-extraction workflow passes.

| Gate | Current evidence |
| --- | --- |
| Core/domain/storage/geometry/translation regression | 168 synthetic tests passed without skips on Windows 11 Pro x64 build 26200, Python 3.10.19, 2026-09-21 |
| Integrated source and release tooling suite | Windows: 201 tests run, 200 passed, one explicit native-macOS `ditto` skip; the 33 release-tool tests passed without skips on each Mac architecture |
| Public package and legacy import | Synthetic validation, project isolation, offline export and unchanged legacy input hashes included in the test suite |
| Windows bilingual/native UI | Source: 30 integration checks plus independent restart; 219 layout checks in both languages at 1050×650, 1280×784 and 1440×784 logical pixels, 125% display scaling; create/resume 25/33 checks |
| Mac arm64 Cocoa UI | Frozen common source passed 168 regressions and 237 layout assertions at 1050×650, 1280×800 and 1440×900 logical pixels; independent Slicer-fixture create/resume 25/33 checks; macOS 26.2, Python 3.11.16 |
| Mac x86_64 under Rosetta | The same frozen common source passed 168 regressions, 237 layout assertions and create/resume 25/33 checks under Rosetta on macOS 26.2; Intel physical hardware not tested |
| Live Slicer nonlinear export | Official Slicer 5.12.4 on Mac under Rosetta: exporter 12,057 probes max 0.008868 native voxel; independent 6,000 probes max 0.009696, below 0.1; exported synthetic package loaded, annotated, restored and exported on Windows and both Mac architectures |
| Windows candidate ZIP | Re-extraction, complete file-hash verification and independent native create/resume processes passed 25/33 checks; bundled Python/Qt paths isolated from the development environment |
| Mac final ZIP workflows | Recorded separately for each immutable archive in the release acceptance record |
| Hosted CI, Pages and public download hash | Check the repository Actions runs and release acceptance record for publication-time results |

This source-stage evidence does not replace the final archive report. A build target is not
evidence of testing on an older OS. Rosetta does not establish Intel hardware
acceptance. No clinical/reader acceptance is claimed by technical checks.
