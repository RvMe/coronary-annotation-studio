# Mac release build contract

Build version 0.1.0 as separate thin arm64 and x86_64 bundles. Use an isolated,
matching CPython 3.11 environment with the pinned dependencies and a SHA-256
wheel lock. Deployment targets are macOS 12.3 for arm64 and 12.0 for x86_64;
these are binary compatibility settings, and
actual tested systems and hardware are recorded in docs/acceptance.md.

## Exact source and runtime identity

Commit the reviewed source, then run `tools/write_source_freeze.py` with an output
outside the checkout. Transfer that manifest and the exact tracked bytes without
line-ending conversion. Each record identifies its path, size and SHA-256, and
the manifest records the full Git revision. Both architectures use the same tree.

`tools/build_macos.py` refuses an incomplete or changed freeze. Its optional
`--repo-root` builds an immutable source tree using separately recorded helper
files. Every runtime file and `tools/macos.spec` must remain identical when later
source materials add or revise documentation and build helpers. Assembly retains
both revisions and manifest hashes. Changing runtime code or the spec requires
rebuilding; changing a source snapshot cannot relabel an existing executable.

## Native build and verification

See docs/build.md for the command sequence. Use new build, work, release and
verification directories. Build preflight checks the interpreter architecture,
dependency pins, wheel hashes and deployment constraints. The static audit checks
all Mach-O architectures, dynamic references, minimum OS versions, bundle links,
identity/version and strict ad-hoc signatures. Ordinary PyInstaller ad-hoc signing
is intentional; these releases do not claim Developer ID notarization.

The compiled-source verifier compares every embedded application code object
against the reviewed source. Translation files must match the source hashes.
Frozen workflow checks require real Cocoa windows, the requested architecture,
English first launch, persistent translations, draft and label recovery, native
coordinate agreement, normal exit and complete hashed batch export.

Assembly includes the reviewed source, documentation, mathematical examples,
original dependency notices and official hash-verified Qt/PySide source archives.
Public build reports replace private host paths with neutral tokens while
retaining the original report SHA-256 and all source/dependency hashes. Original
local evidence is preserved. Compiled code filenames and public text are also
checked for private host paths before packaging.
Only native macOS `ditto` packages and re-extracts the bundle. Inventories cover
file bytes, executable permissions and symlink targets. A ZIP receives PASS only
after independent create/resume processes succeed from its re-extracted app.

## Python standalone license provenance

An install-only Python archive omits some original dependency notices.
`tools/prepare_mac_python_licenses.py` retrieves the corresponding official full
and install-only archives, verifies their release SHA-256 values, matches the
installed native files and exports the original notices plus proof:

```console
python tools/prepare_mac_python_licenses.py --architecture arm64 --cache /local/license-cache --output /new/python-license-proof
```

When native tar cannot decode zstd, use a separate license-tool environment with
`zstandard==0.25.0`. This decompressor is not an application dependency.
`mac_licenses.validate_python_license_proof` rechecks interpreter version,
architecture, native files and complete original license coverage at assembly.
An older license-proof schema identifier is retained solely for reading immutable
provenance; it is not the public application or annotation schema.

## Acceptance limits

Host testing distinguishes native arm64 from x86_64 under Rosetta. A Rosetta run
does not establish Intel hardware acceptance, and a deployment target does not
establish testing on an older macOS release. First-download Gatekeeper behavior,
notarization and clinical acceptance are separate from these technical checks.
Public reports identify tested environments and any missing physical-device tests.
