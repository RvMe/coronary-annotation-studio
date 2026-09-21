"""Assemble a new Windows release with exact dependency/source materials."""
from pathlib import Path
import argparse
import json
import shutil
import subprocess
import sys
import zipfile
from release_licenses import (digest,write_json,file_record,copy_checked,environment_inventory,
    audit_qt_binaries,bind_qt_to_build_environment,collect_wheel_licenses,collect_conda_runtime_licenses,collect_qt_sources)
from verify_compiled_sources import verify as verify_compiled

def main():
    p=argparse.ArgumentParser();p.add_argument('--app',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--source-cache',type=Path,required=True);a=p.parse_args()
    root=Path(__file__).resolve().parents[1];out=a.output.resolve()
    if out.exists():raise FileExistsError('Choose a new release output')
    payload=out/'CoronaryAnnotationStudio-v0.1.0-windows-x64';payload.mkdir(parents=True)
    app=payload/'app';shutil.copytree(a.app,app)
    compiled=verify_compiled(app/'CoronaryAnnotationStudio.exe',root)
    if compiled['status']!='PASS':raise ValueError('Final executable differs from reviewed application source')
    write_json(payload/'COMPILED-SOURCE-VERIFICATION.json',compiled)
    audit=audit_qt_binaries(app);bind_qt_to_build_environment(app,audit)
    inventory=environment_inventory();inventory['qt_binary_audit']=audit
    inventory['wheel_license_records']=collect_wheel_licenses(payload,inventory)
    inventory['native_runtime']=collect_conda_runtime_licenses(app,payload)
    runtime=inventory['native_runtime']
    if runtime.get('unmatched_nonwheel_dlls_require_review') or runtime.get('missing_local_license_text_packages'):
        write_json(out/'DEPENDENCY-FAILURE.json',inventory)
        raise ValueError('Incomplete native binary provenance/license materials')
    inventory['corresponding_qt_sources']=collect_qt_sources(payload,inventory,a.source_cache)
    write_json(payload/'DEPENDENCIES.json',inventory)
    for name in ('LICENSE','NOTICE','README.md','THIRD_PARTY_NOTICES.md','CITATION.cff','CHANGELOG.md'):
        copy_checked(root/name,payload/name)
    shutil.copytree(root/'examples/synthetic-v1',payload/'examples/synthetic-v1')
    shutil.copytree(root/'docs',payload/'docs')
    # Only explicitly reviewed, tracked source files enter the corresponding source snapshot.
    result=subprocess.run(['git','ls-files','-z'],cwd=root,check=True,stdout=subprocess.PIPE)
    sourcefiles=[]
    for name in result.stdout.decode().split('\0'):
        if not name:continue
        path=root/name
        if not path.is_file() or path.is_symlink():raise ValueError(f'Unsafe tracked source: {name}')
        copy_checked(path,payload/'source'/name)
        sourcefiles.append(file_record(path,root))
    if not sourcefiles:raise ValueError('No reviewed source staged in Git')
    revision=subprocess.run(['git','rev-parse','HEAD'],cwd=root,check=True,capture_output=True,text=True).stdout.strip()
    write_json(payload/'SOURCE-VERSION.json',{'version':'0.1.0','git_revision':revision,'source_files':sourcefiles})
    write_json(payload/'FILE-MANIFEST.json',{'schema_version':'cas-release-files-1.0','files':[file_record(p,payload) for p in sorted(payload.rglob('*')) if p.is_file()]})
    archive=out/(payload.name+'.zip')
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for path in sorted(payload.rglob('*')):
            if path.is_file():z.write(path,path.relative_to(out).as_posix())
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None:raise ValueError('ZIP CRC failure')
    report={'status':'ASSEMBLED_NOT_ACCEPTED','archive':archive.name,'bytes':archive.stat().st_size,'sha256':digest(archive),
            'version':'0.1.0','target':'windows-x64','final_extracted_workflow':'pending'}
    write_json(out/'ASSEMBLY.json',report);print(json.dumps(report,indent=2))

if __name__=='__main__':main()
