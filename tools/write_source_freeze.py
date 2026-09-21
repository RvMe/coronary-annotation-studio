"""Export exact reviewed Git bytes for builds on hosts without that checkout.

Run after committing the common source; write the manifest outside the checkout.
Git's revision alone is not a byte manifest (checkout line endings may differ).
"""
from pathlib import Path, PurePosixPath
import argparse
import json
import subprocess
from audit_macos import digest


def source_path(value):
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or '..' in path.parts or ':' in value
            or '\\' in value or path.as_posix() != value or value == '.'):
        raise ValueError('Unsafe or noncanonical source path: ' + value)
    return path


def create_freeze(root):
    def git(*args):
        return subprocess.run(['git', *args], cwd=root, check=True, stdout=subprocess.PIPE).stdout
    if git('status', '--porcelain', '--untracked-files=normal').strip():
        raise ValueError('Commit all reviewed source before freezing')
    revision = git('rev-parse', 'HEAD').decode('ascii').strip()
    records = []
    seen = set()
    for value in git('ls-files', '-z').decode('utf-8').split('\0'):
        if not value:
            continue
        relative = source_path(value)
        if value.casefold() in seen:
            raise ValueError('Source paths collide on a case-insensitive filesystem')
        seen.add(value.casefold())
        path = root / relative
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError('Tracked source must be a contained regular file: ' + value)
        records.append({'path': value, 'bytes': path.stat().st_size, 'sha256': digest(path)})
    if not records or git('status', '--porcelain', '--untracked-files=normal').strip():
        raise ValueError('Source changed or is empty')
    return {'schema_version': 'cas-source-freeze-1.0', 'version': '0.1.0',
            'git_revision': revision, 'files': records,
            'note': 'Exact checkout bytes; transport without line-ending conversion.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root, output = args.root.resolve(), args.output.resolve()
    if output.is_relative_to(root):
        raise ValueError('Freeze manifest must be outside the checkout')
    data = create_freeze(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(data, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'git_revision': data['git_revision'], 'files': len(data['files']),
                      'manifest_sha256': digest(output)}))


if __name__ == '__main__':
    main()
