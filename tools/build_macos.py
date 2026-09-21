"""Build exactly one Mac architecture from a coordinator-frozen source manifest."""
from pathlib import Path,PurePosixPath
import argparse,json,os,platform,re,subprocess,sys,time
from audit_macos import audit_bundle,digest,write_json,execution_identity,deployment_target
from verify_compiled_sources import verify as verify_compiled
from write_source_freeze import source_path


def verify_freeze(root,manifest):
    root=Path(root).resolve()
    data=json.loads(Path(manifest).read_text(encoding='utf-8-sig'))
    if not re.fullmatch(r'[0-9a-f]{40}',data.get('git_revision','')):raise ValueError('Freeze must identify the coordinator Git revision')
    records=data.get('files',data.get('source_files'));seen=set()
    if not isinstance(records,list) or not records:raise ValueError('Freeze must contain exact source file hashes')
    for item in records:
        name=item['path'];rel=source_path(name)
        if rel.is_absolute() or '..' in rel.parts or ':' in name or '\\' in name or name.casefold() in seen:raise ValueError('Unsafe/duplicate frozen source path')
        seen.add(name.casefold());p=root/name
        if p.is_symlink() or not p.is_file() or not p.resolve().is_relative_to(root) or p.stat().st_size!=item['bytes'] or digest(p)!=item['sha256']:
            raise ValueError('Frozen source mismatch: '+name)
    required={p.relative_to(root).as_posix().casefold() for folder in ['annotation_app','tools'] for p in (root/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix in {'.py','.spec','.ts','.qm','.json'}}
    if not required<=seen:raise ValueError('Freeze omits build/runtime source: '+str(sorted(required-seen)))
    return data,records


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--architecture',choices=['arm64','x86_64'],required=True)
    p.add_argument('--repo-root',type=Path,default=Path(__file__).resolve().parents[1])
    for option in ['source-freeze','output','work','wheel-lock','wheelhouse']:p.add_argument('--'+option,type=Path,required=True)
    a=p.parse_args();root=a.repo_root.resolve();out=a.output.resolve();work=a.work.resolve()
    if sys.platform!='darwin' or platform.machine()!=a.architecture:raise RuntimeError('Use matching Mac Python architecture')
    if out.exists() or work.exists():raise FileExistsError('Build output and work must be new')
    if out==work or out.is_relative_to(work) or work.is_relative_to(out):raise ValueError('Separate output and work directories required')
    freeze,records=verify_freeze(root,a.source_freeze);out.mkdir(parents=True)
    env=dict(os.environ)
    for key in ['PYTHONPATH','PYTHONHOME','QT_PLUGIN_PATH','QT_QPA_PLATFORM_PLUGIN_PATH','DYLD_LIBRARY_PATH','DYLD_FRAMEWORK_PATH']:env.pop(key,None)
    env.update(PYTHONDONTWRITEBYTECODE='1',MACOSX_DEPLOYMENT_TARGET=deployment_target(a.architecture),CAS_BUILD_ARCH=a.architecture,
               PATH=str(Path(sys.executable).parent)+':/usr/bin:/bin:/usr/sbin:/sbin')
    report={'schema_version':'cas-mac-build-1.0','status':'FAIL','architecture':a.architecture,'deployment_target':deployment_target(a.architecture),'version':'0.1.0','source_freeze_sha256':digest(a.source_freeze),
            'git_revision':freeze['git_revision'],'source':records,'execution':execution_identity(),'notarized':False,'physical_intel_tested':False,'macOS12_runtime_tested':False,
            'external_build_tool_files':[{'path':name,'sha256':digest(Path(__file__).parent/name)} for name in ['build_macos.py','audit_macos.py','write_source_freeze.py','verify_compiled_sources.py']]}
    started=time.monotonic()
    try:
        command=[sys.executable,'-B',str(root/'tools/mac_build_preflight.py'),'--architecture',a.architecture,'--wheel-lock',str(a.wheel_lock.resolve()),'--wheelhouse',str(a.wheelhouse.resolve()),'--output',str(out/'preflight')]
        with (out/'preflight.log').open('x') as log:result=subprocess.run(command,cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT)
        if result.returncode:raise RuntimeError('Build preflight failed')
        command=[sys.executable,'-I','-m','PyInstaller','--noconfirm','--distpath',str(out/'dist'),'--workpath',str(work),str(root/'tools/macos.spec')]
        report['command']=command
        with (out/'pyinstaller.log').open('x') as log:result=subprocess.run(command,cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT)
        report['build_exit_code']=result.returncode
        if result.returncode:raise RuntimeError('PyInstaller failed')
        bundle=out/'dist/Coronary Annotation Studio.app';audit=audit_bundle(bundle,architecture=a.architecture)
        write_json(out/'macos_binary_audit.json',audit)
        if audit['status']!='PASS' or audit['bundle_identifier']!='org.coronaryannotationstudio.desktop' or audit['bundle_version']!='0.1.0':raise RuntimeError('Binary audit, identity or version failed')
        compiled=verify_compiled(bundle/'Contents/MacOS/CoronaryAnnotationStudio',root)
        write_json(out/'compiled_source_verification.json',compiled)
        if compiled['status']!='PASS':raise RuntimeError('Embedded code differs from reviewed source')
        resources=bundle/'Contents/Resources/annotation_app/translations'
        for name in ['cas_zh_CN.qm','cas_zh_CN.ts','legacy_error_sources.json']:
            if digest(resources/name)!=digest(root/'annotation_app/translations'/name):raise RuntimeError('Bundled translation resource differs: '+name)
        verify_freeze(root,a.source_freeze)
        report.update(status='PASS',bundle=str(bundle),audit_sha256=digest(out/'macos_binary_audit.json'),
                      compiled_source_report_sha256=digest(out/'compiled_source_verification.json'),preflight_sha256=digest(out/'preflight/preflight.json'))
    except Exception as exc:report['error']=repr(exc)
    report['elapsed_seconds']=round(time.monotonic()-started,3);write_json(out/'build_report.json',report)
    print(json.dumps({k:v for k,v in report.items() if k not in ['source','command']},indent=2))
    return 0 if report['status']=='PASS' else 2


if __name__=='__main__':raise SystemExit(main())
