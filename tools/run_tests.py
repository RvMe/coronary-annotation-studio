"""Run meaningful synthetic tests and produce a structured acceptance record."""
import argparse
import io
import json
import os
from pathlib import Path
import platform
import sys
import unittest
from datetime import datetime,timezone

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    suite=unittest.defaultTestLoader.discover(str(root/'tests'),top_level_dir=str(root))
    if not suite.countTestCases():raise ValueError('No tests discovered')
    a.output.mkdir(parents=True,exist_ok=False)
    text=io.StringIO();result=unittest.TextTestRunner(stream=text,verbosity=2).run(suite)
    (a.output/'tests.log').write_text(text.getvalue(),encoding='utf-8')
    report={'status':'PASS' if result.wasSuccessful() else 'FAIL','run':result.testsRun,'failures':len(result.failures),
            'errors':len(result.errors),'skipped':[{'test':str(t),'reason':r} for t,r in result.skipped],
            'python':sys.version,'platform':platform.platform(),'machine':platform.machine(),
            'qt_platform_requested':os.environ['QT_QPA_PLATFORM'],'data':'synthetic only',
            'utc':datetime.now(timezone.utc).isoformat(),'clinical_acceptance':False}
    (a.output/'tests.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2))
    if not result.wasSuccessful():print(text.getvalue())
    return 0 if result.wasSuccessful() else 1

if __name__=='__main__':raise SystemExit(main())
