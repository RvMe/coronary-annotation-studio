"""Qt translation, explicit English source messages and persistent UI language.

Only display properties are refreshed. No model, database or annotation method
is called while installing a translator. Application data is never translated.
"""
from __future__ import annotations

import json
import os
import uuid
import weakref
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, QTranslator, QLibraryInfo, QSignalBlocker, Signal
from shiboken6 import isValid

SUPPORTED_LANGUAGES = ("en", "zh_CN")
_bindings = {}


def display(value):
    return value.render() if isinstance(value, Text) else str(value)


class Text(str):
    """A Qt-compatible string retaining its source and typed format parameters."""
    def __new__(cls, source, parameters=None):
        parameters = parameters or {}
        translated = QCoreApplication.translate("CAS", source)
        value = translated.format(**{k: display(v) if isinstance(v, Text) else v for k, v in parameters.items()}) if parameters else translated
        obj = str.__new__(cls, value)
        obj.source = source
        obj.parameters = parameters
        return obj

    def render(self):
        translated = QCoreApplication.translate("CAS", self.source)
        return translated.format(**{k: display(v) if isinstance(v, Text) else v for k, v in self.parameters.items()}) if self.parameters else translated

    def __deepcopy__(self, memo):
        from copy import deepcopy
        return Text(self.source, deepcopy(self.parameters, memo))


def tr(source, **parameters):
    return Text(source, parameters)


def error_message(error):
    value=error.args[0] if isinstance(error,Exception) and error.args else error
    if isinstance(value,Text):return value
    raw=str(value)
    resource=Path(__file__).with_name("translations")/"legacy_error_sources.json"
    sources=json.loads(resource.read_text(encoding="utf-8"))
    return tr(sources.get(raw,raw))


def bind(target, method, value):
    """Store only translated display values, with weak ownership of Qt objects."""
    key=id(target)
    entry=_bindings.get(key)
    if entry is None or entry[0]() is not target:
        reference=weakref.ref(target,lambda ref,k=key:_bindings.pop(k,None))
        entry=(reference,{})
        _bindings[key]=entry
    values=entry[1]
    if isinstance(value, Text):
        values[method] = value
    else:
        values.pop(method, None)
    return display(value)


def retranslate_bindings():
    # A blocker prevents textChanged/action-changed observers from interpreting
    # retranslation as a user edit. Widgets, list items and focus stay intact.
    for key, (reference, values) in list(_bindings.items()):
        target=reference()
        if target is None or not isValid(target):
            _bindings.pop(key, None)
            continue
        objects=[target] if isinstance(target,QObject) else []
        if hasattr(target,"listWidget") and target.listWidget() is not None:
            objects.extend([target.listWidget(),target.listWidget().model()])
        blockers=[QSignalBlocker(obj) for obj in objects]
        try:
            for method, value in list(values.items()):
                getattr(target, method)(value)
        finally:
            for blocker in blockers: blocker.unblock()


class LanguageManager(QObject):
    changed = Signal(str)

    def __init__(self, preference_path=None, parent=None):
        super().__init__(parent)
        self.preference_path = Path(preference_path) if preference_path else None
        self.language = "en"
        self.translator = None
        self.qt_translator = None
        self.preference_error = None
        if self.preference_path and self.preference_path.is_file():
            try:
                language = json.loads(self.preference_path.read_text(encoding="utf-8")).get("language", "en")
                if language in SUPPORTED_LANGUAGES:
                    self.set_language(language, persist=False)
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                self.preference_error = str(exc)

    def _save(self, language):
        if self.preference_path is None:
            return
        path = self.preference_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump({"schema_version": "cas-ui-preferences-1.0", "language": language}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def set_language(self, language, *, persist=True):
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError(tr("Unsupported language: {language}", language=language))
        if language == self.language:
            return False
        app = QCoreApplication.instance()
        if app is None:
            raise RuntimeError("A Qt application is required before changing language.")
        candidate = None
        qt_candidate = None
        if language == "zh_CN":
            candidate = QTranslator(self)
            path = Path(__file__).with_name("translations") / "cas_zh_CN.qm"
            if not candidate.load(str(path)):
                candidate.deleteLater()
                raise RuntimeError(tr("Could not load the language resource: {path}", path=str(path)))
            qt_candidate = QTranslator(self)
            qt_candidate.load("qtbase_zh_CN", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))
        # Persist first: a write failure leaves the currently visible language
        # and all user state unchanged.
        try:
            if persist:
                self._save(language)
        except Exception:
            if candidate is not None: candidate.deleteLater()
            if qt_candidate is not None: qt_candidate.deleteLater()
            raise
        for old in (self.translator, self.qt_translator):
            if old is not None:
                app.removeTranslator(old)
                old.deleteLater()
        self.translator, self.qt_translator = candidate, qt_candidate
        for new in (qt_candidate, candidate):
            if new is not None:
                app.installTranslator(new)
        self.language = language
        retranslate_bindings()
        self.changed.emit(language)
        return True


def language_manager(preference_path=None):
    app = QCoreApplication.instance()
    if app is None:
        raise RuntimeError("A Qt application is required.")
    manager = getattr(app, "_cas_language_manager", None)
    if manager is None:
        manager = LanguageManager(preference_path, app)
        app._cas_language_manager = manager
    return manager
