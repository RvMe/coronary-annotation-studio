"""Synthetic-only native desktop UI gates; requires Qt windows or cocoa by OS.

Run separately under native arm64 and x86_64 Python. This is UI integration
validation, not frozen-app, physical-device or clinical acceptance.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import traceback

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest,QSignalSpy
from PySide6.QtWidgets import QApplication
from annotation_app import app as ui
from annotation_app import projects
from annotation_app.i18n import tr,display,error_message


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def stable(window):
    return {"case_object":id(window.case),"state_object":id(window.state),"store_object":id(window.store),
            "queue_objects":[id(window.case_list.item(i)) for i in range(window.case_list.count())],
            "path_buttons":{key:id(button) for key,button in window.path_buttons.items()},
            "case_id":window.case.case_id,"state":deepcopy(window.state),"draft":window.draft_snapshot(),
            "view":window.view_state(),"undo":deepcopy(window.draft_undo),"redo":deepcopy(window.draft_redo),
            "sql":digest(list(window.store.connection.iterdump()))}


def settle(window,errors,seconds=30):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        QTest.qWait(25)
        if errors:raise AssertionError(errors)
        if window.case and not window.loading and not window.render_busy and all(c.qimage is not None for c in window.canvases):return
    raise AssertionError('Images did not become ready')


def clone_project(package,target,project_id,view_only=False,corrupt=False):
    shutil.copytree(package,target)
    mpath=target/'manifest.json';manifest=json.loads(mpath.read_text());manifest['project_id']=project_id
    if view_only:manifest['cases']=[x for x in manifest['cases'] if x['case_id']=='multiple']
    mpath.write_text(json.dumps(manifest))
    for entry in manifest['cases']:
        p=target/entry['case_manifest'];data=json.loads(p.read_text());data['project_id']=project_id
        if view_only:data['annotation_scope']=['RCA']
        p.write_text(json.dumps(data))
    if corrupt:
        entry=manifest['cases'][0];p=target/entry['case_manifest'];data=json.loads(p.read_text());asset=p.parent/data['native']
        with asset.open('ab') as stream:stream.write(b'corruption-for-negative-test')
    return target


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--package',type=Path,required=True);parser.add_argument('--restart-check',type=Path)
    args=parser.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    app=QApplication([]);app.setQuitOnLastWindowClosed(False)
    expected_platform='windows' if sys.platform=='win32' else 'cocoa'
    assert app.platformName()==expected_platform,'This gate requires the native interactive Qt platform'
    report={'status':'FAIL','machine':platform.machine(),'qt_platform':app.platformName(),'synthetic_only':True,'frozen':False,'checks':[],'errors':[]}
    windows=[];errors=[]
    def check(name,condition,details=None):
        row={'name':name,'passed':bool(condition)}
        if details is not None:row['details']=details
        report['checks'].append(row)
        print(json.dumps(row,ensure_ascii=False),flush=True)
        assert condition,name
    def open_window(db,package=None,case_id=None):
        w=ui.MainWindow(db_path=db,package=package,reader='QA-CAS',case_id=case_id)
        windows.append(w);w.error=lambda title,error:errors.append({'title':display(title),'error':str(error)})
        w.show();settle(w,errors);return w
    try:
        if args.restart_check:
            db=args.restart_check.resolve();w=open_window(db)
            check('fresh_process_language_restored',w.languages.language=='zh_CN')
            check('fresh_process_case_draft_restored',w.case.case_id=='single' and w.label.get('finding_status')=='positive' and w.dirty)
            check('fresh_process_rotation_zero',w.native_rotation_quarters==0)
            check('fresh_process_recovered_formal_label',len(w.state['annotations'])==1)
            check('close_saved',w.close() and w._last_close_save_succeeded)
        else:
            # All derived projects, preferences and SQLite files are under output.
            projects.user_data_dir=lambda:out/'isolated-projects'
            package=out/'synthetic-input';shutil.copytree(args.package,package)
            db=out/'workspace-A/annotations.sqlite';w=open_window(db,package,'single')
            check('default_english',w.languages.language=='en' and 'Research annotation' in w.windowTitle())
            check('native_ct_default_zero',w.native_rotation_quarters==0)
            check('single_arbitrary_path',list(w.path_buttons)==['coronary-path-A'])
            check('source_uses_shared_contract',w.source_info()==__import__('annotation_app.package',fromlist=['source_for_case']).source_for_case(w.case))
            w.a=1.;w.b=min(4.,w.path.length_mm);w.typical_normal();check('synthetic_formal_commit',w.apply_current())
            w.set_label('finding_status','positive');w.set_label('plaque_composition','calcified');w.undo();w.save_view();w.save_timer.stop()
            before=stable(w)
            spies=[QSignalSpy(w.start_spin.valueChanged),QSignalSpy(w.end_spin.valueChanged),QSignalSpy(w.case_list.currentItemChanged),QSignalSpy(w.reader_box.textChanged)]
            w.hint(tr('Saved · Revision {p0}',p0=w.state['revision']))
            w.languages.set_language('zh_CN');QTest.qWait(100)
            check('language_switch_preserves_model_draft_undo_view_sql',stable(w)==before)
            check('language_switch_emits_no_edit_signals',all(spy.count()==0 for spy in spies))
            check('dynamic_and_accessible_translation','读者' in w.reader_box.accessibleName() and '应用' in w.apply_button.text() and '病例' in w.case_list.item(0).text() and '修订' in w.statusBar().currentMessage())
            check('neutral_brand_both_languages','ImageCAS' not in w.windowTitle())
            w.resize(1280,800);QTest.qWait(100);w.grab().save(str(out/'single-zh-1280x800.png'))
            w.languages.set_language('en');QTest.qWait(100)
            check('english_roundtrip_preserves_model',stable(w)==before)
            check('backend_validation_error_localized',str(error_message(ValueError('请先选择 1 · 本段所见')))=='Select 1 · Interval finding first.')
            check('english_restored',w.apply_button.text()=='Apply label / S or Enter' and w.reader_box.accessibleName()=='Reader ID')
            w.grab().save(str(out/'single-en-1280x800.png'))
            # Stable comparison precedes resize, which is view-only but may recenter.
            w.languages.set_language('zh_CN');w.save_view();check('close_before_restart',w.close())
            child=out/'restart-process'
            command=[sys.executable,'-B',str(Path(__file__).resolve()),'--output',str(child),'--package',str(package),'--restart-check',str(db)]
            result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',timeout=90,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONIOENCODING':'utf-8','QT_QPA_PLATFORM':expected_platform})
            (out/'restart.stdout.log').write_text(result.stdout);(out/'restart.stderr.log').write_text(result.stderr)
            check('fresh_process_restart',result.returncode==0,{'exit_code':result.returncode})
            w=open_window(db);w.languages.set_language('en');w.clear_draft();w.select_case_in_queue('multiple');settle(w,errors)
            check('multiple_paths_dynamic',set(w.path_buttons)==set(w.case.paths)=={'LAD','LCX','RCA'})
            w.change_path('RCA');settle(w,errors);check('path_switch',w.path_id=='RCA')
            w.a=1.;w.b=3.;w.typical_normal();check('multi_formal_commit',w.apply_current());w.set_label('finding_status','uncertain');w.save_view()
            old_project=w.project_id;old_db=w.db_path;old_state=deepcopy(w.state);old_draft=w.draft_snapshot()
            candidate=clone_project(package,out/'project-B','synthetic-project-B',view_only=True)
            w._confirm_project_switch=lambda p:True
            check('project_switch_started',w.import_package(candidate));settle(w,errors)
            check('independent_project_database',w.project_id=='synthetic-project-B' and w.db_path!=old_db and w.store.list_case_ids('QA-CAS')==['multiple'])
            check('same_case_id_no_record_leak',not w.state['annotations'])
            check('view_only_scope_enforced',w.path_id=='LAD' and not w.apply_button.isEnabled() and not w.apply_current())
            check('coverage_uses_declared_scope',set(w.completion_report()['by_path'])=={'RCA'})
            w.change_path('RCA');settle(w,errors);w.typical_normal();check('in_scope_commit_allowed',w.apply_current())
            w.resize(1440,900);QTest.qWait(100);w.grab().save(str(out/'multiple-en-1440x900.png'))
            check('switch_back_started',w.import_package(package,'multiple'));settle(w,errors)
            check('project_A_records_and_draft_restored',w.project_id==old_project and w.draft_snapshot()==old_draft and w.state['annotations']==old_state['annotations'])
            # Corrupt the candidate's asset in a new QA-only copy. Candidate DB
            # may be prepared, but the active project and DB must never change.
            corrupt=clone_project(package,out/'corrupt-project','synthetic-corrupt')
            data_path=corrupt/'single/native.nii.gz'
            with data_path.open('ab') as stream:stream.write(b'corrupt')
            active=(w.project_id,w.db_path,id(w.store),deepcopy(w.label))
            check('corrupt_candidate_started',w.import_package(corrupt,'single'))
            deadline=time.monotonic()+30
            while w.loading and time.monotonic()<deadline:QTest.qWait(25)
            check('corrupt_candidate_rejected_without_active_swap',bool(errors) and (w.project_id,w.db_path,id(w.store),w.label)==active)
            report['expected_negative_errors']=errors[:];errors.clear();settle(w,errors)
            # GUI export path must use backend batch manifest, even with images
            # physically unavailable. Move only our own synthetic copy.
            w.save_view();moved=package.with_name('synthetic-input-disconnected');package.rename(moved)
            captured=[];original_dialog=ui.QFileDialog.getExistingDirectory;original_info=ui.QMessageBox.information
            export_dir=out/'exports';export_dir.mkdir()
            ui.QFileDialog.getExistingDirectory=lambda *a,**k:str(export_dir)
            ui.QMessageBox.information=lambda *a,**k:captured.append([display(x) for x in a[1:]])
            try:w.export_batch()
            finally:ui.QFileDialog.getExistingDirectory=original_dialog;ui.QMessageBox.information=original_info
            manifests=list(export_dir.glob('batch_*/batch-manifest.json'))
            check('offline_GUI_batch_export_manifest',len(manifests)==1 and len(json.loads(manifests[0].read_text())['cases'])==2 and bool(captured))
            moved.rename(package)
            # Unequal u/v geometry is a display contract independent of the
            # loader; validate physical aspect and vertical reference scaling.
            path=w.path;old_uv=getattr(path,'spacing_uv',None);path.spacing_uv=[.4,.9]
            rectangle=w.cross.image_rect();expected=w.cross.qimage.width()*.4/(w.cross.qimage.height()*.9)
            check('unequal_uv_cross_section_aspect',abs(rectangle.width()/rectangle.height()-expected)<1e-10)
            if old_uv is None:del path.spacing_uv
            else:path.spacing_uv=old_uv
            check('close_after_gates',w.close())
        report['status']='PASS'
    except Exception:
        report['failure']=traceback.format_exc();print(report['failure'],file=sys.stderr,flush=True)
    finally:
        for w in windows:
            if not w._close_completed:
                w._smoke_silent_exit=True
                w.close()
        report['errors']=errors
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return 0 if report['status']=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
