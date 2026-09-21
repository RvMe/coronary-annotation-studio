"""Collect original wheel and hash-bound Python standalone dependency notices."""
from pathlib import Path
import importlib.metadata as metadata
import json,sys
from audit_macos import digest
from prepare_mac_python_licenses import license_paths,target_triple,safe_member
from release_licenses import copy_checked,file_record,is_license,normalize_name
from public_macos_evidence import public_json,sanitize,host_roots
relative_safe=safe_member


def collect_licenses(payload, python_root, python_provenance, architecture, redaction_roots=None):
    redaction_roots=redaction_roots or host_roots()
    records = []
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata['Name'].lower()):
        name = dist.metadata['Name']
        destination = payload / 'third_party_licenses' / f'{normalize_name(name)}-{dist.version}'
        destination.mkdir(parents=True)
        text = dist.read_text('METADATA')
        if text:
            (destination / 'METADATA.txt').write_text(text, encoding='utf-8')
        licenses = []
        for entry in dist.files or []:
            if not is_license(str(entry)):
                continue
            source = Path(dist.locate_file(entry))
            if not source.is_file():
                continue
            target = destination / (digest(source)[:20] + '.txt')
            if target.exists() and digest(target)!=digest(source):
                raise ValueError('License content-addressed filename collision')
            if not target.exists():
                copy_checked(source.resolve(), target)
            licenses.append({'original_distribution_path': str(entry), **file_record(target, payload)})
        records.append({'distribution': name, 'version': dist.version,
                        'license_files': licenses, 'license_expression': dist.metadata.get('License-Expression'),
                        'requires': dist.requires or []})
    python_root = Path(python_root).resolve()
    if not python_root.is_dir():
        raise ValueError('Actual Python standalone license directory is required')
    provenance = json.loads(Path(python_provenance).read_text(encoding='utf-8'))
    license_proof = validate_python_license_proof(python_root, Path(sys.base_prefix), provenance,
                                                sys.version.split()[0], architecture)
    python_files = []
    for item in license_proof['licenses']:
        source = python_root / item['path']
        target = payload / 'third_party_licenses/Python-standalone' / source.name
        if not target.exists():
            copy_checked(source, target)
        python_files.append({'original_path': item['original_path'], **file_record(target, payload)})
    copy_checked(python_root / 'PYTHON_LICENSE_PROVENANCE.json',
                 payload / 'third_party_licenses/Python-standalone/PYTHON_LICENSE_PROVENANCE.json')
    # PYTHON.json can contain upstream build-host paths. Do not change the
    # original metadata/proof pair or give its derivative the original name.
    metadata_target=payload / 'third_party_licenses/Python-standalone/PYTHON_METADATA_PUBLIC.json'
    public_json(python_root / 'PYTHON.json', metadata_target, redaction_roots)
    public_json(Path(python_provenance), payload / 'PYTHON_BUILD_PROVENANCE.json', redaction_roots)
    records.append({'distribution': 'CPython standalone and bundled native dependencies',
                    'version': sys.version, 'license_files': python_files,
                    'provenance_file': 'PYTHON_BUILD_PROVENANCE.json',
                    'provenance': sanitize(provenance, redaction_roots),
                    'original_provenance_sha256': digest(python_provenance),
                    'public_provenance_is_sanitized_derivative': True,
                    'original_license_proof_sha256': digest(python_root / 'PYTHON_LICENSE_PROVENANCE.json'),
                    'original_python_metadata_sha256': license_proof['python_metadata_sha256'],
                    'public_python_metadata': file_record(metadata_target,payload),
                    'metadata_note': 'Original full-distribution metadata was verified locally against the unchanged proof before assembly. The separately named public metadata is a path-only derivative with its original SHA; reproduce the original using the proof-linked official archive.'})
    return records


def validate_python_license_proof(python_root, base_prefix, bootstrap, python_version, architecture='arm64'):
    """Bind original dependency notices to the exact installed native runtime."""
    root, base = Path(python_root).resolve(), Path(base_prefix).resolve()
    proof_path = root / 'PYTHON_LICENSE_PROVENANCE.json'
    if not proof_path.is_file():
        raise ValueError('Run prepare_python_licenses.py; a lone Python LICENSE is insufficient')
    proof = json.loads(proof_path.read_text(encoding='utf-8'))
    if (proof.get('schema_version') != 'imagecasx-python-standalone-licenses-1.0'
            or proof.get('status') != 'PASS'
            or proof.get('python_version') != python_version
            or proof.get('target_triple') != target_triple(architecture)
            or not proof.get('full_and_install_only_native_binaries_identical')):
        raise ValueError('Unverified or mismatched CPython standalone license provenance')
    if bootstrap.get('sha256') != proof.get('install_only_archive', {}).get('sha256'):
        raise ValueError('Bootstrap interpreter artifact differs from the licensed artifact')
    if digest(root / 'PYTHON.json') != proof.get('python_metadata_sha256'):
        raise ValueError('Original full-distribution PYTHON.json changed')
    if not proof.get('licenses') or not proof.get('native_install_files'):
        raise ValueError('Missing native runtime/license inventories')
    metadata = json.loads((root / 'PYTHON.json').read_text(encoding='utf-8'))
    if set(license_paths(metadata)) != {item['original_path'] for item in proof['licenses']}:
        raise ValueError('Incomplete full-distribution dependency license coverage')
    for item in proof['licenses']:
        source = root / relative_safe(item['path'])
        if (not source.is_file() or source.is_symlink() or not source.resolve().is_relative_to(root)
                or digest(source) != item['sha256'] or source.stat().st_size != item['bytes']):
            raise ValueError('Original Python/dependency license hash mismatch: ' + item['path'])
    for item in proof['native_install_files']:
        native = base / relative_safe(item['path'])
        if (not native.is_file() or native.is_symlink() or not native.resolve().is_relative_to(base)
                or digest(native) != item['sha256'] or native.stat().st_size != item['bytes']):
            raise ValueError('Installed CPython native binary differs from the licensed artifact: ' + item['path'])
    return proof
