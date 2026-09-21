# Coronary Annotation Studio

![English application window with synthetic data](images/studio-en.png)

Actual desktop screenshot using a mathematical phantom, not patient data.

Annotate coronary lesion intervals on existing 3D CPR images, review gaps and
uncertainties, recover drafts, and export labels with a full audit record.

English is the default interface language. Simplified Chinese is available at
runtime and the choice is remembered. [中文说明](zh/index.md)

Start with [installation](install.md) and the [synthetic walkthrough](quickstart.md).
For your own images, use the [neutral data format](data-format.md) or the
[3D Slicer adapter](slicer.md). See [release evidence](acceptance.md) for the
actual tested environments and [limitations](limitations.md) for scope.

Inputs stay local. Exported annotation folders contain labels and audit records,
without copying source images. Project identity and reader identity remain
separate, so identical case IDs from different sources cannot collide.
