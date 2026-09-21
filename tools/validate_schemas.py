"""Validate public examples against JSON Schema (install jsonschema separately)."""
from pathlib import Path
import argparse
import json
from jsonschema import Draft202012Validator

root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--exports',type=Path,help='Also validate an actual synthetic batch export')
a=p.parse_args()
schemas={p.stem.split('.')[0]:json.loads(p.read_text()) for p in (root/'schemas').glob('*.schema.json')}
for schema in schemas.values():Draft202012Validator.check_schema(schema)
count=0
for name,pattern in [('package','manifest.json'),('case','*/case.json')]:
    for path in (root/'examples/synthetic-v1').glob(pattern):
        Draft202012Validator(schemas[name]).validate(json.loads(path.read_text()));count+=1
export_count=0
if a.exports:
    for path in a.exports.rglob('*.json'):
        document=json.loads(path.read_text(encoding='utf-8'))
        name={'cas-annotations-1.0':'annotations','cas-export-1.0':'export','cas-batch-export-1.0':'batch-export'}.get(document.get('schema_version'))
        if name:
            Draft202012Validator(schemas[name]).validate(document);export_count+=1
    if export_count<3:raise ValueError('Expected annotations, per-case and batch manifests')
print(json.dumps({'status':'PASS','schemas':len(schemas),'synthetic_documents':count,'export_documents':export_count}))
