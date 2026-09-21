"""Read-only dependency/build-input gate. Does not invoke PyInstaller or install."""
from __future__ import annotations
import argparse
import ctypes
import errno
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import platform
import re
import sys
from datetime import datetime,timezone
from audit_macos import deployment_target

PINS={'PySide6':'6.9.3','PySide6_Essentials':'6.9.3','PySide6_Addons':'6.9.3','shiboken6':'6.9.3',
      'SimpleITK':'2.5.2','numpy':'2.2.6','scipy':'1.15.3','pynrrd':'1.1.3','PyInstaller':'6.18.0'}


def sha256(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(4*1024*1024),b''):value.update(block)
    return value.hexdigest()


def translation_state():
    libc=ctypes.CDLL(None,use_errno=True);fn=libc.sysctlbyname
    fn.argtypes=[ctypes.c_char_p,ctypes.c_void_p,ctypes.POINTER(ctypes.c_size_t),ctypes.c_void_p,ctypes.c_size_t]
    fn.restype=ctypes.c_int;value=ctypes.c_int();size=ctypes.c_size_t(ctypes.sizeof(value))
    if fn(b'sysctl.proc_translated',ctypes.byref(value),ctypes.byref(size),None,0)==0:return value.value
    if ctypes.get_errno()==errno.ENOENT:return 0
    raise OSError(ctypes.get_errno(),'Could not read current process translation state')


def wheel_supports(filename,architecture):
    tags=filename[:-4].rsplit('-',1)[-1].split('.')
    if tags==['any']:return True
    for tag in tags:
        match=re.fullmatch(r'macosx_(\d+)_(\d+)_(arm64|x86_64|universal2)',tag)
        if match and match.group(3) in {architecture,'universal2'} and tuple(map(int,match.group(1,2)))<=tuple(map(int,deployment_target(architecture).split('.'))):return True
    return False


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--architecture',choices=['arm64','x86_64'],required=True)
    p.add_argument('--wheel-lock',type=Path,required=True);p.add_argument('--wheelhouse',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    report={'schema_version':'cas-mac-build-preflight-1.0','status':'FAIL','architecture':args.architecture,
            'built_application':False,'physical_intel_tested':False,'macOS12_runtime_tested':False,'errors':[]}
    try:
        if sys.platform!='darwin' or platform.machine()!=args.architecture:raise RuntimeError('Use a matching macOS Python architecture')
        if sys.version_info[:2]!=(3,11) or sys.prefix==sys.base_prefix:raise RuntimeError('Use an isolated CPython 3.11 environment')
        report.update(python=sys.version,machine=platform.machine(),macOS=platform.mac_ver()[0],proc_translated=translation_state())
        report['dependencies']={name:metadata.version(name) for name in PINS}
        if report['dependencies']!=PINS:raise ValueError('Pinned dependency versions differ')
        lock=json.loads(args.wheel_lock.read_text());records=lock if isinstance(lock,list) else lock.get('files',lock.get('wheels',[]))
        if not records:raise ValueError('Wheel lock must not be empty')
        seen=set();wheels=[]
        for item in records:
            name=item.get('filename',item.get('path',''))
            if Path(name).name!=name or name in seen or not name.endswith('.whl'):raise ValueError('Unsafe or repeated wheel name')
            seen.add(name);path=args.wheelhouse/name
            if not wheel_supports(name,args.architecture):raise ValueError('Incompatible wheel: '+name)
            if path.is_symlink() or not path.is_file() or sha256(path)!=item['sha256'] or path.stat().st_size!=item['bytes']:raise ValueError('Wheel hash/size mismatch: '+name)
            wheels.append({'filename':name,'bytes':path.stat().st_size,'sha256':item['sha256']})
        if seen!={p.name for p in args.wheelhouse.glob('*.whl')}:raise ValueError('Wheelhouse has unlocked or missing files')
        report['wheels']=wheels;report['wheel_lock_sha256']=sha256(args.wheel_lock)
        root=Path(__file__).resolve().parents[1]
        report['source']=[{'path':p.relative_to(root).as_posix(),'bytes':p.stat().st_size,'sha256':sha256(p)}
            for folder in ('annotation_app','tools') for p in sorted((root/folder).rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts and p.suffix in {'.py','.spec','.ts','.qm','.json'}]
        compile((root/'tools/macos.spec').read_text(),'macos.spec','exec')
        resources=('cas_zh_CN.ts','cas_zh_CN.qm','legacy_error_sources.json')
        if not all((root/'annotation_app/translations'/name).is_file() for name in resources):raise ValueError('Translation resource missing')
        report.update(status='PASS',deployment_target=deployment_target(args.architecture),signature_intent='ordinary ad-hoc',notarized=False,
            pending=['integration snapshot freeze','PyInstaller per architecture','Mach-O dependencies/minimum OS/signatures',
                     'frozen create/resume workflow','release ZIP extraction and workflow','third-party license assembly'])
    except Exception as exc:report['errors'].append(str(exc))
    report['utc']=datetime.now(timezone.utc).isoformat()
    (args.output/'preflight.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('source','wheels')},indent=2))
    return 0 if report['status']=='PASS' else 2


if __name__=='__main__':raise SystemExit(main())
