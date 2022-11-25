# -*- coding: utf-8 -*-
#
# Copyright © Spyder Project Contributors
# Licensed under the terms of the MIT License
# (see spyder/__init__.py for details)

"""
Package table widget.

This is the main widget used in the Spyder env Manager plugin
"""

# Standard library imports
import os
import os.path as osp

# Third library imports
from qtpy import PYQT5
from qtpy.compat import getopenfilenames, getsavefilename, to_qvariant
from qtpy.QtCore import Qt, Signal, Slot, QAbstractTableModel, QModelIndex, QPoint
from qtpy.QtGui import QCursor, QContextMenuEvent, QMouseEvent, QColor
from qtpy.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QInputDialog,
    QMessageBox,
    QVBoxLayout,
    QWidget,
    QTableView,
    QAbstractItemView,
    QLabel,
    QMenu,
)
from spyder_kernels.utils.iofuncs import iofunctions
from spyder_kernels.utils.misc import fix_reference_name
from spyder_kernels.utils.nsview import REMOTE_SETTINGS
from spyder.utils.qthelpers import (
    add_actions,
    create_action,
    MENU_SEPARATOR,
    mimedata2url,
)
from spyder.config.fonts import DEFAULT_LARGE_DELTA, DEFAULT_SMALL_DELTA
from spyder.config.gui import get_font

# Local imports
from spyder.api.translations import get_translation
from spyder.api.widgets.mixins import SpyderWidgetMixin
from spyder.widgets.collectionseditor import RemoteCollectionsEditorTableView
from spyder.plugins.variableexplorer.widgets.importwizard import ImportWizard
from spyder.utils import encoding
from spyder.utils.misc import getcwd_or_home, remove_backslashes
from spyder.widgets.helperwidgets import FinderLineEdit, ItemDelegate
from spyder.utils.palette import SpyderPalette

# Localization
_ = get_translation("spyder")

# Constants
VALID_VARIABLE_CHARS = r"[^\w+*=¡!¿?'\"#$%&()/<>\-\[\]{}^`´;,|¬]*\w"


PACKAGE, DESCRIPTION, VERSION = [0, 1, 2]


class EnvironmentPackagesModel(QAbstractTableModel):
    def __init__(self, parent, text_color=None, text_color_highlight=None):
        QAbstractTableModel.__init__(self)
        self._parent = parent

        self.packages = []
        self.server_map = {}
        # self.scores = []
        self.rich_text = []
        self.normal_text = []
        self.letters = ""
        self.label = QLabel()
        self.widths = []

        # Needed to compensate for the HTMLDelegate color selection unawareness
        palette = parent.palette()
        if text_color is None:
            self.text_color = palette.text().color().name()
        else:
            self.text_color = text_color

        if text_color_highlight is None:
            self.text_color_highlight = palette.highlightedText().color().name()
        else:
            self.text_color_highlight = text_color_highlight

    def sortByName(self):
        """Qt Override."""
        self.packages = sorted(self.packages, key=lambda x: x.language)
        self.reset()

    def flags(self, index):
        """Qt Override."""
        if not index.isValid():
            return Qt.ItemIsEnabled
        return Qt.ItemFlags(QAbstractTableModel.flags(self, index))

    def data(self, index, role=Qt.DisplayRole):
        """Qt Override."""
        row = index.row()
        if not index.isValid() or not (0 <= row < len(self.packages)):
            return to_qvariant()

        package = self.packages[row]
        column = index.column()

        if role == Qt.DisplayRole:
            if column == PACKAGE:
                text = package["package"]
                return to_qvariant(text)
            elif column == DESCRIPTION:
                text = package["description"]
                return to_qvariant(text)
            elif column == VERSION:
                text = package["version"]
                return to_qvariant(text)
        elif role == Qt.TextAlignmentRole:
            return to_qvariant(int(Qt.AlignCenter))
        elif role == Qt.FontRole:
            return to_qvariant(get_font(font_size_delta=DEFAULT_SMALL_DELTA))
        elif role == Qt.BackgroundColorRole:
            if package["dependence"]:
                return to_qvariant(QColor(SpyderPalette.GROUP_1))  # )

        # elif role == Qt.Role
        return to_qvariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        """Qt Override."""
        if role == Qt.TextAlignmentRole:
            if orientation == Qt.Horizontal:
                return to_qvariant(int(Qt.AlignHCenter | Qt.AlignVCenter))
            return to_qvariant(int(Qt.AlignRight | Qt.AlignVCenter))
        if role != Qt.DisplayRole:
            return to_qvariant()
        if orientation == Qt.Horizontal:
            if section == PACKAGE:
                return to_qvariant(_("Package"))
            elif section == DESCRIPTION:
                return to_qvariant(_("Description"))
            elif section == VERSION:
                return to_qvariant(_("Version"))
        return to_qvariant()

    def rowCount(self, index=QModelIndex()):
        """Qt Override."""
        return len(self.packages)

    def columnCount(self, index=QModelIndex()):
        """Qt Override."""
        return 3

    def row(self, row_num):
        """Get row based on model index. Needed for the custom proxy model."""
        return self.packages[row_num]

    def reset(self):
        """ "Reset model to take into account new search letters."""
        self.beginResetModel()
        self.endResetModel()


class EnvironmentPackagesTable(QTableView):

    sig_update_package = Signal()
    sig_uninstall_package = Signal()
    sig_change_package_version = Signal()

    def __init__(self, parent, text_color=None):
        QTableView.__init__(self, parent)
        self.menu = None
        self.menu_actions = []
        self.empty_ws_menu = None
        self.update_action = None
        self.uninstall_action = None
        self.change_action = None
        self._parent = parent
        self.delete_queue = []
        self.source_model = EnvironmentPackagesModel(self, text_color=text_color)
        self.setModel(self.source_model)
        # self.setItemDelegateForColumn(PACKAGE, ItemDelegate(self))
        # self.setItemDelegateForColumn(DESCRIPTION, ItemDelegate(self))
        # self.setItemDelegateForColumn(VERSION, ItemDelegate(self))
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setSortingEnabled(True)
        self.setEditTriggers(QAbstractItemView.AllEditTriggers)
        # self.selectionModel().selectionChanged.connect(self.selection)
        self.verticalHeader().hide()
        self.load_packages(False)

    def contextMenuEvent(self, event):
        """Setup context menu"""
        col = self.columnAt(event.pos().x())
        row = self.rowAt(event.pos().y())
        packages = self.source_model.packages
        if not packages[row]["dependence"]:
            self.update_action = create_action(
                self, _("Update package(s)"), triggered=self.selection
            )
            self.uninstall_action = create_action(
                self, _("Uninstall package(s)"), triggered=self.selection
            )
            self.change_action = create_action(
                self,
                _("Change package version with a version constraint"),
                triggered=self.selection,
            )
            menu = QMenu(self)
            self.menu_actions = [
                self.update_action,
                self.uninstall_action,
                self.change_action,
            ]
            add_actions(menu, self.menu_actions)
            menu.setMinimumWidth(100)
            menu.popup(event.globalPos())
            event.accept()

    def focusOutEvent(self, e):
        """Qt Override."""
        # self.source_model.update_active_row()
        # self._parent.delete_btn.setEnabled(False)
        super(EnvironmentPackagesTable, self).focusOutEvent(e)

    def focusInEvent(self, e):
        """Qt Override."""
        super(EnvironmentPackagesTable, self).focusInEvent(e)
        self.selectRow(self.currentIndex().row())

    def selection(self, index):
        """Update selected row."""
        print("Context menu")
        # self.update()
        # self.isActiveWindow()
        self._parent.delete_btn.setEnabled(True)

    def adjust_cells(self):
        """Adjust column size based on contents."""
        self.resizeColumnsToContents()
        fm = self.horizontalHeader().fontMetrics()
        names = [fm.width(s["description"]) for s in self.source_model.packages]
        if names:
            self.setColumnWidth(DESCRIPTION, max(names))
        self.horizontalHeader().setStretchLastSection(True)

    def get_server_by_lang(self, lang):
        return self.source_model.server_map.get(lang)

    def load_packages(self, option):
        packages = [
            {
                "package": "aa",
                "description": "Fragmento de un escrito con unidad temática, que queda diferenciado del resto de fragmentos ",
                "version": "2.3.5",
                "dependence": False,
            },
            {
                "package": "bb",
                "description": "Fragmento de un escrito con unidad temática, diferenciado del resto de fragmentos ",
                "version": "2.5",
                "dependence": False,
            },
            {
                "package": "cc",
                "description": "Fragmento de un escrito con unidad temática, ",
                "version": "2",
                "dependence": True,
            },
        ]
        if option:
            packages = list(filter(lambda x: not x["dependence"], packages))
        # packages=packagesExample[1:3]
        for i, package in enumerate(packages):
            package["index"] = i

        package_map = {x["package"]: x for x in packages}
        self.source_model.packages = packages
        self.source_model.server_map = package_map
        self.source_model.reset()
        self.adjust_cells()
        self.sortByColumn(PACKAGE, Qt.AscendingOrder)

    def save_packages(self):
        language_set = set({})
        for package in self.source_model.packages:
            language_set |= {server.language.lower()}
            package.save()
        while len(self.delete_queue) > 0:
            server = self.delete_queue.pop(0)
            language_set |= {server.language.lower()}
            server.delete()
        return language_set

    def delete_server(self, idx):
        server = self.source_model.servers.pop(idx)
        self.delete_queue.append(server)
        self.source_model.server_map.pop(server.language)
        self.source_model.reset()
        self.adjust_cells()
        self.sortByColumn(PACKAGE, Qt.AscendingOrder)

    def delete_server_by_lang(self, language):
        idx = next(
            (
                i
                for i, x in enumerate(self.source_model.servers)
                if x.language == language
            ),
            None,
        )
        if idx is not None:
            self.delete_server(idx)

    def show_editor(self, new_server=False):
        pass

    def next_row(self):
        """Move to next row from currently selected row."""
        row = self.currentIndex().row()
        rows = self.source_model.rowCount()
        if row + 1 == rows:
            row = -1
        self.selectRow(row + 1)

    def previous_row(self):
        """Move to previous row from currently selected row."""
        row = self.currentIndex().row()
        rows = self.source_model.rowCount()
        if row == 0:
            row = rows
        self.selectRow(row - 1)

    def keyPressEvent(self, event):
        """Qt Override."""
        key = event.key()
        if key in [Qt.Key_Enter, Qt.Key_Return]:
            self.show_editor()
        elif key in [Qt.Key_Backtab]:
            self.parent().reset_btn.setFocus()
        elif key in [Qt.Key_Up, Qt.Key_Down, Qt.Key_Left, Qt.Key_Right]:
            super(EnvironmentPackagesTable, self).keyPressEvent(event)
        else:
            super(EnvironmentPackagesTable, self).keyPressEvent(event)

    def mouseDoubleClickEvent(self, event):
        """Qt Override."""
        self.show_editor()
