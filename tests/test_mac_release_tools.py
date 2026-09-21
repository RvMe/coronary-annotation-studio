"""Release gates reject mismatched source, escaping archives and false provenance."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from audit_macos import digest, classify_dylib_references, tree_inventory, deployment_target
from assemble_macos import make_verified_zip, verify_runtime_binding
from build_macos import verify_freeze
from mac_licenses import validate_python_license_proof
from mac_build_preflight import wheel_supports
from verify_macos_workflow import safe_archive, same_inventory
from write_source_freeze import create_freeze, source_path
from public_macos_evidence import public_json,sanitize,audit_public_text


class MacReleaseTools(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def write(self, name, contents):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding='utf-8')
        return path

    def freeze(self):
        self.write('repo/annotation_app/app.py', 'answer=42\n')
        self.write('repo/tools/macos.spec', 'a=1\n')
        root = self.root / 'repo'
        records = [{'path': p.relative_to(root).as_posix(), 'bytes': p.stat().st_size, 'sha256': digest(p)}
                   for p in sorted(root.rglob('*')) if p.is_file()]
        data = {'git_revision': 'a' * 40, 'files': records}
        manifest = self.write('freeze.json', json.dumps(data))
        return root, manifest, data

    def test_frozen_exact_bytes_accepted(self):
        root, manifest, data = self.freeze()
        self.assertEqual(verify_freeze(root, manifest)[1], data['files'])

    def test_later_source_materials_may_add_tools_only(self):
        _, _, data = self.freeze()
        verify_runtime_binding(data['files'], data['files'] + [{'path': 'tools/new.py', 'bytes': 4, 'sha256': 'abcd'}])

    def test_later_source_materials_cannot_change_runtime_or_spec(self):
        _, _, data = self.freeze()
        for index in range(len(data['files'])):
            changed = [dict(v) for v in data['files']]
            changed[index]['sha256'] = 'different'
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, 'Runtime or build spec differs'):
                verify_runtime_binding(data['files'], changed)
        with self.assertRaises(ValueError):
            verify_runtime_binding(data['files'], data['files'] + [data['files'][0]])

    def test_frozen_file_change_rejected(self):
        root, manifest, _ = self.freeze()
        (root / 'annotation_app/app.py').write_bytes(b'answer=43\n')
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            verify_freeze(root, manifest)

    def test_added_runtime_source_rejected(self):
        root, manifest, _ = self.freeze()
        (root / 'annotation_app/new.py').write_bytes(b'x=1\n')
        with self.assertRaisesRegex(ValueError, 'omits'):
            verify_freeze(root, manifest)

    def test_duplicate_casefold_source_rejected(self):
        root, manifest, data = self.freeze()
        data['files'].append(dict(data['files'][0]))
        manifest.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            verify_freeze(root, manifest)

    def test_freeze_requires_real_revision_format(self):
        root, manifest, data = self.freeze()
        data['git_revision'] = 'working-tree'
        manifest.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Git revision'):
            verify_freeze(root, manifest)

    def test_noncanonical_source_paths_rejected(self):
        for name in ('', '.', '/tmp/file', '../file', 'A/../file', 'A//file', './file', 'A\\file', 'C:file'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                source_path(name)

    def test_freeze_producer_requires_clean_commit(self):
        root = self.root / 'gitrepo'
        root.mkdir()
        def git(*args):
            return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True).stdout
        git('init')
        (root / 'app.py').write_bytes(b'answer=42\n')
        git('add', 'app.py')
        git('-c', 'user.name=CAS QA', '-c', 'user.email=qa@example.invalid', 'commit', '-m', 'synthetic gate fixture')
        data = create_freeze(root)
        self.assertEqual(data['git_revision'], git('rev-parse', 'HEAD').decode().strip())
        self.assertEqual(data['files'][0]['sha256'], digest(root / 'app.py'))
        (root / 'app.py').write_bytes(b'answer=43\n')
        with self.assertRaisesRegex(ValueError, 'Commit'):
            create_freeze(root)

    def archive(self, members):
        path = self.root / 'sample.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            for name, contents, symlink in members:
                item = zipfile.ZipInfo(name)
                item.filename = name
                item.create_system = 3
                item.external_attr = (0o120777 if symlink else 0o100644) << 16
                archive.writestr(item, contents)
        return path

    def test_archive_framework_links_accepted(self):
        archive = self.archive([('App/F/Versions/A/F', 'binary', False),
                                ('App/F/Versions/Current', 'A', True),
                                ('App/F/F', 'Versions/Current/F', True)])
        safe_archive(archive)

    def test_archive_traversal_rejected(self):
        for name in ('../outside', '/outside', 'C:outside', 'App/../outside', 'App\\outside', 'App//file'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                safe_archive(self.archive([(name, 'data', False)]))

    def test_archive_case_collision_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            safe_archive(self.archive([('App/a', '1', False), ('App/A', '2', False)]))

    def test_archive_escape_link_rejected(self):
        with self.assertRaisesRegex(ValueError, 'escapes'):
            safe_archive(self.archive([('App/link', '../../outside', True)]))

    def test_archive_nul_truncation_rejected(self):
        with self.assertRaisesRegex(ValueError, 'normalized or truncated'):
            safe_archive(self.archive([('App/a\x00hidden', 'data', False)]))

    def test_archive_symlink_cycle_rejected(self):
        with self.assertRaisesRegex(ValueError, 'cycle'):
            safe_archive(self.archive([('App/a', 'b', True), ('App/b', 'a', True)]))

    def test_archive_files_below_alias_rejected(self):
        with self.assertRaisesRegex(ValueError, 'below a symlink'):
            safe_archive(self.archive([('App/link', 'target', True), ('App/link/file', 'data', False)]))

    def test_archive_chain_escape_rejected(self):
        with self.assertRaisesRegex(ValueError, 'chain escapes'):
            safe_archive(self.archive([('App/deep/a', '../..', True), ('App/deep/b', 'a/../outside', True)]))

    def test_inventory_duplicate_and_mode_change_rejected(self):
        original = [{'path': 'a', 'kind': 'file', 'mode': '0o755', 'sha256': 'a'}]
        self.assertFalse(same_inventory(original * 2, original))
        changed = [dict(original[0], mode='0o644')]
        self.assertFalse(same_inventory(original, changed))

    def test_public_evidence_preserves_original_hashes_and_bytes(self):
        private = '/' + 'Users' + '/qa-person/build'
        data = {'version': '0.1.0', 'sha256': 'f' * 64, 'command': [private + '/app', '--flag'],
                'native': {'sha256': 'a' * 64}, 'url': 'https://github.com/vendor/releases/download/file'}
        source = self.write('raw.json', json.dumps(data))
        before = source.read_bytes()
        target = self.root / 'public.json'
        result = public_json(source, target, {private: '<BUILD_ROOT>'})
        self.assertEqual(result['command'], ['<BUILD_ROOT>/app', '--flag'])
        self.assertEqual(result['sha256'], data['sha256'])
        self.assertEqual(result['native'], data['native'])
        self.assertEqual(result['url'], data['url'])
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(result['public_evidence']['original_report_sha256'], digest(source))

    def test_public_evidence_refuses_overwrite_or_source(self):
        source = self.write('raw.json', '{"status":"PASS"}')
        with self.assertRaises(FileExistsError):
            public_json(source, source, {})
        target = self.write('public.json', 'existing')
        with self.assertRaises(FileExistsError):
            public_json(source, target, {})

    def test_public_path_redaction_has_component_boundaries(self):
        roots = {'/build/project': '<SOURCE_ROOT>'}
        result = sanitize(['/build/project/a.py', '/build/project-other/a.py', '/usr/lib/libSystem.B.dylib'], roots)
        self.assertEqual(result, ['<SOURCE_ROOT>/a.py', '/build/project-other/a.py', '/usr/lib/libSystem.B.dylib'])

    def test_public_redaction_handles_windows_home(self):
        private = 'C:' + chr(92) + 'Users' + chr(92) + 'qa-person' + chr(92) + 'project'
        self.assertEqual(sanitize({'path': private}, {})['path'], '<USER_HOME>' + chr(92) + 'project')

    def test_public_path_audit_rejects_unredacted_payload(self):
        private = '/' + 'Users' + '/qa-person/source/app.py'
        self.write('payload/report.json', json.dumps({'path': private}))
        with self.assertRaisesRegex(ValueError, 'Private host paths'):
            audit_public_text(self.root / 'payload', {})

    def test_public_redaction_rejects_key_collision(self):
        first = '/' + 'Users' + '/person-a'
        second = '/' + 'Users' + '/person-b'
        with self.assertRaisesRegex(ValueError, 'merge distinct JSON keys'):
            sanitize({first: 1, second: 2}, {})

    @unittest.skipUnless(sys.platform == 'darwin', 'Native ditto test requires macOS')
    def test_native_zip_preserves_modes_links_and_refuses_overwrite(self):
        payload = self.root / 'payload'
        binary = self.write('payload/Example.app/Contents/MacOS/example', 'synthetic executable fixture\n')
        binary.chmod(0o755)
        (payload / 'alias').symlink_to('Example.app')
        archive, extracted = self.root / 'delivery.zip', self.root / 'extract'
        before = tree_inventory(payload)
        result = make_verified_zip(payload, archive, extracted)
        self.assertTrue(result['reextracted_inventory_equal'])
        actual = [x for x in tree_inventory(extracted / 'payload') if x['path'] != 'FILE-MANIFEST.json']
        self.assertTrue(same_inventory(before, actual))
        with self.assertRaises(FileExistsError):
            make_verified_zip(payload, archive, extracted)

    def test_wheels_require_target_architecture_and_os12(self):
        self.assertTrue(wheel_supports('a-1-cp311-cp311-macosx_12_0_x86_64.whl', 'x86_64'))
        self.assertTrue(wheel_supports('a-1-py3-none-any.whl', 'arm64'))
        self.assertTrue(wheel_supports('a-1-cp311-cp311-macosx_11_0_universal2.whl', 'arm64'))
        self.assertFalse(wheel_supports('a-1-cp311-cp311-macosx_13_0_arm64.whl', 'arm64'))
        self.assertFalse(wheel_supports('a-1-cp311-cp311-macosx_11_0_arm64.whl', 'x86_64'))

    def test_architecture_specific_deployment_targets(self):
        self.assertEqual(deployment_target('arm64'), '12.3')
        self.assertEqual(deployment_target('x86_64'), '12.0')
        with self.assertRaises(ValueError):
            deployment_target('universal2')

    def test_wheel_minor_os_target_is_architecture_specific(self):
        self.assertTrue(wheel_supports('a-1-cp311-cp311-macosx_12_3_arm64.whl', 'arm64'))
        self.assertFalse(wheel_supports('a-1-cp311-cp311-macosx_12_3_x86_64.whl', 'x86_64'))
        self.assertFalse(wheel_supports('a-1-cp311-cp311-macosx_12_4_arm64.whl', 'arm64'))

    def test_dylib_identity_does_not_hide_equal_named_dependency(self):
        name = '@rpath/libexample.dylib'
        load = f'Load command 1\n cmd LC_ID_DYLIB\n name {name} (offset 24)\nLoad command 2\n cmd LC_LOAD_DYLIB\n name {name} (offset 24)\n'
        libraries = 'example:\n' + f' {name} (compatibility version 1.0.0, current version 1.0.0)\n' * 2
        result = classify_dylib_references(load, libraries)
        self.assertEqual(len(result['dylib_load_commands']), 1)
        with self.assertRaisesRegex(ValueError, 'disagree'):
            classify_dylib_references(load, '\n'.join(libraries.splitlines()[:-1]))

    def license_fixture(self):
        root = self.root / 'licenses'
        base = self.root / 'python'
        self.write('licenses/PYTHON.json', json.dumps({'license_path': 'LICENSE', 'build_info': {}}))
        notice = self.write('licenses/original.txt', 'original notice')
        native = self.write('python/lib/libpython.dylib', 'synthetic native proof fixture')
        proof = {'schema_version': 'imagecasx-python-standalone-licenses-1.0', 'status': 'PASS',
                 'python_version': '3.11.16', 'target_triple': 'x86_64-apple-darwin',
                 'full_and_install_only_native_binaries_identical': True,
                 'install_only_archive': {'sha256': 'archive'}, 'python_metadata_sha256': digest(root / 'PYTHON.json'),
                 'licenses': [{'path': notice.name, 'original_path': 'LICENSE', 'sha256': digest(notice), 'bytes': notice.stat().st_size}],
                 'native_install_files': [{'path': 'lib/libpython.dylib', 'sha256': digest(native), 'bytes': native.stat().st_size}]}
        self.write('licenses/PYTHON_LICENSE_PROVENANCE.json', json.dumps(proof))
        return root, base, {'sha256': 'archive'}

    def test_license_proof_binds_architecture(self):
        root, base, bootstrap = self.license_fixture()
        validate_python_license_proof(root, base, bootstrap, '3.11.16', 'x86_64')
        with self.assertRaisesRegex(ValueError, 'mismatched'):
            validate_python_license_proof(root, base, bootstrap, '3.11.16', 'arm64')

    def test_license_proof_rejects_changed_native_binary(self):
        root, base, bootstrap = self.license_fixture()
        (base / 'lib/libpython.dylib').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'native binary differs'):
            validate_python_license_proof(root, base, bootstrap, '3.11.16', 'x86_64')

    def test_license_proof_rejects_changed_notice(self):
        root, base, bootstrap = self.license_fixture()
        (root / 'original.txt').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'license hash mismatch'):
            validate_python_license_proof(root, base, bootstrap, '3.11.16', 'x86_64')

    def test_license_proof_rejects_different_bootstrap(self):
        root, base, _ = self.license_fixture()
        with self.assertRaisesRegex(ValueError, 'artifact differs'):
            validate_python_license_proof(root, base, {'sha256': 'different'}, '3.11.16', 'x86_64')


if __name__ == '__main__':
    unittest.main(verbosity=2)
