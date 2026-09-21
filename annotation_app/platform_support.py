"""Small platform boundary; clinical schemas and coordinate logic stay shared.

Qt maps ControlModifier / the portable ``Ctrl`` key-sequence token to physical
Command on macOS.  Do not substitute MetaModifier for Command in event handlers.
"""
from __future__ import annotations

from .i18n import tr

import os
import sys
from pathlib import Path


APP_DIRECTORY = "CoronaryAnnotationStudio"


def is_macos():
    return sys.platform == "darwin"


def user_data_dir():
    if is_macos():
        return Path.home() / "Library" / "Application Support" / APP_DIRECTORY
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / APP_DIRECTORY


def user_log_dir(db_path=None):
    # Tests and --db workspaces must not touch the doctor's default directory.
    if db_path is not None:
        return Path(db_path).resolve().parent / "logs"
    if is_macos():
        return Path.home() / "Library" / "Logs" / APP_DIRECTORY
    return user_data_dir()


def primary_modifier_label():
    return "⌘" if is_macos() else "Ctrl"


def alternate_modifier_label():
    return "⌥" if is_macos() else "Alt"


def font_family_css():
    return "'PingFang SC','Heiti SC','.AppleSystemUIFont'" if is_macos() else "'Microsoft YaHei UI','Segoe UI'"


def shortcut_help():
    return tr('Review and annotation shortcuts\n\nZ: toggle green reference lines\nZ only types inside text fields\nCT 90° button: rotate clockwise\nInitial 0°; retained across paths and restarts\n\nWheel / two-finger scroll: path / CT slices\nSpace: place a marker\nSelection horizontal edges: drag start / end\nSelection vertical edges: move the interval\n{p0} + drag: suspend snapping\nClick saved overlays: edit intervals\nClick red gaps: navigate\nNext issue: gaps / reviews across paths\n\nRight-drag / ⇧+left-drag: pan\nRight-double-click: center\n⌘+right-drag / ⌘⇧+left-drag: CPR rotation\n⌘+left-drag: CPR offset\n⌘+scroll: zoom\n⌘+middle-click: reset zoom\nMiddle-drag: window / level; double-click resets\nWithout middle button: use the W/L button\nCenter, 1× and sampling resets are also buttons\n⌘+right-double-click: reset rotation\n⌘+left-double-click: reset offset\n\nS / Enter: apply · Esc: cancel\nS only types inside text fields\n⌘Z / ⌘⇧Z: undo / redo\n⌘S: save · ⌘Q: save and quit\nDelete (⌫): remove selected marker / interval\nRequires focus in an image or timeline\nHold H: temporarily hide overlays' if is_macos() else 'Review and annotation shortcuts\n\nZ: toggle green reference lines\nZ only types inside text fields\nCT 90° button: rotate clockwise\nInitial 0°; retained across paths and restarts\n\nWheel / two-finger scroll: path / CT slices\nSpace: place a marker\nSelection horizontal edges: drag start / end\nSelection vertical edges: move the interval\n{p0} + drag: suspend snapping\nClick saved overlays: edit intervals\nClick red gaps: navigate\nNext issue: gaps / reviews across paths\n\nRight-drag: pan · Right-double-click: center\nCtrl+right-drag: rotate; double-click resets\nCtrl+left-drag: offset; double-click resets\nCtrl+wheel: zoom\nCtrl+middle-click: reset zoom\nMiddle-drag: window / level; double-click resets\n\nS / Enter: apply · Esc: cancel\nS only types inside text fields\nCtrl+Z / Y: undo / redo\nDelete: remove selected marker / interval\nHold H: temporarily hide overlays',p0=alternate_modifier_label())
