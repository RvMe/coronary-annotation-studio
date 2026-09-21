# Troubleshooting

[简体中文](zh/troubleshooting.md)

| Symptom | Action |
| --- | --- |
| No supported package | Select the directory containing the neutral `manifest.json`, not a ZIP or mixed-project parent directory. Legacy packages need explicit conversion. |
| SHA-256 or byte-count mismatch | Restore the original asset or obtain a new complete package. Do not just change checksums to silence an error. |
| Project/geometry mismatch | Reopen the corresponding project and original geometry, or create a separate project. Case IDs alone do not establish compatibility. |
| Mapping outside field | Use a valid CPR position within the supplied mapping; reduce offset/rotation if it leaves the finite field. No extrapolated native position is invented. |
| Original image drive offline | Reconnect the package to view images. Existing labels still export without images. |
| Another workspace window is open | Return to the existing window. Project locking prevents concurrent local editing. |
| Missing imported audit/checksums | Restore the whole export directory. A lone JSON is insufficient for a verified import. |
| Recovery shows a draft | Review it and Apply to commit. Recovery never silently turns drafts into formal labels. |
| UI too narrow | Enlarge the window or collapse auxiliary panels; English and Chinese layouts are independently checked in release evidence. |

When reporting a defect, provide version, architecture, a synthetic reproduction
and the exact error. Logs may contain local paths; redact them before sharing.
Do not upload patient data or a real annotation database to an issue.
