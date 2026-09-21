# Release acceptance evidence

## Final local artifact acceptance

All three v0.1.0 program ZIPs passed their own final re-extraction workflow on
2026-09-21. Windows x64 ran on Windows 11 Pro; Mac arm64 ran natively and Mac
x86_64 ran under Rosetta on the connected Apple Silicon Mac mini, macOS 26.2.
Each program completed 25 create-session checks and 33 independent resume/export
checks. Both Mac archives arrived with matching receiving-host SHA-256 receipts.

The full synthetic suite ran 201 tests per platform: 200 passed on Windows with
one explicit native-macOS packaging skip; all 201 passed on each Mac architecture.
Each Mac app passed architecture/dependency/signature checks for 131 Mach-O files.
The live Slicer mapping's independent maximum error was 0.009696 native voxel,
below the preset 0.1 limit. Final saved-anchor recovery error was 0.0 voxel.

See the [machine-readable acceptance and archive hashes](release-acceptance.json).
The [hosted CI run](https://github.com/RvMe/coronary-annotation-studio/actions/runs/35636962867)
also passed on Windows, macOS arm64 and macOS Intel runners, with 201 tests per
runner (one macOS-only test skipped on Windows). The bilingual documentation is
deployed on [GitHub Pages](https://rvme.github.io/coronary-annotation-studio/).
The [release's acceptance record](https://github.com/RvMe/coronary-annotation-studio/releases/download/v0.1.0/RELEASE-ACCEPTANCE.json)
records publication-time public download verification. Hosted source tests do not
replace final program ZIP testing on the local machines described above.

## Source-stage evidence

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
