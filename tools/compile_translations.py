"""Validate placeholder parity and compile the checked-in Qt TS catalog."""
import argparse
from pathlib import Path
import shutil
import string
import subprocess
import sys
import xml.etree.ElementTree as ET


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--lrelease');args=parser.parse_args()
    root=Path(__file__).resolve().parents[1];ts=root/'annotation_app/translations/cas_zh_CN.ts'
    messages=ET.parse(ts).findall('.//message');seen=set()
    fields=lambda text:sorted((name,spec,conversion) for _,name,spec,conversion in string.Formatter().parse(text) if name is not None)
    for message in messages:
        source=message.findtext('source');target=message.findtext('translation')
        if not source or not target or source in seen or fields(source)!=fields(target):raise ValueError(source)
        seen.add(source)
    binary=args.lrelease or shutil.which('pyside6-lrelease') or str(Path(sys.executable).parent/'pyside6-lrelease')
    subprocess.run([binary,str(ts),'-qm',str(ts.with_suffix('.qm'))],check=True)
    print(f'Validated {len(messages)} translated messages and compiled QM.')

if __name__=='__main__':main()
