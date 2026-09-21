"""Verify two independent native GUI processes and their actual workflow reports."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

COMMON={'declared_synthetic_input','interactive_platform','shared_source_project',
        'language_preserves_state_en','language_preserves_state_zh_CN',
        'accessible_language_en','accessible_language_zh_CN','qt_standard_dialog_translation_loaded','normal_close_accepted','normal_close_saved'}
REQUIRED={
    'create':COMMON|{'first_launch_is_english','fresh_database_has_no_labels','path_is_in_scope',
        'nondegenerate_path','apply_negative','apply_positive','both_formal_findings_saved','unapplied_draft_present'},
    'resume':COMMON|{'expected_case_project_reader_restored','formal_records_restored_exactly',
        'unapplied_draft_restored_exactly','language_restored_after_independent_process',
        'saved_native_anchors_match_mapping','native_return_selects_correct_axial_slice',
        'native_back_location_subvoxel','batch_export_complete','export_did_not_apply_draft'}}

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def within(root,relative):
    path=(root/relative).resolve()
    if not path.is_relative_to(root):raise ValueError('Unsafe evidence path: '+relative)
    if os.name=='nt' and not str(path).startswith('\\\\?\\'):
        value=str(path);path=Path('\\\\?\\UNC\\'+value[2:] if value.startswith('\\\\') else '\\\\?\\'+value)
    if not path.is_file():raise ValueError('Missing evidence path: '+relative)
    return path

def run_workflow(command,package,output,*,case='single',cwd=None,env=None,qt_platform='windows',frozen=False):
    out=Path(output).resolve();out.mkdir(parents=True,exist_ok=False)
    package=Path(package).resolve();db=out/'workspace/annotations.sqlite'
    environment=dict(os.environ if env is None else env)
    environment.update(QT_QPA_PLATFORM=qt_platform,PYTHONIOENCODING='utf-8')
    reports=[]
    for phase,language in [('create','zh_CN'),('resume','en')]:
        args=[*map(str,command),'--reader','RELEASE-QA','--db',str(db),
            '--workflow-smoke-output',str(out/phase),'--workflow-smoke-phase',phase,'--workflow-smoke-language',language]
        if phase=='create':args+=['--package',str(package),'--case',case]
        result=subprocess.run(args,cwd=cwd,env=environment,capture_output=True,timeout=180,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        (out/(phase+'.stdout.log')).write_bytes(result.stdout)
        (out/(phase+'.stderr.log')).write_bytes(result.stderr)
        if result.returncode:raise RuntimeError(f'{phase} process failed: exit {result.returncode}; see {out}')
        folder=out/phase;report=json.loads((folder/'workflow-report.json').read_text(encoding='utf-8'))
        for key,value in {'schema_version':'cas-workflow-smoke-1.0','status':'PASS','phase':phase,'version':'0.1.0',
                          'qt_platform':qt_platform,'frozen':frozen,'synthetic_only':True,'exit_code':0}.items():
            if report.get(key)!=value:raise ValueError(f'{phase}: incorrect {key}')
        checks=report.get('checks',[])
        if not checks or not all(c['passed'] is True for c in checks):raise ValueError(f'{phase}: incomplete/failed assertions')
        missing=REQUIRED[phase]-{c['name'] for c in checks}
        if missing:raise ValueError(f'{phase}: missing required assertions {missing}')
        if qt_platform=='windows' and report.get('machine','').lower() not in ('amd64','x86_64','win-amd64'):
            raise ValueError('Windows x64 execution identity is missing')
        shots=report.get('screenshots',[])
        if len(shots)<4:raise ValueError('Missing bilingual window screenshots')
        for shot in shots:
            if digest(within(folder,shot['path']))!=shot['sha256']:raise ValueError('Screenshot checksum differs')
        if phase=='resume':
            if not 0<=report.get('maximum_saved_anchor_index_error_voxel',float('inf'))<=.1:
                raise ValueError('Saved native geometry exceeded 0.1 voxel')
            manifest_path=within(folder,report['export_manifest'])
            manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
            if manifest.get('schema_version')!='cas-batch-export-1.0' or not manifest.get('project_id'):
                raise ValueError('Missing project-bound batch export')
            for entry in manifest['files']:
                p=within(manifest_path.parent,entry['path'])
                if p.stat().st_size!=entry['bytes'] or digest(p)!=entry['sha256']:raise ValueError('Export checksum differs')
        reports.append({'phase':phase,'exit_code':result.returncode,'checks':len(checks),
            'report_sha256':digest(folder/'workflow-report.json')})
    summary={'status':'PASS','version':'0.1.0','qt_platform':qt_platform,'frozen':frozen,'case':case,
             'sessions':reports,'synthetic_only':True,'clinical_acceptance':False}
    (out/'workflow-verification.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    return summary

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--package',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--case',default='single')
    p.add_argument('--executable',type=Path);a=p.parse_args()
    root=Path(__file__).resolve().parents[1]
    command=[a.executable.resolve()] if a.executable else [sys.executable,'-m','annotation_app']
    print(json.dumps(run_workflow(command,a.package,a.output,case=a.case,cwd=root,
        qt_platform='windows' if os.name=='nt' else 'cocoa',frozen=bool(a.executable)),indent=2))

if __name__=='__main__':main()
