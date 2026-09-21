# Installation

[简体中文](zh/quickstart.md)

## Standalone packages

Choose the release ZIP matching your system: Windows x64, macOS Apple Silicon
arm64, or macOS Intel x86_64. Compare its SHA-256 with the release manifest before
extracting. Keep the application folder intact; the executable depends on its
adjacent dynamic libraries. Copy it to a local, non-synchronized directory.

Windows: extract the ZIP, then launch `CoronaryAnnotationStudio.exe` inside the
application folder. Do not run from inside the ZIP. If the operating system asks
whether to run an unrecognized application, verify the source and checksum first.

macOS: extract the ZIP and move `Coronary Annotation Studio.app` to a local folder
or Applications. The release uses ad-hoc signing rather than Developer ID
notarization. Follow the system's normal **Privacy & Security → Open Anyway**
workflow if macOS blocks the initial open. See
[Apple's opening instructions](https://support.apple.com/en-us/102445).
No system security feature needs to be disabled. A Rosetta test on Apple Silicon
is identified as Rosetta in the report; it is not an Intel hardware test.

Actual tested systems: Windows 11 Pro x64 and macOS 26.2 on Apple Silicon, with
x86_64 also executed under Rosetta. Mac deployment minima are 12.3 (arm64) and
12.0 (x86_64); those older systems have not been tested. See the
[acceptance report](acceptance.md) for final artifact status and evidence.

The package includes a synthetic example, license material, dependency inventory
and checksums. No external image download is needed for the example.

## Source installation

Use an isolated Python environment and the pinned dependency versions:

```console
python -m venv .venv
python -m pip install .
python -m annotation_app --package examples/synthetic-v1
```

Activate the environment before the last two commands. Windows PowerShell uses
`.venv\Scripts\Activate.ps1`; macOS shells use `source .venv/bin/activate`.
The release evidence records the Python versions actually used for building and
testing. A source installation needs package downloads once; annotation itself
is offline.

## Local records

Application preferences and independent project databases are stored under the
operating system's local application-data folder. Keep live SQLite databases out
of OneDrive or another synchronization service. Use a verified export for backup
or transfer. Reconnecting images does not require moving or deleting the records.
