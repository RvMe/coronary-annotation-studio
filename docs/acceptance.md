# Release acceptance evidence

Status: **implementation and verification in progress; not yet published**.

| Gate | Current evidence |
| --- | --- |
| Core/domain/storage/geometry/translation regression | 168 synthetic tests passed without skips on Windows 11 Pro x64 build 26200, Python 3.10.19, 2026-09-21 |
| Public package and legacy import | Synthetic validation, project isolation, offline export and unchanged legacy input hashes included in the test suite |
| Windows bilingual/native UI | Source: 30 integration checks plus independent restart; 219 layout checks in both languages at 1050×650, 1280×784 and 1440×784 logical pixels, 125% display scaling; create/resume 25/33 checks |
| Mac arm64 Cocoa UI | Source r2 passed 166 regressions, 30 UI checks and 219 layout assertions; final common source/binary acceptance pending |
| Mac x86_64 under Rosetta | Source r2 passed the same checks under Rosetta; Intel physical hardware not tested |
| Live Slicer nonlinear export | Official Slicer 5.12.4 on Mac under Rosetta: exporter 12,057 probes max 0.008868 native voxel; independent 6,000 probes max 0.009696, below 0.1; exported synthetic package loaded, annotated, restored and exported on Windows and both Mac architectures |
| Final ZIP workflow, all three targets | Pending final builds |
| Hosted CI, Pages and public download hash | Awaiting local acceptance, then GitHub login |

This file is updated from actual reports before release. A build target is not
evidence of testing on an older OS. Rosetta does not establish Intel hardware
acceptance. No clinical/reader acceptance is claimed by technical checks.
