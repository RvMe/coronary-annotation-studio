"""One shared, fail-closed synthetic workflow for source and frozen applications.

The create/resume processes use the same explicit QA database. These gates do
not waive ordinary save/close protection and never write to a default workspace.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import sysconfig
import time
import traceback
from PySide6.QtCore import QObject,QTimer
from PySide6.QtWidgets import QApplication
from . import __version__
from .storage import io_path


def prepare_workflow(args):
    """Refuse unsafe QA inputs before constructing a window or opening SQLite.

    A fresh output gets a structured refusal. Existing outputs are immutable,
    including when a caller accidentally requests the same run twice.
    """
    output=Path(args.workflow_smoke_output).resolve()
    output.mkdir(parents=True,exist_ok=False)
    try:
        if args.workflow_smoke_phase not in ('create','resume'):
            raise ValueError('Workflow output and phase must be supplied together')
        if args.db is None:
            raise ValueError('Workflow tests require an explicit isolated --db')
        db=Path(args.db).resolve();expectation=Path(str(db)+'.workflow.json')
        if args.workflow_smoke_phase=='create':
            if args.package is None:
                raise ValueError('Workflow create requires --package')
            if db.exists() or expectation.exists():
                raise ValueError('Workflow create requires a new database')
            from .projects import project_for_package
            from .imaging import safe_relative_path
            project=project_for_package(args.package)
            for entry in project['cases']:
                path=safe_relative_path(Path(project['root']),entry['case_manifest'])
                case=json.loads(path.read_text(encoding='utf-8-sig'))
                if case.get('metadata',{}).get('synthetic') is not True:
                    raise ValueError('Workflow tests require every case to declare metadata.synthetic=true')
        else:
            if args.package is not None or args.case is not None:
                raise ValueError('Workflow resume must restore its case from the saved session')
            if not db.is_file() or not expectation.is_file():
                raise ValueError('Workflow resume requires the existing QA database and expectation')
            data=json.loads(expectation.read_text(encoding='utf-8'))
            if data.get('schema_version')!='cas-workflow-expectation-1.0' or data.get('synthetic_only') is not True:
                raise ValueError('Workflow resume requires an expectation from a synthetic create run')
    except Exception as exc:
        report={'schema_version':'cas-workflow-smoke-1.0','status':'FAIL',
                'phase':args.workflow_smoke_phase,'stage':'preflight','failure':str(exc),
                'exit_code':2,'database_opened':False,'checks':[]}
        (output/'workflow-report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        raise
    return output


def snapshot(w):
    return {"case":id(w.case),"model":id(w.state),"store":id(w.store),"queue":[id(w.case_list.item(i)) for i in range(w.case_list.count())],
            "formal":deepcopy(w.state),"draft":w.draft_snapshot(),"undo":deepcopy(w.draft_undo),"redo":deepcopy(w.draft_redo),"view":w.view_state(),
            "database":list(w.store.connection.iterdump())}


class WorkflowRunner(QObject):
    def __init__(self,window,output,phase,language):
        super().__init__(window)
        self.window=window;self.output=Path(output).resolve();self.phase=phase;self.language=language;self.started=time.monotonic();self.finished=False;self.phase_done=False
        self.expected_path=Path(str(window.db_path)+".workflow.json")
        self.report={"schema_version":"cas-workflow-smoke-1.0","status":"FAIL","phase":phase,"version":__version__,"machine":platform.machine() or sysconfig.get_platform(),"qt_platform":QApplication.platformName(),"frozen":bool(getattr(sys,"frozen",False)),"synthetic_only":True,"checks":[],"screenshots":[],"physical_device_acceptance":False}
        if sys.platform=='darwin':self.report['proc_translated']=subprocess.run(['/usr/sbin/sysctl','-in','sysctl.proc_translated'],capture_output=True,text=True).stdout.strip()
        window.error=lambda title,error:self.fail(RuntimeError(f"{title}: {error}"))
        self.timer=QTimer(self);self.timer.timeout.connect(self.tick);self.timer.start(50)

    def check(self,name,value):
        self.report['checks'].append({'name':name,'passed':bool(value)})
        if not value:raise AssertionError(name)

    def capture(self,name):
        app=QApplication.instance();app.processEvents()
        path=self.output/(name+'.png')
        if path.exists():raise FileExistsError(path)
        self.check('screenshot_'+name,self.window.grab().save(str(path)) and path.is_file() and path.stat().st_size>0)
        self.report['screenshots'].append({'path':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})

    def tick(self):
        if self.finished:return
        w=self.window
        if time.monotonic()-self.started>90:
            self.fail(TimeoutError('The synthetic case did not become ready within 90 seconds.'));return
        if not w.case or w.loading or w.render_busy or any(c.qimage is None for c in w.canvases):return
        self.timer.stop()
        try:
            self.check('declared_synthetic_input',w.case.manifest.get('metadata',{}).get('synthetic') is True)
            self.check('interactive_platform',QApplication.platformName() in ('cocoa','windows'))
            self.check('shared_source_project',w.source_info()['project_id']==w.project_id)
            if not self.phase_done:
                if self.phase=='create':self.create()
                else:self.resume()
                self.phase_done=True
            if w.render_busy:self.timer.start(50)
            else:self.finish()
        except Exception as exc:self.fail(exc)

    def language_gate(self):
        w=self.window
        self.check('pre_language_view_saved',w.save_view());w.save_timer.stop()
        before=snapshot(w)
        for index,language in enumerate(('en','zh_CN','en')):
            w.languages.set_language(language)
            if language=='zh_CN':
                self.check('qt_standard_dialog_translation_loaded',w.languages.qt_translator is not None and not w.languages.qt_translator.isEmpty())
            self.check('language_preserves_state_'+language,snapshot(w)==before)
            expected='Reader ID' if language=='en' else '读者编号'
            self.check('accessible_language_'+language,w.reader_box.accessibleName()==expected)
            self.capture(self.phase+'-'+str(index)+'-'+language)
        w.languages.set_language(self.language)

    def create(self):
        w=self.window
        self.check('first_launch_is_english',w.languages.language=='en')
        self.check('fresh_database_has_no_labels',not w.state['annotations'] and w.state['revision']==0)
        self.check('path_is_in_scope',w.annotation_allowed())
        length=w.path.length_mm;self.check('nondegenerate_path',length>.1)
        # Use two disjoint intervals and actual reader actions. No direct store
        # insertion, model patch, synthetic PASS flag or disabled validation.
        for finding,a,b in [('negative',.10*length,.25*length),('positive',.55*length,.75*length)]:
            w.numeric_range(a,b,'end')
            if finding=='negative':w.typical_normal()
            else:
                for key,value in [('finding_status','positive'),('plaque_composition','calcified'),('stenosis_grade','25_49'),('confidence','high')]:
                    w.groups[key][1][value].click()
            self.check('apply_'+finding,w.apply_current())
        self.check('both_formal_findings_saved',{'negative','positive'}<=set(r['label']['finding_status'] for r in w.state['annotations']))
        w.set_label('finding_status','positive');w.set_label('plaque_composition','non_calcified');w.undo()
        self.check('unapplied_draft_present',w.dirty and w.label.get('finding_status')=='positive' and bool(w.draft_redo))
        self.language_gate()
        self.expected={"schema_version":"cas-workflow-expectation-1.0","synthetic_only":True,"project_id":w.project_id,"case_id":str(w.case.case_id),"reader_id":w.reader_id,"annotations":deepcopy(w.state['annotations']),"revision":w.state['revision'],"draft":w.draft_snapshot(),"path_id":w.path_id,"language":self.language}

    def resume(self):
        w=self.window;expected=json.loads(self.expected_path.read_text(encoding='utf-8'))
        self.check('expected_case_project_reader_restored',(w.project_id,str(w.case.case_id),w.reader_id)==(expected['project_id'],expected['case_id'],expected['reader_id']))
        self.check('formal_records_restored_exactly',w.state['annotations']==expected['annotations'] and w.state['revision']==expected['revision'])
        self.check('unapplied_draft_restored_exactly',w.draft_snapshot()==expected['draft'] and w.path_id==expected['path_id'])
        self.check('language_restored_after_independent_process',w.languages.language==expected['language'])
        self.language_gate()
        maximum_anchor_error=0.
        for record in w.state['annotations']:
            path=w.case.paths[record['path_id']]
            anchors={item['role']:item['point_lps_mm'] for item in record['native_anchors']}
            for role,key in [('start','s_start_mm'),('end','s_end_mm')]:
                actual=w.case.native.world_to_index(anchors[role])
                reference=w.case.native.world_to_index(path.point(record[key]))
                maximum_anchor_error=max(maximum_anchor_error,float(max(abs(actual-reference))))
        self.check('saved_native_anchors_match_mapping',maximum_anchor_error<=.1)
        self.report['maximum_saved_anchor_index_error_voxel']=maximum_anchor_error
        w.native_return()
        # centerline/native cross-reference must derive from the actual nonlinear
        # mapping; a display affine is never substituted for point(s).
        native_index=w.case.native.world_to_index(w.path.point(w.s))
        self.check('native_return_selects_correct_axial_slice',w.native_z==round(native_index[2]))
        projected=w.native.native_index_to_screen(native_index[0],native_index[1])
        recovered=w.native.native_screen_to_index_unbounded(projected)
        self.check('native_back_location_subvoxel',max(abs(recovered[i]-native_index[i]) for i in (0,1))<=.1)
        export=w.store.export_batch(w.reader_id,self.output/'export',case_ids=[str(w.case.case_id)])
        manifest=Path(export['manifest']);data=json.loads(io_path(manifest).read_text(encoding='utf-8'))
        self.check('batch_export_complete',export['status']=='PASS' and export['case_count']==1 and data['schema_version']=='cas-batch-export-1.0')
        for record in data['files']:
            path=io_path(manifest.parent/record['path'])
            self.check('export_file_'+record['path'],path.is_file() and path.stat().st_size==record['bytes'] and hashlib.sha256(path.read_bytes()).hexdigest()==record['sha256'])
        self.report['export_manifest']=str(manifest.relative_to(self.output))
        self.check('export_did_not_apply_draft',w.state['annotations']==expected['annotations'] and w.label==expected['draft']['label'])

    def finish(self):
        if self.finished:return
        w=self.window;self.capture(self.phase+'-final')
        self.check('normal_close_accepted',w.close())
        self.check('normal_close_saved',w._last_close_save_succeeded is True and w._close_completed)
        if self.phase=='create':
            with self.expected_path.open('x',encoding='utf-8') as stream:json.dump(self.expected,stream,ensure_ascii=False,indent=2)
        self.report['status']='PASS';self.publish(0)

    def fail(self,error):
        if self.finished:return
        self.timer.stop();self.report['failure']=str(error);self.report['traceback']=traceback.format_exc()
        w=self.window;w._smoke_silent_exit=True
        try:
            if not w._close_completed:w.close()
        except Exception:pass
        self.publish(2)

    def publish(self,exit_code):
        self.finished=True;self.report['elapsed_seconds']=round(time.monotonic()-self.started,3);self.report['exit_code']=exit_code
        (self.output/'workflow-report.json').write_text(json.dumps(self.report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(self.report,ensure_ascii=False),flush=True)
        QApplication.instance().exit(exit_code)
