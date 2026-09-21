# Annotate the synthetic example

[简体中文](zh/quickstart.md)

1. Open the app and import the included `examples/synthetic-v1` package folder.
   The three cases demonstrate one path, multiple paths and a declared shared LM.
2. Set your reader ID before annotating. Each reader has a separate record within
   the project. Reusing an ID means continuing that reader's work.
3. Select a case and a supplied path. Scroll along the CPR and inspect the linked
   cross-section and native CT. Reference lines are display aids, not diagnoses.
4. Set the interval start and end using the orange handles or numeric controls.
   Select a finding, plaque composition where applicable, stenosis category and
   confidence. Apply saves a formal annotation. Draft changes remain a draft
   until applied.
5. Click a previous interval to review or adjust it. Applying an overlapping
   interval uses the newest interval in that scope and preserves the edit in
   the audit. Clipped positive summaries may need local reader confirmation.
6. Use the next-gap/review action to visit unannotated or review-required regions.
   Explicit completion checks only the package's declared annotation scope.
7. Switch between English and 简体中文 with a draft present; the case, draft and
   formal labels should remain the same. Close and reopen to resume.
8. Export a case or a batch. Keep every file in the export folder, including audit
   and checksums. Export does not clear saved work or turn drafts into labels.

Finding meanings:

| State | Meaning |
| --- | --- |
| Unannotated | No explicit finding recorded for this region |
| Negative | Reader explicitly assessed no plaque for this interval |
| Positive | Reader recorded plaque, with composition and stenosis fields |
| Uncertain | Reader could not make a confident finding |
| Non-evaluable | Reader could not assess the interval |

Completion, reader confirmation, technical geometry QA and adjudication are
distinct states. An uncertain or non-evaluable interval is not converted to a
negative result. The initial release has no multi-reader adjudication interface.

## Recover and export offline

Draft position and label choices are saved in the project database. Applied
annotations and undo/redo history use atomic SQLite transactions. Reopen the same
project and reader. If the original images are disconnected, saved labels still
export through the interface or:

```console
python -m annotation_app export --db /local/project/annotations.sqlite --reader READER-A --output /local/exports
```

The batch completion marker is `batch-manifest.json`. An interrupted export
without a valid manifest is incomplete; start a new export directory.
