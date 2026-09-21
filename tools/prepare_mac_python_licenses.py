"""Recover hash-bound CPython-standalone runtime license metadata.

Astral install_only archives intentionally omit PYTHON.json and the full build's
dependency license files. This tool reads the matching official full archive,
checks native install files against the install_only artifact, and exports only
licenses plus lightweight provenance. It does not install or execute Python.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

from audit_macos import digest, write_json, validate_architecture


def target_triple(architecture):
    validate_architecture(architecture)
    return {'arm64': 'aarch64-apple-darwin', 'x86_64': 'x86_64-apple-darwin'}[architecture]


def safe_member(value):
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or '\\' in value or ':' in value:
        raise ValueError(f'Unsafe distribution-relative member: {value}')
    return path


def system_only_dependencies(metadata):
    result = []
    for name, variants in metadata.get('build_info', {}).get('extensions', {}).items():
        for variant in variants:
            links = variant.get('links', [])
            if links and all(link.get('system') or link.get('framework') for link in links):
                result.append({'extension': name, 'links': links,
                    'license_paths': variant.get('license_paths', []),
                    'reason': 'PYTHON.json explicitly identifies every linked dependency as supplied by macOS, not redistributed by the app.'})
    return result


def license_paths(metadata):
    result = set()
    def add(value):
        if isinstance(value, str):
            result.add(str(safe_member(value)))
        elif isinstance(value, list):
            for item in value:
                add(item)
        elif value is not None:
            raise ValueError('Unexpected PBS license_path type')
    add(metadata.get('license_path'))
    add(metadata.get('license_paths'))
    for variants in metadata.get('build_info', {}).get('extensions', {}).values():
        for variant in variants:
            links = variant.get('links', [])
            if links and all(link.get('system') or link.get('framework') for link in links):
                # The archive lists unioned alternatives (e.g. zlib-ng) even
                # when this Mac build links only the OS's zlib. Do not pretend
                # those alternatives are redistributed native dependencies.
                continue
            add(variant.get('license_path'))
            add(variant.get('license_paths'))
    if not result or not metadata.get('license_path'):
        raise ValueError('Full PYTHON.json lacks required license-path metadata')
    return sorted(result)


def download_asset(asset, cache):
    name = asset['name']
    if Path(name).name != name:
        raise ValueError('Unsafe release asset filename')
    expected = asset.get('digest', '').removeprefix('sha256:')
    if not re.fullmatch('[a-f0-9]{64}', expected):
        raise ValueError('Official GitHub release API must supply an exact SHA-256 digest')
    target = cache / name
    if not target.exists():
        partial = target.with_name(target.name + '.partial')
        if partial.exists():
            raise FileExistsError(partial)
        request = urllib.request.Request(asset['browser_download_url'], headers={'User-Agent': 'CAS-License-Audit/1.0'})
        with urllib.request.urlopen(request, timeout=120) as response, partial.open('xb') as out:
            shutil.copyfileobj(response, out, length=4 * 1024 * 1024)
        if partial.stat().st_size != asset['size'] or digest(partial) != expected:
            raise ValueError(f'Official release asset failed size/SHA verification: {name}')
        partial.replace(target)
    if target.stat().st_size != asset['size'] or digest(target) != expected:
        raise ValueError(f'Existing artifact differs from official release digest: {name}')
    return target, {'filename': name, 'url': asset['browser_download_url'],
                    'sha256': expected, 'bytes': asset['size']}


def tar_read(archive, member):
    safe_member(member)
    result = subprocess.run(['tar', '-xOf', str(archive), member], capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError(f'tar cannot read {member}: {result.stderr.decode(errors="replace")}')
    return result.stdout


def prepare(full_archive, install_archive, output, full_provenance, install_provenance, architecture='arm64'):
    if output.exists():
        raise FileExistsError('Use a new license output directory')
    if str(full_archive).endswith('.zst') and shutil.which('zstd') is None:
        # The optional decompressor belongs in a separate license-tool venv;
        # never install it into or change the frozen application's environment.
        try:
            import zstandard
        except ImportError as error:
            raise RuntimeError('Use a separate tool environment with zstandard, or provide zstd on PATH') from error
        with tempfile.TemporaryDirectory(prefix='cas-license-decompression-') as temporary:
            unpacked = Path(temporary) / 'full.tar'
            count = 0
            with Path(full_archive).open('rb') as source, unpacked.open('xb') as destination:
                with zstandard.ZstdDecompressor().stream_reader(source) as reader:
                    for chunk in iter(lambda: reader.read(4 * 1024 * 1024), b''):
                        count += len(chunk)
                        if count > 4 * 1024**3:
                            raise ValueError('Unexpected Python archive expansion')
                        destination.write(chunk)
            return prepare(unpacked, install_archive, output, full_provenance, install_provenance, architecture)
    raw_metadata = tar_read(full_archive, 'python/PYTHON.json')
    metadata = json.loads(raw_metadata)
    if metadata.get('target_triple') != target_triple(architecture):
        raise ValueError(f'Expected matching {architecture} CPython full distribution')
    paths = license_paths(metadata)
    native = []
    with tarfile.open(install_archive, 'r:gz') as archive:
        for member in archive:
            relative = safe_member(member.name)
            if not member.isfile() or not str(relative).startswith('python/'):
                continue
            install_path = relative.relative_to('python')
            name = str(install_path)
            if not (name.endswith(('.so', '.dylib')) or name == 'bin/python' + metadata['python_major_minor_version']):
                continue
            stream = archive.extractfile(member)
            value = hashlib.sha256()
            with stream:
                for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
                    value.update(block)
            native.append({'path': name, 'sha256': value.hexdigest(), 'bytes': member.size})
    if not native or not any(p['path'].startswith('bin/python') for p in native):
        raise ValueError('Install-only native runtime inventory is incomplete')
    # Select exact validated regular install-only paths, not an unrestricted
    # archive extraction. The temporary tree is never shipped to doctors.
    with tempfile.TemporaryDirectory(prefix='cas-pbs-native-') as temp:
        temporary = Path(temp)
        members = ['python/install/' + item['path'] for item in native]
        result = subprocess.run(['tar', '-xf', str(full_archive), '-C', str(temporary), *members],
                                capture_output=True, timeout=180)
        if result.returncode:
            raise RuntimeError('Cannot extract exact full-runtime match: ' + result.stderr.decode(errors='replace'))
        for record in native:
            path = temporary / 'python/install' / record['path']
            if (path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(temporary.resolve())
                    or path.stat().st_size != record['bytes'] or digest(path) != record['sha256']):
                raise ValueError('Full and install-only native runtime mismatch: ' + record['path'])
    output.mkdir(parents=True)
    (output / 'PYTHON.json').write_bytes(raw_metadata)
    license_records = []
    for member in paths:
        content = tar_read(full_archive, 'python/' + member)
        sha = hashlib.sha256(content).hexdigest()
        target = output / ('LICENSE_' + sha[:20] + '.txt')
        if not target.exists():
            target.write_bytes(content)
        license_records.append({'path': target.name, 'original_path': member,
                                'sha256': sha, 'bytes': len(content)})
    report = {'schema_version': 'imagecasx-python-standalone-licenses-1.0',
        'status': 'PASS', 'python_version': metadata['python_version'],
        'target_triple': metadata['target_triple'], 'architecture': architecture,
        'full_archive': full_provenance, 'install_only_archive': install_provenance,
        'full_and_install_only_native_binaries_identical': True,
        'native_install_files': native, 'licenses': license_records,
        'macOS_system_dependencies_not_redistributed': system_only_dependencies(metadata),
        'python_metadata_sha256': digest(output / 'PYTHON.json'),
        'scope': 'Full non-system distribution license superset; not a claim every optional stdlib extension is shipped. Explicit macOS system-library alternatives are not redistributed.',
        'source_documentation': 'https://gregoryszorc.com/docs/python-build-standalone/main/distributions.html'}
    write_json(output / 'PYTHON_LICENSE_PROVENANCE.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--release', default='20260901')
    parser.add_argument('--python-version', default='3.11.16')
    parser.add_argument('--architecture', required=True, choices=['arm64', 'x86_64'])
    args = parser.parse_args()
    if not re.fullmatch('[0-9]{8}', args.release) or not re.fullmatch('[0-9]+\.[0-9]+\.[0-9]+', args.python_version):
        raise ValueError('Invalid version/release')
    url = f'https://api.github.com/repos/astral-sh/python-build-standalone/releases/tags/{args.release}'
    request = urllib.request.Request(url, headers={'User-Agent': 'CAS-License-Audit/1.0'})
    with urllib.request.urlopen(request, timeout=60) as response:
        release = json.load(response)
    prefix = f'cpython-{args.python_version}+{args.release}-{target_triple(args.architecture)}-'
    assets = {item['name']: item for item in release['assets']}
    args.cache.mkdir(parents=True, exist_ok=True)
    full, full_record = download_asset(assets[prefix + 'pgo+lto-full.tar.zst'], args.cache)
    install, install_record = download_asset(assets[prefix + 'install_only.tar.gz'], args.cache)
    report = prepare(full, install, args.output, full_record, install_record, args.architecture)
    print(json.dumps({'status': report['status'], 'python_version': report['python_version'],
                      'license_files': len(report['licenses']),
                      'matched_native_files': len(report['native_install_files']),
                      'output': str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
