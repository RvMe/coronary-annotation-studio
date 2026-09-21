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
