"""Validate public examples against JSON Schema (install jsonschema separately)."""
from pathlib import Path
import json
from jsonschema import Draft202012Validator

root=Path(__file__).resolve().parents[1]
schemas={p.stem.split('.')[0]:json.loads(p.read_text()) for p in (root/'schemas').glob('*.schema.json')}
for schema in schemas.values():Draft202012Validator.check_schema(schema)
count=0
for name,pattern in [('package','manifest.json'),('case','*/case.json')]:
    for path in (root/'examples/synthetic-v1').glob(pattern):
        Draft202012Validator(schemas[name]).validate(json.loads(path.read_text()));count+=1
print(json.dumps({'status':'PASS','schemas':len(schemas),'synthetic_documents':count}))
