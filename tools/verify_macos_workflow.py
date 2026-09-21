"""Execute the shared synthetic gate in two independent frozen Mac processes."""
from pathlib import Path,PurePosixPath
import argparse,json,os,plistlib,posixpath,subprocess,sys,time,zipfile
from audit_macos import audit_bundle,digest,tree_inventory,write_json,execution_identity,deployment_target


def same_inventory(left,right):
    a={x['path']:x for x in left};b={x['path']:x for x in right}
    return len(a)==len(left) and len(b)==len(right) and a==b


def safe_archive(archive):
    with zipfile.ZipFile(archive) as z:
        names=set();links={};total=0
        for item in z.infolist():
            name=item.filename;path=PurePosixPath(name);total+=item.file_size
            if item.orig_filename!=name:raise ValueError('ZIP member was normalized or truncated')
            canonical=name.rstrip('/')
            if not canonical or path.as_posix()!=canonical or path.is_absolute() or '..' in path.parts or ':' in name or '\\' in name or canonical.casefold() in names or item.flag_bits&1:raise ValueError('Unsafe/duplicate ZIP member')
            names.add(canonical.casefold())
            if item.file_size>1024**3 or total>4*1024**3:raise ValueError('Unexpected ZIP expansion size')
            if (item.external_attr>>16)&0o170000==0o120000:
                target=z.read(item).decode('utf-8')
                resolved=posixpath.normpath(posixpath.join(str(path.parent),target))
                if target.startswith('/') or '\\' in target or ':' in target or resolved=='..' or resolved.startswith('../'):raise ValueError('ZIP symlink escapes payload')
                links[canonical.casefold()]=target
        # A file below a link may redirect extraction even when the link itself
        # appears contained. PyInstaller bundles store actual files only at the
        # real versioned framework paths, never below their alias links.
        for name in names:
            if any(str(parent).casefold() in links for parent in PurePosixPath(name).parents):raise ValueError('ZIP member below a symlink')
        for name,target in links.items():
            pending=list(PurePosixPath(name).parent.parts)+target.split('/');resolved=[];steps=0
            while pending:
                part=pending.pop(0)
                if part in ('','.'):continue
                if part=='..':
                    if not resolved:raise ValueError('ZIP symlink chain escapes payload')
                    resolved.pop();continue
                key='/'.join(resolved+[part]).casefold()
                if key in links:
                    steps+=1
                    if steps>64:raise ValueError('ZIP symlink cycle or excessive chain')
                    pending=links[key].split('/')+pending
                else:resolved.append(part)
        if z.testzip() is not None:raise ValueError('ZIP CRC failure')


def main():
    p=argparse.ArgumentParser(description=__doc__);group=p.add_mutually_exclusive_group(required=True)
    group.add_argument('--app',type=Path);group.add_argument('--release-zip',type=Path)
    p.add_argument('--package',type=Path);p.add_argument('--architecture',choices=['arm64','x86_64'],required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if sys.platform!='darwin':raise RuntimeError('Run Mac workflow verification on macOS')
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    report={'schema_version':'cas-mac-frozen-workflow-1.0','status':'FAIL','architecture':a.architecture,'deployment_target':deployment_target(a.architecture),'sessions':[],
            'execution':execution_identity(),'physical_intel_tested':False,'macOS12_runtime_tested':False,'first_Gatekeeper_tested':False}
    try:
        if a.release_zip:
            safe_archive(a.release_zip);extracted=out/'extracted';extracted.mkdir()
            result=subprocess.run(['/usr/bin/ditto','-x','-k',str(a.release_zip.resolve()),str(extracted)],capture_output=True,text=True)
            if result.returncode:raise RuntimeError('Native ZIP extraction failed: '+result.stderr)
            apps=list(extracted.glob('*/Coronary Annotation Studio.app'))
            if len(apps)!=1:raise ValueError('Expected one CAS application in release ZIP')
            app=apps[0];payload=app.parent;manifest=payload/'FILE-MANIFEST.json'
            records=json.loads(manifest.read_text(encoding='utf-8'))['entries'];actual=[x for x in tree_inventory(payload) if x['path']!='FILE-MANIFEST.json']
            if not same_inventory(records,actual):raise ValueError('ZIP payload inventory/hash/mode/symlink differs')
            package=payload/'examples/synthetic-v1';report['archive_sha256']=digest(a.release_zip)
        else:
            app=a.app.resolve();package=a.package
            if package is None:raise ValueError('Explicit synthetic --package required for direct .app gate')
        inventory=tree_inventory(app);audit=audit_bundle(app,architecture=a.architecture);write_json(out/'binary_audit.json',audit)
        if audit['status']!='PASS' or audit['bundle_version']!='0.1.0' or audit['bundle_identifier']!='org.coronaryannotationstudio.desktop':raise RuntimeError('Frozen binary audit failed')
        info=plistlib.loads((app/'Contents/Info.plist').read_bytes());exe=app/'Contents/MacOS'/info['CFBundleExecutable']
        env=dict(os.environ)
        for key in ['PYTHONPATH','PYTHONHOME','QT_SCALE_FACTOR','QT_FONT_DPI','QT_PLUGIN_PATH','QT_QPA_PLATFORM_PLUGIN_PATH','DYLD_LIBRARY_PATH','DYLD_FRAMEWORK_PATH']:env.pop(key,None)
        env.update(PATH='/usr/bin:/bin:/usr/sbin:/sbin',QT_QPA_PLATFORM='cocoa')
        db=out/'workspace/annotations.sqlite'
        for phase,language in [('create','zh_CN'),('resume','en')]:
            destination=out/phase;command=[str(exe),'--reader','RELEASE-QA','--db',str(db),'--workflow-smoke-output',str(destination),'--workflow-smoke-phase',phase,'--workflow-smoke-language',language]
            if phase=='create':command+=['--package',str(package.resolve()),'--case','single']
            started=time.monotonic()
            row={'phase':phase,'command':command,'exit_code':None};report['sessions'].append(row)
            try:
                with (out/(phase+'.log')).open('x',encoding='utf-8') as log:result=subprocess.run(command,cwd=out,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=180)
            except subprocess.TimeoutExpired:
                row.update(timed_out=True,elapsed_seconds=round(time.monotonic()-started,3));raise
            row.update(exit_code=result.returncode,elapsed_seconds=round(time.monotonic()-started,3))
            if result.returncode:raise RuntimeError('Frozen workflow process failed: '+phase)
            output=destination/'workflow-report.json';data=json.loads(output.read_text(encoding='utf-8'))
            if data.get('status')!='PASS' or data.get('phase')!=phase or data.get('frozen') is not True or data.get('machine')!=a.architecture or data.get('qt_platform')!='cocoa' or data.get('synthetic_only') is not True or data.get('exit_code')!=0:raise ValueError('Wrong/missing frozen workflow evidence: '+phase)
            checks=data.get('checks',[])
            from run_workflow_gate import REQUIRED,within
            required=REQUIRED[phase]
            if not checks or any(x['passed'] is not True for x in checks) or not required<={x['name'] for x in checks}:raise ValueError('Missing or failed workflow checks')
            if len(data.get('screenshots',[]))<4:raise ValueError('Workflow screenshots missing')
            for shot in data['screenshots']:
                file=destination/shot['path']
                if not file.resolve().is_relative_to(destination) or digest(file)!=shot['sha256']:raise ValueError('Screenshot hash mismatch')
            if phase=='resume':
                if not 0<=data.get('maximum_saved_anchor_index_error_voxel',float('inf'))<=.1:raise ValueError('Saved native geometry exceeded 0.1 voxel')
                manifest_path=within(destination,data['export_manifest']);manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
                if manifest.get('schema_version')!='cas-batch-export-1.0' or not manifest.get('project_id') or not manifest.get('files'):raise ValueError('Missing project-bound batch export')
                for item in manifest['files']:
                    file=within(manifest_path.parent,item['path'])
                    if file.stat().st_size!=item['bytes'] or digest(file)!=item['sha256']:raise ValueError('Batch export checksum mismatch')
            row['report_sha256']=digest(output)
        if not same_inventory(inventory,tree_inventory(app)):raise ValueError('App modified during workflow')
        report['status']='PASS'
    except Exception as exc:report['error']=repr(exc)
    write_json(out/'verification.json',report);print(json.dumps(report,indent=2))
    return 0 if report['status']=='PASS' else 2


if __name__=='__main__':raise SystemExit(main())
