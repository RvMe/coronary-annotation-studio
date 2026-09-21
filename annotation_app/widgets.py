"""Medical image surfaces and direct-manipulation interval controls.

All coordinates sent to the controller are physical centerline millimetres.
The widget never writes labels; a drag only changes a recoverable draft.
"""
from __future__ import annotations

from .i18n import tr

import math
import time
from copy import deepcopy

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPolygonF, QBrush
from PySide6.QtWidgets import QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout

from .i18n_widgets import QWidget, QLabel, QPushButton

from .domain import snap_endpoint,effective_reader_review_required
from .imaging import window_uint8
from .label_catalog import ANNOTATION_COLORS
from .platform_support import is_macos

TEAL = QColor("#59c7c0")
ORANGE = QColor("#efac62")
TEXT = QColor("#dce5ec")
MUTED = QColor("#8c9cab")
COLORS = ANNOTATION_COLORS


def annotation_color(record):
    label = record.get("label", {})
    key = label.get("plaque_composition") if label.get("finding_status") == "positive" else label.get("finding_status")
    return QColor(COLORS.get(key, "#a4acb5"))


def _wheel_delta(event):
    """Return wheel-notch equivalents without inventing motion for phase events.

    Cocoa supplies pixel deltas for a trackpad. Prefer those to the coarser
    angle delta when both are available; an ordinary wheel retains 120 = 1.
    """
    pixels = event.pixelDelta().y()
    angle = event.angleDelta().y()
    return pixels / 40.0 if pixels and (is_macos() or not angle) else angle / 120.0


class WindowLevelPad(QWidget):
    """Display-only left-drag alternative to a mouse middle button."""

    changed = Signal()

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.c = controller
        self.gesture = None
        self.setMinimumSize(220, 92)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setObjectName("window_level_pad")
        self.setAccessibleName(tr('Window and level drag pad'))
        self.setToolTip(tr('Left-drag horizontally for window and vertically for level. Double-click resets; Esc cancels the drag.'))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#172632"))
        painter.setPen(QColor("#66818c"))
        center = self.rect().center()
        painter.drawLine(12, center.y(), self.width() - 12, center.y())
        painter.drawLine(center.x(), 12, center.x(), self.height() - 12)
        painter.setPen(TEXT)
        painter.drawText(self.rect().adjusted(4, 4, -4, -4), Qt.AlignmentFlag.AlignCenter,
                         tr('Left-drag\n← Window →    ↑ Level ↓'))

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.setFocus()
        self.gesture = (QPointF(event.position()), self.c.width_hu, self.c.level_hu)
        self.grabMouse()
        event.accept()

    def mouseMoveEvent(self, event):
        if self.gesture is None:
            return
        position, width, level = self.gesture
        delta = event.position() - position
        self.c.set_window(width + delta.x() * 3, level - delta.y() * 2)
        self.changed.emit()
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.gesture is not None:
            self.gesture = None
            self.releaseMouse()
            self.c.schedule_view_save()
            event.accept()

    def cancel_drag(self):
        if self.gesture is None:
            return False
        _, width, level = self.gesture
        self.gesture = None
        self.releaseMouse()
        self.c.set_window(width, level)
        self.c.schedule_view_save()
        self.changed.emit()
        return True

    def reset_window(self):
        self.gesture = None
        self.releaseMouse()
        self.c.set_window(700, 250)
        self.c.schedule_view_save()
        self.changed.emit()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.reset_window()
            event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.cancel_drag():
            event.accept()
        else:
            super().keyPressEvent(event)


class WindowLevelPopover(QWidget):
    """Compact image-header popup; it does not occupy the annotation panel."""

    def __init__(self, controller, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.c = controller
        self.setObjectName("window_level_popover")
        self.setWindowTitle(tr('Window / level'))
        self.setFixedWidth(280)
        layout = QVBoxLayout(self)
        self.value_label = QLabel(self)
        self.value_label.setObjectName("window_level_value")
        layout.addWidget(self.value_label)
        self.pad = WindowLevelPad(controller, self)
        layout.addWidget(self.pad)
        footer = QHBoxLayout()
        self.reset_button = QPushButton(tr('Reset 700 / 250'), self)
        self.reset_button.setObjectName("window_level_reset")
        self.reset_button.clicked.connect(self.pad.reset_window)
        footer.addWidget(self.reset_button)
        self.close_button = QPushButton(tr('Close'), self)
        self.close_button.clicked.connect(self.close)
        footer.addWidget(self.close_button)
        layout.addLayout(footer)
        self.pad.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        self.value_label.setText(tr('Window {p0:.0f} HU · Level {p1:.0f} HU' ,p0=self.c.width_hu,p1=self.c.level_hu))

    def popup_at(self, global_position):
        self.refresh()
        self.adjustSize()
        self.move(global_position)
        self.show()
        # Keep the small popup within the current monitor, including its dock.
        available = self.screen().availableGeometry()
        self.move(max(available.left(), min(self.x(), available.right() - self.width() + 1)),
                  max(available.top(), min(self.y(), available.bottom() - self.height() + 1)))
        self.pad.setFocus()

    def hideEvent(self, event):
        self.pad.cancel_drag()
        super().hideEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if not self.pad.cancel_drag():
                self.close()
            event.accept()
        else:
            super().keyPressEvent(event)


class ImageCanvas(QWidget):
    """HU display plus consistent pan/zoom/window and CPR interval gestures."""

    def __init__(self, controller, kind, parent=None):
        super().__init__(parent)
        self.c = controller
        self.kind = kind
        self.hu = None
        self.qimage = None
        self.zoom = 1.0
        self.pan = QPointF(0, 0)
        self.gesture = None
        self.held_snap = None
        self._native_wheel_remainder = 0.0
        self._native_wheel_path = None
        self.setMinimumSize(130, 80)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setObjectName(f"canvas_{kind}")
        self.setAccessibleName(tr({"cpr":"Longitudinal CPR image","cross":"Orthogonal cross-section image","native":"Native CT image"}[kind]))
        self._edge_timer = QTimer(self)
        self._edge_timer.setInterval(40)
        self._edge_timer.timeout.connect(self._edge_scroll)

    def set_hu(self, hu):
        self.hu = hu
        self.rewindow()

    def rewindow(self):
        if self.hu is not None:
            a = window_uint8(self.hu, self.c.width_hu, self.c.level_hu)
            if self.kind == "native":
                # Rotate only the windowed display buffer, never source HU or
                # native voxel coordinates. rot90 is a view with negative
                # strides, so materialize a contiguous buffer before QImage.
                a = np.ascontiguousarray(np.rot90(a, -self.native_rotation_quarters()))
            self.qimage = QImage(a.data, a.shape[1], a.shape[0], a.strides[0], QImage.Format.Format_Grayscale8).copy()
        self.update()

    def native_rotation_quarters(self):
        return int(getattr(self.c, "native_rotation_quarters", 0)) % 4

    def _native_size(self):
        """Unrotated width/height, independent of displayed QImage dimensions."""
        if self.hu is not None:
            height, width = self.hu.shape
            return width, height
        # Compatibility with readiness-only tests that supply QImage directly.
        # Real medical display always reaches this widget via set_hu().
        if getattr(self.c, "case", None) is not None:
            shape = self.c.case.native.array_zyx.shape
            return shape[2], shape[1]
        if self.qimage is not None:
            width, height = self.qimage.width(), self.qimage.height()
            return (height, width) if self.native_rotation_quarters() % 2 else (width, height)
        raise ValueError(tr("Native image is not ready"))

    def native_index_to_screen(self, x, y):
        """Project a continuous native voxel-center index through display only.

        Indices outside the native raster are deliberately not clamped: an
        off-image centerline projection must not be moved onto an image edge.
        """
        if self.kind != "native" or self.qimage is None:
            raise ValueError(tr("Native image is not ready"))
        width, height = self._native_size()
        quarter = self.native_rotation_quarters()
        if quarter == 1:
            x, y = height - 1 - y, x
        elif quarter == 2:
            x, y = width - 1 - x, height - 1 - y
        elif quarter == 3:
            x, y = y, width - 1 - x
        display_width, display_height = (height, width) if quarter % 2 else (width, height)
        rectangle = self.image_rect()
        return QPointF(rectangle.left() + (x + .5) / display_width * rectangle.width(),
                       rectangle.top() + (y + .5) / display_height * rectangle.height())

    def native_screen_to_index(self, pos):
        """Map an on-image click to original continuous (x,y); reject outside."""
        if self.kind != "native" or self.qimage is None:
            return None
        rectangle = self.image_rect()
        if not rectangle.contains(pos):
            return None
        return self.native_screen_to_index_unbounded(pos)

    def native_screen_to_index_unbounded(self, pos):
        """Invert display geometry even outside the raster, for rotation pivots.

        This must not be used for picking anatomy: native_screen_to_index keeps
        the image-boundary guard. An off-image viewport centre still has a
        well-defined affine inverse, so rotation need not switch pivot rules
        when pan moves that centre across the edge of a rectangular image.
        """
        if self.kind != "native" or self.qimage is None:
            return None
        rectangle = self.image_rect()
        if rectangle.width() <= 0 or rectangle.height() <= 0:
            return None
        width, height = self._native_size()
        quarter = self.native_rotation_quarters()
        display_width, display_height = (height, width) if quarter % 2 else (width, height)
        x = (pos.x() - rectangle.left()) / rectangle.width() * display_width - .5
        y = (pos.y() - rectangle.top()) / rectangle.height() * display_height - .5
        if quarter == 1:
            x, y = y, height - 1 - x
        elif quarter == 2:
            x, y = width - 1 - x, height - 1 - y
        elif quarter == 3:
            x, y = width - 1 - y, x
        return float(x), float(y)

    def native_orientation_labels(self):
        """Move the actual affine-derived labels with the displayed raster."""
        labels = dict(self.c.case.native.orientation_labels())
        for _ in range(self.native_rotation_quarters()):
            labels = {"left": labels["bottom"], "top": labels["left"],
                      "right": labels["top"], "bottom": labels["right"]}
        return labels

    def path_spacing_uv(self):
        spacing=getattr(self.c.path,"spacing_uv",None)
        if spacing is None:
            spacing=(self.c.path.spacing_mm,self.c.path.spacing_mm)
        u,v=map(float,spacing)
        if not np.isfinite([u,v]).all() or min(u,v)<=0:
            raise ValueError(tr("Invalid cross-section pixel spacing."))
        return u,v

    def image_rect(self):
        if self.qimage is None:
            return QRectF(0, 0, self.width(), self.height())
        iw, ih = self.qimage.width(), self.qimage.height()
        aspect_x,aspect_y=1.0,1.0
        if self.kind=="native" and self.c.case is not None:
            spacing=self.c.case.native.image.GetSpacing();aspect_x,aspect_y=spacing[0],spacing[1]
            if self.native_rotation_quarters() % 2:
                aspect_x,aspect_y=aspect_y,aspect_x
        if self.kind == "cross" and self.c.path is not None:
            aspect_x,aspect_y=self.path_spacing_uv()
        if self.kind == "cpr":
            # Physical square pixels; show a useful local length, never stretch anatomy.
            base = min(max(1, self.width() - 76) / iw, 3.0)
        else:
            base = min(max(1, self.width() - 28) / (iw*aspect_x), max(1, self.height() - 28) / (ih*aspect_y))
        scale = base * self.zoom
        w, h = iw * scale * aspect_x, ih * scale * aspect_y
        return QRectF((self.width() - w) / 2 + self.pan.x(), (self.height() - h) / 2 + self.pan.y(), w, h)

    def screen_s(self, s):
        p = self.c.path
        rect = self.image_rect()
        if p is None:
            return 0.0
        index = float(np.interp(s, p.distances, np.arange(len(p.distances))))
        return rect.top() + (index + 0.5) * rect.height() / len(p.distances)

    def s_at(self, y):
        p = self.c.path
        if p is None:
            return 0.0
        r = self.image_rect()
        index = (y - r.top()) / max(r.height(), 1) * len(p.distances) - 0.5
        return float(np.interp(index, np.arange(len(p.distances)), p.distances))

    def mm_per_pixel(self):
        return max(1e-9, self.c.path.length_mm / max(self.image_rect().height(), 1)) if self.c.path else 1

    def center_observation(self, reset_zoom=False):
        if reset_zoom:
            self.zoom = 1.0
        self.pan = QPointF(0, 0)
        if self.kind == "cpr" and self.c.path is not None:
            self.pan.setY(self.height() / 2 - self.screen_s(self.c.s))
        elif self.kind == "native" and self.c.case is not None:
            self._center_native_anchor()
        self.update()
        self.c.schedule_view_save()

    def _center_native_anchor(self):
        if self.qimage is None or self.c.path is None:
            return
        idx = self.c.case.native.world_to_index(self.c.path.point(self.c.s))
        point = self.native_index_to_screen(idx[0], idx[1])
        self.pan += QPointF(self.width() / 2 - point.x(), self.height() / 2 - point.y())

    def ensure_observed(self):
        if self.kind != "cpr" or self.c.path is None or self.gesture:
            return
        y = self.screen_s(self.c.s)
        if y < .12 * self.height() or y > .88 * self.height():
            self.pan.setY(self.pan.y() + self.height() / 2 - y)
            self.update()

    def zoom_at(self, factor, pos):
        old = self.image_rect()
        self.zoom = min(12.0, max(.15, self.zoom * factor))
        new = self.image_rect()
        fx = (pos.x() - old.left()) / max(old.width(), 1)
        fy = (pos.y() - old.top()) / max(old.height(), 1)
        self.pan += QPointF(pos.x() - new.left() - fx * new.width(), pos.y() - new.top() - fy * new.height())
        self.update()
        self.c.schedule_view_save()

    def _hit(self, pos):
        if self.kind != "cpr" or self.c.path is None:
            return None
        r = self.image_rect()
        x, y = pos.x(), pos.y()
        ya, yb = self.screen_s(self.c.a), self.screen_s(self.c.b)
        if r.left() - 10 <= x <= r.right() + 10:
            if abs(y - ya) <= 9:
                return ("start", None)
            if abs(y - yb) <= 9:
                return ("end", None)
            if min(ya, yb) <= y <= max(ya, yb) and min(abs(x-r.left()), abs(x-r.right())) <= 9:
                return ("body", None)
        for m in self.c.path_markers():
            if abs(x - (r.left() - 15)) < 12 and abs(y - self.screen_s(m["s_mm"])) < 8:
                return ("marker", m["marker_id"])
        if r.left() <= x <= r.right():
            s = self.s_at(y)
            for rec in reversed(self.c.path_records()):
                if rec["s_start_mm"] <= s < rec["s_end_mm"]:
                    return ("record", rec["annotation_id"])
        return ("observe", None)

    def paint_footer(self, painter, text):
        flags=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap
        bounds=painter.boundingRect(QRectF(10,0,max(1,self.width()-20),100),flags,str(text))
        rectangle=QRectF(10,max(0,self.height()-bounds.height()-5),max(1,self.width()-20),bounds.height())
        painter.fillRect(rectangle.adjusted(-2,-1,2,1),QColor(12,17,23,220))
        painter.drawText(rectangle,flags,str(text))
        return rectangle.top()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0c1117"))
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.qimage is None:
            p.setPen(MUTED)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, tr('Select a case to display images'))
            return
        r = self.image_rect()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(r, self.qimage)
        if self.kind == "cpr" and self.c.path is not None:
            if not self.c.hide_overlays:
                for rec in self.c.path_records():
                    col = annotation_color(rec)
                    col.setAlphaF(.12)
                    y0, y1 = self.screen_s(rec["s_start_mm"]), self.screen_s(rec["s_end_mm"])
                    p.fillRect(QRectF(r.left(), y0, r.width(), y1-y0), col)
                    if effective_reader_review_required(rec):
                        p.setPen(QColor("#d6bd8a"))
                        p.drawText(QPointF(r.right() + 6, (y0+y1)/2), tr('Review'))
            for mark in self.c.path_markers():
                y = self.screen_s(mark["s_mm"])
                col = QColor("#ffe3a5") if self.c.selected_marker == mark["marker_id"] else QColor("#b3bbc5")
                p.setPen(QPen(col, 1, Qt.PenStyle.DotLine))
                p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
                p.setBrush(col)
                p.drawPolygon(QPolygonF([QPointF(r.left()-21,y),QPointF(r.left()-15,y-5),QPointF(r.left()-9,y),QPointF(r.left()-15,y+5)]))
            ya, yb = self.screen_s(self.c.a), self.screen_s(self.c.b)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(ORANGE, 1.7))
            p.drawRect(QRectF(r.left(), ya, r.width(), yb-ya))
            for y, caption in [(ya, tr('Start {p0:.2f}' ,p0=self.c.a)), (yb, tr('End {p0:.2f}' ,p0=self.c.b))]:
                p.setPen(QPen(ORANGE, 4))
                p.drawLine(QPointF(r.center().x()-14,y),QPointF(r.center().x()+14,y))
                p.setPen(ORANGE)
                p.drawText(QPointF(r.right()+7,y+4),caption)
            ys = self.screen_s(self.c.s)
            if getattr(self.c, "reference_lines_visible", True):
                p.setPen(QPen(TEAL, 1.4))
                p.drawLine(QPointF(r.left()-5,ys),QPointF(r.right()+5,ys))
                p.setBrush(TEAL)
                p.drawEllipse(QPointF(r.left()-5,ys),4,4)
            if not 0 <= ys <= self.height():
                p.setPen(MUTED)
                p.drawText(12, 22, tr('Observation outside view · Scroll to follow'))
            p.setPen(MUTED)
            self.paint_footer(p, f"{self.c.path_id}  ·  {self.c.s:.2f} mm  ·  {self.zoom:.2f}×")
        elif self.kind == "cross":
            cx, cy = r.center().x(), r.center().y()
            if self.c.path:
                cy += self.c.offset_mm / (self.path_spacing_uv()[1]*self.qimage.height()) * r.height()
            if getattr(self.c, "reference_lines_visible", True):
                p.setPen(QPen(TEAL, 1, Qt.PenStyle.DashLine))
                p.drawLine(QPointF(r.left(),cy),QPointF(r.right(),cy))
                p.drawLine(QPointF(cx,r.top()),QPointF(cx,r.bottom()))
            p.setPen(MUTED)
            self.paint_footer(p,tr("Orthogonal · s {position:.2f} mm · {width:g} × {height:g} mm",position=self.c.s,width=self.qimage.width()*self.path_spacing_uv()[0],height=self.qimage.height()*self.path_spacing_uv()[1]))
        elif self.kind == "native" and self.c.case:
            orientation=self.native_orientation_labels()
            p.setPen(MUTED)
            p.drawText(5,self.height()//2,orientation["left"])
            p.drawText(self.width()-25,self.height()//2,orientation["right"])
            p.drawText(self.width()//2,14,orientation["top"])
            idx = self.c.case.native.world_to_index(self.c.path.point(self.c.s))
            point = self.native_index_to_screen(idx[0],idx[1])
            x,y=point.x(),point.y()
            if getattr(self.c, "reference_lines_visible", True):
                p.setPen(QPen(TEAL,1.3,Qt.PenStyle.DashLine if abs(self.c.native_z-idx[2]) > .6 else Qt.PenStyle.SolidLine))
                p.drawLine(QPointF(x-10,y),QPointF(x+10,y))
                p.drawLine(QPointF(x,y-10),QPointF(x,y+10))
            p.setPen(MUTED)
            delta = (self.c.native_z-idx[2])*self.c.case.native.image.GetSpacing()[2]
            caption_top=self.paint_footer(p,tr('Native axial z={p0} · Δ {p1:+.2f} mm · Dashed = off-slice projection' ,p0=self.c.native_z,p1=delta))
            orientation_x=self.width()//2
            orientation_y=int(caption_top)-3
            if abs(orientation_x-x)<16 and abs(orientation_y-y)<18:
                orientation_x+=22
            p.drawText(orientation_x,orientation_y,orientation["bottom"])
        p.setPen(MUTED)
        p.drawText(10,24 if self.kind=="native" else 38,self.c.window_text())
        p.end()

    def mousePressEvent(self, event):
        self.setFocus()
        if self.c.path is None:
            return
        pos = event.position()
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        trackpad_pan = is_macos() and bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        button = event.button()
        if ctrl and button == Qt.MouseButton.MiddleButton:
            self.center_observation(reset_zoom=True)
            self.gesture = {"mode":"consumed"}
            event.accept()
            return
        if button == Qt.MouseButton.RightButton:
            mode = "rotate" if ctrl and self.kind != "native" else ("ignored" if ctrl else "pan")
            hit = None
        elif button == Qt.MouseButton.MiddleButton:
            mode, hit = "window", None
        elif button == Qt.MouseButton.LeftButton and trackpad_pan:
            mode = "rotate" if ctrl and self.kind != "native" else ("ignored" if ctrl else "pan")
            hit = None
        elif button == Qt.MouseButton.LeftButton and ctrl:
            mode, hit = ("offset" if self.kind != "native" else "ignored"), None
        elif button == Qt.MouseButton.LeftButton:
            hit = self._hit(pos)
            mode = hit[0] if hit else "observe"
        else:
            return
        self.gesture = {"mode":mode,"pos":QPointF(pos),"last":QPointF(pos),"pan":QPointF(self.pan),"a":self.c.a,"b":self.c.b,"angle":self.c.angle_deg,"offset":self.c.offset_mm,"width":self.c.width_hu,"level":self.c.level_hu,"hit":hit,"moved":False,"draft":self.c.draft_snapshot()}
        self.held_snap = None
        self.grabMouse()
        if mode in ("start","end","body"):
            self._edge_timer.start()
        event.accept()

    def mouseMoveEvent(self, event):
        if not self.gesture:
            modifiers = event.modifiers()
            if is_macos() and modifiers & Qt.KeyboardModifier.ShiftModifier:
                command = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
                self.setCursor(Qt.CursorShape.SizeHorCursor if command else Qt.CursorShape.SizeAllCursor)
                self.c.hint((tr('⌘+Shift+left-drag rotates; double-click with the same modifiers resets rotation') if self.kind != "native"
                             else tr('Native CT is unchanged; Shift+left-drag pans')) if command
                            else tr('Shift+left-drag pans; Shift+left-double-click centers'))
                return
            hit = self._hit(event.position())
            mode = hit[0] if hit else "observe"
            cursor = Qt.CursorShape.SizeVerCursor if mode in ("start","end") else Qt.CursorShape.SizeAllCursor if mode == "body" else Qt.CursorShape.PointingHandCursor if mode in ("record","marker") else Qt.CursorShape.CrossCursor
            self.setCursor(cursor)
            primary, alternate = ("⌘", "⌥") if is_macos() else ("Ctrl", "Alt")
            self.c.hint({"start":tr('Drag start · {p0} suspends snapping' ,p0=alternate),"end":tr('Drag end · {p0} suspends snapping' ,p0=alternate),"body":tr('Drag interval · Length stays fixed'),"record":tr('Click to edit a saved interval'),"marker":tr('Click a marker to navigate · Delete removes it')}.get(mode,tr('Scroll to review · Space adds a marker · {p0}+scroll zooms' ,p0=primary)))
            return
        g = self.gesture
        if g["mode"] == "consumed":
            return
        pos = event.position()
        g["last"] = QPointF(pos)
        dx, dy = pos.x()-g["pos"].x(),pos.y()-g["pos"].y()
        if math.hypot(dx,dy) < 4 and not g["moved"]:
            return
        g["moved"] = True
        mode = g["mode"]
        if mode == "pan":
            self.pan = g["pan"]+QPointF(dx,dy)
            self.update()
        elif mode == "rotate":
            self.c.set_sampling(angle=g["angle"]+dx*.4)
        elif mode == "offset":
            self.c.set_sampling(offset=g["offset"]+dx*.025)
        elif mode == "window":
            self.c.set_window(g["width"]+dx*3,g["level"]-dy*2)
        elif mode in ("start","end","body"):
            self._drag_range(pos, bool(event.modifiers() & Qt.KeyboardModifier.AltModifier))
        event.accept()

    def _drag_range(self, pos, disabled=False):
        g = self.gesture
        delta = self.s_at(pos.y()) - self.s_at(g["pos"].y())
        if g["mode"] == "body":
            self.c.drag_range(g["a"]+delta,g["b"]+delta,"body",self.mm_per_pixel(),disabled)
        else:
            val = self.s_at(pos.y())
            self.c.drag_range(val if g["mode"]=="start" else g["a"], val if g["mode"]=="end" else g["b"],g["mode"],self.mm_per_pixel(),disabled)

    def _edge_scroll(self):
        g = self.gesture
        if not g or not g.get("moved") or self.kind != "cpr":
            return
        y = g["last"].y()
        shift = 5 if y < 18 else -5 if y > self.height()-18 else 0
        if shift:
            # Move the image and the initial pointer consistently, preserving physical drag delta.
            self.pan.setY(self.pan.y()+shift)
            g["pos"].setY(g["pos"].y()+shift)
            self._drag_range(g["last"], bool(self.c.app.keyboardModifiers() & Qt.KeyboardModifier.AltModifier))
            self.update()

    def mouseReleaseEvent(self, event):
        g, self.gesture = self.gesture, None
        self._edge_timer.stop()
        self.releaseMouse()
        if not g or g["mode"] == "consumed":
            return
        if not g["moved"]:
            mode = g["mode"]
            if mode == "record":
                self.c.select_annotation(g["hit"][1])
            elif mode == "marker":
                self.c.select_marker(g["hit"][1])
            elif mode == "observe" and self.kind == "cpr":
                self.c.observe(self.s_at(event.position().y()))
            elif mode == "observe" and self.kind == "native":
                self.c.native_pick(event.position(),self)
        elif g["mode"] in ("start","end","body"):
            self.c.finish_draft_gesture(g["draft"])
        self.c.clear_snap()
        self.c.schedule_view_save()
        event.accept()

    def mouseDoubleClickEvent(self, event):
        self.cancel_gesture()
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        trackpad_pan = is_macos() and bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if event.button() == Qt.MouseButton.RightButton:
            if ctrl and self.kind != "native": self.c.set_sampling(angle=0)
            elif not ctrl: self.center_observation()
        elif event.button() == Qt.MouseButton.LeftButton and trackpad_pan:
            if ctrl and self.kind != "native": self.c.set_sampling(angle=0)
            elif not ctrl: self.center_observation()
        elif event.button() == Qt.MouseButton.LeftButton and ctrl and self.kind != "native":
            self.c.set_sampling(offset=0)
        elif event.button() == Qt.MouseButton.MiddleButton:
            if ctrl: self.center_observation(reset_zoom=True)
            else: self.c.set_window(700,250)
        event.accept()

    def cancel_gesture(self):
        g, self.gesture = self.gesture, None
        self._edge_timer.stop()
        self.releaseMouse()
        if g and "pan" in g:
            self.pan = g["pan"]
            self.c.restore_draft(g["draft"])
            self.c.set_sampling(angle=g["angle"],offset=g["offset"])
            self.c.set_window(g["width"],g["level"])
            self.c.clear_snap()
            self.update()

    def wheelEvent(self, event):
        if self.c.path is None:
            return
        phase = event.phase()
        path_identity = id(self.c.path)
        if phase == Qt.ScrollPhase.ScrollBegin or self._native_wheel_path != path_identity:
            self._native_wheel_remainder = 0.0
            self._native_wheel_path = path_identity
        delta = _wheel_delta(event)
        if not delta:
            if phase == Qt.ScrollPhase.ScrollEnd:
                self._native_wheel_remainder = 0.0
            event.accept()
            return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._native_wheel_remainder = 0.0
            self.zoom_at(1.15**delta,event.position())
        elif self.kind == "native":
            if not event.pixelDelta().y() and delta.is_integer():
                # A conventional mouse notch must never be delayed by a prior
                # fractional trackpad remainder, particularly after reversal.
                steps = int(delta)
                self._native_wheel_remainder = 0.0
            else:
                self._native_wheel_remainder += delta
                steps = int(math.copysign(math.floor(abs(self._native_wheel_remainder) + 1e-9),
                                          self._native_wheel_remainder))
                self._native_wheel_remainder -= steps
            if steps:
                self.c.step_native(steps)
        else:
            self.c.observe(self.c.s+delta*.5)
        if phase == Qt.ScrollPhase.ScrollEnd:
            self._native_wheel_remainder = 0.0
        event.accept()

    def keyPressEvent(self, event):
        if self.c.handle_view_key(event):
            event.accept()
        else:
            super().keyPressEvent(event)


class IntervalTrack(QWidget):
    """Separate hit lanes for bookmarks, orange interval, observation, labels."""

    def __init__(self, controller):
        super().__init__()
        self.c = controller
        self.setFixedHeight(116)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.gesture = None
        self.setObjectName("interval_track")

    def x_s(self,s):
        return 22 + s/max(self.c.path.length_mm if self.c.path else 1,1e-6)*max(self.width()-44,1)

    def s_x(self,x):
        return min(self.c.path.length_mm,max(0,(x-22)/max(self.width()-44,1)*self.c.path.length_mm)) if self.c.path else 0

    def paintEvent(self,event):
        p=QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(),QColor("#151f29"))
        if self.c.path is None:
            p.setPen(MUTED); p.drawText(18,28,tr('Position along path / annotated intervals'))
            return
        p.setPen(MUTED)
        p.drawText(20,17,tr('Observation {p0:.2f} mm    Selection {p1:.2f}–{p2:.2f} mm    Length {p3:.2f} mm' ,p0=self.c.s,p1=self.c.a,p2=self.c.b,p3=self.c.b - self.c.a))
        p.setPen(QPen(QColor("#53616f"),2));p.drawLine(QPointF(22,51),QPointF(self.width()-22,51))
        for m in self.c.path_markers():
            x=self.x_s(m["s_mm"])
            col=QColor("#ffe3a5") if m["marker_id"]==self.c.selected_marker else MUTED
            p.setPen(QPen(col,1));p.drawLine(QPointF(x,24),QPointF(x,57))
            p.setBrush(col);p.drawPolygon(QPolygonF([QPointF(x-4,23),QPointF(x+4,23),QPointF(x,29)]))
        p.setPen(QPen(ORANGE,4));p.drawLine(QPointF(self.x_s(self.c.a),36),QPointF(self.x_s(self.c.b),36))
        p.setBrush(ORANGE)
        for v in [self.c.a,self.c.b]:p.drawEllipse(QPointF(self.x_s(v),36),4,4)
        p.setBrush(TEAL);p.setPen(Qt.PenStyle.NoPen);p.drawEllipse(QPointF(self.x_s(self.c.s),51),5,5)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for rec in self.c.path_records():
            x0,x1=self.x_s(rec["s_start_mm"]),self.x_s(rec["s_end_mm"])
            p.fillRect(QRectF(x0,64,max(1,x1-x0),11),annotation_color(rec))
            if effective_reader_review_required(rec):
                p.setPen(QPen(QColor("#ffdea6"),1,Qt.PenStyle.DotLine));p.drawRect(QRectF(x0,64,max(1,x1-x0),11))
        for issue in self.c.coverage_issues_for_path():
            x0,x1=self.x_s(issue["s_start_mm"]),self.x_s(issue["s_end_mm"])
            rectangle=QRectF(x0,78,max(4,x1-x0),8)
            color=QColor("#d68786" if issue["kind"]=="gap" else "#cfb47b")
            p.fillRect(rectangle,QBrush(color,Qt.BrushStyle.BDiagPattern))
            p.setPen(QPen(color,1));p.drawRect(rectangle)
        p.setPen(MUTED);p.drawText(22,96,"0 mm");p.drawText(self.width()-80,96,f"{self.c.path.length_mm:.1f} mm")
        legend=self.c.snap_caption or tr('Red hatch: unannotated · Gold hatch: review · Click to navigate')
        if self.c.snap_caption:p.setPen(ORANGE)
        p.drawText(QRectF(85,87,max(1,self.width()-175),28),Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,legend)

    def issue_at(self,x):
        matches=[]
        for issue in self.c.coverage_issues_for_path():
            left,right=self.x_s(issue["s_start_mm"]),self.x_s(issue["s_end_mm"])
            if left-3<=x<=max(left+4,right)+3:matches.append(issue)
        return min(matches,key=lambda r:abs(x-self.x_s((r["s_start_mm"]+r["s_end_mm"])/2))) if matches else None

    def mousePressEvent(self,event):
        self.setFocus()
        if self.c.path is None or event.button()!=Qt.MouseButton.LeftButton:return
        x,y=event.position().x(),event.position().y()
        if 77<=y<=88:
            issue=self.issue_at(x)
            if issue:self.c.jump_to_coverage_issue(issue);return
        if 27<=y<=44:
            mode="start" if abs(x-self.x_s(self.c.a))<10 else "end" if abs(x-self.x_s(self.c.b))<10 else "body" if self.x_s(self.c.a)<x<self.x_s(self.c.b) else "observe"
        elif 18<=y<28:
            matches=[m for m in self.c.path_markers() if abs(self.x_s(m["s_mm"])-x)<7]
            if matches:self.c.select_marker(matches[0]["marker_id"]);return
            mode="observe"
        elif 62<=y<=80:
            matches=[r for r in self.c.path_records() if r["s_start_mm"]<=self.s_x(x)<r["s_end_mm"]]
            if matches:self.c.select_annotation(matches[-1]["annotation_id"]);return
            mode="observe"
        else:mode="observe"
        self.gesture={"mode":mode,"x":x,"a":self.c.a,"b":self.c.b,"draft":self.c.draft_snapshot(),"moved":False}
        if mode=="observe":self.c.observe(self.s_x(x))
        self.grabMouse()

    def mouseMoveEvent(self,event):
        g=self.gesture
        if not g:
            issue=self.issue_at(event.position().x()) if self.c.path and 77<=event.position().y()<=88 else None
            self.setCursor(Qt.CursorShape.PointingHandCursor if issue else Qt.CursorShape.ArrowCursor)
            self.setToolTip(tr("{issue}. Click to position the selection on this range.",issue=self.c.coverage_issue_text(issue)) if issue else tr('Red hatch indicates gaps and gold hatch indicates review. Click to navigate.'))
            return
        if abs(event.position().x()-g["x"])<3 and not g["moved"]:return
        g["moved"]=True
        val=self.s_x(event.position().x())
        mode=g["mode"]
        if mode=="observe":self.c.observe(val)
        else:
            delta=val-self.s_x(g["x"])
            a=val if mode=="start" else g["a"]+delta if mode=="body" else g["a"]
            b=val if mode=="end" else g["b"]+delta if mode=="body" else g["b"]
            self.c.drag_range(a,b,mode,self.c.path.length_mm/max(self.width()-44,1),bool(event.modifiers() & Qt.KeyboardModifier.AltModifier))

    def mouseReleaseEvent(self,event):
        g,self.gesture=self.gesture,None
        self.releaseMouse()
        if g and g["moved"] and g["mode"]!="observe":self.c.finish_draft_gesture(g["draft"])
        self.c.clear_snap();self.c.schedule_view_save()

    def cancel_gesture(self):
        g,self.gesture=self.gesture,None
        self.releaseMouse()
        if g:self.c.restore_draft(g["draft"])

    def keyPressEvent(self,event):
        if self.c.handle_view_key(event):event.accept()
        else:super().keyPressEvent(event)

    def wheelEvent(self,event):
        delta = _wheel_delta(event)
        if self.c.path is not None and delta:
            self.c.observe(self.c.s+delta*.5)
        event.accept()
