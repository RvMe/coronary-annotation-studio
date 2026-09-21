"""Audit the explicit Git source inventory; never scan private data directories."""
from pathlib import Path
import hashlib
import json
import re
import subprocess

root=Path(__file__).resolve().parents[1]
files=subprocess.run(['git','ls-files','-z'],cwd=root,check=True,capture_output=True).stdout.decode().split('\0')
records=[];failures=[]
private=re.compile(r'(?:[A-Za-z]:[\\/](?:Users|ResearchData)[\\/]|/[U]sers/[^/]+/|gh[pousr]_[A-Za-z0-9]{30,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----)')
for name in files:
    if not name:continue
    path=root/name;data=path.read_bytes()
    if path.suffix.lower() in ('.sqlite','.db','.dcm','.pt','.pth','.onnx'):
        failures.append({'path':name,'reason':'Unapproved database/model/medical asset'})
    if name.lower().endswith(('.nii','.nii.gz','.nrrd','.npz')):
        if not name.startswith('examples/synthetic-v1/'):
            failures.append({'path':name,'reason':'Binary data outside approved synthetic fixtures'})
    else:
        try:text=data.decode('utf-8-sig')
        except UnicodeDecodeError:text=''
        for match in private.finditer(text):
            failures.append({'path':name,'reason':'Private path or credential pattern','line':text[:match.start()].count('\n')+1})
    records.append({'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
for path in (root/'examples/synthetic-v1').glob('*/case.json'):
    if json.loads(path.read_text())['metadata'].get('synthetic') is not True:
        failures.append({'path':path.relative_to(root).as_posix(),'reason':'Fixture missing explicit synthetic declaration'})
report={'status':'PASS' if not failures else 'FAIL','files':len(records),'failures':failures,'inventory':records}
output=root/'release-evidence/public-source-audit.json';output.parent.mkdir(exist_ok=True)
output.write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='inventory'},indent=2))
raise SystemExit(bool(failures))
