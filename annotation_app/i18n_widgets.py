"""Display-only Qt adapters that retain translatable source messages."""
from PySide6 import QtWidgets, QtGui
from .i18n import Text, bind, display


class _DisplayProperties:
    _initial_property = "setText"

    def __init__(self, *args, **kwargs):
        source = args[0] if args and isinstance(args[0], Text) else None
        super().__init__(*args, **kwargs)
        if source is not None:
            getattr(self, self._initial_property)(source)

    def setText(self, value):
        super().setText(bind(self, "setText", value))

    def setToolTip(self, value):
        super().setToolTip(bind(self, "setToolTip", value))

    def setAccessibleName(self, value):
        super().setAccessibleName(bind(self, "setAccessibleName", value))

    def setAccessibleDescription(self, value):
        super().setAccessibleDescription(bind(self, "setAccessibleDescription", value))

    def setWindowTitle(self, value):
        super().setWindowTitle(bind(self, "setWindowTitle", value))

    def setInformativeText(self, value):
        super().setInformativeText(bind(self, "setInformativeText", value))

    def setTitle(self, value):
        super().setTitle(bind(self, "setTitle", value))


class QLabel(_DisplayProperties, QtWidgets.QLabel): pass
class QPushButton(_DisplayProperties, QtWidgets.QPushButton): pass
class QCheckBox(_DisplayProperties, QtWidgets.QCheckBox): pass
class QToolButton(_DisplayProperties, QtWidgets.QToolButton): pass
class QWidget(_DisplayProperties, QtWidgets.QWidget): pass
class QMainWindow(_DisplayProperties, QtWidgets.QMainWindow): pass
class QDialog(_DisplayProperties, QtWidgets.QDialog): pass
class QComboBox(_DisplayProperties, QtWidgets.QComboBox): pass
class QLineEdit(_DisplayProperties, QtWidgets.QLineEdit): pass
class QDoubleSpinBox(_DisplayProperties, QtWidgets.QDoubleSpinBox): pass
class QListWidget(_DisplayProperties, QtWidgets.QListWidget): pass
class QListWidgetItem(_DisplayProperties, QtWidgets.QListWidgetItem): pass
class QAction(_DisplayProperties, QtGui.QAction): pass


class QStatusBar(_DisplayProperties, QtWidgets.QStatusBar):
    def showMessage(self, message, timeout=0):
        # All application hints are persistent until superseded.
        super().showMessage(bind(self, "showMessage", message), timeout)


class QMenu(_DisplayProperties, QtWidgets.QMenu):
    _initial_property = "setTitle"

    def addAction(self, *args):
        if args and isinstance(args[0], Text):
            action = QAction(args[0], self)
            if len(args) > 1:
                action.triggered.connect(args[1])
            super().addAction(action)
            return action
        return super().addAction(*args)


class QMessageBox(_DisplayProperties, QtWidgets.QMessageBox):
    def addButton(self, button, role=None):
        if isinstance(button, Text):
            result = QPushButton(button, self)
            super().addButton(result, role)
            return result
        return super().addButton(button, role) if role is not None else super().addButton(button)

    @staticmethod
    def _message(parent, title, text, icon):
        box = QMessageBox(parent)
        box.setIcon(icon)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok)
        return box.exec()

    @staticmethod
    def information(parent, title, text):
        return QMessageBox._message(parent, title, text, QtWidgets.QMessageBox.Icon.Information)

    @staticmethod
    def warning(parent, title, text):
        return QMessageBox._message(parent, title, text, QtWidgets.QMessageBox.Icon.Warning)

    @staticmethod
    def critical(parent, title, text):
        return QMessageBox._message(parent, title, text, QtWidgets.QMessageBox.Icon.Critical)
