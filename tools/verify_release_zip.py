"""Extract final ZIP into a new directory and execute two independent GUI sessions."""
from pathlib import Path,PurePosixPath
import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import zipfile
from run_workflow_gate import run_workflow

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(8*1024*1024),b''):h.update(block)
    return h.hexdigest()

def main():
    p=argparse.ArgumentParser();p.add_argument('archive',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if os.name!='nt':raise RuntimeError('Windows ZIP verifier; Mac uses native ditto to preserve app metadata')
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    with zipfile.ZipFile(a.archive) as z:
        seen=set()
        for item in z.infolist():
            name=PurePosixPath(item.filename)
            if name.is_absolute() or '..' in name.parts or ':' in item.filename or '\\' in item.filename or item.filename.casefold() in seen:
                raise ValueError('Unsafe or duplicate ZIP path')
            seen.add(item.filename.casefold())
            if (item.external_attr>>16)&0o170000==0o120000:raise ValueError('Windows ZIP symlinks are unsupported')
        z.extractall(out/'extracted')
    candidates=list((out/'extracted').glob('*/FILE-MANIFEST.json'))
    if len(candidates)!=1:raise ValueError('Missing unique release manifest')
    root=candidates[0].parent;manifest=json.loads(candidates[0].read_text(encoding='utf-8'))
    for item in manifest['files']:
        file=(root/item['path']).resolve()
        if not file.is_relative_to(root) or not file.is_file() or file.stat().st_size!=item['bytes'] or sha(file)!=item['sha256']:
            raise ValueError('Release file hash mismatch: '+item['path'])
    exe=root/'app/CoronaryAnnotationStudio.exe'
    env=dict(os.environ);windir=env.get('SystemRoot',r'C:\Windows')
    env['PATH']=str(Path(windir)/'System32')+';'+windir
    for key in ('PYTHONPATH','PYTHONHOME','QT_SCALE_FACTOR','QT_FONT_DPI','QT_PLUGIN_PATH','QT_QPA_PLATFORM_PLUGIN_PATH'):env.pop(key,None)
    env['QT_QPA_PLATFORM']='windows'
    workflow=run_workflow([exe],root/'examples/synthetic-v1',out/'workflow',cwd=exe.parent,
                          env=env,qt_platform='windows',frozen=True)
    report={'status':'PASS','archive_sha256':sha(a.archive),'archive_bytes':a.archive.stat().st_size,'source':'final ZIP re-extraction',
            'platform':platform.platform(),'architecture':platform.machine(),'qt_platform':'windows',
            'sessions':workflow['sessions'],'clinical_acceptance':False,'clean_machine_acceptance':False}
    (out/'zip-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2))

if __name__=='__main__':main()
