"""Offline desktop reader application. No server, network or model inference."""
from __future__ import annotations

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
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot, QLockFile, QEvent, QPoint, QPointF
from PySide6.QtGui import QKeySequence, QShortcut, QFontDatabase, QFont, QIcon, QPixmap, QPainter, QPen, QColor, QAction
from PySide6.QtWidgets import (
    QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QLabel,QPushButton,
    QButtonGroup,QFrame,QSplitter,QScrollArea,QListWidget,QListWidgetItem,QFileDialog,
    QMessageBox,QLineEdit,QDoubleSpinBox,QComboBox,QCheckBox,QAbstractSpinBox,QSizePolicy,QDialog,QToolButton,QMenu,
)

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

FINDINGS=[("明确正常","negative"),("明确有斑块","positive"),("疑似 / 不确定","uncertain"),("不可评估","non_evaluable")]
COMPOSITIONS=[("钙化","calcified"),("非钙化","non_calcified"),("混合","partially_calcified"),("类型不确定","uncertain")]
STENOSES=[("0%","0"),("1–24%","1_24"),("25–49%","25_49"),("50–69%","50_69"),("70–99%","70_99"),("100%","100"),("无法判断","unable")]
CONFIDENCES=[("高","high"),("中","medium"),("低","low")]


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
    if rel.is_absolute() or rel.drive:raise ValueError("数据包必须使用相对路径")
    path=(root/rel).resolve()
    if not path.is_relative_to(root):raise ValueError("数据包路径越界")
    return path


class MainWindow(QMainWindow):
    def __init__(self,db_path=None,package=None,reader=None,case_id=None,restore_last=True):
        super().__init__()
        self.app=QApplication.instance()
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
        self.setWindowTitle(f"ImageCAS-X CPR Studio {__version__} · 正式候选版（研究标注）")
        self.resize(1540,940);self.setMinimumSize(880,520)
        self.base_dir=user_data_dir()
        self.db_path=(Path(db_path) if db_path else self.base_dir/"annotations.sqlite").resolve()
        if "onedrive" in str(self.db_path).lower():raise ValueError("数据库应放本机非 OneDrive 目录")
        self.db_path.parent.mkdir(parents=True,exist_ok=True)
        self._db_lock=QLockFile(str(self.db_path)+".app.lock");self._db_lock.setStaleLockTime(0)
        if not self._db_lock.tryLock(0):raise RuntimeError("此标注工作区已被另一个窗口打开，请使用已有窗口，避免同时编辑。")
        self.store=AnnotationStore(self.db_path)
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
            self.store.close();self._db_lock.unlock();raise ValueError("读者编号须为 1–40 个字符，不能包含路径字符")
        self.reader_id=self.reader_id.strip();self.case=None;self.case_path=None;self.path_id="LAD";self.state=None
        self.s=0.;self.a=0.;self.b=10.;self.angle_deg=0.;self.offset_mm=0.;self.width_hu=700.;self.level_hu=250.;self.native_z=0
        # Display preferences belong to this local workspace, not a case's
        # diagnostic record. Loading another vessel must not restore an old angle.
        display=saved.get("display_preferences",{})
        if not isinstance(display,dict):display={}
        quarters=display.get("native_rotation_quarters",2)
        self.native_rotation_quarters=quarters if type(quarters) is int and 0<=quarters<4 else 2
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
        self._build_ui();self._shortcuts();self.app.installEventFilter(self)
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
                if saved.get("schema_version")!="imagecasx-session-1.0":raise ValueError("不支持的会话版本")
                roots=saved.get("package_roots",[])
                if not isinstance(roots,list) or not all(isinstance(p,str) and p for p in roots):raise ValueError("数据包列表无效")
                saved["package_roots"]=list(dict.fromkeys(str(Path(p).resolve()) for p in roots))
                active_root=saved.get("active_package_root")
                if active_root is not None:
                    if not isinstance(active_root,str) or not active_root:raise ValueError("当前数据目录无效")
                    saved["active_package_root"]=str(Path(active_root).resolve())
                    if saved["active_package_root"] not in saved["package_roots"]:
                        saved["package_roots"].append(saved["active_package_root"])
                elif len(roots)>1:
                    self.session_notice="已从旧版累积列表切换为最近登记目录；如不是当前批次，请重新打开对应数据包文件夹。历史标注均保留。"
                active_cases=saved.get("active_package_cases",[])
                if not isinstance(active_cases,list) or not all(isinstance(cid,str) and cid for cid in active_cases):raise ValueError("当前批次病例列表无效")
                saved["active_package_cases"]=list(dict.fromkeys(active_cases))
                reader=saved.get("reader_id","READER-A")
                if not isinstance(reader,str) or not reader.strip() or len(reader)>40 or any(c in reader for c in '/\\:*?"<>|'):
                    raise ValueError("会话读者编号无效")
                active=saved.get("active_case_id")
                if active is not None and not isinstance(active,(str,int)):raise ValueError("会话病例编号无效")
                saved["active_case_id"]=str(active) if active is not None else None
                return saved
            except (OSError,ValueError,TypeError,AttributeError) as exc:
                self.session_notice=f"上次会话文件未恢复：{exc}。标签数据库未修改。"
                return {}
        if self.db_path==(self.base_dir/"annotations.sqlite").resolve():
            legacy=self.base_dir/"last_package.json"
            try:
                old=json.loads(legacy.read_text(encoding="utf-8"))
                if isinstance(old.get("path"),str) and old["path"]:
                    return {"package_roots":[str(Path(old["path"]).resolve())],"reader_id":"READER-A"}
            except (OSError,ValueError,TypeError,AttributeError):pass
        return {}

    def _save_session(self,*,package_roots=None,reader_id=None,active_case_id=None,active_package_root=None,active_package_cases=None):
        if not self.persist_preferences:return True
        active=active_case_id if active_case_id is not None else str(self.case.case_id) if self.case else self.session_active_case_id
        content={"schema_version":"imagecasx-session-1.0","package_roots":list(package_roots if package_roots is not None else self.package_roots),"reader_id":reader_id if reader_id is not None else self.reader_id,"active_case_id":active}
        content["display_preferences"]={"native_rotation_quarters":self.native_rotation_quarters,"reference_lines_visible":self.reference_lines_visible}
        content["active_package_root"]=active_package_root if active_package_root is not None else self.active_package_root
        content["active_package_cases"]=list(active_package_cases if active_package_cases is not None else self.active_package_cases)
        temporary=self.session_path.with_name(self.session_path.name+"."+uuid.uuid4().hex+".tmp")
        try:
            with temporary.open("x",encoding="utf-8") as stream:
                json.dump(content,stream,ensure_ascii=False,sort_keys=True,allow_nan=False,indent=2)
                stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,self.session_path)
            self.session_active_case_id=active
            return True
        except (OSError,ValueError,TypeError) as exc:
            self.save_status.setText("会话保存失败：请勿关闭");self.hint(f"会话保存失败：{exc}")
            return False
        finally:
            try:temporary.unlink(missing_ok=True)
            except OSError:pass

    def _package_cases(self,root,existing=None):
        root=Path(root).resolve()
        if not root.exists():raise ValueError("原目录当前不可用（磁盘未连接或目录已移动）")
        manifests=[root] if root.is_file() else sorted(root.rglob("manifest.json"))
        if not manifests:raise ValueError("没有找到 manifest.json；请先解压数据 ZIP")
        additions={};existing=self.case_index if existing is None else existing
        for manifest in manifests:
            data=json.loads(manifest.read_text(encoding="utf-8"))
            if data.get("schema_version")!="imagecasx-package-1.0":continue
            for entry in data.get("cases",[]):
                path=safe_child(manifest.parent,entry["case_manifest"]);cid=str(entry["case_id"])
                if not path.is_file():raise ValueError(f"病例 {cid} 缺少 case.json")
                old=existing.get(cid) or additions.get(cid)
                if old and json.loads(old.read_text(encoding="utf-8")).get("geometry_id")!=json.loads(path.read_text(encoding="utf-8")).get("geometry_id"):
                    raise ValueError(f"病例 {cid} 出现不同几何版本；请使用独立工作区")
                additions[cid]=path
        if not additions:raise ValueError("目录中没有受支持的数据包")
        return additions

    def restore_session_roots(self,roots,preferred_case=None):
        if self._closing:return
        self.package_roots=list(dict.fromkeys(str(Path(root).resolve()) for root in roots))
        self.active_package_root=self.active_package_root or (self.package_roots[-1] if self.package_roots else None)
        combined={};self.unavailable_package_roots=[]
        if self.active_package_root:
            try:
                combined=self._package_cases(self.active_package_root,{})
                self.active_package_cases=list(combined)
            except (OSError,ValueError,KeyError,TypeError) as exc:
                self.unavailable_package_roots.append({"path":self.active_package_root,"reason":str(exc)})
        self.case_index=combined;self.refresh_queue()
        if preferred_case is not None and str(preferred_case) not in self.case_index:
            self.session_notice+=("\n" if self.session_notice else "")+f"上次病例 {preferred_case} 不在当前可用目录；历史标注仍保留。"
        if self._save_session() and self.case_index:
            target=str(preferred_case) if str(preferred_case) in self.case_index else next(iter(self.case_index))
            self.select_case_in_queue(target)
        self.show_session_notice()

    def show_session_notice(self):
        if self.unavailable_package_roots:
            details="\n".join(f"{r['path']} — {r['reason']}" for r in self.unavailable_package_roots)
            self.queue_count.setToolTip("当前数据目录暂不可用：\n"+details+"\n历史标注未删除，可导出本批已存记录或全部历史。")
            self.hint("当前数据目录暂不可用；未自动加入其他历史批次。重新打开目录可恢复阅片，已存标签仍可导出。")
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
        banner=QHBoxLayout();brand=QLabel("ImageCAS-X  /  CPR Studio");brand.setObjectName("brand");banner.addWidget(brand)
        sub=QLabel("OFFLINE  ·  正式候选版 / 待验收  ·  v"+__version__);self.subtitle=sub;sub.setObjectName("muted");banner.addWidget(sub);banner.addStretch()
        banner.addWidget(self.button("病例/帮助",self.toggle_sidebar));banner.addWidget(self.button("邻近切片",self.toggle_neighbors))
        self.reference_lines_button=self.button("参考线 Z",self.toggle_reference_lines)
        self.reference_lines_button.setCheckable(True);self.reference_lines_button.setChecked(self.reference_lines_visible)
        self.reference_lines_button.setAccessibleName("三个主影像窗参考线开关 Z")
        self.reference_lines_button.setToolTip("Z：隐藏 / 显示三个主影像窗的绿色参考线；不隐藏橙框、辅助标记或底条观察点。亮起表示参考线开启。")
        banner.addWidget(self.reference_lines_button)
        self.retry_render_button=self.button("重试影像",self.retry_render);self.retry_render_button.hide();banner.addWidget(self.retry_render_button)
        self.save_status=QLabel("未载入病例");banner.addWidget(self.save_status);outer.addLayout(banner)
        split=QSplitter(Qt.Orientation.Horizontal);outer.addWidget(split,1)
        # Left: data, reader identity, queue, full operation legend.
        left=QWidget();self.sidebar=left;lv=QVBoxLayout(left);lv.setContentsMargins(0,0,4,0);lv.setSpacing(7)
        self.section(lv,"病例与工作区")
        lv.addWidget(self.button("打开数据包文件夹…",self.choose_package))
        self.package_caption=QLabel("当前目录：尚未打开");self.package_caption.setObjectName("muted")
        self.package_caption.setMinimumWidth(0);self.package_caption.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        lv.addWidget(self.package_caption)
        identity=QHBoxLayout();identity.addWidget(QLabel("读者"));self.reader_box=QLineEdit(self.reader_id);self.reader_box.setMaxLength(40);self.reader_box.editingFinished.connect(self.change_reader);identity.addWidget(self.reader_box);lv.addLayout(identity)
        self.queue_count=QLabel("0 例 · 原始影像只读");self.queue_count.setObjectName("muted");lv.addWidget(self.queue_count)
        self.case_list=QListWidget();self.case_list.setObjectName("case_list");self.case_list.currentItemChanged.connect(self.choose_case_item);lv.addWidget(self.case_list,1)
        self.complete_button=self.button("检查并标记本例完成",self.mark_complete);lv.addWidget(self.complete_button)
        row=QHBoxLayout();row.addWidget(self.button("导出本例",self.export_current))
        self.batch_export_button=QToolButton();self.batch_export_button.setText("导出本批")
        self.batch_export_button.setToolTip("导出当前目录内本读者的已标病例；右侧小箭头可导出全部历史备份。")
        self.batch_export_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.batch_export_button.clicked.connect(self.export_batch)
        export_menu=QMenu(self.batch_export_button)
        export_menu.addAction("导出当前批次",self.export_batch)
        export_menu.addAction("导出全部历史（本读者备份）",self.export_all)
        self.batch_export_button.setMenu(export_menu);row.addWidget(self.batch_export_button);lv.addLayout(row)
        lv.addWidget(self.button("导入已有标签 JSON…",self.import_labels))
        lv.addWidget(self.button("操作说明 / 数据安全",self.show_help))
        help_text=QLabel(shortcut_help());self.operation_help=help_text
        help_text.setWordWrap(True);help_text.setObjectName("muted")
        hs=QScrollArea();hs.setWidgetResizable(True);hs.setWidget(help_text);hs.setMinimumHeight(180);hs.setMaximumHeight(350);lv.addWidget(hs)
        left.setMinimumWidth(185);left.setMaximumWidth(270);split.addWidget(left)
        # Middle: neighbor strip, primary CPR, large orthogonal view, native CT, timeline.
        middle=QWidget();mv=QVBoxLayout(middle);mv.setContentsMargins(0,0,0,0);mv.setSpacing(6)
        nav=QHBoxLayout();self.case_title=QLabel("选择一个病例开始");nav.addWidget(self.case_title);nav.addStretch()
        self.path_buttons={}
        for name in ("LAD","LCX","RCA"):
            b=self.button(name,lambda checked=False,n=name:self.change_path(n));b.setCheckable(True);self.path_buttons[name]=b;nav.addWidget(b)
        mv.addLayout(nav)
        self.neighbor_container=QWidget();thumbs=QHBoxLayout(self.neighbor_container);thumbs.setContentsMargins(0,0,0,0);thumbs.setSpacing(4);self.neighbors=[]
        for k in range(-3,4):
            w=Neighbor(self,k);thumbs.addWidget(w,1);self.neighbors.append(w)
        mv.addWidget(self.neighbor_container)
        self.neighbor_step=.5
        self.neighbor_controls=QWidget();neighbor_row=QHBoxLayout(self.neighbor_controls);neighbor_row.setContentsMargins(0,0,0,0);neighbor_row.addWidget(QLabel("邻近横截面 · 点击定位"));neighbor_row.addStretch()
        for distance in (.5,1,2):neighbor_row.addWidget(self.button(f"±{distance:g} mm",lambda checked=False,d=distance:self.set_neighbor_step(d)))
        mv.addWidget(self.neighbor_controls)
        images=QSplitter(Qt.Orientation.Horizontal);self.images_splitter=images
        self.cpr=ImageCanvas(self,"cpr");self.cross=ImageCanvas(self,"cross");self.native=ImageCanvas(self,"native");self.canvases=[self.cpr,self.cross,self.native]
        images.addWidget(self.image_panel("纵向 CPR",self.cpr,"cpr"))
        right_images=QSplitter(Qt.Orientation.Vertical);self.right_images=right_images;right_images.addWidget(self.image_panel("正交横截面",self.cross,"cross"));right_images.addWidget(self.image_panel("原始 CT · 自由翻层",self.native,"native"));right_images.setSizes([310,265]);images.addWidget(right_images);images.setSizes([510,355]);mv.addWidget(images,1)
        progress=QHBoxLayout();self.completion_caption=QLabel("覆盖检查：尚未载入");self.completion_caption.setObjectName("muted");self.completion_caption.setMinimumWidth(0);self.completion_caption.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred);progress.addWidget(self.completion_caption,1)
        self.next_issue_button=self.button("下一处未标 / 复核",self.next_coverage_issue);self.next_issue_button.setStyleSheet("padding:1px 5px;min-height:14px;");progress.addWidget(self.next_issue_button);mv.addLayout(progress)
        self.track=IntervalTrack(self);mv.addWidget(self.track)
        controls=QHBoxLayout();self.angle_spin=self.spin(-180,180,0,.5," °");self.offset_spin=self.spin(-10,10,0,.1," mm")
        controls.addWidget(QLabel("旋转"));controls.addWidget(self.angle_spin);controls.addWidget(self.button("归零",lambda:self.set_sampling(angle=0)))
        controls.addWidget(QLabel("离轴"));controls.addWidget(self.offset_spin);controls.addWidget(self.button("归零",lambda:self.set_sampling(offset=0)))
        self.angle_spin.valueChanged.connect(lambda v:self.set_sampling(angle=v) if not self._syncing else None)
        self.offset_spin.valueChanged.connect(lambda v:self.set_sampling(offset=v) if not self._syncing else None)
        self.overlay_box=QCheckBox("隐藏色块");self.overlay_box.toggled.connect(self.set_hide_overlays);controls.addWidget(self.overlay_box);mv.addLayout(controls)
        self.geometry_status=QLabel("几何映射：尚未载入");self.geometry_status.setWordWrap(True);self.geometry_status.setObjectName("muted");mv.addWidget(self.geometry_status)
        split.addWidget(middle)
        # Right: direct labels and optional reason chips; no repeated anatomy typing.
        right=QWidget();rv=QVBoxLayout(right);rv.setContentsMargins(4,0,0,0);rv.setSpacing(7)
        draft_header=QHBoxLayout();self.draft_title=QLabel("待标注 · 诊断未填写");self.draft_title.setObjectName("section");draft_header.addWidget(self.draft_title,1)
        self.exit_edit_button=self.button("退出修改",self.exit_editing);self.exit_edit_button.setObjectName("exitEditing");self.exit_edit_button.setToolTip("退出旧片段修改，保留橙框位置；未应用的改动会先询问。Esc 也可退出。")
        draft_header.addWidget(self.exit_edit_button);rv.addLayout(draft_header)
        limits=QHBoxLayout();self.start_spin=self.spin(0,999,0,.25," mm");self.end_spin=self.spin(0,999,10,.25," mm");limits.addWidget(QLabel("起"));limits.addWidget(self.start_spin);limits.addWidget(QLabel("止"));limits.addWidget(self.end_spin);rv.addLayout(limits)
        self.start_spin.valueChanged.connect(lambda v:self.numeric_range(v,self.b,"start") if not self._syncing else None)
        self.end_spin.valueChanged.connect(lambda v:self.numeric_range(self.a,v,"end") if not self._syncing else None)
        rv.addWidget(self.button("典型正常段",self.typical_normal,"normal"))
        scroll=QScrollArea();scroll.setWidgetResizable(True);fields=QWidget();fv=QVBoxLayout(fields);fv.setContentsMargins(0,0,0,0);fv.setSpacing(6)
        self.groups={};self.field_sections={}
        for key,title,choices,cols in [("finding_status","1 · 本段所见",FINDINGS,2),("plaque_composition","2 · 斑块组成",COMPOSITIONS,2),("stenosis_grade","3 · 本段最大直径狭窄",STENOSES,3),("confidence","4 · 判断把握",CONFIDENCES,3)]:
            panel=QWidget();pv=QVBoxLayout(panel);pv.setContentsMargins(0,0,0,0);pv.setSpacing(4);self.field_sections[key]=panel
            self.section(pv,title);grid=QGridLayout();grid.setSpacing(4);group=QButtonGroup(self);group.setExclusive(True);buttons={}
            for i,(text,value) in enumerate(choices):
                b=self.button(text,lambda checked=False,k=key,v=value:self.set_label(k,v));b.setCheckable(True);group.addButton(b);buttons[value]=b;grid.addWidget(b,i//cols,i%cols)
                b.setProperty("baseText",text);self.style_choice(b,SELECTION_COLORS[key][value])
            pv.addLayout(grid);fv.addWidget(panel);self.groups[key]=(group,buttons)
            if key=="finding_status":
                self.flow_note=QLabel();self.flow_note.setWordWrap(True);self.flow_note.setObjectName("muted");fv.addWidget(self.flow_note)
        self.peak_panel=QWidget();peaklayout=QVBoxLayout(self.peak_panel);peaklayout.setContentsMargins(0,0,0,0);peaklayout.setSpacing(4)
        self.peak_label=QLabel("最狭窄位置：未指定（辅助标记不代替）");self.peak_label.setWordWrap(True);self.peak_label.setObjectName("muted");peaklayout.addWidget(self.peak_label)
        peakrow=QHBoxLayout();peakrow.addWidget(self.button("当前层设为最狭窄处",self.set_peak));peakrow.addWidget(self.button("清除",self.clear_peak));peaklayout.addLayout(peakrow);fv.addWidget(self.peak_panel)
        self.reason_panel=QWidget();reasonlayout=QVBoxLayout(self.reason_panel);reasonlayout.setContentsMargins(0,0,0,0);reasonlayout.setSpacing(4)
        self.section(reasonlayout,"5 · 不可评估原因（可选、多选）")
        reasons=QGridLayout();reasons.setSpacing(4);self.reason_buttons={}
        for i,(code,text,tip) in enumerate(REASON_OPTIONS):
            b=self.button(text,lambda checked=False,c=code:self.toggle_reason(c));b.setCheckable(True)
            b.setProperty("baseText",text);b.setToolTip(tip);self.style_choice(b,"#8eb4c2")
            b.setStyleSheet(b.styleSheet()+" QPushButton{padding:4px 5px;min-height:16px;}")
            self.reason_buttons[code]=b;reasons.addWidget(b,i//2,i%2)
        reasons.addWidget(self.button("清空原因",self.clear_reasons),3,1);reasonlayout.addLayout(reasons)
        optional=QLabel("不选也可直接应用；2–4项不作诊断判断。");optional.setWordWrap(True);optional.setObjectName("muted");reasonlayout.addWidget(optional);fv.addWidget(self.reason_panel)
        self.legacy_reason_label=QLabel();self.legacy_reason_label.setWordWrap(True);self.legacy_reason_label.setTextFormat(Qt.TextFormat.PlainText);self.legacy_reason_label.setObjectName("muted");self.legacy_reason_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);fv.addWidget(self.legacy_reason_label);self.legacy_reason_label.hide()
        fv.addStretch();scroll.setWidget(fields);rv.addWidget(scroll,1)
        self.summary_label=QLabel();self.summary_label.setObjectName("annotationSummary");self.summary_label.setWordWrap(True);self.summary_label.setTextFormat(Qt.TextFormat.RichText);self.summary_label.setAccessibleName("应用前确认：当前片段标注总结")
        self.summary_label.setStyleSheet("QLabel{background:#18252e;border:1px solid #42535d;border-radius:6px;padding:8px;}");rv.addWidget(self.summary_label)
        self.apply_button=self.button("应用标签  /  S 或 Enter",self.apply_current,"apply");rv.addWidget(self.apply_button)
        editrow=QHBoxLayout();editrow.addWidget(self.button("撤销",self.undo));editrow.addWidget(self.button("重做",self.redo));self.delete_button=self.button("删除片段",self.delete_selected,"danger");editrow.addWidget(self.delete_button);rv.addLayout(editrow)
        self.current_note=QLabel("应用后清空诊断；单击旧色块才能修改旧段。");self.current_note.setWordWrap(True);self.current_note.setObjectName("muted");rv.addWidget(self.current_note)
        right.setMinimumWidth(265);right.setMaximumWidth(330);split.addWidget(right);split.setSizes([210,960,310]);self.sync_controls()
        self.statusBar().showMessage("就绪 · 研究标注工具，不用于临床诊断")

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
        def field(key,prefix,missing):
            value=self.label.get(key);text=choices[key].get(value,missing);color=SELECTION_COLORS[key].get(value,"#9eabb6")
            return f'<span style="color:{color};font-weight:600">{escape(prefix+text)}</span>'
        parts=[field("finding_status","","所见未选")]
        finding=self.label.get("finding_status")
        if finding not in ("negative","non_evaluable"):
            parts.append(field("plaque_composition","","组成未选"))
        if finding=="non_evaluable":
            selected=[text for code,text,_ in REASON_OPTIONS if code in self.label.get("reason_codes",[])]
            parts.append('<span style="color:#a7b9c6">'+escape("原因："+"、".join(selected) if selected else "原因未选（可留空）")+'</span>')
        else:parts.extend([field("stenosis_grade","狭窄 ","未选"),field("confidence","把握 ","未选")])
        self.summary_label.setText(f'<span style="color:#98aab6">应用前确认 · {self.a:.2f}–{self.b:.2f} mm</span><br>本段：'+" · ".join(parts)+"。")
        selected=[text for code,text,_ in REASON_OPTIONS if code in self.label.get("reason_codes",[])]
        self.summary_label.setToolTip("原因："+"、".join(selected) if selected else "原因未选择（可留空，不阻止应用）")

    def spin(self,low,high,value,step,suffix):
        spin=QDoubleSpinBox();spin.setRange(low,high);spin.setDecimals(2);spin.setValue(value);spin.setSingleStep(step);spin.setSuffix(suffix);spin.setKeyboardTracking(False);spin.setMinimumWidth(64);spin.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed);return spin

    def image_panel(self,title,canvas,kind):
        frame=QFrame();frame.setObjectName("panel");v=QVBoxLayout(frame);v.setContentsMargins(5,4,5,4);v.setSpacing(2)
        row=QHBoxLayout();row.addWidget(QLabel(title));row.addStretch()
        if is_macos():
            window_button=self.button("窗宽/位");window_button.setObjectName(f"windowLevel_{kind}")
            window_button.setToolTip("触控板 / 无中键：打开调节板，左键拖动改变窗宽和窗位")
            window_button.setStyleSheet("padding:2px 4px;min-height:16px;")
            window_button.clicked.connect(lambda checked=False,b=window_button:self.show_window_level(b));row.addWidget(window_button)
        expand=self.button("放大",lambda:self.expand_image(kind));expand.setToolTip("放大/还原此影像窗");row.addWidget(expand)
        row.addWidget(self.button("居中",lambda:canvas.center_observation()))
        row.addWidget(self.button("1×",lambda:canvas.center_observation(True)))
        if kind=="native":
            self.native_rotate_button=self.button("90°",self.rotate_native_display)
            self.native_rotate_button.setObjectName("nativeRotate90")
            self.native_rotate_button.setAccessibleName("原始 CT 顺时针旋转 90 度")
            self.update_native_rotation_hint();row.addWidget(self.native_rotate_button)
            row.addWidget(self.button("回定位",self.native_return))
        if is_macos():
            # Cocoa's taller header buttons otherwise squeeze the two stacked
            # image panels below their 120 px canvas minimum in compact windows.
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
            self.hint("窗口较矮：请先增大窗口再展开邻近切片；也可单独放大影像窗。")
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
            menu=self.menuBar().addMenu("ImageCAS-X")
            self.quit_action=QAction("退出 ImageCAS-X",self)
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
        self.save_status.setText("操作未完成")
        QMessageBox.warning(self,title,str(error))

    def choose_package(self):
        path=QFileDialog.getExistingDirectory(self,"打开当前批次：选择单个已解压数据包文件夹（父目录会包含其所有子包）")
        if path:self.import_package(Path(path))

    def import_package(self,root,preferred_case=None):
        if self.loading:return False
        try:
            root=Path(root).resolve();additions=self._package_cases(root,{})
            if not self.guard_draft() or not self.save_view():return False
            roots=list(dict.fromkeys(self.package_roots+[str(root)]))
            target=preferred_case or (str(self.case.case_id) if self.case else next(iter(additions)))
            target=str(target) if str(target) in additions else next(iter(additions))
            # Publish the new folder/list only after its first image and saved
            # annotation provenance load successfully. Old labels stay in SQLite.
            self._pending_package_switch={"root":str(root),"roots":roots,"cases":additions,"target":target}
            if not self.load_case_async(target,case_path=additions[target]):
                self._pending_package_switch=None;return False
            return True
        except Exception as exc:
            self._pending_package_switch=None;self.error("数据包未载入",exc);return False

    def refresh_queue(self):
        active=str(self.case.case_id) if self.case else None
        self.case_list.blockSignals(True);self.case_list.clear()
        for cid in self.case_index:
            st=self.store.load(cid,self.reader_id)
            prefix="✓ 完成·待处理" if st.get("case_status")=="complete_with_gaps" else "✓ 完成" if st.get("case_status")=="complete" else "进行" if st.get("annotations") else "待标"
            item=QListWidgetItem(f"{prefix}  Case {cid}");item.setData(Qt.ItemDataRole.UserRole,cid);self.case_list.addItem(item)
            if cid==active:self.case_list.setCurrentItem(item)
        self.case_list.blockSignals(False)
        unavailable=f" · {len(self.unavailable_package_roots)} 包不可用" if self.unavailable_package_roots else ""
        self.queue_count.setText(f"本目录 {len(self.case_index)} 例 · {self.reader_id}"+unavailable)
        self.package_caption.setText("当前目录："+(Path(self.active_package_root).name if self.active_package_root else "尚未打开"))
        self.package_caption.setToolTip((self.active_package_root or "请选择单个已解压的数据包文件夹")+"\n只显示该目录及其子目录中的病例；切换目录不删除历史标注。")
        self.queue_count.setToolTip("仅显示当前打开目录；已完成/未完成病例均保留。其他批次标注仍在本机，可重新打开原目录继续。")

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
            self.reader_box.setText(self.reader_id);self.error("未切换读者",exc);return
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
        self.loading=True;self.save_status.setText(f"正在读取 Case {cid}…");self.case_title.setText(f"Case {cid} · 读取原始 HU 与映射")
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
                    raise ValueError("候选病例与当前数据包请求不一致，未切换目录")
                source=self.source_info(candidate,candidate_path)
                candidate_state=self.store.load(str(candidate.case_id),self.reader_id,source)
                scope={"package_roots":pending["roots"],"active_package_root":pending["root"],"active_package_cases":list(pending["cases"])} if pending else {}
                if not self._save_session(active_case_id=str(candidate.case_id),**scope):
                    raise OSError("候选病例会话记录保存失败，未切换病例")
            except Exception:
                self.worker_failed(generation,"load",traceback.format_exc());return
            if pending:
                self.package_roots=pending["roots"];self.active_package_root=pending["root"]
                self.case_index=pending["cases"];self.active_package_cases=list(self.case_index)
                self.unavailable_package_roots=[];self.session_notice=""
            self._pending_package_switch=None
            self.case,self.case_path,self.state=candidate,candidate_path,candidate_state
            self.loading=False;self.long_key=None;self.centralWidget().setEnabled(True)
            self.path_id="LAD";self.s=5.;self.a=0.;self.b=10.;self.angle_deg=0.;self.offset_mm=0.;self.native_z=0
            view=self.state.get("view_state",{})
            self.restore_view(view);self._follow_pending=not bool(view);self.sync_controls();self.render_images()
            self.save_status.setText("已载入 · 标签自动保存")
            self.case_title.setText(f"Case {self.case.case_id}  ·  {self.path_id}  ·  原始 HU")
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
            self.track.update();self.save_status.setText("已载入 · 标签自动保存");self.show_session_notice()

    @Slot(int,str,str)
    def worker_failed(self,generation,kind,error):
        if self._closing:return
        if kind=="load" and generation!=self.load_generation:return
        if kind=="render" and generation!=self.render_generation:return
        if kind=="load":self._pending_package_switch=None
        # Loading invalidated in-flight renders of the prior case. If the new
        # load fails, old pixels may no longer match its current observation or
        # angle: keep applying disabled until that retained case is resampled.
        self.loading=False;self.render_busy=(kind=="render" or self.case is not None)
        self.centralWidget().setEnabled(True);self.refresh_queue();self.sync_controls()
        self.retry_render_button.setVisible(self.case is not None)
        self.case_title.setText(f"Case {self.case.case_id} · {self.path_id} · 保留先前病例" if self.case else "没有载入病例")
        if kind=="load" and self.case is not None:
            self.long_key=None;self.render_images()
        self.error("影像读取 / 几何检查失败",error)
        if kind=="render":self.hint("当前影像未更新，暂不应用标签。可点上方‘重试影像’；草稿保留，也可切换血管或导出已保存标签。")

    def retry_render(self):
        if not self.case or self.loading:return
        self.long_key=None;self.render_timer.stop();self.invalidate_render();self.render_images()

    def source_info(self,case=None,case_path=None):
        case=case or self.case;case_path=case_path or self.case_path
        return {"dataset":"ImageCAS-X","official_patient_split":case.manifest.get("official_split"),"geometry_id":case.manifest.get("geometry_id"),"physical_coordinate_system":"LPS","coordinate_units":"mm","interval_convention":"half-open-start-inclusive-end-exclusive","case_manifest":str(case_path.name)}

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
        self.save_status.setText("正在更新影像…")

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
        self.native_rotate_button.setToolTip(f"原始 CT 当前显示角度 {self.native_rotation_quarters*90}°；点按顺时针旋转 90°。首次默认 180°，切换血管和重新打开时沿用；不改变影像数据或标注坐标。")

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
        if self._save_session():self.hint(f"原始 CT 显示 {self.native_rotation_quarters*90}° · 切换血管沿用；标注坐标未改变")

    def toggle_reference_lines(self):
        self.reference_lines_visible=not self.reference_lines_visible
        self.reference_lines_button.setChecked(self.reference_lines_visible)
        for canvas in self.canvases:canvas.update()
        if self._save_session():self.hint("绿色参考线已"+("显示" if self.reference_lines_visible else "隐藏")+" · 按 Z 切换；橙框、辅助标记和标签不变")

    def native_pick(self,pos,canvas):
        if not self.case or not self.path:return
        xy=canvas.native_screen_to_index(pos)
        if xy is None:return
        idx=(*xy,self.native_z)
        point=self.case.native.index_to_world(idx);s=self.path.nearest_s(point,max_distance_mm=5)
        if s is None:self.hint("点击位置不在当前血管附近；未自动切换分支")
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
        return float(lm.get("verified_end_mm",0)) if lm.get("status")=="verified" else 0.

    def path_records(self):
        if not self.state:return []
        records=[r for r in self.state["annotations"] if r["path_id"]==self.path_id]
        if self.path_id=="LCX" and self.lm_end()>0:
            records += [r for r in self.state["annotations"] if r["path_id"]=="LAD" and r.get("canonical_anatomy_id")=="LM" and r["s_end_mm"]<=self.lm_end()+1e-6]
        return sorted(records,key=lambda r:r["s_start_mm"])

    def path_markers(self):return [m for m in self.state.get("markers",[]) if m["path_id"]==self.path_id] if self.state else []
    def path_rereview(self):return [r for r in self.state.get("rereview_intervals",[]) if r["path_id"]==self.path_id] if self.state else []

    def change_path(self,path_id):
        if not self.case or path_id==self.path_id:return
        if not self.guard_draft():return
        self.path_id=path_id;self.clear_draft();self.a=0.;self.b=min(10,self.path.length_mm);self.long_key=None
        self.s=min(5,self.path.length_mm);self.observe(self.s);self.cpr.center_observation();self.sync_controls();self.update_geometry_status()
        self.case_title.setText(f"Case {self.case.case_id}  ·  {self.path_id}  ·  原始 HU")

    def update_geometry_status(self):
        lm=self.case.canonical_lm;status=lm.get("status","ambiguous")
        text=f"LM 共用段 0–{self.lm_end():.2f} mm · LAD 编辑 / LCX 同步显示" if status=="verified" else "无共享 LM" if status=="not_present" else "LM 映射待研究端 QA：不自动镜像，不影响保存"
        if lm.get("ambiguous_ranges"):text+=" · 分叉过渡带由研究端检查，不要求医生重复确认"
        self.geometry_status.setText(text+"   |   CPR 与 native 使用 LPS 毫米坐标")

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
        self.clear_draft();self.hint("已退出修改；橙框位置保留，下一次应用为新的片段标签。")
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
        self.snap_caption=f"吸附 {target:.2f} mm · {alternate_modifier_label()} 可脱离" if target is not None else ""
        peak=self.label.get("s_peak_stenosis_mm")
        if peak is not None and not self.a<=peak<self.b:
            self.label.pop("s_peak_stenosis_mm",None);self.hint("原最狭窄位置已在框外，请重新指定")
        self.sync_controls();self.update_overlays();self.schedule_view_save()

    def clear_snap(self):self.snap_target=None;self.snap_caption="";self.track.update()
    def update_overlays(self):self.cpr.update();self.cross.update();self.native.update();self.track.update()
    def set_hide_overlays(self,value):self.hide_overlays=value;self.cpr.update()

    def set_label(self,key,value):
        before=self.draft_snapshot();self.label=transition_label(self.label,key,value)
        self.dirty=self.dirty or self.label!=before["label"];self.sync_controls();self.finish_draft_gesture(before)

    def typical_normal(self):
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
        if not label_applicability(self.label)["peak_enabled"]:self.hint("仅可在已选非零狭窄等级的可评估病变段指定最狭窄处");return
        if not self.a<=self.s<self.b:self.hint("最狭窄位置必须位于当前橙框内");return
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
                button.setEnabled(enabled)
            group.setExclusive(True)
            visible=key=="finding_status" or (applicable["finding_selected"] and finding!="non_evaluable" and not (key=="plaque_composition" and finding=="negative"))
            self.field_sections[key].setVisible(visible)
        self.peak_panel.setVisible(applicable["peak_enabled"])
        self.reason_panel.setVisible(applicable["reasons_enabled"])
        self.flow_note.setText("不可评估：跳过2–4，原因可选；不会保存为正常或低把握诊断。" if finding=="non_evaluable" else "明确正常：无斑块、狭窄0%；请选择判断把握。" if finding=="negative" else "请从上向下选择；更改组成或狭窄后需重新确认把握。" if applicable["finding_selected"] else "先选择本段所见，再显示适用选项。")
        for code,button in self.reason_buttons.items():
            button.setEnabled(applicable["reasons_enabled"])
            button.setChecked(code in self.label.get("reason_codes",[]));button.setIcon(self.selected_icon if button.isChecked() else QIcon())
        old_reason=self.label.get("reason","")
        legacy_codes=[text for code,text,_ in REASON_OPTIONS if code in self.label.get("reason_codes",[]) and not applicable["reasons_enabled"]]
        legacy_text=("旧版备注（保留）："+old_reason if old_reason else "")+("\n旧版原因（保留，非当前可选项）："+"、".join(legacy_codes) if legacy_codes else "")
        self.legacy_reason_label.setText(legacy_text.strip());self.legacy_reason_label.setVisible(bool(legacy_text))
        limit=self.path.length_mm if self.path else 999
        for spin in (self.start_spin,self.end_spin):spin.setMaximum(limit)
        self.start_spin.setValue(self.a);self.end_spin.setValue(self.b)
        for n,b in self.path_buttons.items():b.setChecked(n==self.path_id);b.setEnabled(self.case is not None and not self.loading)
        self.draft_title.setText(f"修改片段 {self.editing_id[:8]}" if self.editing_id else "待标注 · 新片段")
        self.exit_edit_button.setVisible(bool(self.editing_id))
        review=self.edit_requires_review();rec=self.editing_record()
        self.apply_button.setText("确认并应用  /  S 或 Enter" if review else "保存修改  /  S 或 Enter" if self.editing_id else "应用标签  /  S 或 Enter")
        note="裁切保留了原段的汇总判断。请查看本段；点击‘确认并应用’即完成复核，无需额外勾选。" if review else "正在修改旧片段；移动或缩短后，退出的旧范围回到未标注。可点‘退出修改’保留橙框另标。" if self.editing_id else "应用后清空诊断；单击旧色块才能修改旧段。"
        if rec and (rec.get("provenance",{}).get("label_scope")=="group_summary_only" or rec.get("provenance",{}).get("review_reason")=="canonical_split_group_summary_requires_subsegment_review"):
            original=rec["provenance"].get("source_label_group_summary",{})
            composition=dict((v,t) for t,v in COMPOSITIONS).get(original.get("plaque_composition"),"未定")
            stenosis=dict((v,t) for t,v in STENOSES).get(original.get("stenosis_grade"),"未定")
            note=f"原跨分支整段判断：{composition} / 最大狭窄{stenosis}；不等于当前子段判断。此处可独立补充，或退出修改继续阅片；研究端保留整段汇总。"
        if rec and rec.get("provenance",{}).get("training_geometry_eligible") is False:note+=" 几何问题由研究端 QA 处理，不阻止本次保存。"
        self.current_note.setText(note)
        self.current_note.setStyleSheet("color:#e6bd7e;" if review else "color:#92a2af;")
        peak=self.label.get("s_peak_stenosis_mm");self.peak_label.setText(f"最狭窄位置：{peak:.2f} mm" if peak is not None else "最狭窄位置：未指定（辅助标记不代替）")
        self.delete_button.setEnabled(bool(self.editing_id or self.selected_marker));self.apply_button.setEnabled(self.case is not None and not self.loading and not self.render_busy)
        self.update_summary()
        self.update_completion_display()
        self._syncing=False

    def guard_draft(self):
        if not self.dirty:return True
        # An empty new orange range is navigation state, not a diagnosis to force.
        if not self.label and not self.editing_id:return self.save_view()
        dialog=QMessageBox(self);dialog.setWindowTitle("当前草稿尚未应用")
        dialog.setText("是否保存当前片段的判断？");dialog.setInformativeText("‘放弃草稿’仅丢弃未应用的改动，不删除已经保存的标签。")
        apply=dialog.addButton("确认并应用" if self.edit_requires_review() else "应用并继续",QMessageBox.ButtonRole.AcceptRole)
        discard=dialog.addButton("放弃草稿",QMessageBox.ButtonRole.DestructiveRole)
        keep=dialog.addButton("返回编辑",QMessageBox.ButtonRole.RejectRole);dialog.setDefaultButton(keep);dialog.setEscapeButton(keep);dialog.exec()
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
            self.path_id=rec["path_id"];self.long_key=None;self.hint("共享 LM：已跳转 LAD 主视图编辑")
        self.a=rec["s_start_mm"];self.b=rec["s_end_mm"];self.editing_id=annotation_id;self.selected_marker=None;self.label=deepcopy(rec["label"]);self.dirty=False
        self.legacy_anatomy=rec.get("anatomical_segment","");self.draft_undo.clear();self.draft_redo.clear()
        self.observe(self.label.get("s_peak_stenosis_mm",(self.a+self.b)/2));self.sync_controls();self.update_geometry_status();self.update_overlays()
        self.hint("已选待确认片段：阅片后点‘确认并应用’或按 S / Enter；无需另外勾选。" if self.edit_requires_review() else "已选旧片段：拖框会修改此片段，点‘退出修改’可保留橙框另标。")

    def select_marker(self,marker_id):
        marker=next((m for m in self.state["markers"] if m["marker_id"]==marker_id),None)
        if marker:self.observe(marker["s_mm"]);self.selected_marker=marker_id;self.sync_controls();self.update_overlays()

    def bookmark(self):
        if not self.state:return
        try:
            result=add_marker(self.state,self.path_id,self.s)
            if result==self.state:self.hint("此位置已有辅助标记");return
            self.state=self.store.commit(result,"add_marker",{"path_id":self.path_id,"s_mm":self.s})
            self.selected_marker=None;self.update_overlays();self.hint(f"辅助标记已放置于 {self.s:.2f} mm；未修改标签")
        except Exception as exc:self.error("辅助标记未保存",exc)

    def make_annotation(self,a,b,path_id,canonical,group):
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
        if self.case.canonical_lm.get("status") not in ("verified","not_present") and path_id in ("LAD","LCX"):
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
        if any(view.gesture for view in self.canvases+[self.track]):
            self.hint("请先松开鼠标完成当前拖动，再应用标签")
            return False
        if self.render_busy:
            self.hint("当前影像正在更新，请稍候再应用标签")
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
            self.save_status.setText(f"已保存 · 修订 {self.state['revision']}" if view_saved else "标签已保存；草稿/会话保存失败，请勿关闭")
            self.hint("已应用；诊断已清空。拖橙框准备下一段，或单击旧色块修改。")
            return True
        except Exception as exc:self.error("标签未应用",exc);return False

    def delete_selected(self):
        if not self.state:return
        try:
            if self.selected_marker:
                changed=delete_marker(self.state,self.selected_marker);self.state=self.store.commit(changed,"delete_marker");self.selected_marker=None
            elif self.editing_id:
                changed=delete_annotation(self.state,self.editing_id);self.state=self.store.commit(changed,"delete_annotation",{"annotation_id":self.editing_id});self.clear_draft();self.refresh_queue()
                self.hint(f"已删除片段：该范围回到待重阅；{primary_modifier_label()}+Z 可恢复")
            else:return
            self.sync_controls();self.update_overlays();self.save_view()
        except Exception as exc:self.error("删除未完成",exc)

    def undo(self):
        if not self.state:return
        try:
            if self.draft_undo:
                self.draft_redo.append(self.draft_snapshot());self.restore_draft(self.draft_undo.pop());self.schedule_view_save();return
            if self.dirty and not self.guard_draft():return
            result=self.store.undo(str(self.case.case_id),self.reader_id)
            if result:self.state=result;self.clear_draft();self.refresh_queue();self.hint("已撤销整个事务；历史仍保留")
        except Exception as exc:self.error("撤销失败",exc)

    def redo(self):
        if not self.state:return
        try:
            if self.draft_redo:
                self.draft_undo.append(self.draft_snapshot());self.restore_draft(self.draft_redo.pop());self.schedule_view_save();return
            if self.dirty and not self.guard_draft():return
            result=self.store.redo(str(self.case.case_id),self.reader_id)
            if result:self.state=result;self.clear_draft();self.refresh_queue();self.hint("已重做事务")
        except Exception as exc:self.error("重做失败",exc)

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
            self.save_status.setText("保存失败：请勿关闭");self.hint(f"草稿保存失败：{exc}");return False

    def restore_view(self,view):
        self.clear_draft()
        self.path_id=view.get("path_id","LAD")
        if self.path_id not in self.case.paths:self.path_id="LAD"
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
            lengths={name:path.length_mm for name,path in self.case.paths.items()}
            samples={name:path.distances.tolist() for name,path in self.case.paths.items()}
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
        return sorted(issues,key=lambda r:(("LAD","LCX","RCA").index(r["path_id"]),r["s_start_mm"],r["kind"]))

    def coverage_issues_for_path(self):
        return [r for r in self.coverage_issues() if r["path_id"]==self.path_id]

    @staticmethod
    def coverage_issue_text(issue):
        kind=issue.get("kind")
        reason="未标注" if kind=="gap" else "待重阅" if kind=="rereview" else "片段需复核"
        provenance=issue.get("provenance",{})
        if kind=="review":reason="范围 / 原判断待确认，选中后确认并应用"
        return f"{issue['path_id']} · {issue['s_start_mm']:.2f}–{issue['s_end_mm']:.2f} mm · {reason}"

    def update_completion_display(self):
        if not hasattr(self,"completion_caption"):return
        report=self.completion_report()
        if report is None:
            self.completion_caption.setText("覆盖检查：尚未载入");self.next_issue_button.setEnabled(False);return
        self.completion_caption.setText(f"本例 {report['coverage_percent']:.2f}% · 未标 {len(report['gaps'])} / 复核 {len(report['review_required'])}")
        self.completion_caption.setToolTip(f"按CPR切片位置统计，已标 {report['covered_slices']} / {report['total_slices']}；可靠共享LM只计一次。\n红色缺口可点击定位；另有研究端 QA {report.get('technical_qa_count',0)} 条，不要求医生重复确认。完成标记不代表全部可用于训练。")
        self.next_issue_button.setEnabled(bool(self.coverage_issues(report)))
        for path,button in self.path_buttons.items():
            detail=report.get("by_path",{}).get(path,{})
            button.setToolTip(f"{path} · 已标 {detail.get('coverage_percent',0):.2f}%（LM不重复计数）")

    def jump_to_coverage_issue(self,issue):
        if not self.guard_draft():return False
        if issue.get("annotation_id"):
            self.select_annotation(issue["annotation_id"])
        else:
            self.clear_draft();self.path_id=issue["path_id"];self.long_key=None
            self.a=max(0.,issue["s_start_mm"]);self.b=min(self.path.length_mm,issue["s_end_mm"])
            self.observe((self.a+self.b)/2);self.sync_controls();self.update_geometry_status();self.update_overlays()
            self.cpr.center_observation();self.schedule_view_save()
        self.track.setFocus();self.hint(self.coverage_issue_text(issue)+"；橙框已定位，请阅片后应用标签。")
        return True

    def next_coverage_issue(self):
        issues=self.coverage_issues()
        if not issues:return
        path_order=("LAD","LCX","RCA");here=(path_order.index(self.path_id),self.s)
        later=[r for r in issues if (path_order.index(r["path_id"]),r["s_start_mm"])>here]
        self.jump_to_coverage_issue(later[0] if later else issues[0])

    def build_completion_dialog(self,report):
        dialog=QDialog(self);dialog.setWindowTitle("本例覆盖检查 · 点击缺口定位");dialog.resize(650,460)
        layout=QVBoxLayout(dialog);heading=QLabel(f"已标 {report['coverage_percent']:.2f}%（{report['covered_slices']} / {report['total_slices']} 个切片位置）")
        heading.setStyleSheet("font-size:17px;font-weight:600;");layout.addWidget(heading)
        detail=[]
        for path,row in report.get("by_path",{}).items():detail.append(f"{path}  {row.get('coverage_percent',0):.2f}%")
        layout.addWidget(QLabel("　 |　 ".join(detail)))
        note=QLabel(f"未标 {len(report['gaps'])}处，需复核 {len(report['review_required'])}处。\n红色底条可点击补标；下列条目单击选中，双击直接定位。未标注不会补成正常。");note.setWordWrap(True);layout.addWidget(note)
        items=QListWidget();items.setObjectName("completionIssues");layout.addWidget(items,1)
        for issue in self.coverage_issues(report):
            item=QListWidgetItem(self.coverage_issue_text(issue));item.setData(Qt.ItemDataRole.UserRole,issue);items.addItem(item)
        if items.count():items.setCurrentRow(0)
        warnings=report.get("geometry_warnings",[])
        if warnings or report.get("technical_qa_count"):
            warning=QLabel("另有几何映射 / 整段汇总待研究端 QA；不阻止医生完成，不要求重复确认；导出仍保留限制。");warning.setWordWrap(True);layout.addWidget(warning)
        allowed=bool(report.get("can_override"))
        explanation=QLabel("覆盖已超过90%，可选择依然完成；将明确记录遗漏/待复核，不代表全覆盖或训练验收。" if allowed else "覆盖必须严格超过90%才可选择依然完成；目前请先定位补标（显示四舍五入不改变实际阈值）。")
        explanation.setWordWrap(True);layout.addWidget(explanation)
        row=QHBoxLayout();locate=self.button("定位所选");locate.setEnabled(items.count()>0);row.addWidget(locate);row.addStretch()
        keep=self.button("继续标注",dialog.reject);keep.setDefault(True);row.addWidget(keep)
        override=self.button("依然标记为已完成");override.setObjectName("completeWithGaps");override.setEnabled(allowed);row.addWidget(override);layout.addLayout(row)
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
            self.hint("已标记完成（保留缺口 / 待复核），未填补正常标签。" if self.state["case_status"]=="complete_with_gaps" else "本例覆盖检查通过，已标记完成；不等于双读裁决完成。")
            return True
        except Exception as exc:self.error("完成状态未保存",exc);return False

    def mark_complete(self):
        if not self.case or not self.guard_draft():return
        report=self.completion_report(refresh=True);self.update_completion_display();self.track.update()
        if report["complete"]:self.commit_completion(report)
        else:self.build_completion_dialog(report).exec()

    def export_current(self):
        if not self.case:return
        if not self.save_view():return
        folder=QFileDialog.getExistingDirectory(self,"选择标签导出目录（不包含影像）")
        if not folder:return
        try:
            if not self.save_view():return
            result=self.store.export_case(str(self.case.case_id),self.reader_id,Path(folder))
            self.hint(f"标签已导出：{result}");QMessageBox.information(self,"已导出",str(result)+"\n只导出已应用标签；未应用内容保留为独立草稿，不当作正式标注。无需回传影像。")
        except Exception as exc:self.error("导出失败",exc)

    def export_all(self):
        self._export_cases(None)

    def export_batch(self):
        if not self.active_package_root:
            self.hint("请先打开当前批次的数据包文件夹；全部历史备份可点导出按钮右侧小箭头。")
            return
        self._export_cases(set(self.active_package_cases))

    def _export_cases(self,case_ids):
        if not self.save_view():return
        scope="全部历史" if case_ids is None else "当前批次"
        folder=QFileDialog.getExistingDirectory(self,f"选择{scope}标签导出目录（建议新建结果文件夹）")
        if not folder:return
        if not self.save_view():return
        count=0
        try:
            for cid in self.store.list_case_ids(self.reader_id):
                if case_ids is not None and cid not in case_ids:continue
                state=self.store.load(cid,self.reader_id)
                if state.get("annotations") or state.get("markers") or state.get("rereview_intervals"):
                    self.store.export_case(cid,self.reader_id,Path(folder));count+=1
            QMessageBox.information(self,"批量导出完成",f"{scope}：导出 {count} 例的 {self.reader_id} 独立记录（不需要连接影像）。\n未应用草稿仍为草稿；未标注不补成正常。\n导出不会清空标签。可将整个结果文件夹压缩回传，无需回传影像。")
        except Exception as exc:self.error("批量导出未完成",exc)

    def import_labels(self):
        if not self.guard_draft() or not self.save_view():return
        path,_=QFileDialog.getOpenFileName(self,"导入本读者的标签 JSON",filter="JSON (*.json)")
        if not path:return
        try:
            result=self.store.import_case(path,reader_id=self.reader_id)
            self.refresh_queue()
            if self.case and str(result["case_id"])==str(self.case.case_id):self.state=self.store.load(str(self.case.case_id),self.reader_id,self.source_info());self.clear_draft()
            self.hint("导入完成；未自动覆盖其他读者或冲突版本")
        except Exception as exc:self.error("标签未导入",exc)

    def show_help(self):
        architecture={"x86_64":"Intel x86_64","arm64":"Apple Silicon arm64"}.get(runtime_platform.machine(),runtime_platform.machine())
        platform_note=f"Mac {__version__}：{architecture}，兼容构建目标 macOS 12+；首次打开及医生实际设备操作仍需确认。" if is_macos() else f"Windows {__version__}：与 Mac 版共享标注引擎；既有数据包与标签兼容。"
        platform_note+="\n原始 CT 默认显示 180°，标题 90° 按钮每次顺时针转 90°，切换血管及重启保留。Z 切换三窗绿色参考线；不改变标签或坐标。"
        platform_note+="\n当前目录模式：每次打开一个解压后的批次文件夹，只列出其中病例。选父目录会包含其子包。切换不删除旧标注，重新打开旧目录即可继续。‘导出本批’只导出当前批次；右侧小箭头可导出本读者全部历史备份。"
        QMessageBox.information(self,"使用与数据安全", "1. 解压数据包，打开其文件夹；确认读者编号。\n2. 滚轮 / 双指滚动阅片，Space 放辅助标记，拖橙框确定范围。\n3. 点击标签或‘典型正常段’，再点击应用 / S / Enter。\n4. 单击已有色块可修改；重叠部分以新标签为准。\n5. 待确认残片直接‘确认并应用’，无需额外勾选。\n6. ‘退出修改’保留橙框另标；几何问题由研究端 QA 处理。\n\n应用成功即保存；草稿单独自动恢复，不是正式标签。导出不强迫应用草稿。‘导出本批’仅包含当前目录的已标病例；右侧小箭头里的‘全部历史’包含本读者在数据库中的已标病例，影像盘断开也可备份。压缩完整结果文件夹回传，无需影像。\n\n应用后不继承诊断。缩短或删除旧段留下的空白是未标注，不是正常。所有已保存操作可撤销。\n\n数据库："+str(self.db_path)+"\n请定期导出备份，不要把正在写入的数据库放入同步盘。\n\n"+platform_note+"\n本程序用于研究标注，不用于临床诊断。")

    def closeEvent(self,event):
        if self._closing:event.accept();return
        popup=getattr(self,"window_level_popup",None)
        if popup is not None:popup.close()
        # Recoverable drafts persist without forcing a diagnostic commit on exit.
        self._last_close_save_succeeded=bool(self.save_view())
        if not self._last_close_save_succeeded:
            if not self._smoke_silent_exit:
                QMessageBox.warning(self,"退出已暂停","草稿保存失败。请检查磁盘可用空间或导出当前标签，再重试退出。")
            event.ignore();return
        self._closing=True;self.render_timer.stop();self.save_timer.stop()
        self.pool.clear();self.pool.waitForDone(3000);self.store.close();self._db_lock.unlock();self._close_completed=True;event.accept()


class Neighbor(QWidget):
    def __init__(self,c,offset):
        super().__init__();self.c=c;self.offset=offset;self.s=0.;self.hu=None;self.qimage=None
        self.setFixedHeight(106);self.setMinimumWidth(48);self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("点击将此邻近横截面设为当前观察位置")
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


def finalize_smoke_test(window,output,started,forced_failure=None,verify_display=False,folder_scope_package=None):
    """Publish PASS only after screenshot and the ordinary save/close gate pass.

    This runs only for explicit, isolated smoke QA. Failure is returned to the
    process runner; a failed save still vetoes the window close, without a
    modal dialog leaving an unattended QA process blocked forever.
    """
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    app=QApplication.instance();case=window.case
    report={"status":"FAIL","app_version":__version__,"frozen":bool(getattr(sys,"frozen",False)),
            "executable":sys.executable,"qt_platform":app.platformName(),"host_platform":sys.platform,
            "host_machine":runtime_platform.machine(),"host_macos_version":runtime_platform.mac_ver()[0] if is_macos() else None,
            "logical_window_size":[window.width(),window.height()],"device_pixel_ratio":float(window.devicePixelRatioF()),
            "native_cocoa_runtime":app.platformName()=="cocoa","startup_seconds":round(time.monotonic()-started,3),
            "clinical_labels_created":False,"clean_windows_acceptance":False,"clean_macos_acceptance":False,
            "system_first_open_acceptance":False,"physical_input_acceptance":False,"reader_acceptance":False,
            "screenshot_saved":False,"view_saved_on_close":False,"close_accepted":False,"close_completed":False}
    failures=[]
    if forced_failure:failures.append(forced_failure)
    try:
        if case:
            report.update(case_id=str(case.case_id),geometry_id=case.manifest.get("geometry_id"),
                          image_shapes={n:list(p.volume.shape) for n,p in case.paths.items()},native_shape=list(case.native.array_zyx.shape))
        if not case or window.loading or window.render_busy or any(canvas.qimage is None for canvas in window.canvases):
            failures.append("IMAGES_NOT_READY")
        elif not forced_failure:
            if verify_display:
                from .display_smoke import verify_display_controls
                report["display_controls"]=verify_display_controls(window,output)
            if folder_scope_package is not None:
                from .folder_smoke import verify_folder_controls
                report["folder_scope_controls"]=verify_folder_controls(window,folder_scope_package,output)
            screenshot=output/"packaged_app.png"
            report["screenshot_saved"]=bool(window.grab().save(str(screenshot))) and screenshot.is_file() and screenshot.stat().st_size>0
            if not report["screenshot_saved"]:failures.append("SCREENSHOT_SAVE_FAILED")
    except Exception as exc:failures.append(f"SCREENSHOT_OR_METADATA_FAILED: {exc}")
    previous_silent=window._smoke_silent_exit;window._smoke_silent_exit=True
    try:
        report["close_accepted"]=bool(window.close())
        report["view_saved_on_close"]=window._last_close_save_succeeded is True
        report["close_completed"]=bool(window._close_completed)
        if not report["view_saved_on_close"]:failures.append("VIEW_SAVE_FAILED")
        if not report["close_accepted"] or not report["close_completed"]:failures.append("CLOSE_NOT_COMPLETED")
    except Exception as exc:failures.append(f"CLOSE_FAILED: {exc}")
    finally:window._smoke_silent_exit=previous_silent
    report["status"]="PASS" if not failures else forced_failure or "FAIL"
    if failures:report["failures"]=failures
    (output/"smoke_test.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description="Offline ImageCAS-X CPR interval annotation workstation")
    parser.add_argument("--package",type=Path);parser.add_argument("--case");parser.add_argument("--reader",default=None);parser.add_argument("--db",type=Path)
    parser.add_argument("--smoke-test-output",type=Path,help="Write packaged-app diagnostic evidence and exit; does not create clinical labels")
    parser.add_argument("--smoke-test-next-package",type=Path,help="With isolated smoke QA only: verify two real package folders do not accumulate")
    args=parser.parse_args(argv)
    if args.smoke_test_next_package is not None and (args.smoke_test_output is None or args.package is None):
        parser.error("--smoke-test-next-package requires --smoke-test-output and --package")
    if args.smoke_test_output is not None and args.db is None:
        args.db=args.smoke_test_output.resolve()/"qa_workspace"/"annotations.sqlite"
    app=QApplication(sys.argv[:1]);app.setApplicationName("ImageCASXAnnotator");app.setOrganizationName("ImageCASXResearch")
    def exception_hook(kind,value,tb):
        message="".join(traceback.format_exception(kind,value,tb))
        if sys.stderr:sys.stderr.write(message)
        logdir=user_log_dir(args.db)
        try:
            logdir.mkdir(parents=True,exist_ok=True)
            with (logdir/"errors.log").open("a",encoding="utf-8") as log:log.write(message+"\n")
        except OSError as exc:
            if sys.stderr:sys.stderr.write(f"Error log unavailable: {exc}\n")
        if args.smoke_test_output is not None:
            app.exit(2)
        else:QMessageBox.critical(None,"程序错误（未保存操作请检查）",message[-3000:])
    sys.excepthook=exception_hook
    window=MainWindow(args.db,args.package,args.reader,args.case,restore_last=not bool(args.smoke_test_output));window.show()
    if args.smoke_test_output:
        started=time.monotonic();output=args.smoke_test_output;output.mkdir(parents=True,exist_ok=True)
        timer=QTimer(window)
        def check_ready():
            if window.case and window.cpr.qimage is not None and not window.loading and not window.render_busy:
                timer.stop();report=finalize_smoke_test(window,output,started,verify_display=True,folder_scope_package=args.smoke_test_next_package)
                # No second close: it could duplicate a save or a failure dialog.
                app.exit(0 if report["status"]=="PASS" else 2)
            elif time.monotonic()-started>90:
                timer.stop();finalize_smoke_test(window,output,started,forced_failure="FAIL_TIMEOUT");app.exit(2)
        timer.timeout.connect(check_ready);timer.start(100)
    return app.exec()
