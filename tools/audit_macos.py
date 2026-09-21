"""Read-only audit of a thin arm64/x86_64 ad-hoc signed macOS research .app.

Checks static compatibility; it does not claim runtime testing on macOS 12 or
successful first opening under Gatekeeper on another computer.
"""
from __future__ import annotations

import argparse
from collections import Counter
import ctypes
import errno
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import subprocess
import sys

MACHO_MAGICS = {bytes.fromhex(value) for value in (
    'feedface', 'cefaedfe', 'feedfacf', 'cffaedfe',
    'cafebabe', 'bebafeca', 'cafebabf', 'bfbafeca')}
ARCHITECTURES = {'arm64', 'x86_64'}


def deployment_target(architecture):
    validate_architecture(architecture)
    return {'arm64': '12.3', 'x86_64': '12.0'}[architecture]


def validate_architecture(architecture):
    if architecture not in ARCHITECTURES:
        raise ValueError('Select exactly arm64 or x86_64; mixed/universal releases are not supported')
    return architecture


def sysctl_int(name):
    """Read the current Python process's translation state, not a child tool's."""
    if sys.platform != 'darwin':
        return None
    libc = ctypes.CDLL(None, use_errno=True)
    function = libc.sysctlbyname
    function.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
    function.restype = ctypes.c_int
    value, size = ctypes.c_int(), ctypes.c_size_t(ctypes.sizeof(ctypes.c_int))
    if function(name.encode('ascii'), ctypes.byref(value), ctypes.byref(size), None, 0) == 0:
        return value.value
    if ctypes.get_errno() == errno.ENOENT:
        return 0
    return None


def execution_identity():
    machine = platform.machine()
    translated, has_arm = sysctl_int('sysctl.proc_translated'), sysctl_int('hw.optional.arm64')
    hardware = 'arm64' if translated == 1 or has_arm == 1 else 'x86_64' if machine == 'x86_64' and has_arm == 0 else 'unknown'
    mode = ('rosetta2_translated_x86_64' if translated == 1 and machine == 'x86_64'
            else 'native_' + machine if translated == 0 else 'unknown')
    return {'process_architecture': machine, 'hardware_architecture': hardware,
            'rosetta_translated': translated == 1 if translated is not None else None,
            'execution_mode': mode, 'macOS': platform.mac_ver()[0]}


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                               allow_nan=False) + '\n', encoding='utf-8')


def run(command):
    result = subprocess.run([str(x) for x in command], capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=120)
    return {'command': [str(x) for x in command], 'exit_code': result.returncode,
            'stdout': result.stdout.strip(), 'stderr': result.stderr.strip()}


def version_tuple(value):
    parts = str(value).split('.')
    if not 1 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f'Invalid macOS version: {value}')
    return tuple(int(p) for p in parts) + (0,) * (3 - len(parts))


def parse_load_commands(text):
    versions, rpaths = [], []
    for block in re.split(r'(?m)^Load command \d+\s*$', text):
        if re.search(r'\bcmd LC_BUILD_VERSION\b', block):
            found = re.search(r'(?m)^\s*minos\s+(\S+)', block)
            if found:
                versions.append(found.group(1))
        elif re.search(r'\bcmd LC_VERSION_MIN_MACOSX\b', block):
            found = re.search(r'(?m)^\s*version\s+(\S+)', block)
            if found:
                versions.append(found.group(1))
        if re.search(r'\bcmd LC_RPATH\b', block):
            found = re.search(r'(?m)^\s*path (.+?) \(offset \d+\)', block)
            if found:
                rpaths.append(found.group(1))
    return {'minimum_versions': versions, 'rpaths': rpaths}


def parse_dependencies(text):
    """Read otool -L entries; these include a dylib's own LC_ID_DYLIB name."""
    return [match.group(1).strip() for line in text.splitlines()[1:]
            if (match := re.match(r'^\s+(.+?)\s+\(compatibility version ', line))]


def classify_dylib_references(load_text, libraries_text):
    """Distinguish install IDs from dependencies using actual load-command types.

    Cross-check against all otool -L entries, including multiplicity, so an
    unknown/missing command or a dependency with the same name as an ID cannot
    disappear from the audit. Never infer an ID from position or filename.
    """
    dependency_commands = {'LC_LOAD_DYLIB', 'LC_LOAD_WEAK_DYLIB',
                           'LC_REEXPORT_DYLIB', 'LC_LOAD_UPWARD_DYLIB',
                           'LC_LAZY_LOAD_DYLIB'}
    identities, dependencies = [], []
    for block in re.split(r'(?m)^Load command \d+\s*$', load_text):
        command = re.search(r'(?m)^\s*cmd\s+(\S+)\s*$', block)
        if command is None:
            continue
        kind = command.group(1)
        if kind not in dependency_commands | {'LC_ID_DYLIB'}:
            if kind.endswith('_DYLIB'):
                raise ValueError('Unsupported dylib load command: ' + kind)
            continue
        name = re.search(r'(?m)^\s*name (.+?) \(offset \d+\)\s*$', block)
        if name is None:
            raise ValueError('Missing readable name for ' + kind)
        reference = name.group(1)
        if kind == 'LC_ID_DYLIB':
            identities.append(reference)
        else:
            dependencies.append({'load_command': kind, 'reference': reference})
    classified = identities + [item['reference'] for item in dependencies]
    if Counter(classified) != Counter(parse_dependencies(libraries_text)):
        raise ValueError('otool -l dylib commands and -L entries disagree')
    return {'dylib_install_names': identities, 'dylib_load_commands': dependencies}


def system_library(value):
    return value.startswith(('/System/Library/', '/usr/lib/'))


def resolve_dependency(value, binary, executable, bundle, rpaths, main_rpaths):
    """Resolve dyld load paths without accepting arbitrary build-machine paths."""
    binary, executable, bundle = Path(binary), Path(executable), Path(bundle).resolve()
    if system_library(value):
        return {'kind': 'macOS_system', 'reference': value}

    def expand(path, owner):
        if path.startswith('@loader_path/'):
            return owner.parent / path[len('@loader_path/'):]
        if path == '@loader_path':
            return owner.parent
        if path.startswith('@executable_path/'):
            return executable.parent / path[len('@executable_path/'):]
        if path == '@executable_path':
            return executable.parent
        if path.startswith('/'):
            return Path(path)
        return None

    candidates = []
    if value.startswith('@rpath/'):
        suffix = value[len('@rpath/'):]
        for path, owner in [(r, binary) for r in rpaths] + [(r, executable) for r in main_rpaths]:
            expanded = expand(path, owner)
            if expanded is not None:
                candidates.append(expanded / suffix)
    else:
        expanded = expand(value, binary)
        if expanded is not None:
            candidates.append(expanded)
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_relative_to(bundle) and resolved.is_file():
            return {'kind': 'bundled', 'reference': value,
                    'resolved': resolved.relative_to(bundle).as_posix()}
    return {'kind': 'unresolved_or_external', 'reference': value,
            'candidates': [str(p) for p in candidates]}


def tree_inventory(root):
    """Do not recurse into symlink directories; record the link itself."""
    root = Path(root).resolve()
    records = []
    for directory, folders, files in os.walk(root, followlinks=False):
        for name in sorted(folders + files):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                target = os.readlink(path)
                resolved = path.resolve()
                records.append({'path': relative, 'kind': 'symlink', 'target': target,
                                'inside_bundle': resolved.is_relative_to(root),
                                'target_exists': resolved.exists()})
            elif path.is_file():
                records.append({'path': relative, 'kind': 'file',
                                'bytes': path.stat().st_size, 'sha256': digest(path),
                                'mode': oct(path.stat().st_mode & 0o777)})
    return sorted(records, key=lambda item: item['path'])


def audit_bundle(bundle, maximum_os=None, require_adhoc=True, architecture='arm64'):
    validate_architecture(architecture)
    maximum_os = maximum_os or deployment_target(architecture)
    if sys.platform != 'darwin':
        raise RuntimeError('A native macOS host with Apple command-line tools is required')
    bundle = Path(bundle).resolve()
    if bundle.suffix != '.app' or not bundle.is_dir():
        raise ValueError('Expected an existing .app bundle')
    with (bundle / 'Contents/Info.plist').open('rb') as stream:
        info = plistlib.load(stream)
    executable = bundle / 'Contents/MacOS' / str(info['CFBundleExecutable'])
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError('CFBundleExecutable is missing or not executable')
    errors = []
    minimum = info.get('LSMinimumSystemVersion')
    if minimum is None or version_tuple(minimum) != version_tuple(maximum_os):
        errors.append(f'Info.plist minimum system version {minimum!r} differs from selected target {maximum_os}')
    inventory = tree_inventory(bundle)
    for item in inventory:
        if item['kind'] == 'symlink' and (not item['inside_bundle'] or not item['target_exists']):
            errors.append(f'Broken or escaping bundle symlink: {item["path"]}')
    main_load = run(['/usr/bin/otool', '-l', executable])
    if main_load['exit_code']:
        errors.append('otool could not inspect the main executable')
    main_rpaths = parse_load_commands(main_load['stdout'])['rpaths']
    binaries = []
    for item in inventory:
        if item['kind'] != 'file':
            continue
        path = bundle / item['path']
        with path.open('rb') as stream:
            if stream.read(4) not in MACHO_MAGICS:
                continue
        arches = run(['/usr/bin/lipo', '-archs', path])
        load = run(['/usr/bin/otool', '-l', path])
        libraries = run(['/usr/bin/otool', '-L', path])
        signature = run(['/usr/bin/codesign', '--verify', '--strict', '--verbose=2', path])
        parsed = parse_load_commands(load['stdout'])
        architectures = arches['stdout'].split()
        problems = []
        if arches['exit_code'] or architectures != [architecture]:
            problems.append(f'Binary is not a thin {architecture} binary')
        if load['exit_code'] or not parsed['minimum_versions']:
            problems.append('Missing readable Mach-O macOS deployment target')
        elif any(version_tuple(v) > version_tuple(maximum_os) for v in parsed['minimum_versions']):
            problems.append(f'Minimum macOS exceeds {maximum_os}')
        if any(r.startswith('/') and not system_library(r) for r in parsed['rpaths']):
            problems.append('Absolute non-system RPATH leaks a build-machine dependency location')
        if libraries['exit_code']:
            problems.append('Unable to read dynamic library references')
        classified = {'dylib_install_names': [], 'dylib_load_commands': []}
        try:
            classified = classify_dylib_references(load['stdout'], libraries['stdout'])
        except ValueError as error:
            problems.append('Unable to classify dynamic library references: ' + str(error))
        dependencies = [resolve_dependency(item['reference'], path, executable, bundle,
                        parsed['rpaths'], main_rpaths) for item in classified['dylib_load_commands']]
        if any(d['kind'] == 'unresolved_or_external' for d in dependencies):
            problems.append('Unresolved or non-system external dynamic library reference')
        if signature['exit_code']:
            problems.append('Mach-O code signature failed strict verification')
        binaries.append({**item, 'architectures': architectures, **parsed, **classified,
                         'dependencies': dependencies, 'signature_verification': signature,
                         'errors': problems})
        errors.extend(f'{item["path"]}: {problem}' for problem in problems)
    if not binaries:
        errors.append('No Mach-O binaries found')
    unwanted_qt = sorted({Path(b['path']).name for b in binaries
                         if Path(b['path']).name.startswith(('QtWebEngine', 'QtQml', 'QtQuick'))})
    if unwanted_qt:
        errors.append('Unexpected web/QML runtime modules in raster workstation: ' + ', '.join(unwanted_qt))
    deep = run(['/usr/bin/codesign', '--verify', '--deep', '--strict', '--verbose=2', bundle])
    details = run(['/usr/bin/codesign', '--display', '--verbose=4', bundle])
    if deep['exit_code']:
        errors.append('Whole bundle deep/strict signature verification failed')
    adhoc = 'Signature=adhoc' in details['stderr'] + details['stdout']
    if details['exit_code'] or (require_adhoc and not adhoc):
        errors.append('Expected explicit ad-hoc signature; no Developer ID/notarization claim permitted')
    return {'schema_version': 'cas-macos-audit-1.0',
            'status': 'PASS' if not errors else 'FAIL', 'errors': errors,
            'generated_utc': datetime.now(timezone.utc).isoformat(), 'bundle': str(bundle),
            'audit_execution': execution_identity(), 'target_architecture': architecture,
            'native_intel_runtime_tested': False,
            'compatibility_target': maximum_os, 'actual_macos12_runtime_tested': False,
            'physician_gatekeeper_first_open_verified': False,
            'bundle_version': info.get('CFBundleShortVersionString'),
            'bundle_identifier': info.get('CFBundleIdentifier'),
            'minimum_system_version': minimum, 'signature_kind': 'ad-hoc' if adhoc else 'other',
            'notarized': False, 'mach_o_count': len(binaries),
            'binaries': binaries, 'bundle_inventory': inventory,
            'bundle_signature_verification': deep, 'bundle_signature_details': details}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--maximum-os', default=None)
    parser.add_argument('--architecture', required=True, choices=sorted(ARCHITECTURES))
    args = parser.parse_args()
    report = audit_bundle(args.app, args.maximum_os, architecture=args.architecture)
    write_json(args.output, report)
    print(json.dumps({k: report[k] for k in ('status', 'errors', 'mach_o_count', 'compatibility_target')}, indent=2))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
