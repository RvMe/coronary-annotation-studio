"""Path-only public JSON derivatives with immutable original-report identities."""
from pathlib import Path
from types import CodeType
import hashlib
import json
import marshal
import re
import sys
import tempfile
import zipfile
from audit_macos import digest, write_json

PRIVATE_HOME = re.compile(r'/[U]sers/[^/\s\"\'<>]+|[A-Za-z]:[\\/]Users[\\/][^\\/\s\"\'<>]+')


def host_roots(**paths):
    values = {'USER_HOME': Path.home(), 'BUILD_VENV': Path(sys.prefix),
              'PYTHON_RUNTIME': Path(sys.base_prefix), 'HOST_TEMP': Path(tempfile.gettempdir())}
    values.update(paths)
    # Broad filesystem roots must never be redacted: system/official dependency
    # locations and remote URLs remain useful evidence.
    result = {}
    for token, path in values.items():
        value = str(Path(path).resolve())
        if len(Path(value).parts) < 3:
            raise ValueError('Refuse an overbroad redaction root')
        result[value] = '<' + token + '>'
    return result


def sanitize(value, roots):
    ordered = sorted(roots.items(), key=lambda pair: len(pair[0]), reverse=True)
    def text(item):
        for source, token in ordered:
            # Require a path-component boundary so /project does not alter
            # /project-other; hashes, versions and unrelated strings are intact.
            item = re.sub(re.escape(source) + r'(?=$|[/\\\s\"\'\):,])', lambda match: token, item)
        return PRIVATE_HOME.sub('<USER_HOME>', item)
    def walk(item):
        if isinstance(item, str):
            return text(item)
        if isinstance(item, list):
            return [walk(v) for v in item]
        if isinstance(item, dict):
            result = {}
            for key, nested in item.items():
                key = text(key)
                if key in result:
                    raise ValueError('Redaction would merge distinct JSON keys')
                result[key] = walk(nested)
            return result
        return item
    return walk(value)


def public_json(source, destination, roots):
    source, destination = Path(source), Path(destination)
    if destination.exists() or destination.resolve() == source.resolve():
        raise FileExistsError('Public derivative requires a new destination')
    raw = source.read_bytes()
    original = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(original, dict):
        raise ValueError('Evidence must be a JSON object')
    result = sanitize(original, roots)
    if 'public_evidence' in result:
        raise ValueError('Do not recursively sanitize an existing derivative')
    result['public_evidence'] = {
        'schema_version': 'cas-public-evidence-1.0', 'sanitized_derivative': True,
        'original_report_sha256': hashlib.sha256(raw).hexdigest(), 'original_report_bytes': len(raw),
        'method': 'Private host path roots replaced by neutral tokens; nested hashes and non-path values retained.',
        'neutral_root_tokens': sorted(set(roots.values()) | {'<USER_HOME>'})}
    if PRIVATE_HOME.search(json.dumps(result, ensure_ascii=False)):
        raise ValueError('Public derivative still contains a private home path')
    write_json(destination, result)
    if source.read_bytes() != raw:
        raise ValueError('Original evidence changed during sanitization')
    return result


def audit_compiled_filenames(executable, base_archives=()):
    """Read code objects without executing the application or altering code."""
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(str(executable))
    pyz = archive.open_embedded_archive('PYZ.pyz')
    problems, modules, count = [], [], 0
    def visit(code, module):
        nonlocal count
        if not isinstance(code, CodeType):
            return
        count += 1
        if PRIVATE_HOME.search(code.co_filename):
            problems.append({'module': module, 'filename': code.co_filename})
        for value in code.co_consts:
            if isinstance(value, CodeType):
                visit(value, module)
    for name in pyz.toc:
        code = pyz.extract(name)
        visit(code, name)
        if isinstance(code, CodeType):
            modules.append(name)
    for name, item in archive.toc.items():
        if item[-1] == 's':
            visit(marshal.loads(archive.extract(name)), name)
            modules.append(name)
    for path in sorted({Path(p).resolve() for p in base_archives}):
        with zipfile.ZipFile(path) as library:
            for name in library.namelist():
                if name.endswith('.pyc'):
                    visit(marshal.loads(library.read(name)[16:]), 'base_library:' + name)
    return {'schema_version': 'cas-compiled-filename-audit-1.0',
            'status': 'PASS' if not problems else 'FAIL', 'module_count': len(modules),
            'code_objects_examined': count, 'problems': problems,
            'method': 'Read every PYZ, entrypoint and supplied base-library code filename without execution.',
            'executable_sha256': digest(executable)}


def audit_public_text(payload, roots):
    """Check all public JSON/Markdown/text evidence, including source copies."""
    failures = []
    for path in sorted(Path(payload).rglob('*')):
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in {'.json', '.md', '.txt', '.log'}:
            continue
        try:
            text = path.read_text(encoding='utf-8-sig')
        except UnicodeDecodeError:
            continue
        if PRIVATE_HOME.search(text) or any(root in text for root in roots):
            failures.append(path.relative_to(payload).as_posix())
    if failures:
        raise ValueError('Private host paths in public payload: ' + ', '.join(failures))
