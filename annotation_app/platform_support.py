"""Small platform boundary; clinical schemas and coordinate logic stay shared.

Qt maps ControlModifier / the portable ``Ctrl`` key-sequence token to physical
Command on macOS.  Do not substitute MetaModifier for Command in event handlers.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


APP_DIRECTORY = "ImageCASXAnnotator"


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
    common = (
        "阅片与标注速查\n\n"
        "Z：三窗绿色参考线开 / 关\n输入框内 Z 仅输入\n"
        "CT 标题 90°：顺时针转向\n默认 180°；新血管沿用，重启保留\n\n"
        "滚轮 / 双指滚动：沿血管 / CT 翻层\n"
        "Space：在当前层放辅助标记\n橙框横边：拖起点 / 终点\n"
        "橙框竖边：移动整段\n"
        f"{alternate_modifier_label()} + 拖框：暂停吸附\n"
        "单击旧色块：修改该片段\n底条红色缺口：点击定位补标\n"
        "下一处：跨血管查漏 / 复核\n\n"
    )
    if is_macos():
        gestures = (
            "右拖 / ⇧ + 左拖：平移\n右双击：居中\n"
            "⌘ + 右拖 / ⌘⇧ + 左拖：CPR 旋转\n"
            "⌘ + 左拖：CPR 离轴\n⌘ + 滚动：缩放\n"
            "⌘ + 中键：缩放复位\n中拖：窗宽 / 窗位；双击复位\n"
            "无中键：点影像标题‘窗宽/位’\n"
            "居中、1×、旋转/离轴归零均可点击\n"
            "⌘ + 右双击：旋转复位\n⌘ + 左双击：离轴复位\n\n"
            "S / Enter：应用　Esc：取消\n输入框内 S 仅输入，不应用\n"
            "⌘Z / ⌘⇧Z：撤销 / 重做\n⌘S：保存　⌘Q：保存并退出\n"
            "Delete（⌫）：删除选中标记或片段\n仅在影像 / 区间条有焦点时生效\n"
        )
    else:
        gestures = (
            "右拖：平移　右双击：居中\nCtrl + 右拖：旋转；双击复位\n"
            "Ctrl + 左拖：离轴；双击复位\nCtrl + 滚轮：缩放\n"
            "Ctrl + 中键：缩放复位\n中拖：窗宽 / 窗位；双击复位\n\n"
            "S / Enter：应用　Esc：取消\n输入框内 S 仅输入，不应用\n"
            "Ctrl + Z / Y：撤销 / 重做\nDelete：删除选中标记或片段\n"
        )
    return common + gestures + "H：暂时隐藏色块（按住）"
