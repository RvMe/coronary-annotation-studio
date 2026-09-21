"""Fail-closed command-line gates; full create/resume runs are separate GUI QA."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT=Path(__file__).resolve().parents[1]


class WorkflowPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)

    def refuse(self,arguments,output=None):
        output=output or self.root/'report'
        result=subprocess.run([sys.executable,'-B','-m','annotation_app',
            '--workflow-smoke-output',str(output),*arguments],cwd=ROOT,
            env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,2,result.stdout+result.stderr)
        report=json.loads((output/'workflow-report.json').read_text())
        self.assertEqual(report['status'],'FAIL');self.assertEqual(report['stage'],'preflight')
        self.assertFalse(report['database_opened'])
        return report

    def test_explicit_database_required_before_opening_window(self):
        report=self.refuse(['--workflow-smoke-phase','create'])
        self.assertIn('isolated --db',report['failure'])
        self.assertEqual(list(self.root.iterdir()),[self.root/'report'])

    def test_missing_phase_is_structured_failure(self):
        self.assertIn('phase',self.refuse([])['failure'])

    def test_existing_database_bytes_are_never_changed(self):
        db=self.root/'keep.sqlite';original=b'non-QA database sentinel';db.write_bytes(original)
        report=self.refuse(['--workflow-smoke-phase','create','--db',str(db),'--package',str(ROOT/'examples/synthetic-v1')])
        self.assertIn('new database',report['failure']);self.assertEqual(db.read_bytes(),original)
        self.assertFalse((self.root/'project.json').exists())

    def test_non_synthetic_metadata_refused_before_database_creation(self):
        package=self.root/'package';package.mkdir()
        (package/'manifest.json').write_text(json.dumps({'schema_version':'cas-package-1.0','project_id':'test',
            'cases':[{'case_id':'case','case_manifest':'case.json'}]}))
        (package/'case.json').write_text(json.dumps({'project_id':'test','case_id':'case','metadata':{'synthetic':False}}))
        db=self.root/'workspace/annotations.sqlite'
        report=self.refuse(['--workflow-smoke-phase','create','--db',str(db),'--package',str(package)])
        self.assertIn('metadata.synthetic=true',report['failure']);self.assertFalse(db.parent.exists())

    def test_missing_resume_files_do_not_create_database(self):
        db=self.root/'workspace/annotations.sqlite'
        report=self.refuse(['--workflow-smoke-phase','resume','--db',str(db)])
        self.assertIn('existing QA database',report['failure']);self.assertFalse(db.parent.exists())

    def test_resume_cannot_substitute_package_for_recovered_session(self):
        report=self.refuse(['--workflow-smoke-phase','resume','--db',str(self.root/'qa.sqlite'),
            '--package',str(ROOT/'examples/synthetic-v1')])
        self.assertIn('saved session',report['failure'])

    def test_output_directory_is_immutable_on_repeated_invocation(self):
        output=self.root/'report';self.refuse(['--workflow-smoke-phase','create'],output)
        before=hashlib.sha256((output/'workflow-report.json').read_bytes()).hexdigest()
        self.refuse(['--workflow-smoke-phase','resume'],output)
        self.assertEqual(hashlib.sha256((output/'workflow-report.json').read_bytes()).hexdigest(),before)


if __name__=='__main__':unittest.main()
