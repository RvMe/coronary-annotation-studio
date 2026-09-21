"""GUI launcher and headless validation/import/export commands."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys


def main(argv=None):
    args=list(sys.argv[1:] if argv is None else argv)
    commands={"validate","demo","import-legacy-package","import-legacy-labels","export"}
    if not args or args[0] not in commands:
        from .app import main as gui_main
        return gui_main() if argv is None else gui_main(args)
    p=argparse.ArgumentParser(prog="coronary-annotation-studio")
    sub=p.add_subparsers(dest="command",required=True)
    check=sub.add_parser("validate");check.add_argument("package")
    demo=sub.add_parser("demo");demo.add_argument("destination")
    legacy=sub.add_parser("import-legacy-package");legacy.add_argument("source");legacy.add_argument("destination");legacy.add_argument("--project-id",required=True)
    labels=sub.add_parser("import-legacy-labels");labels.add_argument("export");labels.add_argument("--case",required=True);labels.add_argument("--db")
    export=sub.add_parser("export");export.add_argument("--db",required=True);export.add_argument("--reader",required=True);export.add_argument("--output",required=True)
    a=p.parse_args(args)
    try:
        if a.command=="validate":
            from .package import validate_package
            result=validate_package(a.package)
        elif a.command=="demo":
            from .synthetic import generate
            result=generate(a.destination)
        elif a.command=="import-legacy-package":
            from .legacy import import_package
            result=import_package(a.source,a.destination,a.project_id)
        elif a.command=="import-legacy-labels":
            from .legacy import import_annotations
            from .imaging import load_case
            from .projects import database_for_project,bind_database
            from .storage import AnnotationStore
            case=load_case(a.case)
            db=bind_database(a.db or database_for_project(case.manifest["project_id"]),case.manifest["project_id"])
            with AnnotationStore(db) as store:
                state=import_annotations(a.export,store,case)
            result={"status":"PASS","case_id":state["case_id"],"reader_id":state["reader_id"],"revision":state["revision"]}
        else:
            from .storage import AnnotationStore
            if not Path(a.db).is_file():
                raise ValueError("Database does not exist")
            with AnnotationStore(a.db) as store:
                result=store.export_batch(a.reader,a.output)
        print(json.dumps(result,indent=2,ensure_ascii=False))
        return 0
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print(json.dumps({"status":"FAIL","reason":str(exc)},ensure_ascii=False),file=sys.stderr)
        return 2
