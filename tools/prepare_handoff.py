"""Produce an immutable source ZIP from a clean, reviewed Git inventory."""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import sys
import zipfile

def main():
    p=argparse.ArgumentParser();p.add_argument('output');a=p.parse_args()
    root=Path(__file__).resolve().parents[1];output=Path(a.output).resolve()
    if output.exists():raise FileExistsError(output)
    dirty=subprocess.run(['git','status','--porcelain','--untracked-files=normal'],cwd=root,check=True,capture_output=True,text=True).stdout
    if dirty.strip():raise ValueError('Commit the reviewed source before creating a handoff')
    subprocess.run([sys.executable,str(root/'tools/audit_public_source.py')],cwd=root,check=True)
    revision=subprocess.run(['git','rev-parse','HEAD'],cwd=root,check=True,capture_output=True,text=True).stdout.strip()
    names=subprocess.run(['git','ls-files','-z'],cwd=root,check=True,capture_output=True).stdout.decode().split('\0')
    files=[root/name for name in names if name]
    if not files:raise ValueError('No reviewed source files')
    for path in files:
        if path.is_symlink() or not path.is_file():raise ValueError('Only regular tracked source files may be transferred')
    records=[]
    with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for path in sorted(files):
            relative=path.relative_to(root).as_posix();data=path.read_bytes()
            archive.writestr(relative,data)
            records.append({'path':relative,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
        archive.writestr('HANDOFF-FILES.json',json.dumps({'git_revision':revision,'files':records},indent=2))
    print(json.dumps({'path':str(output),'bytes':output.stat().st_size,'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'files':len(records)}))

if __name__=='__main__':main()
