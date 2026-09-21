# Build and verify a release

Use a new local build directory and an isolated Python environment. The build
must not depend on a running annotation database or real image data. Runtime
versions are pinned in `annotation_app/requirements.txt`; install the packaging
requirements with `python -m pip install -r requirements-build.txt`.

## Windows

```powershell
./tools/build_windows.ps1 -PythonExe /path/to/venv/Scripts/python.exe -OutputRoot /new/dist -WorkRoot /new/work
python tools/assemble_windows.py --app /new/dist/CoronaryAnnotationStudio --output /new/release --source-cache /local/source-cache
python tools/verify_release_zip.py /new/release/CoronaryAnnotationStudio-v0.1.0-windows-x64.zip --output /new/zip-verification
```

Run all commands from the source root using the same Python environment. Each
output/work directory must be new. The assembler compares the application's
embedded code objects with reviewed source and collects the actual runtime's
license/provenance material. It fetches version-matched QtBase/PySide source
archives and checks their official SHA-256 values. The final ZIP verifier checks
every payload file, removes interpreter paths from the environment, and runs
two independent native GUI processes to create and resume/export synthetic work.

## macOS

Build arm64 and x86_64 separately with matching Python interpreters and wheel
architectures. Build and sign locally; transfer the resulting ZIP as an opaque
artifact. Preserve bundle symlinks and executable permissions with native macOS
packaging/extraction tools. Do not unpack and rebuild a Mac bundle on Windows.

Commit the reviewed source, then create an exact byte manifest outside the
checkout. Use the matching architecture's interpreter for the build and assembly:

```console
python tools/write_source_freeze.py --output /local/source-freeze.json
python tools/build_macos.py --architecture arm64 --source-freeze /local/source-freeze.json --output /new/build --work /new/work --wheel-lock /local/wheels.json --wheelhouse /local/wheels
python tools/verify_macos_workflow.py --app "/new/build/dist/Coronary Annotation Studio.app" --package examples/synthetic-v1 --architecture arm64 --output /new/app-check
python tools/assemble_macos.py --app "/new/build/dist/Coronary Annotation Studio.app" --build-report /new/build/build_report.json --source-freeze /local/source-freeze.json --architecture arm64 --output /new/release --python-provenance /local/python-bootstrap.json --python-license-root /local/python-licenses --source-cache /local/source-cache
```

Replace `arm64` with `x86_64` and use a matching interpreter and wheelhouse for
Intel. The wheel lock records the filename, byte count and SHA-256 of every
downloaded wheel; the preflight rejects missing, additional or incompatible
wheels. The binary audit checks every Mach-O architecture, dependency,
deployment target, signature and bundle link. Assembly checks the code embedded
in the executable against the reviewed source, includes original license texts
and corresponding sources, and runs the workflow from the final re-extracted ZIP.
See `MAC_BUILD_CONTRACT.md` in the source tree for interpreter-license provenance
and separate runtime/source-material revisions.

The deployment minimum is macOS 12.3 for arm64 and 12.0 for x86_64. The arm64
SciPy 1.15.3 OpenBLAS wheel contains binaries targeting 12.3 even though its
filename uses the major-version `12_0` tag; the
[upstream build configuration](https://github.com/scipy/scipy/blob/v1.15.3/.github/workflows/wheels.yml#L159-L167)
records that distinction. The release audit uses the actual Mach-O headers.
Runtime acceptance currently covers macOS 26.2, with Intel execution under
Rosetta; the deployment minima are not claims of tests on those older systems.

Run actual Cocoa window tests for both binaries, then repeat the annotation,
save, exit, recovery and export workflow after extracting the final ZIP. Mark
Intel execution on Apple Silicon as Rosetta. Record the actual OS/toolchain and
distinguish the build deployment target from tested hardware/system versions.

## Acceptance records

Run `python tools/run_tests.py --output /new/test-report` for structured synthetic
test results. Validate the public schemas with `python tools/validate_schemas.py`
after installing `jsonschema==4.25.1`. Build docs with `mkdocs build --strict`
using MkDocs 1.6.1. `tools/audit_public_source.py` audits the explicit Git inventory.

Final source changes require rebuilding the affected executable, not just
replacing its source snapshot. Build outputs remain candidates until the final
extracted workflow and required geometry/UI checks pass.
