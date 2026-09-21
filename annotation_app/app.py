"""Offline desktop reader application. No server, network or model inference."""
from __future__ import annotations

from .i18n import tr, Text, display, language_manager, error_message

import argparse
import json
import os
import sys
import traceback
import uuid
import time
import platform as runtime_platform
from html import escape
from copy import deepcopy
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot, QLockFile, QEvent, QPoint, QPointF, QSignalBlocker
from PySide6.QtGui import QKeySequence, QShortcut, QFontDatabase, QFont, QIcon, QPixmap, QPainter, QPen, QColor, QAction
from PySide6.QtWidgets import (
    QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QLabel,QPushButton,
    QButtonGroup,QFrame,QSplitter,QScrollArea,QListWidget,QListWidgetItem,QFileDialog,
    QMessageBox,QLineEdit,QDoubleSpinBox,QComboBox,QCheckBox,QAbstractSpinBox,QSizePolicy,QDialog,QToolButton,QMenu,
)

from .i18n_widgets import (QLabel, QPushButton, QCheckBox, QToolButton, QWidget, QMainWindow, QDialog, QComboBox, QLineEdit, QDoubleSpinBox, QListWidget, QListWidgetItem, QAction, QStatusBar, QMenu, QMessageBox)

from . import __version__
from .domain import empty_state,apply_annotation,delete_annotation,add_marker,delete_marker,coverage_report,snap_endpoint,mark_case_complete,effective_reader_review_required,reader_review_reasons
from .storage import AnnotationStore
from .imaging import load_case
from .widgets import ImageCanvas,IntervalTrack
from .label_catalog import REASON_OPTIONS,REASON_CATALOG_VERSION,PROTOCOL_VERSION,SELECTION_COLORS
from .label_logic import transition_label,label_applicability,prepare_label_for_submission,validate_new_label
from .platform_support import is_macos,user_data_dir,user_log_dir,font_family_css,shortcut_help,primary_modifier_label,alternate_modifier_label

STYLE = """
QMainWindow,QWidget{background:#131b24;color:#dce5ec;font-family:'Microsoft YaHei UI','Segoe UI';font-size:12px;}
QLabel#brand{font-size:18px;font-weight:650;color:#e8eef3;}
QLabel#muted{color:#92a2af;} QLabel#section{color:#c5d5df;font-weight:600;}
QFrame#panel{background:#18232e;border:1px solid #2c3a47;border-radius:7px;}
QPushButton,QToolButton{background:#243340;border:1px solid #394b5a;border-radius:5px;padding:6px 8px;min-height:18px;}
QPushButton:hover,QToolButton:hover{background:#304555;border-color:#6e8b9e;} QPushButton:pressed,QToolButton:pressed{background:#3c5360;}
QToolButton{padding-right:22px;} QToolButton::menu-button{width:17px;border-left:1px solid #394b5a;}
QPushButton:checked{background:#32605f;border:1px solid #61c7bd;color:#f1ffff;}
QPushButton:disabled{color:#66737e;background:#1e2933;border-color:#2c3944;}
QPushButton#apply{background:#39796f;border-color:#6cc2b6;font-weight:600;min-height:26px;}
QPushButton#normal{background:#304c3d;border-color:#749981;min-height:27px;font-size:15px;}
QPushButton#danger{color:#e3a4a4;border-color:#79595f;}
QLineEdit,QDoubleSpinBox,QComboBox{background:#0f1821;border:1px solid #354653;border-radius:4px;padding:5px;}
QListWidget{background:#111b24;border:1px solid #2d3d48;border-radius:5px;outline:none;}
QListWidget::item{padding:7px 4px;} QListWidget::item:selected{background:#294951;color:#f1ffff;}
QScrollArea{border:0;} QScrollBar:vertical{background:#17222d;width:9px;} QScrollBar::handle:vertical{background:#475968;min-height:30px;}
QSplitter::handle{background:#283844;width:5px;height:5px;} QToolTip{color:#e5edf3;background:#263a48;border:1px solid #638394;}
QStatusBar{background:#101821;color:#9eafbc;}
"""

FINDINGS=[(tr('Normal'),"negative"),(tr('Plaque present'),"positive"),(tr('Suspected / uncertain'),"uncertain"),(tr('Not evaluable'),"non_evaluable")]
COMPOSITIONS=[(tr('Calcified'),"calcified"),(tr('Non-calcified'),"non_calcified"),(tr('Mixed'),"partially_calcified"),(tr('Uncertain type'),"uncertain")]
STENOSES=[("0%","0"),("1–24%","1_24"),("25–49%","25_49"),("50–69%","50_69"),("70–99%","70_99"),("100%","100"),(tr('Unable to assess'),"unable")]
CONFIDENCES=[(tr('High'),"high"),(tr('Medium'),"medium"),(tr('Low'),"low")]


class WorkerSignals(QObject):
    done=Signal(int,str,object)
    failed=Signal(int,str,str)


class Worker(QRunnable):
    def __init__(self,generation,kind,fn):
        super().__init__();self.generation=generation;self.kind=kind;self.fn=fn;self.signals=WorkerSignals()
    @Slot()
    def run(self):
        try:self.signals.done.emit(self.generation,self.kind,self.fn())
        except Exception:self.signals.failed.emit(self.generation,self.kind,traceback.format_exc())


def safe_child(root,relative):
    root=Path(root).resolve();rel=Path(relative)
    if rel.is_absolute() or rel.drive:raise ValueError(tr('Package paths must be relative.'))
    path=(root/rel).resolve()
    if not path.is_relative_to(root):raise ValueError(tr('A package path escapes its root directory.'))
    return path


class MainWindow(QMainWindow):
    def __init__(self,db_path=None,package=None,reader=None,case_id=None,restore_last=True):
        super().__init__()
        self.app=QApplication.instance()
        self.base_dir=user_data_dir()
        preference_path=(Path(db_path).resolve().parent/"ui-preferences.json") if db_path is not None else self.base_dir/"ui-preferences.json"
        self.languages=language_manager(preference_path)
        self.project=None;self.project_id=None
        if package is not None:
            self.project=self._project_for_package(package);self.project_id=self.project["project_id"]
            if db_path is None:
                from .projects import database_for_project
                db_path=database_for_project(self.project_id)
        # Windows offscreen QA has no platform font database. Use the installed
        # system font, not a redistributed Microsoft font in the application ZIP.
        if is_macos():
            families=QFontDatabase.families()
            self.app.setFont(QFont("PingFang SC",10) if "PingFang SC" in families else QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont))
        elif not QFontDatabase.families():
            for font in ("msyh.ttc","segoeui.ttf"):
                path=Path(os.environ.get("WINDIR","C:/Windows"))/"Fonts"/font
                if path.exists():QFontDatabase.addApplicationFont(str(path))
            self.app.setFont(QFont("Microsoft YaHei UI",9))
        if package is None and db_path is not None:
            identity_path=Path(db_path).resolve().parent/"project.json"
            if identity_path.is_file():
                from .projects import validate_id
                identity=json.loads(identity_path.read_text(encoding="utf-8"))
                self.project_id=validate_id(identity.get("project_id"));self.project={"project_id":self.project_id}
        self.setWindowTitle(tr('Coronary Annotation Studio {p0} · Research annotation' ,p0=__version__))
        self.resize(1540,940);self.setMinimumSize(880,520)
        self.base_dir=user_data_dir()
        self.db_path=(Path(db_path) if db_path else self.base_dir/"welcome.sqlite").resolve()
        if "onedrive" in str(self.db_path).lower():raise ValueError(tr('Keep the database in a local directory outside OneDrive.'))
        self.db_path.parent.mkdir(parents=True,exist_ok=True)
        self._db_lock=QLockFile(str(self.db_path)+".app.lock");self._db_lock.setStaleLockTime(0)
        if not self._db_lock.tryLock(0):raise RuntimeError(tr('This workspace is already open in another window. Use that window to avoid concurrent editing.'))
        if self.project_id:
            from .projects import bind_database
            try: bind_database(self.db_path,self.project_id)
            except Exception:
                self._db_lock.unlock();raise
        try:self.store=AnnotationStore(self.db_path)
        except Exception:
            self._db_lock.unlock();raise
        self._project_databases={self.project_id:self.db_path} if self.project_id else {}
        self.persist_preferences=restore_last
        self.session_path=Path(str(self.db_path)+".session.json")
        self.session_notice="";self.unavailable_package_roots=[]
        saved=self._read_session() if restore_last else {}
        self.package_roots=list(saved.get("package_roots",[]))
        self.active_package_root=saved.get("active_package_root") or (self.package_roots[-1] if self.package_roots else None)
        self.active_package_cases=list(saved.get("active_package_cases",[]))
        self._pending_package_switch=None
        self.session_active_case_id=saved.get("active_case_id")
        self.reader_id=reader if reader is not None else saved.get("reader_id","READER-A")
        if not isinstance(self.reader_id,str) or not self.reader_id.strip() or len(self.reader_id)>40 or any(c in self.reader_id for c in '/\\:*?"<>|'):
            self.store.close();self._db_lock.unlock();raise ValueError(tr('Reader ID must contain 1–40 characters and no path separators.'))
        self.reader_id=self.reader_id.strip();self.case=None;self.case_path=None;self.path_id="";self.state=None
        self.s=0.;self.a=0.;self.b=10.;self.angle_deg=0.;self.offset_mm=0.;self.width_hu=700.;self.level_hu=250.;self.native_z=0
        # Display preferences belong to this local workspace, not a case's
        # diagnostic record. Loading another vessel must not restore an old angle.
        display=saved.get("display_preferences",{})
        if not isinstance(display,dict):display={}
        quarters=display.get("native_rotation_quarters",0)
        self.native_rotation_quarters=quarters if type(quarters) is int and 0<=quarters<4 else 0
        visible=display.get("reference_lines_visible",True)
        self.reference_lines_visible=visible if type(visible) is bool else True
        self.editing_id=None;self.selected_marker=None;self.label={};self.dirty=False;self.hide_overlays=False;self.legacy_anatomy=""
        self.snap_caption="";self.snap_target=None;self.draft_undo=[];self.draft_redo=[]
        self.case_index={};self.loading=False;self.render_generation=0;self.load_generation=0;self.long_key=None
        self.pool=QThreadPool(self);self.pool.setMaxThreadCount(2);self.pending_workers=set()
        self.render_timer=QTimer(self);self.render_timer.setSingleShot(True);self.render_timer.setInterval(20);self.render_timer.timeout.connect(self.render_images)
        self.save_timer=QTimer(self);self.save_timer.setSingleShot(True);self.save_timer.setInterval(450);self.save_timer.timeout.connect(self.save_view)
        self._syncing=False;self._closing=False;self._close_completed=False;self._last_close_save_succeeded=None;self._smoke_silent_exit=False
        self._follow_pending=False;self.requested_case=None;self.render_busy=False
        self.image_panels={};self.expanded_kind=None
        self._auto_compact_width=False;self._auto_compact_height=False
        self._coverage_cache_key=None;self._coverage_cache=None
        self.selected_icon=self.make_selected_icon()
        self.setStatusBar(QStatusBar(self))
        self._build_ui();self._shortcuts();self.app.installEventFilter(self)
        self.languages.changed.connect(self._language_changed)
        self._language_changed(self.languages.language)
        self.setStyleSheet(STYLE.replace("'Microsoft YaHei UI','Segoe UI'",font_family_css()))
        available=self.app.primaryScreen().availableGeometry()
        self.resize(min(1540,max(880,available.width()-30)),min(940,max(520,available.height()-60)))
        roots=list(self.package_roots)
        if package:
            explicit=str(Path(package).resolve())
            if explicit not in roots:roots.append(explicit)
            self.active_package_root=explicit;self.active_package_cases=[]
        target=case_id if case_id is not None else self.session_active_case_id
        if roots:QTimer.singleShot(0,lambda:self.restore_session_roots(roots,target))
        elif self.session_notice:self.hint(self.session_notice)

    def _read_session(self):
        """Per-database preferences only; legacy global root is default-DB migration."""
        if self.session_path.exists():
            try:
                saved=json.loads(self.session_path.read_text(encoding="utf-8"))
                if saved.get("schema_version")!="cas-session-1.0":raise ValueError(tr('Unsupported session version.'))
                if saved.get("project_id")!=self.project_id:raise ValueError(tr("The session belongs to another project."))
                roots=saved.get("package_roots",[])
                if not isinstance(roots,list) or not all(isinstance(p,str) and p for p in roots):raise ValueError(tr('Invalid package list.'))
                saved["package_roots"]=list(dict.fromkeys(str(Path(p).resolve()) for p in roots))
                active_root=saved.get("active_package_root")
                if active_root is not None:
                    if not isinstance(active_root,str) or not active_root:raise ValueError(tr('Invalid active data directory.'))
                    saved["active_package_root"]=str(Path(active_root).resolve())
                    if saved["active_package_root"] not in saved["package_roots"]:
                        saved["package_roots"].append(saved["active_package_root"])
                elif len(roots)>1:
                    self.session_notice=tr('Restored the most recently registered directory. Open a different package folder if needed. Saved annotations are retained.')
                active_cases=saved.get("active_package_cases",[])
                if not isinstance(active_cases,list) or not all(isinstance(cid,str) and cid for cid in active_cases):raise ValueError(tr('Invalid case list for the current batch.'))
                saved["active_package_cases"]=list(dict.fromkeys(active_cases))
                reader=saved.get("reader_id","READER-A")
                if not isinstance(reader,str) or not reader.strip() or len(reader)>40 or any(c in reader for c in '/\\:*?"<>|'):
                    raise ValueError(tr('Invalid reader ID in session.'))
                active=saved.get("active_case_id")
                if active is not None and not isinstance(active,(str,int)):raise ValueError(tr('Invalid case ID in session.'))
                saved["active_case_id"]=str(active) if active is not None else None
                return saved
            except (OSError,ValueError,TypeError,AttributeError) as exc:
                self.session_notice=tr('Could not restore the previous session: {p0}. The annotation database was not changed.' ,p0=exc)
                return {}
        return {}

    def _save_session(self,*,package_roots=None,reader_id=None,active_case_id=None,active_package_root=None,active_package_cases=None,workspace=None):
        if not self.persist_preferences:return True
        active=active_case_id if active_case_id is not None else str(self.case.case_id) if self.case else self.session_active_case_id
        content={"schema_version":"cas-session-1.0","package_roots":list(package_roots if package_roots is not None else self.package_roots),"reader_id":reader_id if reader_id is not None else self.reader_id,"active_case_id":active}
        content["project_id"]=workspace["project"]["project_id"] if workspace else self.project_id
        content["display_preferences"]={"native_rotation_quarters":self.native_rotation_quarters,"reference_lines_visible":self.reference_lines_visible}
        content["active_package_root"]=active_package_root if active_package_root is not None else self.active_package_root
        content["active_package_cases"]=list(active_package_cases if active_package_cases is not None else self.active_package_cases)
        session_path=workspace["session_path"] if workspace else self.session_path
        temporary=session_path.with_name(session_path.name+"."+uuid.uuid4().hex+".tmp")
        try:
            with temporary.open("x",encoding="utf-8") as stream:
                json.dump(content,stream,ensure_ascii=False,sort_keys=True,allow_nan=False,indent=2)
                stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,session_path)
            if workspace is None:self.session_active_case_id=active
            return True
        except (OSError,ValueError,TypeError) as exc:
            self.save_status.setText(tr('Session save failed: keep this window open'));self.hint(tr('Session save failed: {p0}' ,p0=exc))
            return False
        finally:
            try:temporary.unlink(missing_ok=True)
            except OSError:pass

    def _package_cases(self,root,existing=None):
        project=self._project_for_package(root)
        root=Path(project["root"]).resolve()
        manifest=Path(project["manifest_path"]).resolve()
        data=json.loads(manifest.read_text(encoding="utf-8"))
        if manifest!=root/"manifest.json" or data.get("schema_version")!="cas-package-1.0":
            raise ValueError(tr("Open the package root containing a cas-package-1.0 manifest.json."))
        additions={};existing=self.case_index if existing is None else existing
        for entry in data.get("cases",[]):
            path=safe_child(root,entry["case_manifest"]);cid=str(entry["case_id"])
            case=json.loads(path.read_text(encoding="utf-8"))
            if case.get("schema_version")!="cas-case-1.0" or case.get("project_id")!=project["project_id"] or str(case.get("case_id"))!=cid:
                raise ValueError(tr("Case {case_id} does not belong to the selected project.",case_id=cid))
            if cid in additions:raise ValueError(tr("Duplicate case ID: {case_id}",case_id=cid))
            old=existing.get(cid)
            if old and json.loads(old.read_text(encoding="utf-8")).get("geometry_id")!=case.get("geometry_id"):
                raise ValueError(tr("Case {case_id} has conflicting geometry versions.",case_id=cid))
            additions[cid]=path
        if not additions:raise ValueError(tr("The package contains no cases."))
        return additions

    @staticmethod
    def _project_for_package(root):
        from .projects import project_for_package
        project=project_for_package(Path(root).resolve())
        if not isinstance(project,dict) or not isinstance(project.get("project_id"),str) or not project["project_id"]:
            raise ValueError(tr("The package has no valid project identity."))
        return project

    def _prepare_project_workspace(self,project):
        from .projects import database_for_project,bind_database
        path=Path(self._project_databases.get(project["project_id"]) or database_for_project(project["project_id"])).resolve()
        if path==self.db_path:
            raise ValueError(tr("Different projects must use different databases."))
        if "onedrive" in str(path).lower():raise ValueError(tr("Keep the database in a local directory outside OneDrive."))
        path.parent.mkdir(parents=True,exist_ok=True)
        lock=QLockFile(str(path)+".app.lock");lock.setStaleLockTime(0)
        if not lock.tryLock(0):raise RuntimeError(tr("The selected project is already open in another window."))
        try:
            bind_database(path,project["project_id"])
            store=AnnotationStore(path)
            return {"project":project,"db_path":path,"session_path":Path(str(path)+".session.json"),"lock":lock,"store":store}
        except Exception:
            lock.unlock();raise

    def _discard_pending_project(self):
        pending=self._pending_package_switch
        workspace=pending.get("workspace") if pending else None
        if workspace:
            workspace["store"].close();workspace["lock"].unlock()
        self._pending_package_switch=None

    def _confirm_project_switch(self,project):
        dialog=QMessageBox(self);dialog.setWindowTitle(tr("Switch project"))
        dialog.setText(tr("Open project {project_id} in its own workspace?",project_id=project["project_id"]))
        dialog.setInformativeText(tr("The current draft and view will be saved. Each project keeps a separate annotation database."))
        accept=dialog.addButton(tr("Save and switch"),QMessageBox.ButtonRole.AcceptRole)
        cancel=dialog.addButton(tr("Cancel"),QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel);dialog.setEscapeButton(cancel);dialog.exec()
        return dialog.clickedButton() is accept

    def restore_session_roots(self,roots,preferred_case=None):
        if self._closing:return
        self.package_roots=list(dict.fromkeys(str(Path(root).resolve()) for root in roots))
        self.active_package_root=self.active_package_root or (self.package_roots[-1] if self.package_roots else None)
        combined={};self.unavailable_package_roots=[]
        if self.active_package_root:
            try:
                restored_project=self._project_for_package(self.active_package_root)
                if restored_project["project_id"]!=self.project_id:raise ValueError(tr("The session belongs to another project."))
                combined=self._package_cases(self.active_package_root,{})
                self.active_package_cases=list(combined)
            except (OSError,ValueError,KeyError,TypeError) as exc:
                self.unavailable_package_roots.append({"path":self.active_package_root,"reason":str(exc)})
        self.case_index=combined;self.refresh_queue()
        if preferred_case is not None and str(preferred_case) not in self.case_index:
            self.session_notice+=("\n" if self.session_notice else "")+tr('Previous case {p0} is unavailable in the current directory. Saved annotations are retained.' ,p0=preferred_case)
        if self._save_session() and self.case_index:
            target=str(preferred_case) if str(preferred_case) in self.case_index else next(iter(self.case_index))
            self.select_case_in_queue(target)
        self.show_session_notice()

    def show_session_notice(self):
        if self.unavailable_package_roots:
            details="\n".join(f"{r['path']} — {r['reason']}" for r in self.unavailable_package_roots)
            self.queue_count.setToolTip(tr("The current data directory is unavailable:\n{details}\nSaved annotations are retained and can be exported.",details=details))
            self.hint(tr('The current directory is unavailable. Other historical batches were not added. Reopen the directory to view images; saved annotations can still be exported.'))
        elif self.session_notice:self.hint(self.session_notice)

    @property
    def path(self):return self.case.paths.get(self.path_id) if self.case else None

    def button(self,text,slot=None,name=None):
        b=QPushButton(text);b.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        if slot:b.clicked.connect(slot)
        if name:b.setObjectName(name)
        return b

    def section(self,layout,title):
        label=QLabel(title);label.setObjectName("section");layout.addWidget(label)

    def _build_ui(self):
        central=QWidget();outer=QVBoxLayout(central);outer.setContentsMargins(12,8,12,8);outer.setSpacing(8);self.setCentralWidget(central)
        banner=QHBoxLayout();brand=QLabel("Coronary Annotation Studio");brand.setObjectName("brand");banner.addWidget(brand)
        sub=QLabel(tr("OFFLINE · Research annotation · v{version}",version=__version__));self.subtitle=sub;sub.setObjectName("muted");banner.addWidget(sub);banner.addStretch()
        self.language_box=QComboBox();self.language_box.setObjectName("language_selector")
        self.language_box.addItem("English","en");self.language_box.addItem("简体中文","zh_CN")
        self.language_box.setAccessibleName(tr("Interface language"));self.language_box.setToolTip(tr("Change interface language without changing annotations or drafts."))
        self.language_box.setCurrentIndex(self.language_box.findData(self.languages.language))
        self.language_box.currentIndexChanged.connect(self._choose_language);banner.addWidget(self.language_box)
        banner.addWidget(self.button(tr('Cases / help'),self.toggle_sidebar));banner.addWidget(self.button(tr('Neighbors'),self.toggle_neighbors))
        self.reference_lines_button=self.button(tr('Reference lines Z'),self.toggle_reference_lines)
        self.reference_lines_button.setCheckable(True);self.reference_lines_button.setChecked(self.reference_lines_visible)
        self.reference_lines_button.setAccessibleName(tr('Toggle reference lines in the three image views (Z)'))
        self.reference_lines_button.setToolTip(tr('Z toggles green reference lines in the three image views. The selection, markers and timeline position remain visible. Highlighted means enabled.'))
        banner.addWidget(self.reference_lines_button)
        self.retry_render_button=self.button(tr('Retry images'),self.retry_render);self.retry_render_button.hide();banner.addWidget(self.retry_render_button)
        self.save_status=QLabel(tr('No case loaded'));banner.addWidget(self.save_status);outer.addLayout(banner)
        split=QSplitter(Qt.Orientation.Horizontal);outer.addWidget(split,1)
        # Left: data, reader identity, queue, full operation legend.
        left=QWidget();self.sidebar=left;lv=QVBoxLayout(left);lv.setContentsMargins(0,0,4,0);lv.setSpacing(7)
        self.section(lv,tr('Cases and workspace'))
        lv.addWidget(self.button(tr('Open package folder…'),self.choose_package))
        self.package_caption=QLabel(tr('Directory: not opened'));self.package_caption.setObjectName("muted")
        self.package_caption.setMinimumWidth(0);self.package_caption.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        lv.addWidget(self.package_caption)
        identity=QHBoxLayout();identity.addWidget(QLabel(tr('Reader')));self.reader_box=QLineEdit(self.reader_id);self.reader_box.setAccessibleName(tr("Reader ID"));self.reader_box.setMaxLength(40);self.reader_box.editingFinished.connect(self.change_reader);identity.addWidget(self.reader_box);lv.addLayout(identity)
        self.queue_count=QLabel(tr('0 cases · Images are read-only'));self.queue_count.setWordWrap(True);self.queue_count.setObjectName("muted");lv.addWidget(self.queue_count)
        self.case_list=QListWidget();self.case_list.setObjectName("case_list");self.case_list.setAccessibleName(tr("Case queue"));self.case_list.currentItemChanged.connect(self.choose_case_item);lv.addWidget(self.case_list,1)
        self.complete_button=self.button(tr('Check and mark case complete'),self.mark_complete);lv.addWidget(self.complete_button)
        row=QHBoxLayout();row.addWidget(self.button(tr('Export case'),self.export_current))
        self.batch_export_button=QToolButton();self.batch_export_button.setText(tr('Export batch'))
        self.batch_export_button.setToolTip(tr("Export this reader's annotated cases in the current directory. Use the arrow to export all project history."))
        self.batch_export_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.batch_export_button.clicked.connect(self.export_batch)
        export_menu=QMenu(self.batch_export_button)
        export_menu.addAction(tr('Export current batch'),self.export_batch)
        export_menu.addAction(tr('Export all project history (reader backup)'),self.export_all)
        self.batch_export_button.setMenu(export_menu);row.addWidget(self.batch_export_button);lv.addLayout(row)
        import_button=QToolButton();import_button.setText(tr("Import annotations…"));import_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        import_button.clicked.connect(self.import_labels)
        import_menu=QMenu(import_button)
        import_menu.addAction(tr("Import annotation JSON…"),self.import_labels)
        import_menu.addAction(tr("Import legacy annotation export…"),self.import_legacy_labels)
        import_menu.addAction(tr("Convert legacy package to a new project…"),self.import_legacy_package)
        import_button.setMenu(import_menu);lv.addWidget(import_button)
        lv.addWidget(self.button(tr('Help / data safety'),self.show_help))
        help_text=QLabel(shortcut_help());self.operation_help=help_text
        help_text.setWordWrap(True);help_text.setObjectName("muted")
        hs=QScrollArea();hs.setWidgetResizable(True);hs.setWidget(help_text);hs.setMinimumHeight(180);hs.setMaximumHeight(350);lv.addWidget(hs)
        left.setMinimumWidth(225);left.setMaximumWidth(270);split.addWidget(left)
        # Middle: neighbor strip, primary CPR, large orthogonal view, native CT, timeline.
        middle=QWidget();mv=QVBoxLayout(middle);mv.setContentsMargins(0,0,0,0);mv.setSpacing(6)
        nav=QHBoxLayout();self.case_title=QLabel(tr('Select a case to begin'));nav.addWidget(self.case_title);nav.addStretch()
        self.path_buttons={}
        self.path_navigation=QHBoxLayout();nav.addLayout(self.path_navigation)
        mv.addLayout(nav)
        self.neighbor_container=QWidget();thumbs=QHBoxLayout(self.neighbor_container);thumbs.setContentsMargins(0,0,0,0);thumbs.setSpacing(4);self.neighbors=[]
        for k in range(-3,4):
            w=Neighbor(self,k);thumbs.addWidget(w,1);self.neighbors.append(w)
        mv.addWidget(self.neighbor_container)
        self.neighbor_step=.5
        self.neighbor_controls=QWidget();neighbor_row=QHBoxLayout(self.neighbor_controls);neighbor_row.setContentsMargins(0,0,0,0);neighbor_row.addWidget(QLabel(tr('Neighboring cross-sections · Click to navigate')));neighbor_row.addStretch()
        for distance in (.5,1,2):neighbor_row.addWidget(self.button(f"±{distance:g} mm",lambda checked=False,d=distance:self.set_neighbor_step(d)))
        mv.addWidget(self.neighbor_controls)
        images=QSplitter(Qt.Orientation.Horizontal);self.images_splitter=images
        self.cpr=ImageCanvas(self,"cpr");self.cross=ImageCanvas(self,"cross");self.native=ImageCanvas(self,"native");self.canvases=[self.cpr,self.cross,self.native]
        images.addWidget(self.image_panel(tr('Longitudinal CPR'),self.cpr,"cpr"))
        right_images=QSplitter(Qt.Orientation.Vertical);self.right_images=right_images;right_images.addWidget(self.image_panel(tr('Orthogonal section'),self.cross,"cross"));right_images.addWidget(self.image_panel(tr('Native CT · Free scrolling'),self.native,"native"));right_images.setSizes([310,265]);images.addWidget(right_images);images.setSizes([510,355]);mv.addWidget(images,1)
        progress=QHBoxLayout();self.completion_caption=QLabel(tr('Coverage: no case loaded'));self.completion_caption.setObjectName("muted");self.completion_caption.setMinimumWidth(0);self.completion_caption.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred);progress.addWidget(self.completion_caption,1)
        self.next_issue_button=self.button(tr('Next gap / review'),self.next_coverage_issue);self.next_issue_button.setStyleSheet("padding:1px 5px;min-height:14px;");progress.addWidget(self.next_issue_button);mv.addLayout(progress)
        self.track=IntervalTrack(self);mv.addWidget(self.track)
        controls=QHBoxLayout();self.angle_spin=self.spin(-180,180,0,.5," °");self.offset_spin=self.spin(-10,10,0,.1," mm")
        self.angle_spin.setAccessibleName(tr("CPR rotation in degrees"));self.offset_spin.setAccessibleName(tr("CPR offset in millimeters"))
        controls.addWidget(QLabel(tr('Rotation')));controls.addWidget(self.angle_spin);controls.addWidget(self.button(tr('Reset'),lambda:self.set_sampling(angle=0)))
        controls.addWidget(QLabel(tr('Offset')));controls.addWidget(self.offset_spin);controls.addWidget(self.button(tr('Reset'),lambda:self.set_sampling(offset=0)))
        self.angle_spin.valueChanged.connect(lambda v:self.set_sampling(angle=v) if not self._syncing else None)
        self.offset_spin.valueChanged.connect(lambda v:self.set_sampling(offset=v) if not self._syncing else None)
        self.overlay_box=QCheckBox(tr('Hide overlays'));self.overlay_box.toggled.connect(self.set_hide_overlays);controls.addWidget(self.overlay_box);mv.addLayout(controls)
        self.geometry_status=QLabel(tr('Geometry: no case loaded'));self.geometry_status.setWordWrap(True);self.geometry_status.setObjectName("muted");mv.addWidget(self.geometry_status)
        split.addWidget(middle)
        # Right: direct labels and optional reason chips; no repeated anatomy typing.
        right=QWidget();rv=QVBoxLayout(right);rv.setContentsMargins(4,0,0,0);rv.setSpacing(7)
        draft_header=QHBoxLayout();self.draft_title=QLabel(tr('Draft · No finding selected'));self.draft_title.setObjectName("section");draft_header.addWidget(self.draft_title,1)
        self.exit_edit_button=self.button(tr('Exit edit'),self.exit_editing);self.exit_edit_button.setObjectName("exitEditing");self.exit_edit_button.setToolTip(tr('Exit interval editing and keep the selection. Unapplied changes require a choice. Esc also exits.'))
        draft_header.addWidget(self.exit_edit_button);rv.addLayout(draft_header)
        limits=QHBoxLayout();self.start_spin=self.spin(0,999,0,.25," mm");self.end_spin=self.spin(0,999,10,.25," mm");self.start_spin.setAccessibleName(tr("Interval start in millimeters"));self.end_spin.setAccessibleName(tr("Interval end in millimeters"));limits.addWidget(QLabel(tr('Start')));limits.addWidget(self.start_spin);limits.addWidget(QLabel(tr('End')));limits.addWidget(self.end_spin);rv.addLayout(limits)
        self.start_spin.valueChanged.connect(lambda v:self.numeric_range(v,self.b,"start") if not self._syncing else None)
        self.end_spin.valueChanged.connect(lambda v:self.numeric_range(self.a,v,"end") if not self._syncing else None)
        rv.addWidget(self.button(tr('Typical normal interval'),self.typical_normal,"normal"))
        scroll=QScrollArea();self.annotation_scroll=scroll;scroll.setWidgetResizable(True);fields=QWidget();fv=QVBoxLayout(fields);fv.setContentsMargins(0,0,0,0);fv.setSpacing(6)
        self.groups={};self.field_sections={}
        for key,title,choices,cols in [("finding_status",tr('1 · Interval finding'),FINDINGS,2),("plaque_composition",tr('2 · Plaque composition'),COMPOSITIONS,2),("stenosis_grade",tr('3 · Maximum diameter stenosis'),STENOSES,3),("confidence",tr('4 · Confidence'),CONFIDENCES,3)]:
            panel=QWidget();pv=QVBoxLayout(panel);pv.setContentsMargins(0,0,0,0);pv.setSpacing(4);self.field_sections[key]=panel
            self.section(pv,title);grid=QGridLayout();grid.setSpacing(4);group=QButtonGroup(self);group.setExclusive(True);buttons={}
            for i,(text,value) in enumerate(choices):
                b=self.button(text,lambda checked=False,k=key,v=value:self.set_label(k,v));b.setCheckable(True);group.addButton(b);buttons[value]=b;grid.addWidget(b,i//cols,i%cols)
                b.setProperty("baseText",text);self.style_choice(b,SELECTION_COLORS[key][value])
            pv.addLayout(grid);fv.addWidget(panel);self.groups[key]=(group,buttons)
            if key=="finding_status":
                self.flow_note=QLabel();self.flow_note.setWordWrap(True);self.flow_note.setObjectName("muted");fv.addWidget(self.flow_note)
        self.peak_panel=QWidget();peaklayout=QVBoxLayout(self.peak_panel);peaklayout.setContentsMargins(0,0,0,0);peaklayout.setSpacing(4)
        self.peak_label=QLabel(tr('Peak stenosis: not set (markers do not substitute)'));self.peak_label.setWordWrap(True);self.peak_label.setObjectName("muted");peaklayout.addWidget(self.peak_label)
        peakrow=QHBoxLayout();peakrow.addWidget(self.button(tr('Set peak at current slice'),self.set_peak));peakrow.addWidget(self.button(tr('Clear'),self.clear_peak));peaklayout.addLayout(peakrow);fv.addWidget(self.peak_panel)
        self.reason_panel=QWidget();reasonlayout=QVBoxLayout(self.reason_panel);reasonlayout.setContentsMargins(0,0,0,0);reasonlayout.setSpacing(4)
        self.section(reasonlayout,tr('5 · Reasons not evaluable (optional)'))
        reasons=QGridLayout();reasons.setSpacing(4);self.reason_buttons={}
        for i,(code,text,tip) in enumerate(REASON_OPTIONS):
            b=self.button(tr(text),lambda checked=False,c=code:self.toggle_reason(c));b.setCheckable(True)
            b.setProperty("baseText",text);b.setToolTip(tr(tip));self.style_choice(b,"#8eb4c2")
            b.setStyleSheet(b.styleSheet()+" QPushButton{padding:4px 5px;min-height:16px;}")
            self.reason_buttons[code]=b;reasons.addWidget(b,i//2,i%2)
        reasons.addWidget(self.button(tr('Clear reasons'),self.clear_reasons),3,1);reasonlayout.addLayout(reasons)
        optional=QLabel(tr('Reasons are optional. Fields 2–4 do not supply a diagnosis for a non-evaluable interval.'));optional.setWordWrap(True);optional.setObjectName("muted");reasonlayout.addWidget(optional);fv.addWidget(self.reason_panel)
        self.legacy_reason_label=QLabel();self.legacy_reason_label.setWordWrap(True);self.legacy_reason_label.setTextFormat(Qt.TextFormat.PlainText);self.legacy_reason_label.setObjectName("muted");self.legacy_reason_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);fv.addWidget(self.legacy_reason_label);self.legacy_reason_label.hide()
        fv.addStretch();scroll.setWidget(fields);rv.addWidget(scroll,1)
        self.summary_label=QLabel();self.summary_label.setObjectName("annotationSummary");self.summary_label.setWordWrap(True);self.summary_label.setTextFormat(Qt.TextFormat.RichText);self.summary_label.setAccessibleName(tr('Before applying: interval annotation summary'))
        self.summary_label.setStyleSheet("QLabel{background:#18252e;border:1px solid #42535d;border-radius:6px;padding:8px;}");rv.addWidget(self.summary_label)
        self.apply_button=self.button(tr('Apply label / S or Enter'),self.apply_current,"apply");rv.addWidget(self.apply_button)
        editrow=QHBoxLayout();editrow.addWidget(self.button(tr('Undo'),self.undo));editrow.addWidget(self.button(tr('Redo'),self.redo));self.delete_button=self.button(tr('Delete interval'),self.delete_selected,"danger");editrow.addWidget(self.delete_button);rv.addLayout(editrow)
        self.current_note=QLabel(tr('Applying clears the finding. Click an existing overlay to edit a saved interval.'));self.current_note.setWordWrap(True);self.current_note.setObjectName("muted");rv.addWidget(self.current_note)
        right.setMinimumWidth(330);right.setMaximumWidth(350);split.addWidget(right);split.setSizes([230,960,330]);self.sync_controls()
        self.statusBar().showMessage(tr('Ready · Research annotation, not clinical diagnosis'))

    def style_choice(self,button,color):
        button.setProperty("selectionColor",color)
        button.setStyleSheet(f"QPushButton:checked{{background:{color};color:#111b24;border:2px solid {color};font-weight:600;}} QPushButton:checked:hover{{border-color:#f2f5f7;}}")

    def make_selected_icon(self):
        # Vector-painted check, independent of installed symbol-font coverage.
        pixmap=QPixmap(16,16);pixmap.fill(Qt.GlobalColor.transparent)
        painter=QPainter(pixmap);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#111b24"),2.2,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(3,8,7,12);painter.drawLine(7,12,13,4);painter.end()
        return QIcon(pixmap)

    def window_text(self):return f"W {self.width_hu:g} / L {self.level_hu:g} HU"

    def update_summary(self):
        choices={"finding_status":dict((value,text) for text,value in FINDINGS),"plaque_composition":dict((value,text) for text,value in COMPOSITIONS),"stenosis_grade":dict((value,text) for text,value in STENOSES),"confidence":dict((value,text) for text,value in CONFIDENCES)}
        def field(key,missing,template=None):
            value=self.label.get(key);text=choices[key].get(value,missing);color=SELECTION_COLORS[key].get(value,"#9eabb6")
            rendered=tr(template,value=text) if template else text
            return f'<span style="color:{color};font-weight:600">{escape(display(rendered))}</span>'
        parts=[field("finding_status",tr("Finding not selected"))]
        finding=self.label.get("finding_status")
        if finding not in ("negative","non_evaluable"):parts.append(field("plaque_composition",tr("Composition not selected")))
        reasons=", ".join(display(tr(text)) for code,text,_ in REASON_OPTIONS if code in self.label.get("reason_codes",[]))
        if finding=="non_evaluable":
            text=tr("Reasons: {reasons}",reasons=reasons) if reasons else tr("Reasons not selected (optional)")
            parts.append('<span style="color:#a7b9c6">'+escape(display(text))+'</span>')
        else:
            parts.extend([field("stenosis_grade",tr("not selected"),"Stenosis: {value}"),field("confidence",tr("not selected"),"Confidence: {value}")])
        self.summary_label.setText(tr('<span style="color:#98aab6">Before applying · {start:.2f}–{end:.2f} mm</span><br>Interval: {summary}',start=self.a,end=self.b,summary=" · ".join(parts)))
        self.summary_label.setToolTip(tr("Reasons: {reasons}",reasons=reasons) if reasons else tr("Reasons not selected (optional; does not prevent applying)"))

    def spin(self,low,high,value,step,suffix):
        spin=QDoubleSpinBox();spin.setRange(low,high);spin.setDecimals(2);spin.setValue(value);spin.setSingleStep(step);spin.setSuffix(suffix);spin.setKeyboardTracking(False);spin.setMinimumWidth(64);spin.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed);return spin

    def image_panel(self,title,canvas,kind):
        frame=QFrame();frame.setObjectName("panel");v=QVBoxLayout(frame);v.setContentsMargins(5,4,5,4);v.setSpacing(2)
        caption=QLabel(title);caption.setToolTip(title);v.addWidget(caption)
        row=QHBoxLayout();row.addStretch()
        if is_macos():
            window_button=self.button(tr('W/L'));window_button.setObjectName(f"windowLevel_{kind}")
            window_button.setToolTip(tr('Trackpad / no middle button: open the panel and drag with the left button to adjust window and level.'))
            window_button.setStyleSheet("padding:2px 4px;min-height:16px;")
            window_button.clicked.connect(lambda checked=False,b=window_button:self.show_window_level(b));row.addWidget(window_button)
        expand=self.button(tr('Expand'),lambda:self.expand_image(kind));expand.setToolTip(tr('Expand / restore this image view'));row.addWidget(expand)
        row.addWidget(self.button(tr('Center'),lambda:canvas.center_observation()))
        row.addWidget(self.button("1×",lambda:canvas.center_observation(True)))
        if kind=="native":
            self.native_rotate_button=self.button("90°",self.rotate_native_display)
            self.native_rotate_button.setObjectName("nativeRotate90")
            self.native_rotate_button.setAccessibleName(tr('Rotate native CT clockwise by 90 degrees'))
            self.update_native_rotation_hint();row.addWidget(self.native_rotate_button)
            row.addWidget(self.button(tr('Locate'),self.native_return))
        # Compact image actions leave room for both stacked canvases at native
        # Windows/Cocoa font metrics and high display scaling.
        for index in range(row.count()):
            control=row.itemAt(index).widget()
            if isinstance(control,QPushButton):control.setStyleSheet("padding:2px 4px;min-height:18px;")
        v.addLayout(row);v.addWidget(canvas,1);self.image_panels[kind]=frame;return frame

    def show_window_level(self,anchor):
        from .widgets import WindowLevelPopover
        if getattr(self,"window_level_popup",None) is not None:
            self.window_level_popup.close();self.window_level_popup.deleteLater()
        self.window_level_popup=WindowLevelPopover(self,self)
        self.window_level_popup.popup_at(anchor.mapToGlobal(QPoint(0,anchor.height())))

    def expand_image(self,kind):
        self.expanded_kind=None if self.expanded_kind==kind else kind
        target=self.expanded_kind
        for key,panel in self.image_panels.items():panel.setVisible(target is None or key==target)
        self.right_images.setVisible(target!="cpr")
        if target is None:self.images_splitter.setSizes([510,355]);self.right_images.setSizes([310,265])

    def _observed_cpr_is_visible(self):
        if (not self.path or not self.cpr.isVisible()
                or any(view.gesture for view in self.canvases+[self.track])):
            return False
        rectangle=self.cpr.image_rect()
        return (0<=self.cpr.screen_s(self.s)<=self.cpr.height()
                and rectangle.right()>=0 and rectangle.left()<=self.cpr.width())

    def _preserve_observed_cpr_after_layout(self,was_visible):
        if not was_visible:return
        def settled():
            if (self._closing or self.loading or not self.path
                    or any(view.gesture for view in self.canvases+[self.track])):
                return
            # Visibility changes post a layout request. Re-anchor only after the
            # resulting image-rectangle scale is known; manual offscreen pans
            # never request this callback and remain under the reader's control.
            self.centralWidget().layout().activate()
            self.cpr.ensure_observed()
            self.schedule_view_save()
        QTimer.singleShot(0,settled)

    def toggle_sidebar(self):
        was_visible=self._observed_cpr_is_visible()
        self.sidebar.setVisible(not self.sidebar.isVisible())
        self._preserve_observed_cpr_after_layout(was_visible)

    def toggle_neighbors(self):
        was_visible=self._observed_cpr_is_visible()
        visible=not self.neighbor_container.isVisible()
        if visible and self.height()<700:
            self.neighbor_container.hide();self.neighbor_controls.hide()
            self.hint(tr('Increase the window height before showing neighbors, or expand an individual image view.'))
            return
        self.neighbor_container.setVisible(visible);self.neighbor_controls.setVisible(visible)
        self._preserve_observed_cpr_after_layout(was_visible)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if not hasattr(self,"sidebar"):return
        compact_width=self.width()<1120;compact_height=self.height()<700
        if compact_width!=self._auto_compact_width:
            self.sidebar.setVisible(not compact_width);self._auto_compact_width=compact_width
        self.subtitle.setVisible(self.width()>=1200)
        if compact_height!=self._auto_compact_height:
            self.neighbor_container.setVisible(not compact_height);self.neighbor_controls.setVisible(not compact_height);self._auto_compact_height=compact_height
        for thumb in self.neighbors:thumb.setFixedHeight(80 if self.height()<850 else 106)

    def _shortcuts(self):
        # Portable Ctrl tokens are physical Command on Cocoa, per Qt's mapping.
        bindings=[("Ctrl+Z",self.undo),("Ctrl+Shift+Z",self.redo),("Ctrl+S",self.save_view)]
        if not is_macos():bindings.append(("Ctrl+Y",self.redo))
        self.reader_shortcuts=[]
        for key,fn in bindings:
            shortcut=QShortcut(QKeySequence(key),self);shortcut.setAutoRepeat(False)
            shortcut.activated.connect(lambda f=fn:f() if not self.text_focus() else None);self.reader_shortcuts.append(shortcut)
        if is_macos():
            menu=QMenu("Coronary Annotation Studio",self);self.menuBar().addMenu(menu)
            self.quit_action=QAction(tr('Quit Coronary Annotation Studio'),self)
            self.quit_action.setMenuRole(QAction.MenuRole.QuitRole)
            self.quit_action.setShortcut(QKeySequence("Ctrl+Q"));self.quit_action.setAutoRepeat(False)
            # Never connect Quit to app.quit(): closeEvent must save the draft
            # and may veto exit on a disk error while preserving the DB lock.
            self.quit_action.triggered.connect(self.close);menu.addAction(self.quit_action)

    def text_focus(self):return isinstance(QApplication.focusWidget(),(QLineEdit,QAbstractSpinBox))

    def eventFilter(self,watched,event):
        # S works after clicking a label button too, without stealing text input
        # or shortcuts from dialogs / other application windows.
        if (event.type()==QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_S,Qt.Key.Key_Z,Qt.Key.Key_Return,Qt.Key.Key_Enter,Qt.Key.Key_Escape)
                and event.modifiers()==Qt.KeyboardModifier.NoModifier
                and isinstance(watched,QWidget) and watched.window() is self
                and QApplication.activeModalWidget() is None and QApplication.activePopupWidget() is None and not self.text_focus()):
            if not event.isAutoRepeat():
                if event.key()==Qt.Key.Key_Escape:self.exit_editing()
                elif event.key()==Qt.Key.Key_Z:self.toggle_reference_lines()
                else:self.apply_current()
            return True
        return super().eventFilter(watched,event)

    def handle_view_key(self,event):
        if self.text_focus() or QApplication.activeModalWidget() is not None or QApplication.activePopupWidget() is not None:return False
        key=event.key()
        plain_keys=(Qt.Key.Key_Space,Qt.Key.Key_Return,Qt.Key.Key_Enter,Qt.Key.Key_H,Qt.Key.Key_Z,Qt.Key.Key_Left,Qt.Key.Key_Right,Qt.Key.Key_Up,Qt.Key.Key_Down)
        if key in plain_keys and event.modifiers()!=Qt.KeyboardModifier.NoModifier:return False
        delete_keys=(Qt.Key.Key_Delete,Qt.Key.Key_Backspace) if is_macos() else (Qt.Key.Key_Delete,)
        if key in delete_keys and is_macos() and (QApplication.focusWidget() not in self.canvases+[self.track] or event.modifiers()!=Qt.KeyboardModifier.NoModifier):return False
        if event.isAutoRepeat() and key in (Qt.Key.Key_Space,Qt.Key.Key_Return,Qt.Key.Key_Enter,Qt.Key.Key_S,Qt.Key.Key_Z,*delete_keys):return True
        if key==Qt.Key.Key_Z:self.toggle_reference_lines();return True
        if key==Qt.Key.Key_S and event.modifiers()==Qt.KeyboardModifier.NoModifier:self.apply_current();return True
        if key==Qt.Key.Key_Space:self.bookmark();return True
        if key in (Qt.Key.Key_Return,Qt.Key.Key_Enter):self.apply_current();return True
        if key in delete_keys:self.delete_selected();return True
        if key==Qt.Key.Key_Escape:
            self.exit_editing()
            return True
        if key==Qt.Key.Key_H:self.set_hide_overlays(True);return True
        if key in (Qt.Key.Key_Left,Qt.Key.Key_Up):self.observe(self.s-.25);return True
        if key in (Qt.Key.Key_Right,Qt.Key.Key_Down):self.observe(self.s+.25);return True
        return False

    def keyReleaseEvent(self,event):
        if event.key()==Qt.Key.Key_H and not event.isAutoRepeat():self.set_hide_overlays(self.overlay_box.isChecked())
        else:super().keyReleaseEvent(event)

    def hint(self,text):self.statusBar().showMessage(text)

    def error(self,title,error):
        self.save_status.setText(tr("Operation incomplete"))
        message=error.args[0] if isinstance(error,Exception) and error.args else error
        raw=str(error)
        text=error_message(error)
        if "\n" in raw or (self.languages.language!="en" and display(text)==raw):
            text=tr("This operation could not be completed. Open the technical details for the original error.")
        box=QMessageBox(self);box.setIcon(QMessageBox.Icon.Warning);box.setWindowTitle(title);box.setText(text)
        box.setDetailedText(raw);ok=box.addButton(tr("OK"),QMessageBox.ButtonRole.AcceptRole);box.setDefaultButton(ok);box.exec()

    def choose_package(self):
        path=QFileDialog.getExistingDirectory(self,tr('Open a batch: select an extracted package folder (a parent folder includes its child packages)'))
        if path:self.import_package(Path(path))

    def import_package(self,root,preferred_case=None):
        if self.loading:return False
        workspace=None
        try:
            project=self._project_for_package(root);root=Path(project["root"]).resolve()
            additions=self._package_cases(root,{})
            switching=project["project_id"]!=self.project_id
            if switching:
                if self.project_id and not self._confirm_project_switch(project):return False
                if not self.save_view():return False
                workspace=self._prepare_project_workspace(project)
            elif not self.guard_draft() or not self.save_view():return False
            roots=[str(root)] if switching else list(dict.fromkeys(self.package_roots+[str(root)]))
            target=str(preferred_case) if preferred_case is not None else str(self.case.case_id) if self.case and not switching else next(iter(additions))
            if target not in additions:target=next(iter(additions))
            self._pending_package_switch={"root":str(root),"roots":roots,"cases":additions,"target":target,"workspace":workspace,"project":project}
            if not self.load_case_async(target,case_path=additions[target]):
                self._discard_pending_project();return False
            return True
        except Exception as exc:
            if self._pending_package_switch:self._discard_pending_project()
            elif workspace:workspace["store"].close();workspace["lock"].unlock()
            self.error(tr("Package not loaded"),exc);return False

    def refresh_queue(self):
        active=str(self.case.case_id) if self.case else None
        self.case_list.blockSignals(True);self.case_list.clear()
        for cid in self.case_index:
            st=self.store.load(cid,self.reader_id)
            prefix=tr('✓ Complete · Follow-up') if st.get("case_status")=="complete_with_gaps" else tr('✓ Complete') if st.get("case_status")=="complete" else tr('In progress') if st.get("annotations") else tr('Unannotated')
            item=QListWidgetItem(tr("{status} · Case {case_id}",status=prefix,case_id=cid));item.setData(Qt.ItemDataRole.UserRole,cid);self.case_list.addItem(item)
            if cid==active:self.case_list.setCurrentItem(item)
        self.case_list.blockSignals(False)
        unavailable=tr(' · {p0} packages unavailable' ,p0=len(self.unavailable_package_roots)) if self.unavailable_package_roots else ""
        self.queue_count.setText(tr("{count} cases · Reader {reader} · {unavailable} packages unavailable",count=len(self.case_index),reader=self.reader_id,unavailable=len(self.unavailable_package_roots)))
        self.package_caption.setText(tr("Directory: {name}",name=Path(self.active_package_root).name if self.active_package_root else tr("not opened")))
        self.package_caption.setToolTip(tr("{directory}\nOnly cases in this package are listed. Each project has its own database.",directory=self.active_package_root or tr("Select an extracted package folder")))
        self.queue_count.setToolTip(tr('Only the current directory is shown. Completed and incomplete cases remain listed. Reopen another batch to continue its saved annotations.'))

    def select_case_in_queue(self,cid):
        for i in range(self.case_list.count()):
            item=self.case_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole)==cid:
                self.case_list.blockSignals(True);self.case_list.setCurrentItem(item);self.case_list.blockSignals(False)
                if not self.case or str(self.case.case_id)!=cid:
                    if not self.load_case_async(cid):self.refresh_queue()
                return

    def choose_case_item(self,current,previous):
        if not current:return
        cid=current.data(Qt.ItemDataRole.UserRole)
        if self.case and str(self.case.case_id)==cid:return
        if not self.guard_draft():
            self.case_list.blockSignals(True);self.case_list.setCurrentItem(previous);self.case_list.blockSignals(False);return
        if not self.load_case_async(cid):
            self.case_list.blockSignals(True);self.case_list.setCurrentItem(previous);self.case_list.blockSignals(False)

    def change_reader(self):
        value=self.reader_box.text().strip()
        if value==self.reader_id:return
        if not value or any(c in value for c in '/\\:*?"<>|'):
            self.reader_box.setText(self.reader_id);return
        if not self.guard_draft():self.reader_box.setText(self.reader_id);return
        if not self.save_view():self.reader_box.setText(self.reader_id);return
        try:
            candidate=self.store.load(str(self.case.case_id),value,self.source_info()) if self.case else None
        except Exception as exc:
            self.reader_box.setText(self.reader_id);self.error(tr('Reader not changed'),exc);return
        if not self._save_session(reader_id=value):self.reader_box.setText(self.reader_id);return
        self.reader_id=value
        if self.case:
            self.state=candidate
            self.restore_view(self.state.get("view_state",{}));self.sync_controls();self.invalidate_render();self.render_images()
        self.refresh_queue()

    def load_case_async(self,cid,case_path=None):
        if self.loading and self.requested_case==cid:return True
        if not self.save_view():return False
        self.load_generation+=1;self.render_generation+=1
        if self._pending_package_switch is not None:self._pending_package_switch["generation"]=self.load_generation
        self.requested_case=cid
        self.loading=True;self.save_status.setText(tr('Loading case {p0}…' ,p0=cid));self.case_title.setText(tr('Case {p0} · Loading native HU and geometry' ,p0=cid))
        self.apply_button.setEnabled(False)
        self.centralWidget().setEnabled(False)
        path=case_path if case_path is not None else self.case_index[cid]
        self.run_worker(self.load_generation,"load",lambda:(load_case(path),path))
        return True

    def run_worker(self,generation,kind,fn):
        worker=Worker(generation,kind,fn);self.pending_workers.add(worker)
        worker.signals.done.connect(self.worker_ready);worker.signals.failed.connect(self.worker_failed)
        worker.signals.done.connect(lambda *args,w=worker:self.pending_workers.discard(w))
        worker.signals.failed.connect(lambda *args,w=worker:self.pending_workers.discard(w))
        self.pool.start(worker)

    @Slot(int,str,object)
    def worker_ready(self,generation,kind,result):
        if self._closing:return
        if kind=="load":
            if generation!=self.load_generation:return
            candidate,candidate_path=result
            try:
                pending=self._pending_package_switch
                if pending and (pending.get("generation")!=generation or str(candidate.case_id)!=pending["target"] or Path(candidate_path).resolve()!=pending["cases"][pending["target"]].resolve()):
                    raise ValueError(tr('The candidate case does not match the requested package. The directory was not changed.'))
                source=self.source_info(candidate,candidate_path)
                workspace=pending.get("workspace") if pending else None
                expected_project=pending["project"]["project_id"] if pending else self.project_id
                if source.get("project_id")!=expected_project:raise ValueError(tr("Loaded geometry belongs to another project."))
                target_store=workspace["store"] if workspace else self.store
                candidate_state=target_store.load(str(candidate.case_id),self.reader_id,source)
                scope={"package_roots":pending["roots"],"active_package_root":pending["root"],"active_package_cases":list(pending["cases"])} if pending else {}
                if not self._save_session(active_case_id=str(candidate.case_id),workspace=workspace,**scope):
                    raise OSError(tr('Could not save the candidate session. The case was not changed.'))
            except Exception:
                self.worker_failed(generation,"load",traceback.format_exc());return
            if pending:
                if workspace:
                    old_store,old_lock=self.store,self._db_lock
                    self.store,self._db_lock=workspace["store"],workspace["lock"]
                    self.db_path,self.session_path=workspace["db_path"],workspace["session_path"]
                    self.project,self.project_id=pending["project"],pending["project"]["project_id"]
                    self._project_databases[self.project_id]=self.db_path
                    old_store.close();old_lock.unlock()
                self.package_roots=pending["roots"];self.active_package_root=pending["root"]
                self.case_index=pending["cases"];self.active_package_cases=list(self.case_index)
                self.unavailable_package_roots=[];self.session_notice=""
            self._pending_package_switch=None
            self.case,self.case_path,self.state=candidate,candidate_path,candidate_state
            self.loading=False;self.long_key=None;self.centralWidget().setEnabled(True)
            self.path_id=next(iter(self.case.paths));self._rebuild_path_buttons();self.s=5.;self.a=0.;self.b=10.;self.angle_deg=0.;self.offset_mm=0.;self.native_z=0
            view=self.state.get("view_state",{})
            self.restore_view(view);self._follow_pending=not bool(view);self.sync_controls();self.render_images()
            self.save_status.setText(tr('Loaded · Labels save automatically'))
            self.case_title.setText(tr('Case {p0} · {p1} · Native HU' ,p0=self.case.case_id,p1=self.path_id))
            self.update_geometry_status();self.refresh_queue();self.show_session_notice()
        elif kind=="render":
            if generation!=self.render_generation:return
            longitudinal,cross,native,neighbors,key=result
            if longitudinal is not None:self.cpr.set_hu(longitudinal);self.long_key=key
            self.cross.set_hu(cross)
            # Free native scrolling may have happened while CPR sampling was in flight.
            self.native.set_hu(self.case.native.axial(self.native_z))
            for thumb,(s,hu) in zip(self.neighbors,neighbors):thumb.set_hu(s,hu)
            if self._follow_pending:self.cpr.ensure_observed();self._follow_pending=False
            self.render_busy=False;self.retry_render_button.hide();self.sync_controls()
            self.track.update();self.save_status.setText(tr('Loaded · Labels save automatically'));self.show_session_notice()

    @Slot(int,str,str)
    def worker_failed(self,generation,kind,error):
        if self._closing:return
        if kind=="load" and generation!=self.load_generation:return
        if kind=="render" and generation!=self.render_generation:return
        if kind=="load":self._discard_pending_project()
        # Loading invalidated in-flight renders of the prior case. If the new
        # load fails, old pixels may no longer match its current observation or
        # angle: keep applying disabled until that retained case is resampled.
        self.loading=False;self.render_busy=(kind=="render" or self.case is not None)
        self.centralWidget().setEnabled(True);self.refresh_queue();self.sync_controls()
        self.retry_render_button.setVisible(self.case is not None)
        self.case_title.setText(tr('Case {p0} · {p1} · Previous case retained' ,p0=self.case.case_id,p1=self.path_id) if self.case else tr('No case loaded'))
        if kind=="load" and self.case is not None:
            self.long_key=None;self.render_images()
        self.error(tr('Image loading / geometry validation failed'),error)
        if kind=="render":self.hint(tr('Images are not current; applying is disabled. Retry images, switch paths or export saved labels. Your draft is retained.'))

    def retry_render(self):
        if not self.case or self.loading:return
        self.long_key=None;self.render_timer.stop();self.invalidate_render();self.render_images()

    def source_info(self,case=None,case_path=None):
        from .package import source_for_case
        return source_for_case(case or self.case,case_path or self.case_path)

    def annotation_allowed(self):
        return bool(self.case and self.path_id in self.case.manifest.get("annotation_scope",[]))

    def _rebuild_path_buttons(self):
        for button in self.path_buttons.values():
            self.path_navigation.removeWidget(button);button.deleteLater()
        self.path_buttons={}
        for path_id,path in self.case.paths.items():
            metadata=self.case.manifest.get("paths",{}).get(path_id,{})
            title=metadata.get("display_name") or path_id
            button=self.button(title,lambda checked=False,p=path_id:self.change_path(p))
            button.setCheckable(True);button.setObjectName("path_"+path_id)
            button.setAccessibleName(tr("Path {name}",name=title))
            self.path_navigation.addWidget(button);self.path_buttons[path_id]=button

    def _choose_language(self,index):
        language=self.language_box.itemData(index)
        try:self.languages.set_language(language)
        except (OSError,ValueError,RuntimeError) as exc:
            with QSignalBlocker(self.language_box):self.language_box.setCurrentIndex(self.language_box.findData(self.languages.language))
            self.error(tr("Language was not changed"),exc)

    def update_legacy_reason_display(self):
        if not hasattr(self,"legacy_reason_label"):return
        old_reason=self.label.get("reason","")
        applicable=label_applicability(self.label)
        legacy_codes=[tr(text) for code,text,_ in REASON_OPTIONS if code in self.label.get("reason_codes",[]) and not applicable["reasons_enabled"]]
        legacy_text=tr("Legacy note (retained): {note}\nLegacy reasons (retained): {reasons}",note=old_reason,reasons=", ".join(display(x) for x in legacy_codes)) if old_reason or legacy_codes else ""
        self.legacy_reason_label.setText(legacy_text);self.legacy_reason_label.setVisible(bool(legacy_text))

    def _language_changed(self,language):
        if not hasattr(self,"language_box"):return
        with QSignalBlocker(self.language_box):self.language_box.setCurrentIndex(self.language_box.findData(language))
        # Display-only refresh: do not call sync_controls/refresh_queue, write a
        # session or replace the current model on a language change.
        self.update_summary();self.update_completion_display();self.update_native_rotation_hint();self.update_legacy_reason_display()
        if self.case:self.update_geometry_status()
        for canvas in self.canvases+[self.track]+self.neighbors:canvas.update()
        self.update()

    def render_images(self):
        if not self.path or self.loading:return
        self.render_generation+=1;gen=self.render_generation;self.render_busy=True;self.apply_button.setEnabled(False)
        path,case,s,angle,offset,z=self.path,self.case,self.s,self.angle_deg,self.offset_mm,self.native_z
        key=(str(case.case_id),self.path_id,angle,offset);need_long=key!=self.long_key
        step=self.neighbor_step
        def render():
            long=path.longitudinal(angle,offset) if need_long else None
            cross=path.cross_section(s,angle)
            native=case.native.axial(z)
            neighbors=[(min(path.length_mm,max(0,s+k*step)),path.cross_section(min(path.length_mm,max(0,s+k*step)),angle)) for k in range(-3,4)]
            return long,cross,native,neighbors,key
        self.run_worker(gen,"render",render)

    def invalidate_render(self):
        # Invalidate at parameter change, not after the debounce delay.
        self.render_generation+=1;self.render_busy=True;self.apply_button.setEnabled(False)
        self.save_status.setText(tr('Updating images…'))

    def observe(self,s):
        if not self.path or self.loading:return
        self.invalidate_render()
        self.s=float(min(self.path.length_mm,max(0,s)));self.selected_marker=None
        idx=self.case.native.world_to_index(self.path.point(self.s));self.native_z=int(np.clip(round(idx[2]),0,self.case.native.array_zyx.shape[0]-1))
        self._follow_pending=True;self.cpr.ensure_observed();self.track.update();self.cpr.update();self.render_timer.start();self.schedule_view_save()

    def step_native(self,step):
        if not self.case:return
        self.native_z=int(np.clip(self.native_z+step,0,self.case.native.array_zyx.shape[0]-1))
        self.native.set_hu(self.case.native.axial(self.native_z));self.schedule_view_save()

    def native_return(self):
        self.observe(self.s);self.native.center_observation()

    def update_native_rotation_hint(self):
        self.native_rotate_button.setToolTip(tr('Native CT display: {p0}°. Click to rotate clockwise by 90°. The initial view is 0°; the setting persists across paths and restarts. Image data and annotation coordinates remain unchanged.' ,p0=self.native_rotation_quarters * 90))

    def rotate_native_display(self):
        # Keep the native pixel at the viewport centre stationary while rotating.
        # This is independent of the CPR sampling angle and free CT slice index.
        canvas=self.native;centre=QPointF(canvas.width()/2,canvas.height()/2)
        pivot=canvas.native_screen_to_index_unbounded(centre)
        self.native_rotation_quarters=(self.native_rotation_quarters+1)%4
        canvas.rewindow()
        if pivot is not None:
            canvas.pan+=centre-canvas.native_index_to_screen(*pivot);canvas.update()
        self.update_native_rotation_hint();self.schedule_view_save()
        if self._save_session():self.hint(tr('Native CT display: {p0}° · Retained across paths; annotation coordinates unchanged' ,p0=self.native_rotation_quarters * 90))

    def toggle_reference_lines(self):
        self.reference_lines_visible=not self.reference_lines_visible
        self.reference_lines_button.setChecked(self.reference_lines_visible)
        for canvas in self.canvases:canvas.update()
        if self._save_session():self.hint(tr("Green reference lines are visible. Press Z to hide them; selection, markers and labels remain unchanged.") if self.reference_lines_visible else tr("Green reference lines are hidden. Press Z to show them; selection, markers and labels remain unchanged."))

    def native_pick(self,pos,canvas):
        if not self.case or not self.path:return
        xy=canvas.native_screen_to_index(pos)
        if xy is None:return
        idx=(*xy,self.native_z)
        point=self.case.native.index_to_world(idx);s=self.path.nearest_s(point,max_distance_mm=5)
        if s is None:self.hint(tr('The selected point is not near the current path. No branch was switched.'))
        else:self.observe(s)

    def set_neighbor_step(self,value):self.neighbor_step=value;self.invalidate_render();self.render_timer.start()

    def set_sampling(self,angle=None,offset=None):
        self.invalidate_render()
        if angle is not None:self.angle_deg=float((angle+180)%360-180)
        if offset is not None:self.offset_mm=float(np.clip(offset,-10,10))
        self._syncing=True;self.angle_spin.setValue(self.angle_deg);self.offset_spin.setValue(self.offset_mm);self._syncing=False
        self.render_timer.start();self.schedule_view_save()

    def set_window(self,width,level):
        self.width_hu=float(np.clip(width,1,10000));self.level_hu=float(np.clip(level,-2000,5000))
        for canvas in self.canvases:canvas.rewindow()
        for thumb in self.neighbors:thumb.rewindow()
        popup=getattr(self,"window_level_popup",None)
        if popup is not None:popup.refresh()
        self.schedule_view_save()

    def lm_end(self):
        lm=self.case.canonical_lm if self.case else {}
        return float(lm.get("verified_end_mm",0)) if lm.get("status")=="verified" and {"LAD","LCX"}.issubset(self.case.manifest.get("annotation_scope",[])) else 0.

    def path_records(self):
        if not self.state:return []
        records=[r for r in self.state["annotations"] if r["path_id"]==self.path_id]
        if self.path_id=="LCX" and self.lm_end()>0:
            records += [r for r in self.state["annotations"] if r["path_id"]=="LAD" and r.get("canonical_anatomy_id")=="LM" and r["s_end_mm"]<=self.lm_end()+1e-6]
        return sorted(records,key=lambda r:r["s_start_mm"])

    def path_markers(self):return [m for m in self.state.get("markers",[]) if m["path_id"]==self.path_id] if self.state else []
    def path_rereview(self):return [r for r in self.state.get("rereview_intervals",[]) if r["path_id"]==self.path_id] if self.state else []

    def change_path(self,path_id):
        if not self.case or path_id==self.path_id or path_id not in self.case.paths:return
        if not self.guard_draft():return
        self.path_id=path_id;self.clear_draft();self.a=0.;self.b=min(10,self.path.length_mm);self.long_key=None
        self.s=min(5,self.path.length_mm);self.observe(self.s);self.cpr.center_observation();self.sync_controls();self.update_geometry_status()
        self.case_title.setText(tr('Case {p0} · {p1} · Native HU' ,p0=self.case.case_id,p1=self.path_id))

    def update_geometry_status(self):
        lm=self.case.canonical_lm or {};status=lm.get("status","not_present")
        relation=tr("Shared LM 0–{end:.2f} mm; edit on LAD / mirrored on LCX",end=self.lm_end()) if status=="verified" else tr("No shared LM") if status=="not_present" else tr("LM mapping requires technical review; no automatic mirroring.")
        scope=tr("Annotation enabled") if self.annotation_allowed() else tr("View-only path")
        self.geometry_status.setText(tr("{scope} · {relationship} · CPR and native coordinates use LPS millimeters",scope=scope,relationship=relation))

    def draft_snapshot(self):
        return {"a":self.a,"b":self.b,"label":deepcopy(self.label),"editing_id":self.editing_id,"anatomy":self.legacy_anatomy,"dirty":self.dirty,"reason":self.label.get("reason","")}

    def restore_draft(self,draft):
        self.a=draft.get("a",self.a);self.b=draft.get("b",self.b);self.label=deepcopy(draft.get("label",{}));self.editing_id=draft.get("editing_id");self.dirty=draft.get("dirty",False)
        self.legacy_anatomy=draft.get("anatomy","")
        if "reason" not in self.label and draft.get("reason"):self.label["reason"]=draft["reason"]
        self.sync_controls();self.update_overlays()

    def editing_record(self):
        return next((r for r in self.state.get("annotations",[]) if r["annotation_id"]==self.editing_id),None) if self.state else None

    def edit_requires_review(self):
        rec=self.editing_record()
        return bool(rec and effective_reader_review_required(rec))

    def exit_editing(self):
        active=[c for c in self.canvases+[self.track] if c.gesture]
        if active:
            for canvas in active:canvas.cancel_gesture()
            return False
        if not self.guard_draft():return False
        self.clear_draft();self.hint(tr('Exited editing. The selection is retained; applying creates a new interval label.'))
        return True

    def finish_draft_gesture(self,before):
        after=self.draft_snapshot()
        if after!=before:self.draft_undo.append(before);self.draft_redo.clear();self.schedule_view_save()

    def numeric_range(self,a,b,mode):
        # Spin boxes display 2 decimals; choose the true end when a displayed
        # endpoint is entered, without rounding all stored interval positions.
        if self.path:
            length=self.path.length_mm
            if abs(a-round(length,2))<1e-8:a=length
            if abs(b-round(length,2))<1e-8:b=length
        before=self.draft_snapshot();self.drag_range(a,b,mode,1,True);self.finish_draft_gesture(before)

    def drag_range(self,a,b,mode,mm_per_pixel,disabled=False):
        if not self.path:return
        length=self.path.length_mm;gap=min(.01,length/100)
        candidates=[m["s_mm"] for m in self.path_markers()]
        for r in self.path_records():
            if r["annotation_id"]!=self.editing_id:candidates.extend([r["s_start_mm"],r["s_end_mm"]])
        target=None
        if mode=="body":
            span=b-a;a=min(max(0,a),max(0,length-span));b=a+span
            sa,ta=snap_endpoint(a,candidates,mm_per_pixel,disabled=disabled)
            sb,tb=snap_endpoint(b,candidates,mm_per_pixel,disabled=disabled)
            options=[(abs(sa-a),sa-a,ta),(abs(sb-b),sb-b,tb)]
            options=[o for o in options if o[2] is not None and a+o[1]>=0 and b+o[1]<=length]
            if options:_,shift,target=min(options,key=lambda o:(o[0],o[2]));a+=shift;b+=shift
        elif mode=="start":
            a,target=snap_endpoint(a,candidates,mm_per_pixel,held=self.snap_target,disabled=disabled);a=min(b-gap,max(0,a))
            if target is not None and abs(a-target)>1e-6:target=None
        else:
            b,target=snap_endpoint(b,candidates,mm_per_pixel,held=self.snap_target,disabled=disabled);b=max(a+gap,min(length,b))
            if target is not None and abs(b-target)>1e-6:target=None
        changed=abs(self.a-a)>1e-9 or abs(self.b-b)>1e-9
        self.a=float(a);self.b=float(b);self.dirty=self.dirty or changed;self.selected_marker=None;self.snap_target=target
        self.snap_caption=tr('Snap {p0:.2f} mm · Hold {p1} to release' ,p0=target,p1=alternate_modifier_label()) if target is not None else ""
        peak=self.label.get("s_peak_stenosis_mm")
        if peak is not None and not self.a<=peak<self.b:
            self.label.pop("s_peak_stenosis_mm",None);self.hint(tr('The previous peak stenosis is outside the selection. Set it again.'))
        self.sync_controls();self.update_overlays();self.schedule_view_save()

    def clear_snap(self):self.snap_target=None;self.snap_caption="";self.track.update()
    def update_overlays(self):self.cpr.update();self.cross.update();self.native.update();self.track.update()
    def set_hide_overlays(self,value):self.hide_overlays=value;self.cpr.update()

    def set_label(self,key,value):
        if self.case and not self.annotation_allowed():return
        before=self.draft_snapshot();self.label=transition_label(self.label,key,value)
        self.dirty=self.dirty or self.label!=before["label"];self.sync_controls();self.finish_draft_gesture(before)

    def typical_normal(self):
        if self.case and not self.annotation_allowed():return
        before=self.draft_snapshot();self.label={"finding_status":"negative","plaque_composition":None,"stenosis_grade":"0","confidence":"high","reason":"","reason_codes":[],"entry_method":"typical_normal_preset"}
        self.dirty=self.dirty or self.label!=before["label"];self.sync_controls();self.finish_draft_gesture(before)

    def toggle_reason(self,code):
        if not label_applicability(self.label)["reasons_enabled"]:return
        before=self.draft_snapshot();selected=set(self.label.get("reason_codes",[]))
        if code in selected:selected.remove(code)
        else:selected.add(code)
        self.label["reason_codes"]=[c for c,_,_ in REASON_OPTIONS if c in selected]
        self.dirty=True;self.sync_controls();self.finish_draft_gesture(before)

    def clear_reasons(self):
        if not self.label.get("reason_codes"):return
        before=self.draft_snapshot();self.label["reason_codes"]=[];self.dirty=True;self.sync_controls();self.finish_draft_gesture(before)
    def set_peak(self):
        if not self.annotation_allowed():return
        if not label_applicability(self.label)["peak_enabled"]:self.hint(tr('Peak stenosis is available only for an evaluable lesion with a selected nonzero stenosis grade.'));return
        if not self.a<=self.s<self.b:self.hint(tr('Peak stenosis must be inside the current selection.'));return
        before=self.draft_snapshot();self.label["s_peak_stenosis_mm"]=self.s;self.dirty=self.dirty or self.label!=before["label"];self.sync_controls();self.finish_draft_gesture(before)
    def clear_peak(self):
        if "s_peak_stenosis_mm" not in self.label:return
        before=self.draft_snapshot();self.label.pop("s_peak_stenosis_mm",None);self.dirty=True;self.sync_controls();self.finish_draft_gesture(before)

    def sync_controls(self):
        if not hasattr(self,"groups"):return
        self._syncing=True
        applicable=label_applicability(self.label);finding=self.label.get("finding_status")
        for key,(group,buttons) in self.groups.items():
            group.setExclusive(False)
            for value,button in buttons.items():
                button.setChecked(self.label.get(key)==value)
                button.setIcon(self.selected_icon if button.isChecked() else QIcon())
                enabled=key=="finding_status" or applicable[{"plaque_composition":"composition_enabled","stenosis_grade":"stenosis_enabled","confidence":"confidence_enabled"}[key]]
                button.setEnabled(enabled and (self.case is None or self.annotation_allowed()))
            group.setExclusive(True)
            visible=key=="finding_status" or (applicable["finding_selected"] and finding!="non_evaluable" and not (key=="plaque_composition" and finding=="negative"))
            self.field_sections[key].setVisible(visible)
        self.peak_panel.setVisible(applicable["peak_enabled"])
        self.reason_panel.setVisible(applicable["reasons_enabled"])
        self.flow_note.setText(tr('Not evaluable: skip fields 2–4; reasons are optional. This is not saved as normal or a low-confidence diagnosis.') if finding=="non_evaluable" else tr('Normal: no plaque and 0% stenosis. Select confidence.') if finding=="negative" else tr('Choose from top to bottom. Confirm confidence again after changing composition or stenosis.') if applicable["finding_selected"] else tr('Select an interval finding to show the applicable choices.'))
        for code,button in self.reason_buttons.items():
            button.setEnabled(applicable["reasons_enabled"] and self.annotation_allowed())
            button.setChecked(code in self.label.get("reason_codes",[]));button.setIcon(self.selected_icon if button.isChecked() else QIcon())
        self.update_legacy_reason_display()
        limit=self.path.length_mm if self.path else 999
        for spin in (self.start_spin,self.end_spin):spin.setMaximum(limit)
        self.start_spin.setValue(self.a);self.end_spin.setValue(self.b)
        for n,b in self.path_buttons.items():b.setChecked(n==self.path_id);b.setEnabled(self.case is not None and not self.loading)
        self.draft_title.setText(tr('Editing interval {p0}' ,p0=self.editing_id[:8]) if self.editing_id else tr('Draft · New interval'))
        self.exit_edit_button.setVisible(bool(self.editing_id))
        review=self.edit_requires_review();rec=self.editing_record()
        self.apply_button.setText(tr('Confirm and apply / S or Enter') if review else tr('Save changes / S or Enter') if self.editing_id else tr('Apply label / S or Enter'))
        note=tr('Clipping retained the original interval summary. Review this interval and choose Confirm and apply. No extra checkbox is required.') if review else tr('Editing a saved interval. Moving or shortening it leaves the old range unannotated. Exit edit keeps the selection for a new label.') if self.editing_id else tr('Applying clears the finding. Click an existing overlay to edit a saved interval.')
        if rec and (rec.get("provenance",{}).get("label_scope")=="group_summary_only" or rec.get("provenance",{}).get("review_reason")=="canonical_split_group_summary_requires_subsegment_review"):
            original=rec["provenance"].get("source_label_group_summary",{})
            composition=dict((v,t) for t,v in COMPOSITIONS).get(original.get("plaque_composition"),tr('undetermined'))
            stenosis=dict((v,t) for t,v in STENOSES).get(original.get("stenosis_grade"),tr('undetermined'))
            note=tr('Original cross-branch summary: {p0} / maximum stenosis {p1}. This is not the current subinterval judgment. Edit this interval independently or exit editing. The original summary is retained.' ,p0=composition,p1=stenosis)
        if rec and rec.get("provenance",{}).get("training_geometry_eligible") is False:note=tr("{note}\nGeometry issues require technical review and do not prevent saving.",note=note)
        self.current_note.setText(note)
        self.current_note.setStyleSheet("color:#e6bd7e;" if review else "color:#92a2af;")
        peak=self.label.get("s_peak_stenosis_mm");self.peak_label.setText(tr('Peak stenosis: {p0:.2f} mm' ,p0=peak) if peak is not None else tr('Peak stenosis: not set (markers do not substitute)'))
        self.delete_button.setEnabled(bool(self.editing_id or self.selected_marker));self.apply_button.setEnabled(self.annotation_allowed() and not self.loading and not self.render_busy)
        self.update_summary()
        self.update_completion_display()
        self._syncing=False

    def guard_draft(self):
        if not self.dirty:return True
        # An empty new orange range is navigation state, not a diagnosis to force.
        if not self.label and not self.editing_id:return self.save_view()
        dialog=QMessageBox(self);dialog.setWindowTitle(tr('Draft has not been applied'))
        dialog.setText(tr('Save the judgment for this interval?'));dialog.setInformativeText(tr('Discard draft removes only unapplied changes. Saved labels remain intact.'))
        apply=dialog.addButton(tr('Confirm and apply') if self.edit_requires_review() else tr('Apply and continue'),QMessageBox.ButtonRole.AcceptRole)
        discard=dialog.addButton(tr('Discard draft'),QMessageBox.ButtonRole.DestructiveRole)
        keep=dialog.addButton(tr('Return to editing'),QMessageBox.ButtonRole.RejectRole);dialog.setDefaultButton(keep);dialog.setEscapeButton(keep);dialog.exec()
        if dialog.clickedButton() is apply:return self.apply_current()
        if dialog.clickedButton() is discard:self.clear_draft();return True
        return False

    def clear_draft(self):
        self.editing_id=None;self.label={};self.selected_marker=None;self.dirty=False;self.draft_undo.clear();self.draft_redo.clear()
        self.legacy_anatomy="";self.sync_controls();self.update_overlays();self.schedule_view_save()

    def select_annotation(self,annotation_id):
        if not self.guard_draft():return
        rec=next((r for r in self.state["annotations"] if r["annotation_id"]==annotation_id),None)
        if rec is None:return
        if rec["path_id"]!=self.path_id:
            self.path_id=rec["path_id"];self.long_key=None;self.hint(tr('Shared LM: switched to LAD for editing.'))
        self.a=rec["s_start_mm"];self.b=rec["s_end_mm"];self.editing_id=annotation_id;self.selected_marker=None;self.label=deepcopy(rec["label"]);self.dirty=False
        self.legacy_anatomy=rec.get("anatomical_segment","");self.draft_undo.clear();self.draft_redo.clear()
        self.observe(self.label.get("s_peak_stenosis_mm",(self.a+self.b)/2));self.sync_controls();self.update_geometry_status();self.update_overlays()
        self.hint(tr('Review interval selected. Choose Confirm and apply or press S / Enter; no extra checkbox is required.') if self.edit_requires_review() else tr('Saved interval selected. Dragging edits it; Exit edit keeps the selection for a new label.'))

    def select_marker(self,marker_id):
        marker=next((m for m in self.state["markers"] if m["marker_id"]==marker_id),None)
        if marker:self.observe(marker["s_mm"]);self.selected_marker=marker_id;self.sync_controls();self.update_overlays()

    def bookmark(self):
        if not self.state or not self.annotation_allowed():return
        try:
            result=add_marker(self.state,self.path_id,self.s)
            if result==self.state:self.hint(tr('A marker already exists at this position.'));return
            self.state=self.store.commit(result,"add_marker",{"path_id":self.path_id,"s_mm":self.s})
            self.selected_marker=None;self.update_overlays();self.hint(tr('Marker placed at {p0:.2f} mm. Labels unchanged.' ,p0=self.s))
        except Exception as exc:self.error(tr('Marker not saved'),exc)

    def make_annotation(self,a,b,path_id,canonical,group):
        if path_id not in self.case.manifest.get("annotation_scope",[]):raise ValueError(tr("The interval is outside the annotation scope."))
        label=prepare_label_for_submission(self.label);label.setdefault("reason","");label.setdefault("reason_codes",[])
        path=self.case.paths[path_id]
        anchors=[{"role":"start","point_lps_mm":path.point(a).tolist()},{"role":"end","point_lps_mm":path.point(b).tolist()}]
        peak=label.get("s_peak_stenosis_mm")
        if peak is not None:
            if a<=peak<b:anchors.append({"role":"peak_stenosis","point_lps_mm":path.point(peak).tolist()})
            else:label.pop("s_peak_stenosis_mm",None)
        rec={"annotation_id":uuid.uuid4().hex,"label_group_id":group,"path_id":path_id,"canonical_anatomy_id":canonical,"anatomical_segment":canonical,"s_start_mm":a,"s_end_mm":b,"label":label,"native_anchors":anchors,"review_required":False,"provenance":{"reader_id":self.reader_id,"app_version":__version__,"geometry_id":self.case.manifest.get("geometry_id"),"sampling_angle_deg":self.angle_deg,"sampling_offset_mm":self.offset_mm,"stenosis_label_scope":"maximum_within_label_group_not_every_slice","anatomical_segment_source":"canonical_path_only","annotation_protocol_version":PROTOCOL_VERSION,"reason_catalog_version":REASON_CATALOG_VERSION}}
        rec["provenance"]["label_scope"]="interval"
        if self.legacy_anatomy and self.legacy_anatomy!=canonical:
            rec["provenance"]["legacy_anatomical_segment_not_reaffirmed"]=self.legacy_anatomy
        discarded={key:value for key,value in self.label.items() if key in ("plaque_composition","stenosis_grade","confidence","s_peak_stenosis_mm") and value!=label.get(key)}
        if discarded:rec["provenance"]["legacy_inapplicable_fields_not_reaffirmed"]=discarded
        ambiguous=[r for r in self.case.canonical_lm.get("ambiguous_ranges",[]) if r["path_id"]==path_id and max(a,r["s_start_mm"])<min(b,r["s_end_mm"])-1e-6]
        if ambiguous:
            rec["provenance"].update(geometry_mapping_status="path_local_ambiguous",geometry_ambiguous_ranges=deepcopy(ambiguous),training_geometry_eligible=False,technical_qa_owner="research_team")
        if self.case.canonical_lm and self.case.canonical_lm.get("status") not in ("verified","not_present") and path_id in ("LAD","LCX"):
            rec["provenance"].update(geometry_mapping_status="path_local_ambiguous",training_geometry_eligible=False,technical_qa_owner="research_team")
        previous=self.editing_record()
        if previous:
            rec["provenance"]["replaces_annotation_id"]=previous["annotation_id"]
            if effective_reader_review_required(previous):
                rec["review_status"]="reader_confirmed"
                rec["provenance"]["reader_confirmation"]={"method":"apply_selected_interval","source_annotation_id":previous["annotation_id"],"source_review_reasons":reader_review_reasons(previous)}
        return rec

    def apply_current(self):
        if not self.state or self.loading:return False
        if not self.annotation_allowed():self.hint(tr("This path is view-only; it is outside the annotation scope."));return False
        if any(view.gesture for view in self.canvases+[self.track]):
            self.hint(tr('Release the mouse to finish the current drag before applying.'))
            return False
        if self.render_busy:
            self.hint(tr('Wait for the images to finish updating before applying.'))
            return False
        try:
            validate_new_label(prepare_label_for_submission(self.label))
            end=self.lm_end() if self.path_id in ("LAD","LCX") else 0
            segments=[];group=uuid.uuid4().hex
            if end>0 and self.a<end:
                segments.append((self.a,min(self.b,end),"LAD","LM"))
                if self.b>end:segments.append((end,self.b,self.path_id,self.path_id))
            else:segments=[(self.a,self.b,self.path_id,self.path_id)]
            changed=self.state
            # One transaction, even when canonical LM ownership splits the draft.
            for index,(a,b,path,canonical) in enumerate(segments):
                rec=self.make_annotation(a,b,path,canonical,group)
                if len(segments)>1:
                    rec["provenance"]["label_group_interval"]={"path_id":self.path_id,"s_start_mm":self.a,"s_end_mm":self.b}
                    rec["provenance"]["group_peak_stenosis_mm"]=self.label.get("s_peak_stenosis_mm")
                    rec["provenance"]["source_label_group_summary"]=deepcopy(self.label)
                    rec["provenance"]["source_label_group_native_anchors"]=[{"role":role,"point_lps_mm":self.path.point(s).tolist()} for role,s in (("start",self.a),("end",self.b))]
                    if self.label.get("finding_status") in ("positive","uncertain"):
                        # A group maximum/composition is not a new subsegment diagnosis.
                        rec["provenance"].update(label_scope="group_summary_only",training_segment_label_eligible=False,technical_qa_owner="research_team")
                        rec["label"]["plaque_composition"]="uncertain"
                        if rec["label"].get("s_peak_stenosis_mm") is None:rec["label"]["stenosis_grade"]="unable"
                changed=apply_annotation(changed,rec,self.editing_id if index==0 else None)
            # Clipping changes anchors even when an explicit normal diagnosis
            # remains valid. Recompute from the immutable path, not via a reader click.
            for rec in changed["annotations"]:
                if rec.get("provenance",{}).get("native_anchors_require_recompute"):
                    path=self.case.paths[rec["path_id"]]
                    rec["native_anchors"]=[{"role":role,"point_lps_mm":path.point(rec[key]).tolist()} for role,key in (("start","s_start_mm"),("end","s_end_mm"))]
                    rec["provenance"]["native_anchors_require_recompute"]=False
                    rec["provenance"]["native_anchor_recompute_geometry_id"]=self.case.manifest.get("geometry_id")
            changed["case_status"]="in_progress"
            self.state=self.store.commit(changed,"apply",{"editing_id":self.editing_id,"label_group_id":group,"newest_wins":True})
            self.clear_draft();view_saved=self.save_view();self.refresh_queue()
            self.save_status.setText(tr('Saved · Revision {p0}' ,p0=self.state['revision']) if view_saved else tr('Labels saved; draft/session save failed. Keep this window open.'))
            self.hint(tr('Applied; finding cleared. Select the next interval or click a saved overlay to edit.'))
            return True
        except Exception as exc:self.error(tr('Label not applied'),exc);return False

    def delete_selected(self):
        if not self.state or not self.annotation_allowed():return
        try:
            if self.selected_marker:
                changed=delete_marker(self.state,self.selected_marker);self.state=self.store.commit(changed,"delete_marker");self.selected_marker=None
            elif self.editing_id:
                changed=delete_annotation(self.state,self.editing_id);self.state=self.store.commit(changed,"delete_annotation",{"annotation_id":self.editing_id});self.clear_draft();self.refresh_queue()
                self.hint(tr('Interval deleted; this range requires review. {p0}+Z restores it.' ,p0=primary_modifier_label()))
            else:return
            self.sync_controls();self.update_overlays();self.save_view()
        except Exception as exc:self.error(tr('Deletion incomplete'),exc)

    def undo(self):
        if not self.state or not self.annotation_allowed():return
        try:
            if self.draft_undo:
                self.draft_redo.append(self.draft_snapshot());self.restore_draft(self.draft_undo.pop());self.schedule_view_save();return
            if self.dirty and not self.guard_draft():return
            result=self.store.undo(str(self.case.case_id),self.reader_id)
            if result:self.state=result;self.clear_draft();self.refresh_queue();self.hint(tr('Transaction undone; history retained.'))
        except Exception as exc:self.error(tr('Undo failed'),exc)

    def redo(self):
        if not self.state or not self.annotation_allowed():return
        try:
            if self.draft_redo:
                self.draft_undo.append(self.draft_snapshot());self.restore_draft(self.draft_redo.pop());self.schedule_view_save();return
            if self.dirty and not self.guard_draft():return
            result=self.store.redo(str(self.case.case_id),self.reader_id)
            if result:self.state=result;self.clear_draft();self.refresh_queue();self.hint(tr('Transaction redone.'))
        except Exception as exc:self.error(tr('Redo failed'),exc)

    def schedule_view_save(self):
        if self.state and not self.loading and not self._closing:self.save_timer.start()

    def view_state(self):
        return {"path_id":self.path_id,"s_mm":self.s,"angle_deg":self.angle_deg,"offset_mm":self.offset_mm,"window_width_hu":self.width_hu,"window_level_hu":self.level_hu,"native_z":self.native_z,"draft":self.draft_snapshot(),"canvases":{c.kind:{"zoom":c.zoom,"pan":[c.pan.x(),c.pan.y()]} for c in self.canvases}}

    def save_view(self):
        self.save_timer.stop()
        if self.loading:return True
        try:
            if self.state and self.case:
                view=self.view_state();self.store.save_view(str(self.case.case_id),self.reader_id,view);self.state["view_state"]=view
            return self._save_session()
        except Exception as exc:
            self.save_status.setText(tr('Save failed: keep this window open'));self.hint(tr('Draft save failed: {p0}' ,p0=exc));return False

    def restore_view(self,view):
        self.clear_draft()
        self.path_id=view.get("path_id",next(iter(self.case.paths)))
        if self.path_id not in self.case.paths:self.path_id=next(iter(self.case.paths))
        self.s=float(np.clip(view.get("s_mm",5),0,self.path.length_mm));self.a=0;self.b=min(10,self.path.length_mm)
        self.angle_deg=view.get("angle_deg",0.);self.offset_mm=view.get("offset_mm",0.);self.width_hu=view.get("window_width_hu",700.);self.level_hu=view.get("window_level_hu",250.)
        idx=self.case.native.world_to_index(self.path.point(self.s));self.native_z=int(np.clip(view.get("native_z",round(idx[2])),0,self.case.native.array_zyx.shape[0]-1))
        self.restore_draft(view.get("draft",{}))
        for c in self.canvases:
            settings=view.get("canvases",{}).get(c.kind,{})
            c.zoom=settings.get("zoom",1.);pan=settings.get("pan",[0,0]);c.pan.setX(pan[0]);c.pan.setY(pan[1])
        self._syncing=True;self.angle_spin.setValue(self.angle_deg);self.offset_spin.setValue(self.offset_mm);self._syncing=False

    def completion_report(self,refresh=False):
        if not self.case or not self.state:return None
        key=(id(self.state),self.state.get("revision"),self.case.manifest.get("geometry_id"))
        if refresh or key!=self._coverage_cache_key:
            scope=set(self.case.manifest.get("annotation_scope",[]))
            lengths={name:path.length_mm for name,path in self.case.paths.items() if name in scope}
            samples={name:path.distances.tolist() for name,path in self.case.paths.items() if name in scope}
            self._coverage_cache=coverage_report(self.state,lengths,path_samples=samples,canonical_lm=self.case.canonical_lm)
            self._coverage_cache_key=key
        return self._coverage_cache

    def coverage_issues(self,report=None):
        report=report or self.completion_report()
        if not report:return []
        issues=[dict(gap,kind="gap") for gap in report["gaps"]]
        for rec in report["review_required"]:
            if not rec.get("annotation_id") and any(g["path_id"]==rec["path_id"] and g["s_start_mm"]<=rec["s_start_mm"]+1e-6 and g["s_end_mm"]>=rec["s_end_mm"]-1e-6 for g in report["gaps"]):continue
            issues.append(dict(rec,kind="review" if rec.get("annotation_id") else "rereview"))
        return sorted(issues,key=lambda r:(list(self.case.paths).index(r["path_id"]),r["s_start_mm"],r["kind"]))

    def coverage_issues_for_path(self):
        return [r for r in self.coverage_issues() if r["path_id"]==self.path_id]

    @staticmethod
    def coverage_issue_text(issue):
        kind=issue.get("kind")
        reason=tr('Unannotated') if kind=="gap" else tr('Needs rereading') if kind=="rereview" else tr('Interval needs review')
        provenance=issue.get("provenance",{})
        if kind=="review":reason=tr('Confirm the range and prior judgment, then apply')
        return tr("{path} · {start:.2f}–{end:.2f} mm · {reason}",path=issue["path_id"],start=issue["s_start_mm"],end=issue["s_end_mm"],reason=reason)

    def update_completion_display(self):
        if not hasattr(self,"completion_caption"):return
        report=self.completion_report()
        if report is None:
            self.completion_caption.setText(tr('Coverage: no case loaded'));self.next_issue_button.setEnabled(False);return
        self.completion_caption.setText(tr('Case {p0:.2f}% · Gaps {p1} / Reviews {p2}' ,p0=report['coverage_percent'],p1=len(report['gaps']),p2=len(report['review_required'])))
        self.completion_caption.setToolTip(tr('Annotated CPR slice positions: {p0} / {p1}. Verified shared LM is counted once.\nClick red gaps to navigate. {p2} technical review items remain; repeated reader confirmation is not required. Completion does not certify training eligibility.' ,p0=report['covered_slices'],p1=report['total_slices'],p2=report.get('technical_qa_count', 0)))
        self.next_issue_button.setEnabled(bool(self.coverage_issues(report)))
        for path,button in self.path_buttons.items():
            detail=report.get("by_path",{}).get(path,{})
            button.setToolTip(tr('{p0} · Annotated {p1:.2f}% (shared LM counted once)' ,p0=path,p1=detail.get('coverage_percent', 0)))

    def jump_to_coverage_issue(self,issue):
        if not self.guard_draft():return False
        if issue.get("annotation_id"):
            self.select_annotation(issue["annotation_id"])
        else:
            self.clear_draft();self.path_id=issue["path_id"];self.long_key=None
            self.a=max(0.,issue["s_start_mm"]);self.b=min(self.path.length_mm,issue["s_end_mm"])
            self.observe((self.a+self.b)/2);self.sync_controls();self.update_geometry_status();self.update_overlays()
            self.cpr.center_observation();self.schedule_view_save()
        self.track.setFocus();self.hint(tr("{issue}. Selection positioned; review the images before applying.",issue=self.coverage_issue_text(issue)))
        return True

    def next_coverage_issue(self):
        issues=self.coverage_issues()
        if not issues:return
        path_order=list(self.case.paths);here=(path_order.index(self.path_id),self.s)
        later=[r for r in issues if (path_order.index(r["path_id"]),r["s_start_mm"])>here]
        self.jump_to_coverage_issue(later[0] if later else issues[0])

    def build_completion_dialog(self,report):
        dialog=QDialog(self);dialog.setWindowTitle(tr('Case coverage · Click a gap to navigate'));dialog.resize(650,460)
        layout=QVBoxLayout(dialog);heading=QLabel(tr('Annotated {p0:.2f}% ({p1} / {p2} slice positions)' ,p0=report['coverage_percent'],p1=report['covered_slices'],p2=report['total_slices']))
        heading.setStyleSheet("font-size:17px;font-weight:600;");layout.addWidget(heading)
        detail=[]
        for path,row in report.get("by_path",{}).items():detail.append(f"{path}  {row.get('coverage_percent',0):.2f}%")
        layout.addWidget(QLabel("　 |　 ".join(detail)))
        note=QLabel(tr('{p0} gaps and {p1} review items.\nClick an item to select it; double-click to navigate. Unannotated areas are never filled as normal.' ,p0=len(report['gaps']),p1=len(report['review_required'])));note.setWordWrap(True);layout.addWidget(note)
        items=QListWidget();items.setObjectName("completionIssues");layout.addWidget(items,1)
        for issue in self.coverage_issues(report):
            item=QListWidgetItem(self.coverage_issue_text(issue));item.setData(Qt.ItemDataRole.UserRole,issue);items.addItem(item)
        if items.count():items.setCurrentRow(0)
        warnings=report.get("geometry_warnings",[])
        if warnings or report.get("technical_qa_count"):
            warning=QLabel(tr('Geometry or group summaries require technical review. This does not prevent reader completion; export retains the limitations.'));warning.setWordWrap(True);layout.addWidget(warning)
        allowed=bool(report.get("can_override"))
        explanation=QLabel(tr('Coverage exceeds 90%. You may complete with gaps, which remain explicitly recorded. This does not certify full coverage or training eligibility.') if allowed else tr('Coverage must be strictly greater than 90% to complete with gaps. Continue annotating; displayed rounding does not change the threshold.'))
        explanation.setWordWrap(True);layout.addWidget(explanation)
        row=QHBoxLayout();locate=self.button(tr('Locate selected'));locate.setEnabled(items.count()>0);row.addWidget(locate);row.addStretch()
        keep=self.button(tr('Continue annotation'),dialog.reject);keep.setDefault(True);row.addWidget(keep)
        override=self.button(tr('Complete with gaps'));override.setObjectName("completeWithGaps");override.setEnabled(allowed);row.addWidget(override);layout.addLayout(row)
        def go():
            item=items.currentItem()
            if item and self.jump_to_coverage_issue(item.data(Qt.ItemDataRole.UserRole)):dialog.accept()
        locate.clicked.connect(go);items.itemDoubleClicked.connect(lambda _:go())
        def finish():
            if self.commit_completion(report,allow_incomplete=True):dialog.accept()
        override.clicked.connect(finish)
        return dialog

    def commit_completion(self,report,allow_incomplete=False):
        try:
            # Recompute at the commit boundary, never trust a stale open dialog.
            fresh=self.completion_report(refresh=True)
            changed=mark_case_complete(self.state,fresh,allow_incomplete=allow_incomplete)
            self.state=self.store.commit(changed,"mark_complete",{"explicit_override":allow_incomplete})
            self.refresh_queue();self.update_completion_display();self.update_overlays()
            self.hint(tr('Marked complete with gaps / review items retained. No normal labels were filled in.') if self.state["case_status"]=="complete_with_gaps" else tr('Coverage passed and the case is complete. This does not certify dual-reader adjudication.'))
            return True
        except Exception as exc:self.error(tr('Completion status not saved'),exc);return False

    def mark_complete(self):
        if not self.case or not self.guard_draft():return
        report=self.completion_report(refresh=True);self.update_completion_display();self.track.update()
        if report["complete"]:self.commit_completion(report)
        else:self.build_completion_dialog(report).exec()

    def export_current(self):
        if not self.case:return
        if not self.save_view():return
        folder=QFileDialog.getExistingDirectory(self,tr('Choose an annotation export folder (images excluded)'))
        if not folder:return
        try:
            if not self.save_view():return
            result=self.store.export_case(str(self.case.case_id),self.reader_id,Path(folder))
            self.hint(tr('Annotations exported: {p0}' ,p0=result));QMessageBox.information(self,tr("Exported"),tr("Exported to {path}.\nOnly applied labels are formal annotations; unapplied content remains a draft. Images are not included.",path=str(result)))
        except Exception as exc:self.error(tr('Export failed'),exc)

    def export_all(self):
        self._export_cases(None)

    def export_batch(self):
        if not self.active_package_root:
            self.hint(tr('Open a package folder first. Use the export arrow for all project history.'))
            return
        self._export_cases(set(self.active_package_cases))

    def _export_cases(self,case_ids):
        if not self.save_view():return
        scope=tr("All project history") if case_ids is None else tr("Current batch")
        folder=QFileDialog.getExistingDirectory(self,tr("Choose a folder for {scope} annotations",scope=scope))
        if not folder or not self.save_view():return
        try:
            selected=[]
            for cid in self.store.list_case_ids(self.reader_id):
                if case_ids is not None and cid not in case_ids:continue
                state=self.store.load(cid,self.reader_id)
                if state.get("annotations") or state.get("markers") or state.get("rereview_intervals"):selected.append(cid)
            result=self.store.export_batch(self.reader_id,Path(folder),case_ids=selected)
            QMessageBox.information(self,tr("Batch export complete"),tr("{scope}: exported {count} cases for reader {reader}.\nBatch manifest: {manifest}\nDrafts remain drafts and gaps remain unannotated. Images are not included.",scope=scope,count=result["case_count"],reader=self.reader_id,manifest=result["manifest"]))
        except Exception as exc:self.error(tr("Batch export incomplete"),exc)

    def import_labels(self):
        if not self.guard_draft() or not self.save_view():return
        path,_=QFileDialog.getOpenFileName(self,tr("Import this reader's annotation JSON"),filter="JSON (*.json)")
        if not path:return
        try:
            from .storage import io_path
            incoming=json.loads(io_path(path).read_text(encoding="utf-8-sig"))
            if not self.project_id or incoming.get("source",{}).get("project_id")!=self.project_id:
                raise ValueError(tr("This annotation export belongs to another project."))
            result=self.store.import_case(path,reader_id=self.reader_id)
            self.refresh_queue()
            if self.case and str(result["case_id"])==str(self.case.case_id):self.state=self.store.load(str(self.case.case_id),self.reader_id,self.source_info());self.clear_draft()
            self.hint(tr('Import complete. Other readers and conflicting versions were not overwritten.'))
        except Exception as exc:self.error(tr('Annotations not imported'),exc)

    def import_legacy_labels(self):
        if not self.case or not self.guard_draft() or not self.save_view():return
        path,_=QFileDialog.getOpenFileName(self,tr("Select annotations.json from a complete legacy export"),filter="JSON (*.json)")
        if not path:return
        try:
            from .legacy import import_annotations
            old=json.loads(Path(path).read_text(encoding="utf-8-sig"))
            if old.get("reader_id")!=self.reader_id:
                raise ValueError(tr("The export belongs to reader {reader}. Switch to that reader before importing.",reader=old.get("reader_id","")))
            result=import_annotations(path,self.store,self.case)
            self.state=self.store.load(str(result["case_id"]),self.reader_id,self.source_info())
            self.clear_draft();self.refresh_queue();self.update_overlays()
            self.hint(tr("Legacy export imported with its checksums and audit history. The original database was not opened."))
        except Exception as exc:self.error(tr("Legacy annotations were not imported"),exc)

    def import_legacy_package(self):
        if self.loading:return
        source=QFileDialog.getExistingDirectory(self,tr("Select a legacy package folder (not a database)"))
        if not source:return
        destination,_=QFileDialog.getSaveFileName(self,tr("Choose a new destination folder for the converted package"))
        if not destination:return
        dialog=QDialog(self);dialog.setWindowTitle(tr("New project identity"))
        layout=QVBoxLayout(dialog);layout.addWidget(QLabel(tr("Enter a portable project ID (letters, digits, dots, underscores or hyphens).")))
        identity=QLineEdit();identity.setAccessibleName(tr("Project ID"));layout.addWidget(identity)
        row=QHBoxLayout();accept=self.button(tr("Convert package"),dialog.accept);cancel=self.button(tr("Cancel"),dialog.reject);row.addWidget(accept);row.addWidget(cancel);layout.addLayout(row)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        try:
            from .legacy import import_package
            import_package(source,destination,identity.text().strip())
            self.import_package(Path(destination))
        except Exception as exc:self.error(tr("Legacy package was not converted"),exc)

    def show_help(self):
        QMessageBox.information(self,tr("Help and data safety"),tr(
            "Open a package root and check the project and reader ID. Each project has an independent local database. Only paths in the declared annotation scope can be labeled; other paths are view-only.\n\n"
            "Scroll to review images. Space places a marker. Drag the orange selection to set an interval, choose a finding and apply with S / Enter. Click saved overlays to edit. New labels replace overlapping labels; shortening or deleting leaves unannotated gaps.\n\n"
            "Labels save when applied; drafts recover separately. Language changes preserve drafts, undo history and the current view. Switching projects saves the current draft before opening the other database. Export batch uses the current package; project history includes this reader's saved records, even when images are disconnected.\n\n"
            "Native CT starts at 0°. The 90° button rotates the display; Z toggles reference lines. These controls do not change native coordinates.\n\n"
            "Database: {database}\nExport backups regularly. Keep live databases outside synchronized folders. This tool supports research annotation, not clinical diagnosis.",database=str(self.db_path)))

    def closeEvent(self,event):
        if self._closing:event.accept();return
        popup=getattr(self,"window_level_popup",None)
        if popup is not None:popup.close()
        # Recoverable drafts persist without forcing a diagnostic commit on exit.
        self._last_close_save_succeeded=bool(self.save_view())
        if not self._last_close_save_succeeded:
            if not self._smoke_silent_exit:
                QMessageBox.warning(self,tr('Exit paused'),tr('Draft save failed. Check available disk space or export your labels, then retry closing.'))
            event.ignore();return
        self._closing=True;self.render_timer.stop();self.save_timer.stop()
        self.pool.clear();self.pool.waitForDone(3000);self._discard_pending_project();self.app.removeEventFilter(self);self.store.close();self._db_lock.unlock();self._close_completed=True;event.accept()


class Neighbor(QWidget):
    def __init__(self,c,offset):
        super().__init__();self.c=c;self.offset=offset;self.s=0.;self.hu=None;self.qimage=None
        self.setFixedHeight(106);self.setMinimumWidth(48);self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tr('Click to move the observation to this neighboring cross-section.'))
    def set_hu(self,s,hu):self.s=s;self.hu=hu;self.rewindow()
    def rewindow(self):
        if self.hu is not None:
            from .imaging import window_uint8
            from PySide6.QtGui import QImage
            a=window_uint8(self.hu,self.c.width_hu,self.c.level_hu);self.qimage=QImage(a.data,a.shape[1],a.shape[0],a.strides[0],QImage.Format.Format_Grayscale8).copy()
        self.update()
    def paintEvent(self,e):
        from PySide6.QtGui import QColor,QPainter,QPen
        from PySide6.QtCore import QRectF
        p=QPainter(self);p.fillRect(self.rect(),QColor("#0c1117"));side=min(self.width()-4,self.height()-23)
        if self.qimage:p.drawImage(QRectF((self.width()-side)/2,2,side,side),self.qimage)
        p.setPen(QColor("#61c7bd") if self.offset==0 else QColor("#91a1ae"));p.drawText(self.rect().adjusted(0,self.height()-23,0,0),Qt.AlignmentFlag.AlignCenter,f"{self.s:.2f}" if self.qimage else "—")
        if self.offset==0:p.setPen(QPen(QColor("#61c7bd"),1));p.drawRect(self.rect().adjusted(0,0,-1,-1))
    def mouseReleaseEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton and self.qimage:self.c.observe(self.s);self.c.cpr.setFocus()


def main(argv=None):
    parser=argparse.ArgumentParser(description="Offline Coronary Annotation Studio")
    parser.add_argument("--package",type=Path);parser.add_argument("--case");parser.add_argument("--reader",default=None);parser.add_argument("--db",type=Path)
    parser.add_argument("--workflow-smoke-output",type=Path,help="New output directory for the synthetic workflow gate")
    parser.add_argument("--workflow-smoke-phase",choices=("create","resume"))
    parser.add_argument("--workflow-smoke-language",choices=("en","zh_CN"),default="en")
    args=parser.parse_args(argv)
    workflow=args.workflow_smoke_output is not None
    if not workflow and args.workflow_smoke_phase is not None:parser.error("Workflow output and phase must be supplied together")
    if workflow:
        from .workflow_smoke import prepare_workflow
        try:args.workflow_smoke_output=prepare_workflow(args)
        except (OSError,ValueError,KeyError,TypeError) as exc:
            print(json.dumps({'status':'FAIL','stage':'preflight','failure':str(exc),'exit_code':2}),file=sys.stderr)
            return 2
    app=QApplication(sys.argv[:1]);app.setApplicationName("Coronary Annotation Studio");app.setOrganizationName("CoronaryAnnotationStudio")
    app.setQuitOnLastWindowClosed(not workflow)
    runner=None
    def exception_hook(kind,value,tb):
        message="".join(traceback.format_exception(kind,value,tb))
        if sys.stderr:sys.stderr.write(message)
        logdir=user_log_dir(args.db)
        try:
            logdir.mkdir(parents=True,exist_ok=True)
            with (logdir/"errors.log").open("a",encoding="utf-8") as log:log.write(message+"\n")
        except OSError:pass
        if workflow:
            if runner is not None:runner.fail(value)
            else:
                (args.workflow_smoke_output/"workflow-report.json").write_text(json.dumps({"status":"FAIL","failure":message,"phase":args.workflow_smoke_phase,"exit_code":2},indent=2))
                app.exit(2)
        else:
            box=QMessageBox();box.setIcon(QMessageBox.Icon.Critical)
            box.setWindowTitle(tr("Application error (check unsaved work)"))
            box.setText(tr("This operation could not be completed. Open the technical details for the original error."))
            box.setDetailedText(message);box.exec()
    sys.excepthook=exception_hook
    try:window=MainWindow(args.db,args.package,args.reader,args.case)
    except Exception as exc:
        exception_hook(type(exc),exc,exc.__traceback__)
        return 2
    if workflow:
        from .workflow_smoke import WorkflowRunner
        runner=WorkflowRunner(window,args.workflow_smoke_output,args.workflow_smoke_phase,args.workflow_smoke_language)
    window.show()
    return app.exec()
