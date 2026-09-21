# Release acceptance evidence

Status: **implementation and verification in progress; not yet published**.

| Gate | Current evidence |
| --- | --- |
| Core/domain/storage/geometry regression | 152 synthetic tests passed on Windows, Python 3.10.19, 2026-09-21 |
| Public package and legacy import | Synthetic validation, project isolation, offline export and unchanged legacy input hashes included in the test suite |
| Windows bilingual/native UI | Pending integrated UI |
| Mac arm64 Cocoa UI | Pending connected Mac work |
| Mac x86_64 under Rosetta | Pending connected Mac work |
| Live Slicer nonlinear export | Adapter implemented; real Slicer run pending |
| Final ZIP workflow, all three targets | Pending final builds |
| Hosted CI, Pages and public download hash | Awaiting local acceptance, then GitHub login |

This file is updated from actual reports before release. A build target is not
evidence of testing on an older OS. Rosetta does not establish Intel hardware
acceptance. No clinical/reader acceptance is claimed by technical checks.
