from __future__ import annotations

from PySide6.QtCore import (
    QEvent, QLibraryInfo, QObject, QLocale, QTranslator, Signal, Qt,
)
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QComboBox, QGroupBox, QLabel, QLineEdit,
    QMenu, QStatusBar, QTabWidget, QTableWidget, QTreeWidget, QWidget,
)

from app.i18n_catalog import (
    CATALOG_LANGUAGES, DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES,
    load_catalogs, reverse_index, translate_from_catalogs,
)


SOURCE_ROLE = int(Qt.ItemDataRole.UserRole) + 91
TARGET_ROLE = SOURCE_ROLE + 1


LANGUAGE_ALIASES = {
    "nb": "no",
    "nn": "no",
}
LANGUAGE_LOCALES = {
    "en": "en_US",
    "fr": "fr_FR",
    "de": "de_DE",
    "es": "es_ES",
    "it": "it_IT",
    "pt": "pt_PT",
    "nl": "nl_NL",
    "pl": "pl_PL",
    "ru": "ru_RU",
    "uk": "uk_UA",
    "cs": "cs_CZ",
    "ro": "ro_RO",
    "sv": "sv_SE",
    "no": "nb_NO",
    "tr": "tr_TR",
    "ja": "ja_JP",
    "ko": "ko_KR",
    "zh": "zh_CN",
}
QT_TRANSLATOR_CODES = {
    "fr": "fr",
    "de": "de",
    "es": "es",
    "it": "it",
    "pt": "pt_BR",
    "nl": "nl",
    "pl": "pl",
    "ru": "ru",
    "uk": "uk",
    "cs": "cs",
    "ro": "ro",
    "sv": "sv",
    "no": "nb",
    "tr": "tr",
    "ja": "ja",
    "ko": "ko",
    "zh": "zh_CN",
}


class TranslationManager(QObject):
    language_changed = Signal(str)

    def __init__(self, app: QApplication, requested: str = "auto"):
        super().__init__(app)
        self.app = app
        self.catalogs = load_catalogs()
        self.string_reverse = reverse_index(self.catalogs, "strings")
        self.fragment_reverse = reverse_index(self.catalogs, "fragments")
        self.qt_translator = QTranslator(self)
        self.qt_translator_installed = False
        self.requested = "auto"
        self.effective = DEFAULT_LANGUAGE
        self._busy = False
        app.installEventFilter(self)
        self.set_language(requested, announce=False)

    @staticmethod
    def system_language() -> str:
        for locale_name in QLocale.system().uiLanguages():
            language = locale_name.lower().replace("_", "-").split("-", 1)[0]
            language = LANGUAGE_ALIASES.get(language, language)
            if language in CATALOG_LANGUAGES:
                return language
        return DEFAULT_LANGUAGE

    def set_language(self, requested: str, announce: bool = True) -> None:
        requested = requested if requested in SUPPORTED_LANGUAGES else "auto"
        effective = self.system_language() if requested == "auto" else requested
        if effective not in CATALOG_LANGUAGES:
            effective = DEFAULT_LANGUAGE
        changed = (requested, effective) != (self.requested, self.effective)
        self.requested, self.effective = requested, effective
        locale_name = LANGUAGE_LOCALES.get(effective, LANGUAGE_LOCALES[DEFAULT_LANGUAGE])
        QLocale.setDefault(QLocale(locale_name))
        self._set_qt_translation()
        if changed or not announce:
            self.retranslate_all()
        if announce and changed:
            self.language_changed.emit(effective)

    def _set_qt_translation(self) -> None:
        if self.qt_translator_installed:
            self.app.removeTranslator(self.qt_translator)
            self.qt_translator_installed = False
        if self.effective == DEFAULT_LANGUAGE:
            return
        path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        qt_code = QT_TRANSLATOR_CODES.get(self.effective, self.effective)
        if self.qt_translator.load(f"qtbase_{qt_code}", path):
            self.qt_translator_installed = self.app.installTranslator(
                self.qt_translator
            )

    def canonical(self, value: object, fragments: bool = False) -> str:
        text = str(value or "")
        if text in self.catalogs[DEFAULT_LANGUAGE]["strings"]:
            return text
        key = self.string_reverse.get(text)
        if key:
            return key
        if fragments and text in self.catalogs[DEFAULT_LANGUAGE]["fragments"]:
            return text
        return self.fragment_reverse.get(text, text) if fragments else text

    def translate(self, text: object, fragments: bool = True) -> str:
        return translate_from_catalogs(
            text,
            self.effective,
            self.catalogs,
            self.string_reverse,
            self.fragment_reverse,
            fragments,
        )

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if self._busy:
            return False
        if event.type() == QEvent.Type.Show:
            # Every child receives its own Show event.  Recursively translating
            # the complete subtree here made a tab change rescan the same
            # widgets and tables dozens of times.  Translate each object only
            # on its first Show; language changes still use retranslate_all().
            if not watched.property("_rl_i18n_first_show_done"):
                watched.setProperty("_rl_i18n_first_show_done", True)
                top_level = isinstance(watched, QWidget) and watched.isWindow()
                if top_level:
                    for child in watched.findChildren(QWidget):
                        if child.isVisible():
                            child.setProperty("_rl_i18n_first_show_done", True)
                self.translate_object(watched, recurse=top_level)
        elif (
            event.type() in {QEvent.Type.LayoutRequest, QEvent.Type.UpdateRequest}
            and isinstance(
                watched, (QAbstractButton, QGroupBox, QLabel, QStatusBar)
            )
            and watched.isVisible()
        ):
            # Keep dynamic text translated without rescanning terminals, trees
            # or entire tables on every repaint and layout pass.
            self.translate_object(watched, recurse=False)
        return False

    def retranslate_all(self) -> None:
        for widget in self.app.topLevelWidgets():
            self.translate_object(widget, recurse=True)

    def translate_object(self, obj: QObject, recurse: bool = False) -> None:
        if self._busy or obj is None:
            return
        self._busy = True
        try:
            self._translate_one(obj)
            if recurse and isinstance(obj, QWidget):
                for child in obj.findChildren(QObject):
                    self._translate_one(child)
        finally:
            self._busy = False

    def _property(self, obj: QObject, name: str, value: str, setter) -> None:
        source_name, target_name = f"_rl_source_{name}", f"_rl_target_{name}"
        source, previous = obj.property(source_name), obj.property(target_name)
        if source is None or value != previous:
            source = self.canonical(value)
            obj.setProperty(source_name, source)
        target = self.translate(source)
        obj.setProperty(target_name, target)
        if value != target:
            setter(target)

    def _translate_one(self, obj: QObject) -> None:
        if isinstance(obj, QWidget):
            self._property(obj, "title", obj.windowTitle(), obj.setWindowTitle)
            self._property(obj, "tooltip", obj.toolTip(), obj.setToolTip)
            self._property(obj, "status_tip", obj.statusTip(), obj.setStatusTip)
        if isinstance(obj, (QAbstractButton, QLabel)):
            self._property(obj, "text", obj.text(), obj.setText)
        if isinstance(obj, QGroupBox):
            self._property(obj, "group_title", obj.title(), obj.setTitle)
        if isinstance(obj, QLineEdit):
            self._property(
                obj, "placeholder", obj.placeholderText(), obj.setPlaceholderText
            )
        if isinstance(obj, QAction):
            self._property(obj, "action_text", obj.text(), obj.setText)
            self._property(obj, "action_tooltip", obj.toolTip(), obj.setToolTip)
        if isinstance(obj, QMenu):
            self._property(obj, "menu_title", obj.title(), obj.setTitle)
            for action in obj.actions():
                self._translate_one(action)
        if isinstance(obj, QTabWidget):
            self._translate_tabs(obj)
        if isinstance(obj, QComboBox):
            self._translate_combo(obj)
        if isinstance(obj, QTableWidget):
            self._translate_table(obj)
        if isinstance(obj, QTreeWidget):
            self._translate_tree(obj)
        if isinstance(obj, QStatusBar):
            current = obj.currentMessage()
            target = self.translate(current)
            if current != target:
                obj.showMessage(target)

    def _translate_tabs(self, tabs: QTabWidget) -> None:
        if tabs.property("rl_i18n_skip_tabs"):
            return
        state = getattr(tabs, "_rl_i18n_tabs", {})
        for index in range(tabs.count()):
            current = tabs.tabText(index)
            source, previous = state.get(index, (self.canonical(current), None))
            if current != previous:
                source = self.canonical(current)
            target = self.translate(source)
            state[index] = (source, target)
            if current != target:
                tabs.setTabText(index, target)
        tabs._rl_i18n_tabs = state

    def _translate_combo(self, combo: QComboBox) -> None:
        state = getattr(combo, "_rl_i18n_items", {})
        native_language_names = bool(combo.property("rl_i18n_native_language_names"))
        for index in range(combo.count()):
            if combo.itemData(index) is None:
                continue
            if native_language_names and combo.itemData(index) != "auto":
                continue
            current = combo.itemText(index)
            source, previous = state.get(index, (self.canonical(current), None))
            if current != previous:
                source = self.canonical(current)
            target = self.translate(source)
            state[index] = (source, target)
            if current != target:
                combo.setItemText(index, target)
        combo._rl_i18n_items = state

    def _translate_item(self, item) -> None:
        if item is None:
            return
        current = item.text()
        source, previous = item.data(SOURCE_ROLE), item.data(TARGET_ROLE)
        if source is None or current != previous:
            source = self.canonical(current)
            item.setData(SOURCE_ROLE, source)
        target = self.translate(source, fragments=False)
        item.setData(TARGET_ROLE, target)
        if current != target:
            item.setText(target)

    def _translate_table(self, table: QTableWidget) -> None:
        for column in range(table.columnCount()):
            self._translate_item(table.horizontalHeaderItem(column))
        for row in range(table.rowCount()):
            for column in range(table.columnCount()):
                item = table.item(row, column)
                if item is not None and item.data(SOURCE_ROLE) is not None:
                    self._translate_item(item)

    def _translate_tree(self, tree: QTreeWidget) -> None:
        header = tree.headerItem()
        for column in range(tree.columnCount()):
            self._translate_item_column(header, column)

    def _translate_item_column(self, item, column: int) -> None:
        current = item.text(column)
        source = item.data(column, SOURCE_ROLE)
        previous = item.data(column, TARGET_ROLE)
        if source is None or current != previous:
            source = self.canonical(current)
            item.setData(column, SOURCE_ROLE, source)
        target = self.translate(source, fragments=False)
        item.setData(column, TARGET_ROLE, target)
        if current != target:
            item.setText(column, target)


_manager: TranslationManager | None = None


def configure(app: QApplication, requested: str = "auto") -> TranslationManager:
    global _manager
    _manager = TranslationManager(app, requested)
    app._remote_linux_i18n = _manager
    return _manager


def manager() -> TranslationManager | None:
    return _manager


def tr(text: object, fragments: bool = True) -> str:
    if _manager:
        return _manager.translate(text, fragments)
    catalogs = load_catalogs()
    return translate_from_catalogs(
        text,
        DEFAULT_LANGUAGE,
        catalogs,
        reverse_index(catalogs, "strings"),
        reverse_index(catalogs, "fragments"),
        fragments,
    )


def set_language(requested: str) -> None:
    if _manager:
        _manager.set_language(requested)


def ntr(count: int, singular: str, plural: str) -> str:
    return tr(singular if int(count) == 1 else plural)


def byte_units() -> tuple[str, str, str, str, str]:
    if not _manager or _manager.effective == DEFAULT_LANGUAGE:
        return "B", "KB", "MB", "GB", "TB"
    return "o", "Ko", "Mo", "Go", "To"
