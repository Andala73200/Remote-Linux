DARK_STYLE = """
QWidget { background: #17191d; color: #f0f0f0; font-size: 10pt; }
QMainWindow, QDialog { background: #111317; }
QLineEdit, QSpinBox, QComboBox, QTextEdit, QPlainTextEdit, QListWidget, QTreeWidget, QTableWidget {
    background: #0d0f12; border: 1px solid #343840; border-radius: 4px; padding: 4px;
    selection-background-color: #365b87;
}
QPushButton, QToolButton { background: #2a2e35; border: 1px solid #40454e; border-radius: 4px; padding: 6px 10px; }
QPushButton:hover, QToolButton:hover { background: #353a43; }
QPushButton:pressed, QToolButton:pressed { background: #20242a; }
QPushButton:disabled, QToolButton:disabled { color: #777; background: #202226; }
QTabWidget::pane { border: 1px solid #30343b; }
QTabBar::tab { background: #202329; padding: 8px 14px; border: 1px solid #30343b; }
QTabBar::tab:selected { background: #323841; }
QHeaderView::section { background: #252930; padding: 6px; border: 0; border-right: 1px solid #3b4048; }
QGroupBox { border: 1px solid #343840; border-radius: 5px; margin-top: 12px; padding-top: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #cdd6e0; }
QMenu { background: #17191d; border: 1px solid #3b4048; }
QMenu::item { padding: 6px 24px 6px 10px; }
QMenu::item:selected { background: #365b87; }
QStatusBar { background: #101216; }
QToolTip { background: #2b3038; color: white; border: 1px solid #555; }
QSplitter::handle { background: #30343b; }
"""

# Visual additions V1.4
DARK_STYLE += """
QMenuBar { background:#111317; border-bottom:1px solid #30343b; padding:2px; }
QMenuBar::item { padding:5px 10px; background:transparent; }
QMenuBar::item:selected { background:#323841; border-radius:3px; }
QFrame#storageCard { background:#1d2026; border:1px solid #3a4049; border-radius:8px; }
QFrame#storageCard:hover { border:1px solid #587496; background:#20242b; }
QProgressBar { color:white; }
QScrollArea { border:0; }
"""

DARK_STYLE += """
QFrame#transferRow { background:#1d2229; border:1px solid #39424d; border-radius:6px; }
QFrame#transferRow QLabel { background:transparent; }
QProgressBar { background:#0e1115; border:1px solid #39424d; border-radius:4px; text-align:center; min-height:16px; }
QProgressBar::chunk { background:#3f8edb; border-radius:3px; }
"""
