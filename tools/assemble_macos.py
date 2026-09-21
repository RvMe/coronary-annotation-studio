"""Assemble and test a native Mac ZIP from a passing, source-bound CAS build.

Never unzip/recompress this .app release on Windows. This tool includes reviewed
source, synthetic examples, notices and matching dependency source archives.
"""
from pathlib import Path
import argparse
import json
import platform
import subprocess
import sys
from audit_macos import audit_bundle, digest, tree_inventory, write_json, execution_identity, deployment_target
from build_macos import verify_freeze
from mac_licenses import collect_licenses
from release_licenses import copy_checked, environment_inventory, collect_qt_sources
from verify_compiled_sources import verify as verify_compiled
from verify_macos_workflow import same_inventory, safe_archive
from public_macos_evidence import public_json,host_roots,audit_public_text,audit_compiled_filenames


def checked_run(command):
    result = subprocess.run([str(v) for v in command], capture_output=True, text=True,
                            encoding='utf-8', errors='replace')
    if result.returncode:
        raise RuntimeError(f'{command[0]} failed ({result.returncode}): {result.stderr}')


def verify_runtime_binding(built, current):
    runtime = lambda name: name.startswith('annotation_app/') or name == 'tools/macos.spec'
    selected = lambda items: [v for v in items if runtime(v['path'])]
    before, after = selected(built), selected(current)
    if not before or not same_inventory(before, after):
        raise ValueError('Runtime or build spec differs from the passing build')


def make_verified_zip(payload, archive, verification_root):
    if sys.platform != 'darwin':
        raise RuntimeError('Only native macOS ditto may assemble the Mac release')
    temporary = archive.with_name(archive.name + '.partial')
    if archive.exists() or temporary.exists() or verification_root.exists():
        raise FileExistsError('Use new archive and verification paths')
    write_json(payload / 'FILE-MANIFEST.json', {
        'schema_version': 'cas-mac-release-files-1.0', 'entries': tree_inventory(payload),
        'note': 'Excludes this manifest; the ZIP SHA-256 binds the manifest.'})
    expected = tree_inventory(payload)
    checked_run(['/usr/bin/ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', payload, temporary])
    safe_archive(temporary)
    verification_root.mkdir(parents=True)
    checked_run(['/usr/bin/ditto', '-x', '-k', temporary, verification_root])
    extracted = verification_root / payload.name
    if not same_inventory(expected, tree_inventory(extracted)):
        raise ValueError('ZIP changed file hashes, permissions or symlinks')
    temporary.rename(archive)
    return {'name': archive.name, 'bytes': archive.stat().st_size, 'sha256': digest(archive),
            'native_ditto': True, 'crc_verified': True, 'reextracted_inventory_equal': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('app', 'output', 'build-report', 'source-freeze', 'python-provenance',
                 'python-license-root', 'source-cache'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--architecture', choices=['arm64', 'x86_64'], required=True)
    args = parser.parse_args()
    if sys.platform != 'darwin' or platform.machine() != args.architecture:
        raise RuntimeError('Assemble with the matching Mac architecture build interpreter')
    root = Path(__file__).resolve().parents[1]
    out, app = args.output.resolve(), args.app.resolve()
    if out.exists() or out.is_relative_to(app) or out.is_relative_to(root):
        raise ValueError('Use a new output directory outside the source and application')
    freeze, records = verify_freeze(root, args.source_freeze)
    build = json.loads(args.build_report.read_text(encoding='utf-8'))
    if (build.get('status') != 'PASS' or build.get('architecture') != args.architecture
            or build.get('version') != '0.1.0' or not build.get('source')
            or build.get('deployment_target') != deployment_target(args.architecture)):
        raise ValueError('A passing matching source-bound build report is required')
    # Coordinator may append build tools/docs after freezing application code.
    # Bind every runtime byte and the spec to the actual build, and separately
    # identify the final reviewed source-material revision; never relabel it.
    verify_runtime_binding(build['source'], records)
    audit_path = args.build_report.parent / 'macos_binary_audit.json'
    if digest(audit_path) != build.get('audit_sha256'):
        raise ValueError('Build-time binary audit changed')
    original = json.loads(audit_path.read_text(encoding='utf-8'))
    audit = audit_bundle(app, architecture=args.architecture)
    if (audit['status'] != 'PASS' or audit['bundle_version'] != '0.1.0'
            or audit['bundle_identifier'] != 'org.coronaryannotationstudio.desktop'
            or not same_inventory(original['bundle_inventory'], audit['bundle_inventory'])):
        raise ValueError('App identity, static audit or build inventory differs')
    for item in audit['bundle_inventory']:
        if item['path'].lower().endswith(('.nii', '.nii.gz', '.nrrd', '.dcm', '.db', '.sqlite', '.sqlite3', '.pt', '.pth', '.onnx')):
            raise ValueError('Data, database or model unexpectedly bundled in application')
    out.mkdir(parents=True)
    report = {'schema_version': 'cas-mac-assembly-1.0', 'status': 'FAIL',
              'version': '0.1.0', 'architecture': args.architecture, 'deployment_target': deployment_target(args.architecture),
              'git_revision': freeze['git_revision'], 'build_git_revision': build['git_revision'],
              'source_freeze_sha256': digest(args.source_freeze),
              'build_source_freeze_sha256': build['source_freeze_sha256'], 'execution': execution_identity(),
              'physical_intel_tested': False, 'macOS12_runtime_tested': False,
              'first_Gatekeeper_tested': False, 'notarized': False}
    try:
        name = f'CoronaryAnnotationStudio-v0.1.0-macos-{args.architecture}'
        payload = out / 'assembled' / name
        payload.mkdir(parents=True)
        destination_app = payload / 'Coronary Annotation Studio.app'
        checked_run(['/usr/bin/ditto', '--rsrc', '--extattr', app, destination_app])
        if not same_inventory(audit['bundle_inventory'], tree_inventory(destination_app)):
            raise ValueError('Native app copy differs')
        compiled = verify_compiled(destination_app / 'Contents/MacOS/CoronaryAnnotationStudio', root)
        write_json(payload / 'COMPILED-SOURCE-VERIFICATION.json', compiled)
        if compiled['status'] != 'PASS':
            raise ValueError('Frozen code differs from the reviewed source')
        filenames = audit_compiled_filenames(destination_app / 'Contents/MacOS/CoronaryAnnotationStudio',
                                            destination_app.rglob('base_library.zip'))
        write_json(out / 'compiled_filename_audit.json', filenames)
        if filenames['status'] != 'PASS':
            raise ValueError('Frozen code contains private source filenames; rebuild is required')
        roots = host_roots(SOURCE_ROOT=root, BUILD_ROOT=args.build_report.parent, RELEASE_ROOT=out,
                           APP_BUNDLE=app, PYTHON_LICENSE_ROOT=args.python_license_root,
                           PYTHON_PROVENANCE_ROOT=args.python_provenance.parent, QT_SOURCE_CACHE=args.source_cache)
        public_json(out / 'compiled_filename_audit.json', payload / 'COMPILED-FILENAME-AUDIT.json', roots)
        copy_checked(args.source_freeze, payload / 'SOURCE-VERSION.json')
        public_json(args.build_report, payload / 'BUILD_REPORT.json', roots)
        public_json(audit_path, payload / 'MACOS_BINARY_AUDIT.json', roots)
        for name, expected in [('preflight/preflight.json', build['preflight_sha256']),
                               ('compiled_source_verification.json', build['compiled_source_report_sha256'])]:
            source = args.build_report.parent / name
            if digest(source) != expected:
                raise ValueError('Build evidence differs: ' + name)
            public_json(source, payload / 'build_evidence' / name, roots)
        # Freeze records are the finite reviewed source allow-list. Runtime data
        # and notices must also appear in that list; no recursive workspace copy.
        for record in records:
            relative = record['path']
            copy_checked(root / relative, payload / 'source' / relative)
            if (relative.startswith(('docs/', 'examples/synthetic-v1/'))
                    or relative in {'LICENSE', 'NOTICE', 'README.md', 'THIRD_PARTY_NOTICES.md', 'CITATION.cff', 'CHANGELOG.md'}):
                copy_checked(root / relative, payload / relative)
        for required in ('LICENSE', 'NOTICE', 'README.md', 'THIRD_PARTY_NOTICES.md', 'docs/install.md'):
            if not (payload / required).is_file():
                raise ValueError('Required release material absent from freeze: ' + required)
        inventory = environment_inventory()
        inventory['wheel_and_python_license_records'] = collect_licenses(
            payload, args.python_license_root, args.python_provenance, args.architecture, roots)
        inventory['corresponding_qt_sources'] = collect_qt_sources(payload, inventory, args.source_cache)
        write_json(payload / 'DEPENDENCIES.json', inventory)
        verify_freeze(root, args.source_freeze)
        audit_public_text(payload, roots)
        archive = out / (payload.name + '.zip')
        report['archive'] = make_verified_zip(payload, archive, out / 'inventory_reextraction')
        command = [sys.executable, '-B', str(root / 'tools/verify_macos_workflow.py'),
                   '--release-zip', str(archive), '--architecture', args.architecture,
                   '--output', str(out / 'frozen_zip_verification')]
        with (out / 'frozen_zip_verification.log').open('x', encoding='utf-8') as log:
            result = subprocess.run(command, cwd=out, stdout=log, stderr=subprocess.STDOUT)
        report['frozen_zip_verification_exit_code'] = result.returncode
        verification = out / 'frozen_zip_verification/verification.json'
        if result.returncode or json.loads(verification.read_text(encoding='utf-8'))['status'] != 'PASS':
            raise ValueError('Final ZIP frozen workflow failed; archive is not accepted')
        if not same_inventory(audit['bundle_inventory'], tree_inventory(app)):
            raise ValueError('Input app changed during assembly')
        report.update(status='PASS', frozen_zip_verification_sha256=digest(verification))
        (out / (archive.name + '.sha256')).write_text(digest(archive) + '  ' + archive.name + '\n', encoding='utf-8')
    except Exception as exc:
        report['error'] = repr(exc)
    write_json(out / 'ASSEMBLY.json', report)
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
