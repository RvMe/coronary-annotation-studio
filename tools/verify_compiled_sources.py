"""Compare code embedded in the final executable with reviewed source, without running it."""
import argparse
import hashlib
import json
import marshal
from pathlib import Path
from types import CodeType
from PyInstaller.archive.readers import CArchiveReader

def equivalent(a,b):
    if not isinstance(a,CodeType) or not isinstance(b,CodeType):return type(a) is type(b) and a==b
    for name in dir(a):
        if not name.startswith('co_') or callable(getattr(a,name)):continue
        left,right=getattr(a,name),getattr(b,name)
        if name=='co_consts':
            if len(left)!=len(right) or not all(equivalent(x,y) for x,y in zip(left,right)):return False
        elif left!=right:return False
    return True

def verify(executable,root):
    archive=CArchiveReader(str(executable));pyz=archive.open_embedded_archive('PYZ.pyz')
    records=[]
    expected={'.'.join(p.relative_to(root).with_suffix('').parts).removesuffix('.__init__'):p
              for p in (root/'annotation_app').rglob('*.py') if p.name not in ('launch.py','__main__.py')}
    bundled={name for name in pyz.toc if name=='annotation_app' or name.startswith('annotation_app.')}
    if set(expected)!=bundled:
        raise ValueError(f'Application module inventory differs: missing={set(expected)-bundled}, extra={bundled-set(expected)}')
    for name,path in sorted(expected.items()):
        source=path.read_bytes();built=pyz.extract(name)
        current=compile(source,built.co_filename,'exec',dont_inherit=True,optimize=0)
        records.append({'module':name,'source_sha256':hashlib.sha256(source).hexdigest(),'equal':equivalent(built,current)})
    if 'launch' not in archive.toc:raise ValueError('Executable entrypoint is missing')
    source=(root/'annotation_app/launch.py').read_bytes();built=marshal.loads(archive.extract('launch'))
    records.append({'module':'launch','source_sha256':hashlib.sha256(source).hexdigest(),
                    'equal':equivalent(built,compile(source,built.co_filename,'exec',dont_inherit=True,optimize=0))})
    return {'status':'PASS' if all(r['equal'] for r in records) else 'FAIL','method':'recursive embedded code attributes, no application execution',
            'modules':records,'executable_sha256':hashlib.sha256(executable.read_bytes()).hexdigest()}

def main():
    p=argparse.ArgumentParser();p.add_argument('executable',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    report=verify(a.executable,Path(__file__).resolve().parents[1]);a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x',encoding='utf-8') as stream:json.dump(report,stream,indent=2)
    print(json.dumps(report,indent=2));return 0 if report['status']=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
