"""Dark theme stylesheet."""

STYLESHEET = """
QWidget {
    background: #12141a;
    color: #e6e9ef;
    font-size: 13px;
}
QMainWindow, QDialog { background: #12141a; }
QGroupBox {
    border: 1px solid #262b36;
    border-radius: 8px;
    margin-top: 14px;
    padding: 10px 10px 8px 10px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: #9aa3b5;
}
QPushButton {
    background: #232936;
    border: 1px solid #333b4c;
    border-radius: 6px;
    padding: 6px 12px;
}
QPushButton:hover { background: #2c3444; }
QPushButton:pressed { background: #1c222d; }
QPushButton:disabled { color: #5b6172; background: #1a1e27; }
QPushButton#primary {
    background: #2563eb;
    border: 1px solid #3b82f6;
    font-weight: 600;
}
QPushButton#primary:hover { background: #3b82f6; }
QPushButton#primary:disabled { background: #23324d; color: #7b86a0; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: #1a1e27;
    border: 1px solid #2e3542;
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: #2563eb;
}
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView {
    background: #1a1e27;
    border: 1px solid #2e3542;
    selection-background-color: #2563eb;
}
QTableWidget {
    background: #161a22;
    alternate-background-color: #191e28;
    gridline-color: #232936;
    border: 1px solid #262b36;
    border-radius: 6px;
}
QHeaderView::section {
    background: #1c2029;
    color: #9aa3b5;
    border: none;
    border-bottom: 1px solid #262b36;
    padding: 5px;
}
QTableWidget::item:selected { background: #24406e; }
QProgressBar {
    background: #1a1e27;
    border: 1px solid #2e3542;
    border-radius: 6px;
    height: 18px;
    text-align: center;
}
QProgressBar::chunk { background: #2563eb; border-radius: 5px; }
QSlider::groove:horizontal {
    height: 5px; background: #2a3140; border-radius: 3px;
}
QSlider::sub-page:horizontal { background: #3b82f6; border-radius: 3px; }
QSlider::handle:horizontal {
    background: #e6e9ef; width: 12px; margin: -5px 0; border-radius: 6px;
}
QLabel#hint { color: #8b93a3; font-size: 12px; }
QLabel#panelSummary { color: #9aa3b5; font-size: 12px; }
QLabel#timecode { font-family: monospace; color: #cfd6e4; }
QLabel#fileLabel { font-weight: 600; }
QStatusBar { color: #8b93a3; border-top: 1px solid #262b36; }
QMenuBar { background: #161a22; }
QMenuBar::item:selected { background: #2563eb; }
QMenu { background: #1a1e27; border: 1px solid #2e3542; }
QMenu::item:selected { background: #2563eb; }
QToolTip {
    background: #1a1e27; color: #e6e9ef;
    border: 1px solid #2e3542; padding: 4px;
}
QRadioButton, QCheckBox { spacing: 6px; }
QSplitter::handle { background: #1c2029; }
QTabWidget::pane {
    border: 1px solid #262b36;
    border-radius: 8px;
    top: -1px;
}
QTabBar::tab {
    background: #1a1e27;
    color: #9aa3b5;
    border: 1px solid #262b36;
    border-bottom: none;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
    padding: 8px 22px;
    margin-right: 3px;
    font-weight: 600;
}
QTabBar::tab:selected {
    background: #232936;
    color: #e6e9ef;
    border-bottom: 2px solid #3b82f6;
}
QTabBar::tab:hover:!selected { background: #1f2531; color: #cfd6e4; }
QListWidget {
    background: #161a22;
    alternate-background-color: #191e28;
    border: 1px solid #262b36;
    border-radius: 6px;
    outline: none;
}
QListWidget::item {
    border-radius: 6px;
    padding: 4px;
    color: #cfd6e4;
}
QListWidget::item:selected { background: #24406e; color: #ffffff; }
QListWidget::item:hover:!selected { background: #1d2431; }
"""
