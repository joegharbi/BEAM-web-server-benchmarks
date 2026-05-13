"""
Benchmark Graph Generator — PyQt5 GUI for plotting CSV benchmark results.
Extensible: add categories via CATEGORY_PATH_PARTS and CATEGORY_PREFIXES.
"""
import os
import csv
import sys
import re
from io import BytesIO
from datetime import datetime
import numpy as np
import matplotlib
matplotlib.use("Qt5Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT
import mplcursors

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QListWidget, QListWidgetItem,
    QFileDialog, QMessageBox, QGroupBox, QShortcut, QStyledItemDelegate,
    QStyleFactory, QFrame, QSplitter, QSizePolicy, QMenu, QToolButton,
    QCheckBox, QDoubleSpinBox, QTreeWidget, QTreeWidgetItem, QTabWidget,
    QAbstractSpinBox,
    QProgressDialog, QHeaderView,
    QScrollArea,
    QLineEdit,
)
from PyQt5.QtCore import Qt, QPoint, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QKeySequence, QColor, QPalette

# --- Extensible category detection ---
# Path segment (lowercase) -> display name. Add new benchmark types here.
CATEGORY_PATH_PARTS = {"websocket": "WebSocket", "static": "Static", "dynamic": "Dynamic", "local": "Local", "grpc": "gRPC"}
# Filename prefix -> display name
CATEGORY_PREFIXES = {"ws-": "WebSocket", "st-": "Static", "dy-": "Dynamic", "grpc-": "gRPC"}
FONT_FAMILY = "Sans Serif"
FONT_SIZE = 11
CONTROL_HEIGHT = 26

# Visual design system
BORDER = "1px solid #d7def5"
BORDER_RADIUS = "10px"
BG_APP = "#f4f7ff"
BG_PANEL = "#ffffff"
BG_SOFT = "#eef4ff"
BG_HOVER = "#e0ebff"
BG_PRESSED = "#d2e2ff"
BG_DISABLED = "#e9eef8"
TEXT = "#1f2a44"
TEXT_MUTED = "#5f6f94"
TEXT_DISABLED = "#9aa8c7"
BORDER_HOVER = "#8cb2ff"
SELECTION_BG = "#dbe9ff"
SELECTION_TEXT = "#16315f"
ACCENT = "#3b82f6"
ACCENT_HOVER = "#2563eb"
ACCENT_PRESSED = "#1d4ed8"
ACCENT_ALT = "#7c3aed"
SUCCESS = "#059669"
WARNING = "#f59e0b"


def _make_light_palette():
    p = QPalette()
    p.setColor(QPalette.Window, QColor(BG_APP))
    p.setColor(QPalette.WindowText, QColor(TEXT))
    p.setColor(QPalette.Base, QColor(BG_PANEL))
    p.setColor(QPalette.AlternateBase, QColor(BG_SOFT))
    p.setColor(QPalette.Text, QColor(TEXT))
    p.setColor(QPalette.Button, QColor(BG_PANEL))
    p.setColor(QPalette.ButtonText, QColor(TEXT))
    p.setColor(QPalette.Highlight, QColor(SELECTION_BG))
    p.setColor(QPalette.HighlightedText, QColor(SELECTION_TEXT))
    return p


def _app_stylesheet():
    """Modern colorful desktop styling for the benchmark studio."""
    f = FONT_SIZE
    h = CONTROL_HEIGHT
    return f"""
        QWidget {{ font-family: "{FONT_FAMILY}"; font-size: {f}pt; background-color: {BG_APP}; color: {TEXT}; }}
        QLabel {{ font-size: {f}pt; color: {TEXT}; background: transparent; }}

        QPushButton, QToolButton {{
            font-size: {f}pt;
            background-color: {BG_PANEL};
            color: {TEXT};
            border: {BORDER};
            border-radius: {BORDER_RADIUS};
            padding: 4px 12px;
            min-height: {h}px;
            max-height: {h}px;
            font-weight: 600;
        }}
        QPushButton:hover, QToolButton:hover {{
            background-color: {BG_HOVER};
            border: 1px solid {BORDER_HOVER};
        }}
        QPushButton:pressed, QToolButton:pressed {{
            background-color: {BG_PRESSED};
            border: 1px solid {BORDER_HOVER};
        }}
        QPushButton:disabled, QToolButton:disabled {{
            background-color: {BG_DISABLED};
            color: {TEXT_DISABLED};
            border: 1px solid #dee2e6;
        }}
        QPushButton[role="primary"] {{
            background-color: {ACCENT};
            color: white;
            border: 1px solid {ACCENT};
        }}
        QPushButton[role="primary"]:hover {{
            background-color: {ACCENT_HOVER};
            border: 1px solid {ACCENT_HOVER};
        }}
        QPushButton[role="primary"]:pressed {{
            background-color: {ACCENT_PRESSED};
            border: 1px solid {ACCENT_PRESSED};
        }}
        QPushButton[role="secondary"] {{
            background-color: {ACCENT_ALT};
            color: white;
            border: 1px solid {ACCENT_ALT};
        }}
        QPushButton[role="secondary"]:hover {{
            background-color: #6d28d9;
            border: 1px solid #6d28d9;
        }}
        QPushButton[role="ghost"] {{
            background-color: transparent;
            color: {TEXT_MUTED};
            border: 1px solid #d9e2f5;
        }}
        QPushButton[role="ghost"]:hover {{
            background-color: #f8fbff;
            color: {TEXT};
            border: 1px solid #b8c8eb;
        }}
        QPushButton[role="success"] {{
            background-color: {SUCCESS};
            color: white;
            border: 1px solid {SUCCESS};
        }}
        QPushButton[role="success"]:hover {{
            background-color: #047857;
            border: 1px solid #047857;
        }}
        QPushButton[role="success"]:pressed {{
            background-color: #065f46;
            border: 1px solid #065f46;
        }}
        QPushButton[role="batch"] {{
            background-color: #1d4ed8;
            color: white;
            border: 1px solid #1d4ed8;
        }}
        QPushButton[role="batch"]:hover {{
            background-color: #1e40af;
            border: 1px solid #1e40af;
        }}
        QPushButton[role="batch"]:pressed {{
            background-color: #1e3a8a;
            border: 1px solid #1e3a8a;
        }}
        QPushButton[role="primary"]:disabled,
        QPushButton[role="secondary"]:disabled,
        QPushButton[role="success"]:disabled,
        QPushButton[role="batch"]:disabled,
        QPushButton[role="ghost"]:disabled {{
            background-color: {BG_DISABLED};
            color: {TEXT_DISABLED};
            border: 1px solid #dee2e6;
        }}
        QToolButton {{ text-align: left; }}
        QToolButton::menu-indicator {{ width: 14px; border: none; }}
        QToolButton[spinStep="true"] {{
            text-align: center;
            padding: 0px;
            min-width: 20px;
            max-width: 20px;
            color: {TEXT_MUTED};
            background-color: #f8fbff;
            border: none;
            border-left: 1px solid #d4def4;
            font-size: 8pt;
            font-weight: 700;
        }}
        QToolButton[spinStep="true"][spinPos="up"] {{
            border-top-right-radius: 10px;
            border-bottom: 1px solid #d4def4;
        }}
        QToolButton[spinStep="true"][spinPos="down"] {{
            border-bottom-right-radius: 10px;
        }}
        QToolButton[spinStep="true"]:hover {{
            background-color: {BG_HOVER};
            color: {TEXT};
        }}

        QMenu {{
            font-size: {f}pt;
            background-color: {BG_PANEL};
            border: 1px solid #d8e2f8;
            border-radius: {BORDER_RADIUS};
            padding: 6px 0;
        }}
        QMenu::item {{ padding: 6px 16px; color: {TEXT}; }}
        QMenu::item:selected {{ background-color: {SELECTION_BG}; color: {SELECTION_TEXT}; }}

        QGroupBox {{
            font-size: {f}pt;
            font-weight: 600;
            border: 1px solid #dbe4f8;
            border-radius: 14px;
            margin-top: 10px;
            padding: 12px;
            padding-top: 18px;
            background-color: {BG_PANEL};
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 12px;
            padding: 0 6px;
            color: {ACCENT_ALT};
            background-color: {BG_PANEL};
        }}

        QComboBox {{
            font-size: {f}pt;
            min-height: {h}px;
            max-height: {h}px;
            padding: 4px 12px;
            background-color: {BG_PANEL};
            color: {TEXT};
            border: 1px solid #d4def4;
            border-radius: 10px;
        }}
        QComboBox::drop-down {{ width: 18px; border: none; }}
        QComboBox QAbstractItemView {{
            font-size: {f}pt;
            padding: 4px 8px;
            background-color: {BG_PANEL};
            color: {TEXT};
            selection-background-color: {SELECTION_BG};
            selection-color: {SELECTION_TEXT};
            border: 1px solid #d4def4;
        }}

        QListWidget, QTreeWidget {{
            font-size: {f}pt;
            background-color: {BG_PANEL};
            color: {TEXT};
            border: 1px solid #d4def4;
            border-radius: 12px;
            outline: none;
        }}
        QListWidget::item, QTreeWidget::item {{
            padding: 4px 8px;
            border: none;
            outline: none;
        }}
        QListWidget::item:selected, QTreeWidget::item:selected {{
            background-color: {SELECTION_BG};
            color: {SELECTION_TEXT};
            padding: 4px 8px;
            border: none;
            outline: none;
        }}
        QHeaderView::section {{
            background-color: {BG_SOFT};
            color: {TEXT_MUTED};
            border: none;
            border-bottom: 1px solid #d4def4;
            padding: 8px;
            font-weight: 600;
        }}

        QSplitter::handle {{
            width: 6px;
            background: #dde8ff;
            border: none;
        }}
        QSplitter::handle:hover {{
            background: #bfd6ff;
        }}

        QPushButton#sidebarToggleBtn {{
            min-width: 38px;
            max-width: 38px;
            padding: 4px;
            font-size: 18px;
            line-height: 1;
        }}

        QScrollArea {{ border: none; background: transparent; }}
        QTabWidget::pane {{
            border: 1px solid #dbe4f8;
            border-radius: 16px;
            background-color: {BG_PANEL};
            margin-top: 8px;
        }}
        QTabBar::tab {{
            background: transparent;
            color: {TEXT_MUTED};
            border: none;
            padding: 10px 18px;
            margin-right: 10px;
            font-size: {f + 1}pt;
            font-weight: 700;
            min-width: 120px;
        }}
        QTabBar::tab:selected {{
            color: {ACCENT};
            border-bottom: 3px solid {ACCENT};
        }}
        QTabBar::tab:hover {{
            color: {TEXT};
        }}
        QCheckBox {{
            spacing: 6px;
            color: {TEXT};
        }}
        QCheckBox::indicator {{
            width: 16px;
            height: 16px;
            border-radius: 4px;
            border: 1px solid #b8c8eb;
            background: {BG_PANEL};
        }}
        QCheckBox::indicator:checked {{
            background: {ACCENT};
            border: 1px solid {ACCENT};
        }}
        QDoubleSpinBox {{
            min-height: {h}px;
            max-height: {h}px;
            padding: 4px 10px;
            background-color: {BG_PANEL};
            border: 1px solid #d4def4;
            border-radius: 10px;
        }}

    """


class _ComboDelegate(QStyledItemDelegate):
    """Use the same selection colors as the rest of the UI."""
    def paint(self, painter, option, index):
        option.palette.setColor(QPalette.HighlightedText, QColor(SELECTION_TEXT))
        option.palette.setColor(QPalette.Highlight, QColor(SELECTION_BG))
        super().paint(painter, option, index)


# Left margin (px) reserved for checkbox; clicks here let the default handler toggle. Clicks on the row text toggle programmatically.
_CHECKBOX_MARGIN = 28


class CheckableFileListWidget(QListWidget):
    """List where clicking the row (text) toggles the checkbox; Ctrl+click toggles that row only. Checkbox area uses default behavior."""
    def mousePressEvent(self, event):
        pos = event.pos()
        item = self.itemAt(pos)
        if item and (item.flags() & Qt.ItemIsUserCheckable):
            rect = self.visualItemRect(item)
            # Click on text part (right of checkbox area) -> toggle check ourselves so it works like clicking the box
            if pos.x() - rect.x() >= _CHECKBOX_MARGIN:
                state = Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked
                item.setCheckState(state)
        super().mousePressEvent(event)


class MenuSelectorWidget(QWidget):
    """
    Compact selector: button + QMenu. No arrow chrome; selection only on click.
    Slim height, flat style for a modern look.
    """
    option_chosen = pyqtSignal(str)

    def __init__(self, placeholder="—", parent=None):
        super().__init__(parent)
        self._placeholder = placeholder
        self._options = []
        self._menu = QMenu(self)
        self._button = QToolButton(self)
        self._button.setPopupMode(QToolButton.InstantPopup)
        self._button.setMenu(self._menu)
        self._button.setText(placeholder)
        self._button.setMinimumHeight(CONTROL_HEIGHT)
        self._button.setMaximumHeight(CONTROL_HEIGHT)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._button)

    def set_options(self, options):
        """Set the list of options; rebuilds the menu. Keeps placeholder if current text is the placeholder."""
        self._options = list(options) if options else []
        self._menu.clear()
        for opt in self._options:
            action = self._menu.addAction(opt)
            action.triggered.connect(lambda checked, o=opt: self._on_picked(o))
        current = self._button.text()
        if self._options and current not in self._options and current != self._placeholder:
            self._button.setText(self._options[0])

    def _on_picked(self, text):
        self._button.setText(text)
        self.option_chosen.emit(text)

    def set_current(self, text):
        if text == self._placeholder or text in self._options:
            self._button.setText(text)

    def currentText(self):
        return self._button.text()


class ArrowDoubleSpinBox(QDoubleSpinBox):
    """QDoubleSpinBox with explicit arrow buttons instead of native square steppers."""

    _BUTTON_WIDTH = 20

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.setMinimumWidth(108)
        self._up_button = self._make_step_button("▴", "up", self.stepUp)
        self._down_button = self._make_step_button("▾", "down", self.stepDown)
        if self.lineEdit() is not None:
            self.lineEdit().setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.lineEdit().setTextMargins(0, 0, self._BUTTON_WIDTH + 10, 0)

    def _make_step_button(self, text, pos, slot):
        button = QToolButton(self)
        button.setText(text)
        button.setAutoRepeat(True)
        button.setFocusPolicy(Qt.NoFocus)
        button.setCursor(Qt.ArrowCursor)
        button.setProperty("spinStep", "true")
        button.setProperty("spinPos", pos)
        button.clicked.connect(slot)
        button.raise_()
        return button

    def resizeEvent(self, event):
        super().resizeEvent(event)
        frame = 1
        x = self.width() - self._BUTTON_WIDTH - frame
        height = max(0, self.height() - (frame * 2))
        up_height = height // 2
        down_height = height - up_height
        self._up_button.setGeometry(x, frame, self._BUTTON_WIDTH, up_height)
        self._down_button.setGeometry(x, frame + up_height, self._BUTTON_WIDTH, down_height)
        self._up_button.raise_()
        self._down_button.raise_()


# --- Helper Functions ---
def safe_float(val, default=0.0):
    """Convert value to float; return default on failure (avoids GUI crash on bad CSV data)."""
    if val is None or val == '' or (isinstance(val, str) and val.strip().upper() in ('', 'NAN', 'N/A', '-', '--')):
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

def detect_csv_type(header):
    if "Test Type" in header or "Total Messages" in header:
        return "websocket"
    if "Type" in header and "Total Requests" in header:
        return "http"
    return "unknown"

# HTTP CSV column from measure_docker.py (static/dynamic); WebSocket CSVs omit this.
HTTP_MAX_WORKERS_CSV_COL = "HTTP Max Workers"


def _normalize_http_max_workers_display(value):
    """Unify legacy/alternate tokens with the canonical title-case label."""
    s = (value or "").strip()
    key = s.lower().replace(" ", "_")
    if key == "system_default" or s.lower() == "system default":
        return "System default"
    return s


def http_max_workers_plot_suffix(selected_files, file_types, headers_map, rows_map):
    """Short title suffix when plotting HTTP CSVs that record client ThreadPoolExecutor size."""
    vals = []
    for f in selected_files:
        if file_types.get(f) != "http":
            continue
        header = headers_map.get(f) or []
        if HTTP_MAX_WORKERS_CSV_COL not in header:
            continue
        for r in rows_map.get(f) or []:
            v = r.get(HTTP_MAX_WORKERS_CSV_COL)
            if v is not None and str(v).strip():
                vals.append(_normalize_http_max_workers_display(str(v).strip()))
    if not vals:
        return ""
    uniq = sorted(set(vals), key=lambda x: (x != "System default", x.lower()))
    if len(uniq) == 1:
        return f" \u2014 HTTP max workers: {uniq[0]}"
    return f" \u2014 HTTP max workers: mixed ({'; '.join(uniq)})"

# WebSocket CSV: test subtypes and x-axis column names (from measure_websocket.py + run_benchmarks.sh)
WS_SUBTYPE_CONCURRENCY = "concurrency"
WS_SUBTYPE_PAYLOAD = "payload"
WS_SUBTYPE_BURST = "burst"
WS_SUBTYPE_STREAM = "stream"
WS_XAXIS_COLUMNS = ["Num Clients", "Message Size (KB)", "Rate (msg/s)", "Bursts", "Duration (s)", "Interval (s)"]
WS_TYPE_ALL = "All"
WS_TYPE_CONCURRENCY = "Concurrency"
WS_TYPE_PAYLOAD = "Payload"
WS_TYPE_BURST = "Burst"
WS_TYPE_STREAM = "Stream"
WS_TYPE_OPTIONS = [WS_TYPE_ALL, WS_TYPE_CONCURRENCY, WS_TYPE_PAYLOAD, WS_TYPE_BURST, WS_TYPE_STREAM]
WS_PLOT_HEATMAP = "Heatmap"
WS_PLOT_MULTILINE = "Multi-line"
WS_PLOT_BAR = "Bar"
WS_PLOT_STYLE_OPTIONS = [WS_PLOT_MULTILINE, WS_PLOT_HEATMAP, WS_PLOT_BAR]
BENCHMARK_TYPE_PLACEHOLDER = "Benchmark type"
METRIC_PLACEHOLDER = "Metric"
PLOT_TYPE_PLACEHOLDER = "Plot type"
HOME_PLOT_TYPE_OPTIONS = list(WS_PLOT_STYLE_OPTIONS)
WS_TYPE_PLACEHOLDER = "WebSocket type"
EXPORT_MODE_CURRENT = "Current graph"
EXPORT_MODE_BATCH = "Batch export"
EXPORT_DEST_GRAPHS = "graphs/ (default)"
EXPORT_DEST_PATH = "Use path below"
EXPORT_DEST_ASK = "Ask every time"
EXPORT_SOURCE_CHECKED = "Checked files"
EXPORT_SOURCE_ALL = "All imported files"
EXPORT_PREVIEW_LIMIT = 12
EXPORT_SIZE_MODE_CANVAS = "Match live graph"
EXPORT_SIZE_MODE_PRESET = "Preset"
EXPORT_SIZE_MODE_CUSTOM = "Custom"
EXPORT_TITLE_GRAPH = "In graph (compact)"
EXPORT_TITLE_NONE = "No title"
EXPORT_TITLE_SIDECAR = "Text file next to graph"
EXPORT_TITLE_OPTIONS = [EXPORT_TITLE_GRAPH, EXPORT_TITLE_NONE, EXPORT_TITLE_SIDECAR]
PLOT_STYLE_COLORFUL = "Colorful"
PLOT_STYLE_PAPER = "Paper-first"
PLOT_STYLE_OPTIONS = [PLOT_STYLE_COLORFUL, PLOT_STYLE_PAPER]
EXPORT_SIZE_PRESET_CHOICES = [
    ("Presentation (16:9)", (13.33, 7.50)),
    ("Paper single-column", (3.40, 2.90)),
    ("Paper full-width", (7.00, 4.30)),
    ("Square", (6.00, 6.00)),
]
# Short acronyms for WebSocket subtype in save filenames (avoids overwriting when saving different subtypes in the same second)
WS_TYPE_SAVE_ACRONYM = {
    WS_TYPE_CONCURRENCY: "conc",
    WS_TYPE_PAYLOAD: "pay",
    WS_TYPE_BURST: "burst",
    WS_TYPE_STREAM: "stream",
    WS_TYPE_ALL: "all",
    WS_TYPE_PLACEHOLDER: "all",
}

# Human-readable x-axis labels for plot
XAXIS_DISPLAY_NAMES = {
    "Num Clients": "Number of clients",
    "Message Size (KB)": "Message size (KB)",
    "Rate (msg/s)": "Rate (msg/s)",
    "Bursts": "Bursts",
    "Duration (s)": "Duration (s)",
    "Interval (s)": "Interval (s)",
    "Total Requests": "Total requests",
}

def detect_websocket_subtype(filepath, header, rows):
    """Return WebSocket subtype from filename or CSV content for correct x-axis and display."""
    base = os.path.basename(filepath).lower()
    if "_concurrency" in base:
        return WS_SUBTYPE_CONCURRENCY
    if "_payload" in base:
        return WS_SUBTYPE_PAYLOAD
    if "_burst" in base:
        return WS_SUBTYPE_BURST
    if "_stream" in base:
        return WS_SUBTYPE_STREAM
    # Legacy: detect from Pattern column when filename has no _burst/_stream
    if not rows or "Pattern" not in header:
        return None
    patterns = {str(r.get("Pattern", "")).strip().lower() for r in rows}
    if "burst" in patterns and "stream" not in patterns:
        return WS_SUBTYPE_BURST
    if "stream" in patterns:
        return WS_SUBTYPE_STREAM
    return None

def websocket_xaxis_column(header, rows, subtype):
    """Choose the best x-axis column for WebSocket plot from subtype and data."""
    if subtype == WS_SUBTYPE_CONCURRENCY and "Num Clients" in header:
        return "Num Clients"
    if subtype == WS_SUBTYPE_PAYLOAD and "Message Size (KB)" in header:
        return "Message Size (KB)"
    for col in WS_XAXIS_COLUMNS:
        if col not in header:
            continue
        vals = [safe_float(r.get(col)) for r in rows if r.get(col) not in (None, "")]
        if len(set(vals)) > 1:
            return col
    for col in WS_XAXIS_COLUMNS:
        if col in header:
            return col
    return None

def websocket_subtype_display_name(subtype):
    if subtype == WS_SUBTYPE_CONCURRENCY:
        return "Concurrency"
    if subtype == WS_SUBTYPE_PAYLOAD:
        return "Payload"
    if subtype == WS_SUBTYPE_BURST:
        return "Burst"
    if subtype == WS_SUBTYPE_STREAM:
        return "Stream"
    return None

def _row_value(r, key, header=None):
    """Get value from row by key; fallback to header match if key has spacing differences."""
    val = r.get(key)
    if val is not None and str(val).strip() != "":
        return val
    if header and key:
        for h in header:
            if h.strip().lower() == key.strip().lower():
                return r.get(h)
    return None

def read_csv(filepath):
    with open(filepath, newline='', encoding='utf-8', errors='replace') as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        rows = list(reader)
    return header, rows

def summarize_column(rows, col):
    vals = [safe_float(r.get(col)) for r in rows if r.get(col) not in (None, '', 'NaN')]
    if not vals:
        return {'min': '-', 'max': '-', 'avg': '-'}
    return {'min': min(vals), 'max': max(vals), 'avg': sum(vals) / len(vals)}

def get_numeric_columns(header):
    numeric = []
    for h in header:
        if any(x in h.lower() for x in [
            "cpu", "mem", "latency", "throughput", "energy", "power",
            "requests", "messages", "samples", "rate", "size", "duration",
            "interval", "bursts", "time", "execution", "runtime", "clients"
        ]):
            numeric.append(h)
    return numeric

# --- Main GUI Class ---
class BenchmarkGrapher(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Web Server Benchmark Graph Generator")
        self.setMinimumSize(1100, 700)
        self.files = []
        self.file_checked_state = {}
        self.file_types = {}
        self.headers = {}
        self.rows = {}
        self.file_categories = {}
        self.file_ws_subtypes = {}
        self.series_markers = ['o', 's', 'D', '^', 'v', 'P', 'X', '*']
        self.series_linestyles = ['-', '--', '-.', ':', (0, (5, 1.5)), (0, (3, 1, 1, 1))]
        self.bar_width_scale = 5.0
        self.cursor = None
        self.bar_cursor = None
        self._axes_leave_cid = None
        self._plot_style_presets = self._build_plot_style_presets()
        self.last_plotted_request = None
        self._batch_refreshing = False
        self.init_ui()

    def init_ui(self):
        app = QApplication.instance()
        app.setStyle(QStyleFactory.create("Fusion"))
        app.setPalette(_make_light_palette())
        app.setFont(QFont(FONT_FAMILY, FONT_SIZE))
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)

        # All buttons: same height, same min width, same policy — homogeneous
        BTN_MIN_W = 88
        def _btn(text, slot, min_w=None, role="default"):
            w = min_w if min_w is not None else BTN_MIN_W
            b = QPushButton(text, clicked=slot)
            b.setMinimumWidth(w)
            b.setMinimumHeight(CONTROL_HEIGHT)
            b.setMaximumHeight(CONTROL_HEIGHT)
            b.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
            b.setProperty("role", role)
            return b

        GAP = 6
        left_browser_panel = QWidget()
        left_browser_panel.setMinimumWidth(360)
        left_browser_layout = QVBoxLayout(left_browser_panel)
        left_browser_layout.setContentsMargins(0, 0, 0, 0)
        left_browser_layout.setSpacing(GAP)

        data_group = QGroupBox("Data Browser")
        data_layout = QVBoxLayout(data_group)
        data_layout.setSpacing(GAP)
        row1 = QHBoxLayout()
        row1.setSpacing(GAP)
        row1.addWidget(_btn("Select files", self.browse_files, role="primary"))
        row1.addWidget(_btn("Select folder", self.load_all_csvs_in_folder, role="secondary"))
        row1.addStretch()
        data_layout.addLayout(row1)
        row2 = QHBoxLayout()
        row2.setSpacing(GAP)
        self.clear_all_btn = _btn("Clear all", self.clear_files, role="ghost")
        self.clear_all_btn.setEnabled(False)
        row2.addWidget(self.clear_all_btn)
        self.select_all_btn = _btn("Select all", self.select_all_files, role="ghost")
        self.select_all_btn.setEnabled(False)
        row2.addWidget(self.select_all_btn)
        self.deselect_all_btn = _btn("Deselect all", self.deselect_all_files, role="ghost")
        self.deselect_all_btn.setEnabled(False)
        row2.addWidget(self.deselect_all_btn)
        row2.addStretch()
        data_layout.addLayout(row2)
        row3 = QHBoxLayout()
        row3.setSpacing(GAP)
        self.category_selector = MenuSelectorWidget(placeholder=BENCHMARK_TYPE_PLACEHOLDER, parent=self)
        self.category_selector.set_options([])
        self.category_selector.setEnabled(False)
        self.category_selector.option_chosen.connect(lambda t: QTimer.singleShot(0, self._on_filter_changed))
        row3.addWidget(self.category_selector)
        self.file_count_label = QLabel("0 loaded, 0 selected")
        self.file_count_label.setStyleSheet(
            f"color: {TEXT_MUTED}; background-color: {BG_SOFT}; border: 1px solid #d7e3ff; border-radius: 10px; padding: 4px 10px;"
        )
        row3.addWidget(self.file_count_label, 1)
        data_layout.addLayout(row3)
        self.ws_type_row = QWidget()
        row_ws = QHBoxLayout(self.ws_type_row)
        row_ws.setContentsMargins(0, 0, 0, 0)
        row_ws.setSpacing(GAP)
        self.ws_type_selector = MenuSelectorWidget(placeholder=WS_TYPE_PLACEHOLDER, parent=self)
        self.ws_type_selector.set_options([WS_TYPE_ALL, WS_TYPE_CONCURRENCY, WS_TYPE_PAYLOAD, WS_TYPE_BURST, WS_TYPE_STREAM])
        self.ws_type_selector.option_chosen.connect(lambda t: QTimer.singleShot(0, self._on_filter_changed))
        row_ws.addWidget(self.ws_type_selector)
        row_ws.addStretch()
        self.ws_type_row.setVisible(False)
        data_layout.addWidget(self.ws_type_row)
        self.file_listbox = CheckableFileListWidget(self)
        self.file_listbox.setSelectionMode(QListWidget.ExtendedSelection)
        self.file_listbox.setMinimumHeight(140)
        self.file_listbox.setMaximumHeight(300)
        self.file_listbox.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.file_listbox.itemSelectionChanged.connect(self._on_selection_changed)
        self.file_listbox.itemChanged.connect(self._on_item_changed)
        self.file_listbox.itemDoubleClicked.connect(self.plot_selected)
        data_layout.addWidget(self.file_listbox, 1)
        left_browser_layout.addWidget(data_group, 1)

        plot_group = QGroupBox("Visualization")
        plot_layout = QVBoxLayout(plot_group)
        plot_layout.setSpacing(GAP)
        plot_layout.setContentsMargins(8, 10, 8, 8)
        plot_layout.addWidget(QLabel("Metric:"))
        self.metric_selector = MenuSelectorWidget(placeholder=METRIC_PLACEHOLDER, parent=self)
        self.metric_selector.set_options([])
        self.metric_selector.setEnabled(False)
        self.metric_selector.option_chosen.connect(lambda t: QTimer.singleShot(0, self._on_plot_controls_changed))
        plot_layout.addWidget(self.metric_selector)
        self.plot_style_label = QLabel("Style:")
        self.plot_style_label.setVisible(False)
        plot_layout.addWidget(self.plot_style_label)
        self.plot_style_selector = MenuSelectorWidget(placeholder=PLOT_STYLE_COLORFUL, parent=self)
        self.plot_style_selector.set_options(PLOT_STYLE_OPTIONS)
        self.plot_style_selector.set_current(PLOT_STYLE_COLORFUL)
        self.plot_style_selector.option_chosen.connect(lambda t: QTimer.singleShot(0, self._on_plot_style_changed))
        self.plot_style_selector.setVisible(False)
        plot_layout.addWidget(self.plot_style_selector)
        pr = QHBoxLayout()
        pr.setSpacing(GAP)
        pr.addWidget(QLabel("Type:"))
        self.plot_type_selector = MenuSelectorWidget(placeholder=PLOT_TYPE_PLACEHOLDER, parent=self)
        self.plot_type_selector.set_options(HOME_PLOT_TYPE_OPTIONS)
        self.plot_type_selector.set_current(PLOT_TYPE_PLACEHOLDER)
        self.plot_type_selector.setEnabled(False)
        self.plot_type_selector.option_chosen.connect(lambda t: QTimer.singleShot(0, self._on_plot_controls_changed))
        pr.addWidget(self.plot_type_selector)
        self.plot_btn = _btn("Plot", self.plot_selected, min_w=96, role="primary")
        self.plot_btn.setEnabled(False)
        pr.addWidget(self.plot_btn)
        pr.addStretch()
        plot_layout.addLayout(pr)
        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet(
            f"color: {TEXT_MUTED}; background-color: {BG_SOFT}; border: 1px solid #d7e3ff; border-radius: 10px; padding: 8px 10px; font-size: {FONT_SIZE}pt;"
        )
        self.summary_label.setWordWrap(True)
        plot_layout.addWidget(self.summary_label)
        viz_hint_label = QLabel("Adjust how the current graph is rendered. The canvas in the center stays live while you work.")
        viz_hint_label.setWordWrap(True)
        viz_hint_label.setStyleSheet(
            f"color: {TEXT_MUTED}; padding: 2px; font-size: {FONT_SIZE}pt; font-style: italic;"
        )
        viz_hint_label.setVisible(False)
        plot_layout.insertWidget(0, viz_hint_label)
        left_browser_layout.addWidget(plot_group, 0)

        export_page_body = QWidget()
        export_tab_layout = QVBoxLayout(export_page_body)
        export_tab_layout.setContentsMargins(8, 8, 8, 8)
        export_tab_layout.setSpacing(GAP)

        export_settings_group = QGroupBox("Export Settings")
        export_settings_layout = QVBoxLayout(export_settings_group)
        export_settings_layout.setContentsMargins(8, 10, 8, 8)
        export_settings_layout.setSpacing(GAP)
        mode_row = QHBoxLayout()
        mode_row.setSpacing(GAP)
        mode_row.addWidget(QLabel("Mode:"))
        self.export_mode_selector = QComboBox()
        self.export_mode_selector.addItems([EXPORT_MODE_CURRENT, EXPORT_MODE_BATCH])
        self.export_mode_selector.setCurrentText(EXPORT_MODE_BATCH)
        self.export_mode_selector.setMinimumWidth(150)
        self.export_mode_selector.currentTextChanged.connect(self._on_export_mode_changed)
        mode_row.addWidget(self.export_mode_selector, 1)
        mode_row.addWidget(QLabel("Source:"))
        self.export_source_selector = QComboBox()
        self.export_source_selector.addItems([EXPORT_SOURCE_ALL, EXPORT_SOURCE_CHECKED])
        self.export_source_selector.setCurrentText(EXPORT_SOURCE_ALL)
        self.export_source_selector.setMinimumWidth(150)
        self.export_source_selector.currentTextChanged.connect(self._on_batch_filter_changed)
        mode_row.addWidget(self.export_source_selector, 1)
        mode_row.addWidget(QLabel("Destination:"))
        self.export_destination_selector = QComboBox()
        self.export_destination_selector.addItems([EXPORT_DEST_GRAPHS, EXPORT_DEST_PATH, EXPORT_DEST_ASK])
        self.export_destination_selector.setCurrentText(EXPORT_DEST_GRAPHS)
        self.export_destination_selector.setMinimumWidth(150)
        self.export_destination_selector.currentTextChanged.connect(self._on_export_destination_changed)
        mode_row.addWidget(self.export_destination_selector, 1)
        export_settings_layout.addLayout(mode_row)

        dest_path_row = QHBoxLayout()
        dest_path_row.setSpacing(GAP)
        dest_path_row.addWidget(QLabel("Folder path:"))
        self.export_custom_path_edit = QLineEdit()
        self.export_custom_path_edit.setPlaceholderText("Absolute folder path (used when destination is “Use path below”)")
        self.export_custom_path_edit.textChanged.connect(self._update_export_preview)
        dest_path_row.addWidget(self.export_custom_path_edit, 1)
        self.export_custom_path_browse_btn = _btn("Browse…", self._browse_export_custom_path, min_w=88, role="secondary")
        dest_path_row.addWidget(self.export_custom_path_browse_btn)
        export_settings_layout.addLayout(dest_path_row)

        self.export_hint_label = QLabel("")
        self.export_hint_label.setWordWrap(True)
        self.export_hint_label.setStyleSheet(
            f"color: {TEXT_DISABLED}; padding: 2px; font-size: {FONT_SIZE}pt; font-style: italic;"
        )
        self.export_hint_label.setVisible(False)
        export_settings_layout.addWidget(self.export_hint_label)

        export_opts_row = QHBoxLayout()
        export_opts_row.setSpacing(GAP)
        export_opts_row.addWidget(QLabel("Formats:"))
        self.export_format_png = QCheckBox("PNG")
        self.export_format_png.setChecked(True)
        self.export_format_png.toggled.connect(self._update_export_preview)
        export_opts_row.addWidget(self.export_format_png)
        self.export_format_pdf = QCheckBox("PDF")
        self.export_format_pdf.toggled.connect(self._update_export_preview)
        export_opts_row.addWidget(self.export_format_pdf)
        self.export_format_svg = QCheckBox("SVG")
        self.export_format_svg.toggled.connect(self._update_export_preview)
        export_opts_row.addWidget(self.export_format_svg)
        self.compress_png_checkbox = QCheckBox("Compress PNG")
        self.compress_png_checkbox.setChecked(False)
        self.compress_png_checkbox.setToolTip("When saving PNG, export a smaller file optimized for paper/Overleaf.")
        self.compress_png_checkbox.toggled.connect(self._update_export_preview)
        export_opts_row.addWidget(self.compress_png_checkbox)
        self.export_trim_whitespace_checkbox = QCheckBox("Trim whitespace")
        self.export_trim_whitespace_checkbox.setChecked(True)
        self.export_trim_whitespace_checkbox.setToolTip("Crop extra whitespace around the saved figure.")
        self.export_trim_whitespace_checkbox.toggled.connect(self._update_export_preview)
        export_opts_row.addWidget(self.export_trim_whitespace_checkbox)
        export_opts_row.addWidget(QLabel("PNG DPI:"))
        self.export_dpi_selector = QComboBox()
        self.export_dpi_selector.addItems(["150", "200", "300", "600"])
        self.export_dpi_selector.setCurrentText("300")
        self.export_dpi_selector.setMinimumWidth(84)
        self.export_dpi_selector.setMaximumWidth(96)
        self.export_dpi_selector.setToolTip("Used only when exporting PNG.")
        self.export_dpi_selector.currentTextChanged.connect(self._update_export_preview)
        export_opts_row.addWidget(self.export_dpi_selector)
        export_opts_row.addStretch()
        export_settings_layout.addLayout(export_opts_row)
        export_title_row = QHBoxLayout()
        export_title_row.setSpacing(GAP)
        export_title_row.addWidget(QLabel("Title:"))
        self.export_title_mode_selector = QComboBox()
        self.export_title_mode_selector.addItems(EXPORT_TITLE_OPTIONS)
        self.export_title_mode_selector.setCurrentText(EXPORT_TITLE_GRAPH)
        self.export_title_mode_selector.currentTextChanged.connect(self._update_export_preview)
        export_title_row.addWidget(self.export_title_mode_selector, 1)
        export_title_hint = QLabel(
            "Sidecar mode saves a .title.txt next to the image; both are placed in a subfolder named after the image file."
        )
        export_title_hint.setWordWrap(True)
        export_title_hint.setStyleSheet(
            f"color: {TEXT_DISABLED}; padding: 2px; font-size: {FONT_SIZE}pt; font-style: italic;"
        )
        export_title_row.addWidget(export_title_hint, 3)
        export_settings_layout.addLayout(export_title_row)
        export_tab_layout.addWidget(export_settings_group)

        export_size_group = QGroupBox("Export Size")
        export_size_layout = QVBoxLayout(export_size_group)
        export_size_layout.setContentsMargins(8, 10, 8, 8)
        export_size_layout.setSpacing(GAP)
        size_mode_row = QHBoxLayout()
        size_mode_row.setSpacing(GAP)
        size_mode_row.addWidget(QLabel("Mode:"))
        self.export_size_mode_selector = QComboBox()
        self.export_size_mode_selector.blockSignals(True)
        self.export_size_mode_selector.addItems([EXPORT_SIZE_MODE_CANVAS, EXPORT_SIZE_MODE_PRESET, EXPORT_SIZE_MODE_CUSTOM])
        self.export_size_mode_selector.setCurrentText(EXPORT_SIZE_MODE_PRESET)
        self.export_size_mode_selector.blockSignals(False)
        self.export_size_mode_selector.currentTextChanged.connect(self._on_export_size_mode_changed)
        size_mode_row.addWidget(self.export_size_mode_selector, 1)
        size_mode_row.addWidget(QLabel("Preset:"))
        self.export_size_preset_selector = QComboBox()
        self.export_size_preset_selector.blockSignals(True)
        self.export_size_preset_selector.addItems([label for label, _ in EXPORT_SIZE_PRESET_CHOICES])
        self.export_size_preset_selector.setCurrentText("Paper full-width")
        self.export_size_preset_selector.blockSignals(False)
        self.export_size_preset_selector.currentTextChanged.connect(self._update_export_preview)
        size_mode_row.addWidget(self.export_size_preset_selector, 1)
        export_size_layout.addLayout(size_mode_row)
        self.export_size_source_label = QLabel("Live graph size details will appear here.")
        self.export_size_source_label.setWordWrap(True)
        self.export_size_source_label.setStyleSheet(
            f"color: {TEXT_DISABLED}; padding: 2px; font-size: {FONT_SIZE}pt; font-style: italic;"
        )
        export_size_layout.addWidget(self.export_size_source_label)
        export_size_row = QHBoxLayout()
        export_size_row.setSpacing(GAP)
        export_size_row.addWidget(QLabel("Width override (in):"))
        self.export_width_override = ArrowDoubleSpinBox()
        self.export_width_override.setDecimals(2)
        self.export_width_override.setRange(0.0, 40.0)
        self.export_width_override.setSingleStep(0.25)
        self.export_width_override.setSpecialValueText("auto")
        self.export_width_override.valueChanged.connect(self._update_export_preview)
        export_size_row.addWidget(self.export_width_override)
        export_size_row.addWidget(QLabel("Height override (in):"))
        self.export_height_override = ArrowDoubleSpinBox()
        self.export_height_override.setDecimals(2)
        self.export_height_override.setRange(0.0, 40.0)
        self.export_height_override.setSingleStep(0.25)
        self.export_height_override.setSpecialValueText("auto")
        self.export_height_override.valueChanged.connect(self._update_export_preview)
        export_size_row.addWidget(self.export_height_override)
        self.export_lock_aspect_checkbox = QCheckBox("Lock aspect")
        self.export_lock_aspect_checkbox.setChecked(True)
        self.export_lock_aspect_checkbox.toggled.connect(self._update_export_preview)
        export_size_row.addWidget(self.export_lock_aspect_checkbox)
        self.export_size_reset_btn = _btn("Reset to live graph", self.reset_export_size_overrides, min_w=148, role="ghost")
        export_size_row.addWidget(self.export_size_reset_btn)
        export_size_row.addStretch()
        export_size_layout.addLayout(export_size_row)
        export_size_hint_label = QLabel(
            "Match live graph uses the current on-screen plot size. Preset uses a named layout, and Custom gives exact width/height control."
        )
        export_size_hint_label.setWordWrap(True)
        export_size_hint_label.setStyleSheet(
            f"color: {TEXT_DISABLED}; padding: 2px; font-size: {FONT_SIZE}pt; font-style: italic;"
        )
        export_size_layout.addWidget(export_size_hint_label)
        export_tab_layout.addWidget(export_size_group)

        def _make_batch_list(max_rows, change_handler):
            w = CheckableFileListWidget(self)
            w.setSelectionMode(QListWidget.NoSelection)
            w.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            w.setProperty("maxVisibleRows", max_rows)
            w.itemChanged.connect(change_handler)
            return w

        def _add_batch_section_header(layout, title, *widgets):
            header_row = QHBoxLayout()
            header_row.setSpacing(GAP)
            header_row.setContentsMargins(0, 0, 0, 0)
            title_label = QLabel(title)
            title_label.setStyleSheet(f"color: {ACCENT_ALT}; font-weight: 700;")
            header_row.addWidget(title_label)
            header_row.addStretch()
            for widget in widgets:
                header_row.addWidget(widget)
            layout.addLayout(header_row)

        self.batch_overrides_group = QGroupBox("Batch Builder")
        batch_layout = QVBoxLayout(self.batch_overrides_group)
        batch_layout.setSpacing(GAP)

        batch_defaults_label = QLabel(
            "Pick exactly what to export. Batch export uses the imported dataset directly and does not depend on the current visualization."
        )
        batch_defaults_label.setWordWrap(True)
        batch_defaults_label.setStyleSheet(
            f"color: {TEXT_DISABLED}; padding: 2px; font-size: {FONT_SIZE}pt; font-style: italic;"
        )
        batch_defaults_label.setVisible(False)
        batch_layout.addWidget(batch_defaults_label)

        self.batch_categories_box = QGroupBox()
        batch_categories_layout = QVBoxLayout(self.batch_categories_box)
        batch_categories_layout.setContentsMargins(8, 10, 8, 8)
        self.batch_category_override = QCheckBox("Override current category filter")
        self.batch_category_override.setChecked(True)
        self.batch_category_override.setVisible(False)
        self.batch_category_override.toggled.connect(self._on_batch_filter_changed)
        self.batch_category_select_all_btn = _btn(
            "All", lambda: self._set_checkable_list_state(self.batch_category_list, True, self._on_batch_filter_changed), min_w=44, role="ghost"
        )
        self.batch_category_clear_btn = _btn(
            "Clear", lambda: self._set_checkable_list_state(self.batch_category_list, False, self._on_batch_filter_changed), min_w=52, role="ghost"
        )
        _add_batch_section_header(
            batch_categories_layout,
            "Categories",
            self.batch_category_select_all_btn,
            self.batch_category_clear_btn,
        )
        self.batch_category_list = _make_batch_list(4, self._on_batch_filter_changed)
        batch_categories_layout.addWidget(self.batch_category_list)
        batch_layout.addWidget(self.batch_categories_box)

        self.batch_ws_box = QGroupBox()
        batch_ws_layout = QVBoxLayout(self.batch_ws_box)
        batch_ws_layout.setContentsMargins(8, 10, 8, 8)
        self.batch_ws_override = QCheckBox("Override current WebSocket type")
        self.batch_ws_override.setChecked(True)
        self.batch_ws_override.setVisible(False)
        self.batch_ws_override.toggled.connect(self._on_batch_filter_changed)
        self.batch_ws_select_all_btn = _btn(
            "All", lambda: self._set_checkable_list_state(self.batch_ws_type_list, True, self._on_batch_filter_changed), min_w=44, role="ghost"
        )
        self.batch_ws_clear_btn = _btn(
            "Clear", lambda: self._set_checkable_list_state(self.batch_ws_type_list, False, self._on_batch_filter_changed), min_w=52, role="ghost"
        )
        _add_batch_section_header(
            batch_ws_layout,
            "WebSocket Types",
            self.batch_ws_select_all_btn,
            self.batch_ws_clear_btn,
        )
        self.batch_ws_type_list = _make_batch_list(4, self._on_batch_filter_changed)
        batch_ws_layout.addWidget(self.batch_ws_type_list)
        self.batch_ws_box.setVisible(False)
        batch_layout.addWidget(self.batch_ws_box)

        self.batch_metrics_box = QGroupBox()
        batch_metrics_layout = QVBoxLayout(self.batch_metrics_box)
        batch_metrics_layout.setContentsMargins(8, 10, 8, 8)
        self.batch_metric_override = QCheckBox("Override current metric")
        self.batch_metric_override.setChecked(True)
        self.batch_metric_override.setVisible(False)
        self.batch_metric_override.toggled.connect(self._on_batch_filter_changed)
        self.batch_metric_select_all_btn = _btn(
            "All", lambda: self._set_checkable_list_state(self.batch_metric_list, True, self._update_export_preview), min_w=44, role="ghost"
        )
        self.batch_metric_clear_btn = _btn(
            "Clear", lambda: self._set_checkable_list_state(self.batch_metric_list, False, self._update_export_preview), min_w=52, role="ghost"
        )
        _add_batch_section_header(
            batch_metrics_layout,
            "Metrics",
            self.batch_metric_select_all_btn,
            self.batch_metric_clear_btn,
        )
        self.batch_metric_list = _make_batch_list(6, self._update_export_preview)
        batch_metrics_layout.addWidget(self.batch_metric_list)
        batch_layout.addWidget(self.batch_metrics_box)

        self.batch_plots_box = QGroupBox()
        batch_plots_layout = QVBoxLayout(self.batch_plots_box)
        batch_plots_layout.setContentsMargins(8, 10, 8, 8)
        _add_batch_section_header(batch_plots_layout, "Plot Types By Benchmark")
        self.batch_plot_sections = {}
        self.batch_plot_sections_layout = QVBoxLayout()
        self.batch_plot_sections_layout.setContentsMargins(0, 0, 0, 0)
        self.batch_plot_sections_layout.setSpacing(GAP)
        batch_plots_layout.addLayout(self.batch_plot_sections_layout)
        batch_layout.addWidget(self.batch_plots_box)
        export_tab_layout.addWidget(self.batch_overrides_group)

        preview_group = QGroupBox("Export Plan")
        preview_layout = QVBoxLayout(preview_group)
        preview_layout.setContentsMargins(8, 10, 8, 8)
        preview_layout.setSpacing(GAP)
        self.export_plan_summary_label = QLabel("Export plan summary will appear here.")
        self.export_plan_summary_label.setWordWrap(True)
        self.export_plan_summary_label.setStyleSheet(
            f"color: {TEXT}; background-color: {BG_SOFT}; border: 1px solid #d7e3ff; border-radius: 10px; padding: 8px 10px; font-size: {FONT_SIZE}pt;"
        )
        preview_layout.addWidget(self.export_plan_summary_label)
        self.export_plan_tree = QTreeWidget()
        self.export_plan_tree.setColumnCount(3)
        self.export_plan_tree.setHeaderLabels(["Graph", "Files", "Outputs"])
        self.export_plan_tree.setMinimumHeight(220)
        self.export_plan_tree.setRootIsDecorated(True)
        self.export_plan_tree.setAlternatingRowColors(False)
        self.export_plan_tree.header().setStretchLastSection(False)
        self.export_plan_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.export_plan_tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.export_plan_tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        preview_layout.addWidget(self.export_plan_tree)
        self.export_sample_path_label = QLabel("A sample output path will appear here.")
        self.export_sample_path_label.setWordWrap(True)
        self.export_sample_path_label.setStyleSheet(
            f"color: {TEXT_DISABLED}; padding: 2px; font-size: {FONT_SIZE}pt; font-style: italic;"
        )
        preview_layout.addWidget(self.export_sample_path_label)
        self.export_status_label = QLabel("Export preview will appear here.")
        self.export_status_label.setStyleSheet(
            f"color: {TEXT_DISABLED}; padding: 2px; font-size: {FONT_SIZE}pt; font-style: italic;"
        )
        self.export_status_label.setWordWrap(True)
        preview_layout.addWidget(self.export_status_label)
        export_tab_layout.addWidget(preview_group, 1)

        self.export_run_btn = _btn("Run Batch Export", self.run_export, min_w=168, role="batch")
        self.export_run_btn.setEnabled(False)
        self.export_help_btn = _btn("Help", self.show_help, role="ghost")

        home_tab = QWidget()
        home_tab_layout = QVBoxLayout(home_tab)
        home_tab_layout.setContentsMargins(8, 8, 8, 8)
        home_tab_layout.setSpacing(GAP)
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(6)
        splitter.setChildrenCollapsible(False)
        left_scroll = QScrollArea()
        left_scroll.setWidget(left_browser_panel)
        left_scroll.setWidgetResizable(True)
        left_scroll.setMinimumWidth(340)
        left_scroll.setFrameShape(QFrame.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.left_panel = left_scroll
        self.left_browser_scroll = left_scroll
        splitter.addWidget(left_scroll)

        center_panel = QWidget()
        center_panel.setMinimumWidth(650)
        center_layout = QVBoxLayout(center_panel)
        center_layout.setContentsMargins(0, 0, 0, 0)
        graph_group = QGroupBox("Live Canvas")
        graph_layout = QVBoxLayout(graph_group)
        graph_layout.setContentsMargins(2, 2, 2, 2)
        top_row = QHBoxLayout()
        self.sidebar_toggle_btn = QPushButton("\u00AB")  # « = collapse/hide data browser
        self.sidebar_toggle_btn.setObjectName("sidebarToggleBtn")
        self.sidebar_toggle_btn.setToolTip("Hide data browser")
        self.sidebar_toggle_btn.setFixedSize(38, CONTROL_HEIGHT)
        self.sidebar_toggle_btn.clicked.connect(self._toggle_sidebar)
        top_row.addWidget(self.sidebar_toggle_btn)
        self.fig, self.ax = plt.subplots(figsize=(8, 5))
        self.canvas = FigureCanvas(self.fig)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        for a in self.toolbar.actions():
            if "save" in (a.toolTip() or "").lower() or a.text() == "Save":
                a.setVisible(False)
                break
        top_row.addWidget(self.toolbar, 1)
        graph_layout.addLayout(top_row)
        graph_layout.addWidget(self.canvas)
        center_layout.addWidget(graph_group)
        splitter.addWidget(center_panel)
        splitter.setSizes([430, 1080])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        home_tab_layout.addWidget(splitter, 1)

        export_tab = QWidget()
        export_page_layout = QVBoxLayout(export_tab)
        export_page_layout.setContentsMargins(8, 8, 8, 8)
        export_page_layout.setSpacing(GAP)
        export_data_group = QGroupBox("Export Data")
        export_data_layout = QVBoxLayout(export_data_group)
        export_data_layout.setContentsMargins(8, 10, 8, 8)
        export_data_layout.setSpacing(GAP)
        export_import_row = QHBoxLayout()
        export_import_row.setSpacing(GAP)
        self.export_import_files_btn = _btn("Select files", self.browse_files, role="primary")
        export_import_row.addWidget(self.export_import_files_btn)
        self.export_import_folder_btn = _btn("Select folder", self.load_all_csvs_in_folder, role="secondary")
        export_import_row.addWidget(self.export_import_folder_btn)
        self.export_clear_btn = _btn("Clear all", self.clear_files, role="ghost")
        self.export_clear_btn.setEnabled(False)
        export_import_row.addWidget(self.export_clear_btn)
        export_action_row = QHBoxLayout()
        export_action_row.setSpacing(GAP)
        export_action_row.addStretch()
        export_action_row.addWidget(self.export_run_btn)
        export_action_row.addWidget(self.export_help_btn)
        export_import_row.addLayout(export_action_row)
        export_data_layout.addLayout(export_import_row)
        self.export_data_status_label = QLabel("No CSV data loaded for export yet.")
        self.export_data_status_label.setWordWrap(True)
        self.export_data_status_label.setStyleSheet(
            f"color: {TEXT_MUTED}; background-color: {BG_SOFT}; border: 1px solid #d7e3ff; border-radius: 10px; padding: 8px 10px;"
        )
        export_data_layout.addWidget(self.export_data_status_label)
        export_page_layout.addWidget(export_data_group, 0)
        export_scroll = QScrollArea()
        export_scroll.setWidget(export_page_body)
        export_scroll.setWidgetResizable(True)
        export_scroll.setFrameShape(QFrame.NoFrame)
        export_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        export_page_layout.addWidget(export_scroll, 1)

        self.app_tabs = QTabWidget()
        self.app_tabs.setDocumentMode(True)
        self.app_tabs.setUsesScrollButtons(False)
        self.app_tabs.addTab(home_tab, "Home")
        self.app_tabs.addTab(export_tab, "Export Studio")
        self.app_tabs.currentChanged.connect(lambda _idx: QTimer.singleShot(0, self._update_export_preview))
        main_layout.addWidget(self.app_tabs, 1)
        central.setStyleSheet(_app_stylesheet())

        self._on_export_destination_changed()

        QShortcut(QKeySequence("Ctrl+A"), self).activated.connect(self.select_all_files)
        QShortcut(QKeySequence(Qt.Key_Return), self).activated.connect(self.plot_selected)
        QShortcut(QKeySequence(Qt.Key_Enter), self).activated.connect(self.plot_selected)
        self.showMaximized()

    def show_main_page(self):
        self.app_tabs.setCurrentIndex(0)
        QTimer.singleShot(0, self._update_export_preview)

    def show_export_page(self):
        self.app_tabs.setCurrentIndex(1)
        QTimer.singleShot(0, self._update_export_preview)

    def _toggle_sidebar(self):
        visible = self.left_panel.isVisible()
        self.left_panel.setVisible(not visible)
        if visible:
            self.sidebar_toggle_btn.setText("\u00BB")   # » = expand/show data browser
            self.sidebar_toggle_btn.setToolTip("Show data browser")
        else:
            self.sidebar_toggle_btn.setText("\u00AB")  # « = collapse/hide data browser
            self.sidebar_toggle_btn.setToolTip("Hide data browser")
        QTimer.singleShot(0, self._update_export_preview)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "canvas", None) is not None:
            QTimer.singleShot(0, self._update_export_preview)

    def browse_files(self):
        start_dir = os.path.abspath("results") if os.path.isdir("results") else ""
        files, _ = QFileDialog.getOpenFileNames(self, "Select CSV files", start_dir, "CSV Files (*.csv)")
        if files:
            self.add_files(files)

    def _format_category_name(self, raw_name):
        token = (raw_name or "").strip().strip("/\\")
        if not token:
            return "Unknown"
        key = token.lower()
        if key in CATEGORY_PATH_PARTS:
            return CATEGORY_PATH_PARTS[key]
        special = {
            "grpc": "gRPC",
            "http": "HTTP",
            "https": "HTTPS",
            "ws": "WebSocket",
            "websocket": "WebSocket",
        }
        if key in special:
            return special[key]
        words = [w for w in re.split(r"[_\-]+", token) if w]
        if not words:
            return token.capitalize()
        formatted = []
        for word in words:
            lower = word.lower()
            formatted.append(special.get(lower, word.capitalize()))
        return " ".join(formatted)

    def detect_file_category(self, filepath):
        """Detect category from prefixes or infer it from the selected folder structure."""
        base = os.path.basename(filepath).lower()
        for prefix, cat in CATEGORY_PREFIXES.items():
            if base.startswith(prefix):
                return cat
        norm_parts = [part for part in re.split(r"[\\/]+", os.path.normpath(filepath)) if part]
        lower_parts = [part.lower() for part in norm_parts]
        for part, cat in CATEGORY_PATH_PARTS.items():
            if part in lower_parts:
                return cat
        generic_parts = {"benchmarks", "benchmark", "results", "result", "csv", "graphs", "graph", "output", "outputs"}
        for anchor in ("benchmarks", "results"):
            if anchor in lower_parts:
                idx = lower_parts.index(anchor)
                for part in norm_parts[idx + 1:]:
                    lower = part.lower()
                    if lower in generic_parts or lower.endswith(".csv"):
                        continue
                    return self._format_category_name(part)
        if len(norm_parts) >= 2:
            parent = norm_parts[-2]
            if parent and not parent.lower().endswith(".csv"):
                return self._format_category_name(parent)
        return "Unknown"

    def _build_combined_palette(self, cmap_names):
        palette = []
        for cmap_name in cmap_names:
            cmap = plt.get_cmap(cmap_name)
            if hasattr(cmap, "colors"):
                colors = cmap.colors
            else:
                count = getattr(cmap, "N", 20)
                colors = [cmap(i / max(count - 1, 1)) for i in range(count)]
            for color in colors:
                hex_color = matplotlib.colors.to_hex(color)
                if hex_color not in palette:
                    palette.append(hex_color)
        return palette

    def _build_plot_style_presets(self):
        colorful_palette = [
            "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
            "#8c564b", "#17becf", "#e377c2", "#7f7f7f", "#bcbd22",
            "#aec7e8", "#ffbb78", "#98df8a", "#ff9896", "#c5b0d5",
        ]
        paper_palette = [
            "#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00",
            "#56B4E9", "#000000", "#999999", "#117733", "#882255",
            "#44AA99", "#332288", "#DDCC77", "#AA4499", "#661100",
        ]
        common = {
            "markers": list(self.series_markers),
            "linestyles": list(self.series_linestyles),
            "legend_anchor_y": 0.925,
            "bar_hover_edgecolor": "#111827",
            "bar_hover_linewidth": 1.8,
            "line_hover_zorder": 120,
            "bar_hover_zorder": 80,
            "annotation_fontsize": 9,
        }
        return {
            PLOT_STYLE_COLORFUL: {
                **common,
                "palette": colorful_palette,
                "heatmap_cmap": "viridis",
                "axes_facecolor": "white",
                "figure_facecolor": "white",
                "spine_color": "#b5c2d3",
                "tick_color": "#314056",
                "grid_color": "#d7dde6",
                "grid_width": 0.68,
                "title_size": 11.8,
                "label_size": 10.2,
                "tick_size": 9.2,
                "legend_fontsize": 9.15,
                "legend_edgecolor": "#cfd7e2",
                "legend_facecolor": "white",
                "line_width": 2.05,
                "marker_size": 6.4,
                "marker_edge_width": 1.0,
                "marker_open_cycle": 2,
                "dim_alpha": 0.26,
                "bar_alpha": 0.92,
                "bar_edgecolor": "#5f6368",
                "bar_linewidth": 0.75,
                "annotation_facecolor": "#ffffff",
                "annotation_edgecolor": "#6b7280",
            },
            PLOT_STYLE_PAPER: {
                **common,
                "palette": paper_palette,
                "heatmap_cmap": "cividis",
                "axes_facecolor": "white",
                "figure_facecolor": "white",
                "spine_color": "#8f96a3",
                "tick_color": "#283243",
                "grid_color": "#d3d7dd",
                "grid_width": 0.7,
                "title_size": 12.4,
                "label_size": 10.3,
                "tick_size": 9.2,
                "legend_fontsize": 9.1,
                "legend_edgecolor": "#c7ccd4",
                "legend_facecolor": "white",
                "line_width": 2.0,
                "marker_size": 6.2,
                "marker_edge_width": 1.1,
                "marker_open_cycle": 2,
                "dim_alpha": 0.22,
                "bar_alpha": 0.9,
                "bar_edgecolor": "#555555",
                "bar_linewidth": 0.7,
                "annotation_facecolor": "#ffffff",
                "annotation_edgecolor": "#666666",
            },
        }

    def _current_plot_style_mode(self):
        selector = getattr(self, "plot_style_selector", None)
        if selector is None:
            return PLOT_STYLE_COLORFUL
        current = selector.currentText()
        return current if current in self._plot_style_presets else PLOT_STYLE_COLORFUL

    def _current_plot_style(self):
        return self._plot_style_presets.get(self._current_plot_style_mode(), self._plot_style_presets[PLOT_STYLE_COLORFUL])

    def _figure_layout_profile(self):
        width_in, height_in = self.fig.get_size_inches()
        if width_in <= 4.1 or height_in <= 2.7:
            return "single-column"
        if width_in <= 6.2 or height_in <= 3.7:
            return "canvas"
        if width_in <= 9.4:
            return "full-width"
        return "presentation"

    def _scaled_plot_style(self, style):
        profile = self._figure_layout_profile()
        profile_specs = {
            "single-column": {
                "text_scale": 0.82,
                "stroke_scale": 0.74,
                "marker_scale": 0.76,
                "axes_rect": [0.13, 0.16, 0.83, 0.76],
                "axes_rect_cbar": [0.13, 0.16, 0.68, 0.76],
                "cbar_rect": [0.84, 0.16, 0.028, 0.76],
                "legend_max_cols": 2,
                "legend_headroom_base": 0.10,
                "legend_headroom_per_row": 0.042,
                "legend_headroom_max": 0.20,
                "title_pad": 0.9,
                "legend_columnspacing": 0.56,
                "legend_handlelength": 1.18,
                "legend_labelspacing": 0.16,
                "legend_outside_threshold": 6,
                "legend_band_base": 0.10,
                "legend_band_per_row": 0.026,
                "legend_band_max": 0.19,
                "figure_title_y": 0.975,
                "figure_legend_y": 0.905,
                "line_width_min": 1.15,
                "marker_size_min": 3.8,
            },
            "canvas": {
                "text_scale": 0.95,
                "stroke_scale": 1.1,
                "marker_scale": 1.03,
                "axes_rect": [0.08, 0.12, 0.89, 0.80],
                "axes_rect_cbar": [0.08, 0.12, 0.75, 0.80],
                "cbar_rect": [0.85, 0.12, 0.025, 0.80],
                "legend_max_cols": 2,
                "legend_headroom_base": 0.08,
                "legend_headroom_per_row": 0.045,
                "legend_headroom_max": 0.20,
                "title_pad": 1.0,
                "legend_columnspacing": 0.84,
                "legend_handlelength": 1.5,
                "legend_labelspacing": 0.26,
                "legend_outside_threshold": 999,
                "legend_band_base": 0.0,
                "legend_band_per_row": 0.0,
                "legend_band_max": 0.0,
                "figure_title_y": 0.988,
                "figure_legend_y": 0.93,
                "line_width_min": 1.75,
                "marker_size_min": 4.8,
            },
            "full-width": {
                "text_scale": 1.0,
                "stroke_scale": 1.0,
                "marker_scale": 1.0,
                "axes_rect": [0.08, 0.12, 0.89, 0.80],
                "axes_rect_cbar": [0.08, 0.12, 0.75, 0.80],
                "cbar_rect": [0.85, 0.12, 0.025, 0.80],
                "legend_max_cols": 2,
                "legend_headroom_base": 0.07,
                "legend_headroom_per_row": 0.04,
                "legend_headroom_max": 0.18,
                "title_pad": 1.0,
                "legend_columnspacing": 0.85,
                "legend_handlelength": 1.55,
                "legend_labelspacing": 0.28,
                "legend_outside_threshold": 999,
                "legend_band_base": 0.0,
                "legend_band_per_row": 0.0,
                "legend_band_max": 0.0,
                "figure_title_y": 0.988,
                "figure_legend_y": 0.93,
                "line_width_min": 1.55,
                "marker_size_min": 4.8,
            },
            "presentation": {
                "text_scale": 1.12,
                "stroke_scale": 1.08,
                "marker_scale": 1.08,
                "axes_rect": [0.07, 0.11, 0.90, 0.81],
                "axes_rect_cbar": [0.07, 0.11, 0.78, 0.81],
                "cbar_rect": [0.86, 0.11, 0.022, 0.81],
                "legend_max_cols": 3,
                "legend_headroom_base": 0.06,
                "legend_headroom_per_row": 0.035,
                "legend_headroom_max": 0.16,
                "title_pad": 1.1,
                "legend_columnspacing": 0.95,
                "legend_handlelength": 1.65,
                "legend_labelspacing": 0.30,
                "legend_outside_threshold": 999,
                "legend_band_base": 0.0,
                "legend_band_per_row": 0.0,
                "legend_band_max": 0.0,
                "figure_title_y": 0.988,
                "figure_legend_y": 0.93,
                "line_width_min": 1.7,
                "marker_size_min": 5.2,
            },
        }
        spec = profile_specs[profile]
        scaled = dict(style)
        scaled["layout_profile"] = profile
        scaled["axes_rect"] = tuple(spec["axes_rect"])
        scaled["axes_rect_cbar"] = tuple(spec["axes_rect_cbar"])
        scaled["cbar_rect"] = tuple(spec["cbar_rect"])
        scaled["legend_max_cols"] = spec["legend_max_cols"]
        scaled["legend_headroom_base"] = spec["legend_headroom_base"]
        scaled["legend_headroom_per_row"] = spec["legend_headroom_per_row"]
        scaled["legend_headroom_max"] = spec["legend_headroom_max"]
        scaled["title_pad"] = spec["title_pad"]
        scaled["legend_columnspacing"] = spec["legend_columnspacing"]
        scaled["legend_handlelength"] = spec["legend_handlelength"]
        scaled["legend_labelspacing"] = spec["legend_labelspacing"]
        scaled["legend_outside_threshold"] = spec["legend_outside_threshold"]
        scaled["legend_band_base"] = spec["legend_band_base"]
        scaled["legend_band_per_row"] = spec["legend_band_per_row"]
        scaled["legend_band_max"] = spec["legend_band_max"]
        scaled["figure_title_y"] = spec["figure_title_y"]
        scaled["figure_legend_y"] = spec["figure_legend_y"]
        for key in ("title_size", "label_size", "tick_size", "legend_fontsize", "annotation_fontsize"):
            scaled[key] = round(style[key] * spec["text_scale"], 2)
        scaled["line_width"] = round(max(spec["line_width_min"], style["line_width"] * spec["stroke_scale"]), 2)
        scaled["marker_size"] = round(max(spec["marker_size_min"], style["marker_size"] * spec["marker_scale"]), 2)
        scaled["marker_edge_width"] = round(max(0.8, style["marker_edge_width"] * spec["marker_scale"]), 2)
        scaled["bar_linewidth"] = round(max(0.55, style["bar_linewidth"] * spec["stroke_scale"]), 2)
        scaled["grid_width"] = round(max(0.42, style["grid_width"] * spec["stroke_scale"]), 2)
        return scaled

    def _series_style_for_index(self, idx, style):
        marker = style["markers"][idx % len(style["markers"])]
        linestyle = style["linestyles"][idx % len(style["linestyles"])]
        color = style["palette"][idx % len(style["palette"])]
        cycle_index = idx // max(1, len(style["linestyles"]))
        open_marker = (cycle_index % max(1, style.get("marker_open_cycle", 2))) == (style.get("marker_open_cycle", 2) - 1)
        if marker in {"+", "x", "1", "2", "3", "4", "|", "_", "*"}:
            open_marker = False
        rgb = matplotlib.colors.to_rgb(color)
        luminance = (0.2126 * rgb[0]) + (0.7152 * rgb[1]) + (0.0722 * rgb[2])
        if luminance >= 0.62:
            edge_rgb = tuple(max(0.0, channel * 0.58) for channel in rgb)
            edge_color = matplotlib.colors.to_hex(edge_rgb)
            edge_width = style["marker_edge_width"] + 0.45
        else:
            edge_color = color
            edge_width = style["marker_edge_width"] + 0.15
        return {
            "color": color,
            "marker": marker,
            "linestyle": linestyle,
            "markerfacecolor": "white" if open_marker else color,
            "markeredgecolor": edge_color,
            "markeredgewidth": edge_width + (0.2 if open_marker else 0.0),
            "fillstyle": "none" if open_marker else "full",
        }

    def _tuned_plot_style_for_series_count(self, style, series_count, plot_kind="line"):
        tuned = dict(style)
        if plot_kind == "line":
            if series_count >= 12:
                tuned["line_width"] = round(max(1.1, style["line_width"] * 0.92), 2)
                tuned["marker_size"] = round(max(3.3, style["marker_size"] * 0.78), 2)
                tuned["legend_fontsize"] = round(max(6.1, style["legend_fontsize"] * 0.92), 2)
            elif series_count >= 8:
                tuned["line_width"] = round(max(1.2, style["line_width"] * 0.96), 2)
                tuned["marker_size"] = round(max(3.7, style["marker_size"] * 0.86), 2)
            else:
                tuned["line_width"] = round(style["line_width"] * 1.08, 2)
            if series_count >= 8:
                tuned["line_width"] = round(tuned["line_width"] * 1.05, 2)
            tuned["marker_edge_width"] = round(max(0.85, style["marker_edge_width"] * 1.06), 2)
            if series_count >= 14:
                tuned["marker_open_cycle"] = 4
            elif series_count >= 9:
                tuned["marker_open_cycle"] = max(int(tuned.get("marker_open_cycle", 2)), 3)
        elif plot_kind == "bar" and series_count >= 8:
            tuned["bar_linewidth"] = round(max(0.5, style["bar_linewidth"] * 0.92), 2)
            tuned["legend_fontsize"] = round(max(6.2, style["legend_fontsize"] * 0.94), 2)
        if style.get("layout_profile") == "single-column":
            tuned["legend_fontsize"] = round(max(5.9, tuned["legend_fontsize"] * 0.92), 2)
            tuned["legend_handlelength"] = round(style.get("legend_handlelength", 1.2) * 0.92, 2)
            tuned["legend_columnspacing"] = round(style.get("legend_columnspacing", 0.56) * 0.94, 2)
            tuned["legend_labelspacing"] = round(style.get("legend_labelspacing", 0.16) * 0.92, 2)
        return tuned

    def _compact_single_column_legend_labels(self, labels, style):
        if style.get("layout_profile") != "single-column" or len(labels) <= 5:
            return list(labels)

        def variants_for_label(label):
            base = os.path.splitext(os.path.basename(str(label or "")))[0] or str(label or "")
            tokens = [tok for tok in re.split(r"[-_\s]+", base) if tok]
            while tokens and tokens[0].lower() in {"dy", "st", "ws", "grpc", "http", "local"}:
                tokens = tokens[1:]
            core = list(tokens) if tokens else [base]
            numeric_tail = []
            while len(core) > 2 and core and re.fullmatch(r"\d+", core[-1]):
                numeric_tail.insert(0, core.pop())
            short2 = "-".join(core[:2]) or base
            short3 = "-".join(core[:3]) or short2
            with_tail = f"{short2} ({'-'.join(numeric_tail)})" if numeric_tail else short3
            return [short2, short3, with_tail, base]

        candidates = [variants_for_label(label) for label in labels]
        for idx in range(4):
            variant_labels = [variants[idx] for variants in candidates]
            if len(set(variant_labels)) == len(variant_labels):
                return variant_labels

        deduped = []
        seen = {}
        for variants in candidates:
            label = variants[0]
            count = seen.get(label, 0) + 1
            seen[label] = count
            deduped.append(label if count == 1 else f"{label} #{count}")
        return deduped

    def _apply_line_base_style(self, line, style):
        line.set_linewidth(getattr(line, "_base_linewidth", style["line_width"]))
        line.set_markersize(getattr(line, "_base_markersize", style["marker_size"]))
        line.set_alpha(getattr(line, "_base_alpha", 1.0))
        line.set_zorder(getattr(line, "_base_zorder", 2))

    def _apply_bar_base_style(self, bar, style):
        bar.set_linewidth(getattr(bar, "_base_linewidth", style["bar_linewidth"]))
        bar.set_edgecolor(getattr(bar, "_base_edgecolor", style["bar_edgecolor"]))
        bar.set_alpha(getattr(bar, "_base_alpha", style["bar_alpha"]))
        bar.set_zorder(getattr(bar, "_base_zorder", 2))

    def _legend_columns_for_plot(self, legend_count, style):
        max_cols = style.get("legend_max_cols", 2)
        if legend_count <= 5:
            return 1
        if legend_count <= 10:
            return min(2, max_cols)
        return max_cols

    def _legend_layout_for_plot(self, legend_count, ncol, style, has_colorbar=False, plot_kind="line"):
        mode = "band" if legend_count else "inside"
        rows = max(1, (legend_count + max(1, ncol) - 1) // max(1, ncol))
        axes_rect = list(style["axes_rect_cbar"] if has_colorbar else style["axes_rect"])
        if mode == "band":
            band_base = style.get("legend_band_base", 0.0) or 0.14
            band_per_row = style.get("legend_band_per_row", 0.0) or 0.038
            band_max = style.get("legend_band_max", 0.0) or 0.24
            band_height = min(
                band_max,
                band_base + (band_per_row * max(0, rows - 1)),
            )
            axes_rect[3] = max(0.28, axes_rect[3] - band_height)
        return {
            "mode": mode,
            "rows": rows,
            "axes_rect": tuple(axes_rect),
        }

    def _fit_axes_below_header_band(self, axes_rect, title_artist=None, legend_artist=None, min_height=0.28):
        artists = [artist for artist in (title_artist, legend_artist) if artist is not None]
        if not artists:
            return tuple(axes_rect)
        self.fig.canvas.draw()
        renderer = self.fig.canvas.get_renderer()
        lowest = 1.0
        for artist in artists:
            bbox = artist.get_window_extent(renderer=renderer).transformed(self.fig.transFigure.inverted())
            lowest = min(lowest, bbox.y0)
        fitted = list(axes_rect)
        gap = 0.012
        fitted[3] = max(min_height, lowest - gap - fitted[1])
        return tuple(fitted)

    def _reserve_legend_headroom(self, legend_count, ncol, style):
        if legend_count <= 0:
            return
        y_min, y_max = self.ax.get_ylim()
        span = y_max - y_min
        if not np.isfinite(span) or span <= 0:
            span = max(1.0, abs(y_max) or 1.0)
        rows = max(1, (legend_count + max(1, ncol) - 1) // max(1, ncol))
        extra_ratio = min(
            style.get("legend_headroom_max", 0.18),
            style.get("legend_headroom_base", 0.07) + (style.get("legend_headroom_per_row", 0.04) * max(0, rows - 1)),
        )
        self.ax.set_ylim(y_min, y_max + (span * extra_ratio))

    def _format_hover_value(self, value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return str(value)
        if abs(value) >= 10000:
            return f"{value:,.0f}"
        if abs(value - round(value)) < 1e-9:
            return str(int(round(value)))
        return f"{value:.2f}"

    def _current_export_title_mode(self):
        selector = getattr(self, "export_title_mode_selector", None)
        return selector.currentText() if selector is not None else EXPORT_TITLE_GRAPH

    def _title_mode_writes_sidecar(self):
        return self._current_export_title_mode() == EXPORT_TITLE_SIDECAR

    def _resolve_plot_titles(self, selected_files, metric, x_axis_label, *, heatmap=False):
        base_title = f"{metric} (heatmap)" if heatmap else f"{metric} vs. {x_axis_label}"
        suffix = http_max_workers_plot_suffix(selected_files, self.file_types, self.headers, self.rows)
        full_title = f"{base_title}{suffix}"
        return {
            "compact": base_title,
            "full": full_title,
            "suffix": suffix.strip(" \u2014"),
        }

    def _export_display_title(self, titles, title_mode=None):
        if title_mode == EXPORT_TITLE_NONE or title_mode == EXPORT_TITLE_SIDECAR:
            return ""
        if title_mode == EXPORT_TITLE_GRAPH:
            return titles["compact"]
        return titles["full"]

    def _on_plot_style_changed(self):
        if self._graph_has_data() and self.last_plotted_request and self._live_plot_ready():
            request = dict(self.last_plotted_request)
            metric = self.metric_selector.currentText() or request.get("metric", METRIC_PLACEHOLDER)
            plot_type = self.plot_type_selector.currentText() or request.get("plot_type", WS_PLOT_MULTILINE)
            plotted = self._render_plot(
                request.get("files", []),
                metric,
                plot_type,
                enable_interactivity=True,
            )
            self.last_plotted_request = (
                {**request, "metric": metric, "plot_type": plot_type, "plot_style": self._current_plot_style_mode()} if plotted else None
            )
        self._update_export_preview()

    def _on_filter_changed(self):
        cat = self.category_selector.currentText()
        visible_files = [f for f in self.files if self.file_categories.get(f) == cat] if cat not in (None, "", BENCHMARK_TYPE_PLACEHOLDER, "All") else list(self.files)
        category_plot_types = self._valid_plot_types_for_category(cat, source_files=visible_files)
        is_websocket_category = cat == "WebSocket"
        if getattr(self, "ws_type_row", None):
            self.ws_type_row.setVisible(is_websocket_category)
            if not is_websocket_category and getattr(self, "ws_type_selector", None):
                self.ws_type_selector.set_current(WS_TYPE_PLACEHOLDER)
        if getattr(self, "plot_type_selector", None):
            prev_pt = self.plot_type_selector.currentText()
            self.plot_type_selector.set_options(category_plot_types)
            if prev_pt in category_plot_types:
                self.plot_type_selector.set_current(prev_pt)
            else:
                self.plot_type_selector.set_current(PLOT_TYPE_PLACEHOLDER)
        self.update_file_listbox_display()
        self.update_metric_options()
        self._refresh_export_controls()
        self._update_export_preview()

    def _on_plot_controls_changed(self):
        self.plot_selected()
        self.plot_btn.setEnabled(len(self.files) > 0 and self._live_plot_ready())
        self._refresh_export_controls()
        self._update_export_preview()

    def _on_selection_changed(self):
        self._update_file_count_label()
        self._update_export_preview()

    def _on_item_changed(self, item):
        """Update file count when a checkbox is toggled."""
        filepath = item.data(Qt.UserRole)
        if filepath:
            self.file_checked_state[filepath] = item.checkState() == Qt.Checked
        self._update_file_count_label()
        self._refresh_export_controls()
        self._update_export_preview()

    def _update_selection_buttons_state(self):
        has_items = self.file_listbox.count() > 0
        self.select_all_btn.setEnabled(has_items)
        self.deselect_all_btn.setEnabled(has_items)

    def _update_filter_combo(self):
        """Populate benchmark type from loaded file categories (no separate label; dropdown shows current choice)."""
        cats = ["All"] + sorted(set(self.file_categories.values()))
        current = self.category_selector.currentText()
        self.category_selector.set_options(cats)
        if current in cats:
            self.category_selector.set_current(current)
        else:
            self.category_selector.set_current(BENCHMARK_TYPE_PLACEHOLDER)
        self._on_filter_changed()

    def _update_file_count_label(self):
        total = len(self.get_visible_files())
        selected = sum(1 for i in range(self.file_listbox.count())
                       if self.file_listbox.item(i).checkState() == Qt.Checked)
        self.file_count_label.setText(f"{total} loaded, {selected} selected")
        self._update_export_data_status_label()

    def _update_export_data_status_label(self):
        if not getattr(self, "export_data_status_label", None):
            return
        imported = len(self.files)
        checked = len(self._checked_loaded_files()) if getattr(self, "files", None) else 0
        if imported == 0:
            self.export_data_status_label.setText("No CSV data loaded for export yet.")
            return
        self.export_data_status_label.setText(
            f"{imported} CSV file(s) imported. {checked} checked file(s) available for checked-source export."
        )

    def add_files(self, files):
        for f in files:
            if f not in self.files:
                try:
                    header, rows = read_csv(f)
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"Failed to read {f}: {e}")
                    continue
                if not header:
                    QMessageBox.warning(self, "Empty or invalid CSV", f"No header in {f}; skipping.")
                    continue
                typ = detect_csv_type(header)
                category = self.detect_file_category(f)
                ws_sub = detect_websocket_subtype(f, header, rows) if typ == "websocket" else None
                self.files.append(f)
                self.file_types[f] = typ
                self.headers[f] = header
                self.rows[f] = rows
                self.file_categories[f] = category
                self.file_ws_subtypes[f] = ws_sub
                self.file_checked_state[f] = True
        self._update_filter_combo()
        self.update_metric_options()
        self.update_file_listbox_display()
        self._refresh_export_controls()
        self._set_data_controls_enabled(len(self.files) > 0)
        self._update_export_preview()

    def update_file_listbox_display(self):
        self.file_listbox.clear()
        visible = self.get_visible_files()
        self.file_listbox.blockSignals(True)
        for f in visible:
            typ = self.file_types.get(f, "unknown")
            suffix = f"  [{typ}]"
            if typ == "websocket":
                sub = self.file_ws_subtypes.get(f)
                if sub:
                    sub_display = websocket_subtype_display_name(sub)
                    if sub_display:
                        suffix = f"  [{typ} / {sub_display}]"
            item = QListWidgetItem(os.path.basename(f) + suffix)
            item.setData(Qt.UserRole, f)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if self.file_checked_state.get(f, True) else Qt.Unchecked)
            self.file_listbox.addItem(item)
        self.file_listbox.blockSignals(False)
        self.file_listbox.selectAll()
        self._update_file_count_label()
        self._update_selection_buttons_state()

    def clear_files(self):
        self.files.clear()
        self.file_types.clear()
        self.headers.clear()
        self.rows.clear()
        self.file_categories.clear()
        self.file_ws_subtypes.clear()
        self.file_checked_state.clear()
        self.file_listbox.clear()
        self.category_selector.set_options([BENCHMARK_TYPE_PLACEHOLDER])
        self.category_selector.set_current(BENCHMARK_TYPE_PLACEHOLDER)
        if getattr(self, "ws_type_row", None):
            self.ws_type_row.setVisible(False)
        if getattr(self, "ws_type_selector", None):
            self.ws_type_selector.set_current(WS_TYPE_PLACEHOLDER)
        while self.batch_plot_sections_layout.count():
            item = self.batch_plot_sections_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.batch_plot_sections.clear()
        if getattr(self, "plot_type_selector", None):
            self.plot_type_selector.set_options(HOME_PLOT_TYPE_OPTIONS)
            self.plot_type_selector.set_current(PLOT_TYPE_PLACEHOLDER)
        self.metric_selector.set_options([])
        self.metric_selector.set_current(METRIC_PLACEHOLDER)
        self.export_source_selector.setCurrentText(EXPORT_SOURCE_ALL)
        self.export_destination_selector.setCurrentText(EXPORT_DEST_GRAPHS)
        if getattr(self, "export_custom_path_edit", None):
            self.export_custom_path_edit.clear()
        self.export_size_mode_selector.setCurrentText(EXPORT_SIZE_MODE_PRESET)
        self.export_size_preset_selector.setCurrentText("Paper full-width")
        self._on_export_destination_changed()
        self._set_data_controls_enabled(False)
        self._clear_plot_artists()
        self.canvas.draw()
        self.summary_label.setText("")
        self.last_plotted_request = None
        self.export_plan_tree.clear()
        self.export_plan_summary_label.setText("Export plan summary will appear here.")
        self.export_sample_path_label.setText("A sample output path will appear here.")
        self.export_status_label.setText("Export preview will appear here.")
        self.file_count_label.setText("0 loaded, 0 selected")
        self.select_all_btn.setEnabled(False)
        self.deselect_all_btn.setEnabled(False)
        self._update_export_data_status_label()
        self._refresh_export_controls()
        self._update_export_preview()

    def _set_data_controls_enabled(self, enabled):
        """Enable or disable data/plot/save controls (only usable after files are loaded)."""
        self.clear_all_btn.setEnabled(enabled)
        self.category_selector.setEnabled(enabled)
        self.metric_selector.setEnabled(enabled)
        self.plot_style_selector.setEnabled(True)
        self.plot_type_selector.setEnabled(enabled)
        self.plot_btn.setEnabled(enabled and self._live_plot_ready())
        self.export_import_files_btn.setEnabled(True)
        self.export_import_folder_btn.setEnabled(True)
        self.export_clear_btn.setEnabled(enabled)
        self.export_mode_selector.setEnabled(True)
        self.export_source_selector.setEnabled(True)
        self.export_destination_selector.setEnabled(enabled)
        self.export_format_png.setEnabled(True)
        self.export_format_pdf.setEnabled(True)
        self.export_format_svg.setEnabled(True)
        self.compress_png_checkbox.setEnabled(True)
        self.export_trim_whitespace_checkbox.setEnabled(True)
        self.export_dpi_selector.setEnabled(True)
        self.export_size_mode_selector.setEnabled(True)
        self.export_size_preset_selector.setEnabled(True)
        self.export_width_override.setEnabled(True)
        self.export_height_override.setEnabled(True)
        self.export_lock_aspect_checkbox.setEnabled(True)
        self.export_size_reset_btn.setEnabled(True)
        self.batch_category_override.setEnabled(True)
        self.batch_ws_override.setEnabled(True)
        self.batch_metric_override.setEnabled(True)
        self.batch_category_select_all_btn.setEnabled(True)
        self.batch_category_clear_btn.setEnabled(True)
        self.batch_ws_select_all_btn.setEnabled(True)
        self.batch_ws_clear_btn.setEnabled(True)
        self.batch_metric_select_all_btn.setEnabled(True)
        self.batch_metric_clear_btn.setEnabled(True)
        self.batch_category_list.setEnabled(enabled)
        self.batch_ws_type_list.setEnabled(enabled and self.batch_ws_box.isVisible())
        self.batch_metric_list.setEnabled(enabled)
        for section in getattr(self, "batch_plot_sections", {}).values():
            vis = enabled and section["box"].isVisible()
            section["list"].setEnabled(vis)
            section["none_btn"].setEnabled(vis)
            section["all_btn"].setEnabled(vis)
            section["clear_btn"].setEnabled(vis)
        path_mode = enabled and self._current_export_destination_mode() == EXPORT_DEST_PATH
        if getattr(self, "export_custom_path_edit", None):
            self.export_custom_path_edit.setEnabled(path_mode)
        if getattr(self, "export_custom_path_browse_btn", None):
            self.export_custom_path_browse_btn.setEnabled(path_mode)
        self.export_run_btn.setEnabled(enabled and self.export_run_btn.isEnabled())
        self._update_export_size_controls()

    def _checked_list_values(self, widget):
        return [
            widget.item(i).data(Qt.UserRole) or widget.item(i).text()
            for i in range(widget.count())
            if widget.item(i).checkState() == Qt.Checked
        ]

    def _set_checkable_list_items(self, widget, options, checked_values=None):
        checked = set(checked_values or [])
        widget.blockSignals(True)
        widget.clear()
        for opt in options:
            item = QListWidgetItem(str(opt))
            item.setData(Qt.UserRole, opt)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if opt in checked else Qt.Unchecked)
            widget.addItem(item)
        widget.blockSignals(False)
        self._resize_checkable_list(widget)

    def _resize_checkable_list(self, widget):
        row_height = widget.sizeHintForRow(0)
        if row_height <= 0:
            row_height = 24
        rows = max(1, widget.count())
        max_rows = int(widget.property("maxVisibleRows") or rows)
        visible_rows = min(rows, max_rows)
        frame = 10
        height = row_height * visible_rows + frame
        widget.setMinimumHeight(height)
        widget.setMaximumHeight(height)

    def _set_checkable_list_state(self, widget, checked, callback=None):
        widget.blockSignals(True)
        for i in range(widget.count()):
            widget.item(i).setCheckState(Qt.Checked if checked else Qt.Unchecked)
        widget.blockSignals(False)
        if callback:
            callback()

    def _create_action_button(self, text, slot, min_w=88, role="default"):
        button = QPushButton(text, clicked=slot)
        button.setMinimumWidth(min_w)
        button.setMinimumHeight(CONTROL_HEIGHT)
        button.setMaximumHeight(CONTROL_HEIGHT)
        button.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        button.setProperty("role", role)
        return button

    def _make_batch_list_widget(self, max_rows, change_handler):
        widget = CheckableFileListWidget(self)
        widget.setSelectionMode(QListWidget.NoSelection)
        widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        widget.setProperty("maxVisibleRows", max_rows)
        widget.itemChanged.connect(change_handler)
        return widget

    def _build_batch_section_header(self, title, *widgets):
        header_row = QHBoxLayout()
        header_row.setSpacing(6)
        header_row.setContentsMargins(0, 0, 0, 0)
        title_label = QLabel(title)
        title_label.setStyleSheet(f"color: {ACCENT_ALT}; font-weight: 700;")
        header_row.addWidget(title_label)
        header_row.addStretch()
        for widget in widgets:
            header_row.addWidget(widget)
        return header_row

    def _ensure_batch_plot_section(self, category):
        section = self.batch_plot_sections.get(category)
        if section:
            return section
        box = QGroupBox()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 10, 8, 8)
        none_btn = self._create_action_button(
            "None",
            lambda _checked=False, cat=category: self._set_checkable_list_state(
                self.batch_plot_sections[cat]["list"], False, self._update_export_preview
            ),
            min_w=48,
            role="ghost",
        )
        all_btn = self._create_action_button(
            "All",
            lambda _checked=False, cat=category: self._set_checkable_list_state(
                self.batch_plot_sections[cat]["list"], True, self._update_export_preview
            ),
            min_w=44,
            role="ghost",
        )
        clear_btn = self._create_action_button(
            "Clear",
            lambda _checked=False, cat=category: self._set_checkable_list_state(
                self.batch_plot_sections[cat]["list"], False, self._update_export_preview
            ),
            min_w=52,
            role="ghost",
        )
        layout.addLayout(self._build_batch_section_header(category, none_btn, all_btn, clear_btn))
        list_widget = self._make_batch_list_widget(4, self._update_export_preview)
        layout.addWidget(list_widget)
        self.batch_plot_sections_layout.addWidget(box)
        section = {
            "box": box,
            "list": list_widget,
            "none_btn": none_btn,
            "all_btn": all_btn,
            "clear_btn": clear_btn,
        }
        self.batch_plot_sections[category] = section
        return section

    def _current_export_mode(self):
        return self.export_mode_selector.currentText() or EXPORT_MODE_BATCH

    def _current_export_destination_mode(self):
        return self.export_destination_selector.currentText() or EXPORT_DEST_GRAPHS

    def _on_export_destination_changed(self, *_args):
        use_path = self._current_export_destination_mode() == EXPORT_DEST_PATH
        if getattr(self, "export_custom_path_edit", None):
            self.export_custom_path_edit.setEnabled(use_path)
        if getattr(self, "export_custom_path_browse_btn", None):
            self.export_custom_path_browse_btn.setEnabled(use_path)
        self._update_export_preview()

    def _browse_export_custom_path(self):
        start = self.export_custom_path_edit.text().strip() or os.path.abspath("graphs")
        picked = QFileDialog.getExistingDirectory(self, "Select export folder", start)
        if picked:
            self.export_custom_path_edit.setText(picked)

    def _export_root_directory(self):
        if self._current_export_destination_mode() == EXPORT_DEST_PATH:
            raw = self.export_custom_path_edit.text().strip() if getattr(self, "export_custom_path_edit", None) else ""
            if raw:
                return os.path.abspath(os.path.expanduser(raw))
        return os.path.abspath("graphs")

    def _sidecar_bundle_image_path(self, filepath):
        if not self._title_mode_writes_sidecar():
            return filepath
        parent = os.path.dirname(filepath) or "."
        fname = os.path.basename(filepath)
        stem, _ext = os.path.splitext(fname)
        if not stem:
            return filepath
        return os.path.join(parent, stem, fname)

    def _current_export_source_mode(self):
        return self.export_source_selector.currentText() or EXPORT_SOURCE_ALL

    def _batch_source_files(self):
        if self._current_export_source_mode() == EXPORT_SOURCE_CHECKED:
            return self._checked_loaded_files()
        return list(self.files)

    def _selected_export_formats(self):
        formats = []
        if self.export_format_png.isChecked():
            formats.append(".png")
        if self.export_format_pdf.isChecked():
            formats.append(".pdf")
        if self.export_format_svg.isChecked():
            formats.append(".svg")
        return formats

    def reset_export_size_overrides(self):
        self.export_width_override.blockSignals(True)
        self.export_height_override.blockSignals(True)
        self.export_width_override.setValue(0.0)
        self.export_height_override.setValue(0.0)
        self.export_width_override.blockSignals(False)
        self.export_height_override.blockSignals(False)
        self._update_export_preview()

    def _current_export_size_mode(self):
        return self.export_size_mode_selector.currentText() or EXPORT_SIZE_MODE_PRESET

    def _selected_export_size_preset(self):
        current = self.export_size_preset_selector.currentText()
        preset_map = dict(EXPORT_SIZE_PRESET_CHOICES)
        if current in preset_map:
            return current
        return "Paper full-width"

    def _selected_export_size_preset_inches(self):
        preset_map = dict(EXPORT_SIZE_PRESET_CHOICES)
        return preset_map.get(self._selected_export_size_preset(), (7.00, 4.30))

    def _on_export_size_mode_changed(self, *_args):
        mode = self._current_export_size_mode()
        if mode == EXPORT_SIZE_MODE_CUSTOM and getattr(self, "export_width_override", None):
            if self.export_width_override.value() <= 0.0 and self.export_height_override.value() <= 0.0:
                canvas_w, canvas_h = self._current_canvas_size_inches()
                self.export_width_override.blockSignals(True)
                self.export_height_override.blockSignals(True)
                self.export_width_override.setValue(round(canvas_w, 2))
                self.export_height_override.setValue(round(canvas_h, 2))
                self.export_width_override.blockSignals(False)
                self.export_height_override.blockSignals(False)
        self._update_export_size_controls()
        self._update_export_preview()

    def _update_export_size_controls(self):
        if not getattr(self, "export_width_override", None):
            return
        enabled = bool(self.files)
        mode = self._current_export_size_mode()
        png_enabled = self.export_format_png.isChecked()
        self.compress_png_checkbox.setEnabled(enabled and png_enabled)
        self.export_dpi_selector.setEnabled(enabled and png_enabled)
        self.export_size_preset_selector.setEnabled(enabled and mode == EXPORT_SIZE_MODE_PRESET)
        custom_enabled = enabled and mode == EXPORT_SIZE_MODE_CUSTOM
        self.export_width_override.setEnabled(custom_enabled)
        self.export_height_override.setEnabled(custom_enabled)
        self.export_lock_aspect_checkbox.setEnabled(custom_enabled)
        self.export_size_reset_btn.setEnabled(enabled and mode != EXPORT_SIZE_MODE_CANVAS)

    def _current_canvas_size_px(self):
        if getattr(self, "canvas", None) is None:
            return 0, 0
        width, height = self.canvas.get_width_height()
        return max(int(width), 0), max(int(height), 0)

    def _current_canvas_size_inches(self):
        fig = getattr(self, "fig", None)
        if fig is None:
            return 8.0, 5.0
        width_px, height_px = self._current_canvas_size_px()
        dpi = max(float(fig.get_dpi()), 1.0)
        if width_px <= 0 or height_px <= 0:
            w_in, h_in = fig.get_size_inches()
            return max(float(w_in), 0.1), max(float(h_in), 0.1)
        return max(width_px / dpi, 0.1), max(height_px / dpi, 0.1)

    def _resolved_export_size_plan(self):
        canvas_w, canvas_h = self._current_canvas_size_inches()
        mode = self._current_export_size_mode()
        if mode == EXPORT_SIZE_MODE_PRESET:
            width, height = self._selected_export_size_preset_inches()
            label = f"Preset: {self._selected_export_size_preset()}"
            return {
                "mode": mode,
                "label": label,
                "size_inches": (max(float(width), 0.1), max(float(height), 0.1)),
                "canvas_inches": (canvas_w, canvas_h),
            }
        if mode == EXPORT_SIZE_MODE_CUSTOM:
            custom_w = float(self.export_width_override.value())
            custom_h = float(self.export_height_override.value())
            if custom_w <= 0.0 and custom_h <= 0.0:
                label = "Custom: live graph fallback"
                return {
                    "mode": mode,
                    "label": label,
                    "size_inches": (canvas_w, canvas_h),
                    "canvas_inches": (canvas_w, canvas_h),
                }
            aspect = (canvas_w / canvas_h) if canvas_h else 1.0
            if self.export_lock_aspect_checkbox.isChecked():
                if custom_w <= 0.0:
                    custom_w = custom_h * aspect
                elif custom_h <= 0.0:
                    custom_h = custom_w / aspect
            width = custom_w if custom_w > 0.0 else canvas_w
            height = custom_h if custom_h > 0.0 else canvas_h
            label = "Custom size"
            return {
                "mode": mode,
                "label": label,
                "size_inches": (max(width, 0.1), max(height, 0.1)),
                "canvas_inches": (canvas_w, canvas_h),
            }
        return {
            "mode": EXPORT_SIZE_MODE_CANVAS,
            "label": "Match live graph",
            "size_inches": (canvas_w, canvas_h),
            "canvas_inches": (canvas_w, canvas_h),
        }

    def _resolved_export_size_inches(self):
        width, height = self._resolved_export_size_plan()["size_inches"]
        return width, height

    def _resolved_export_size_text(self):
        canvas_px = self._current_canvas_size_px()
        if canvas_px[0] <= 0 or canvas_px[1] <= 0:
            fig = getattr(self, "fig", None)
            if fig is not None:
                dpi = max(float(fig.get_dpi()), 1.0)
                w_in, h_in = fig.get_size_inches()
                canvas_px = (max(int(round(w_in * dpi)), 1), max(int(round(h_in * dpi)), 1))
            else:
                canvas_px = (1, 1)
        size_plan = self._resolved_export_size_plan()
        default_inches = size_plan["canvas_inches"]
        width_in, height_in = size_plan["size_inches"]
        png_width = max(int(round(width_in * self._selected_png_dpi())), 1)
        png_height = max(int(round(height_in * self._selected_png_dpi())), 1)
        default_label = (
            f"Live graph now: {canvas_px[0]} x {canvas_px[1]} px, "
            f"default export size: {default_inches[0]:.2f} x {default_inches[1]:.2f} in."
        )
        resolved_label = (
            f"{size_plan['label']}: "
            f"{width_in:.2f} x {height_in:.2f} in "
            f"(PNG at {self._selected_png_dpi()} DPI -> {png_width} x {png_height} px)."
        )
        return default_label, resolved_label

    def _capture_figure_geometry(self):
        return {
            "size_inches": tuple(float(v) for v in self.fig.get_size_inches()),
            "dpi": float(self.fig.get_dpi()),
        }

    def _restore_figure_geometry(self, geometry):
        if not geometry:
            return
        self.fig.set_dpi(geometry.get("dpi", self.fig.get_dpi()))
        size_inches = geometry.get("size_inches")
        if size_inches:
            self.fig.set_size_inches(*size_inches, forward=False)
        self.canvas.draw_idle()

    def _checked_loaded_files(self):
        return [f for f in self.files if self.file_checked_state.get(f, True)]

    def _available_batch_categories(self, source_files=None):
        scoped = source_files if source_files is not None else self._checked_loaded_files()
        return sorted({self.file_categories.get(f) for f in scoped if self.file_categories.get(f)})

    def _default_batch_categories(self, source_files=None):
        return self._available_batch_categories(source_files)

    def _selected_batch_categories(self):
        return self._checked_list_values(self.batch_category_list)

    def _effective_batch_categories(self, source_files=None):
        selected = self._selected_batch_categories()
        if selected or self.batch_category_list.count() > 0:
            return selected
        return self._default_batch_categories(source_files)

    def _available_batch_ws_types(self, source_files=None, selected_categories=None):
        selected_categories = selected_categories or []
        if "WebSocket" not in selected_categories:
            return []
        scoped = source_files if source_files is not None else self._checked_loaded_files()
        subtype_to_display = {
            WS_SUBTYPE_CONCURRENCY: WS_TYPE_CONCURRENCY,
            WS_SUBTYPE_PAYLOAD: WS_TYPE_PAYLOAD,
            WS_SUBTYPE_BURST: WS_TYPE_BURST,
            WS_SUBTYPE_STREAM: WS_TYPE_STREAM,
        }
        present = {
            subtype_to_display[sub]
            for f, sub in self.file_ws_subtypes.items()
            if f in scoped and self.file_categories.get(f) == "WebSocket" and sub in subtype_to_display
        }
        return [opt for opt in WS_TYPE_OPTIONS if opt != WS_TYPE_ALL and opt in present]

    def _default_batch_ws_types(self, categories=None, source_files=None):
        categories = categories if categories is not None else self._default_batch_categories(source_files)
        options = self._available_batch_ws_types(source_files=source_files, selected_categories=categories)
        return list(options)

    def _selected_batch_ws_types(self):
        return self._checked_list_values(self.batch_ws_type_list)

    def _effective_batch_ws_types(self, categories=None, source_files=None):
        categories = categories if categories is not None else self._effective_batch_categories(source_files)
        if "WebSocket" not in categories:
            return []
        selected = self._selected_batch_ws_types()
        if selected or self.batch_ws_type_list.count() > 0:
            return selected
        return self._default_batch_ws_types(categories=categories, source_files=source_files)

    def _batch_candidate_groups(self, categories=None, ws_types=None, source_files=None):
        categories = categories if categories is not None else self._effective_batch_categories(source_files)
        ws_types = ws_types if ws_types is not None else self._effective_batch_ws_types(categories, source_files)
        source_files = source_files if source_files is not None else self._checked_loaded_files()
        groups = []
        for category in categories:
            if category == "WebSocket":
                candidates = ws_types
            else:
                candidates = [None]
            seen = set()
            for ws_type in candidates:
                key = ws_type or ""
                if key in seen:
                    continue
                seen.add(key)
                files = self._filter_batch_files(source_files, category, ws_type)
                if files:
                    groups.append((category, ws_type, files))
        return groups

    def _available_batch_metrics(self, categories=None, ws_types=None, source_files=None):
        groups = self._batch_candidate_groups(categories=categories, ws_types=ws_types, source_files=source_files)
        metrics = set()
        for _, _, files in groups:
            metrics.update(self._valid_metrics_for_files(files))
        return sorted(metrics)

    def _default_batch_metrics(self, categories=None, ws_types=None, source_files=None):
        return []

    def _effective_batch_metrics(self, categories=None, ws_types=None, source_files=None):
        selected = self._checked_list_values(self.batch_metric_list)
        if selected or self.batch_metric_list.count() > 0:
            return selected
        return self._default_batch_metrics(categories=categories, ws_types=ws_types, source_files=source_files)

    def _available_batch_plot_types_for_category(self, category, source_files=None):
        if category == "WebSocket":
            return list(HOME_PLOT_TYPE_OPTIONS)
        return [p for p in HOME_PLOT_TYPE_OPTIONS if p != WS_PLOT_HEATMAP]

    def _effective_batch_plot_types_for_category(self, category, source_files=None):
        section = self.batch_plot_sections.get(category)
        if not section:
            return []
        return self._checked_list_values(section["list"])

    def _on_export_mode_changed(self, *_args):
        self._refresh_export_controls()
        self._update_export_preview()

    def _on_batch_filter_changed(self, *_args):
        if self._batch_refreshing:
            return
        self._refresh_export_controls()
        self._update_export_preview()

    def _set_button_role(self, button, role):
        if button.property("role") == role:
            return
        button.setProperty("role", role)
        button.style().unpolish(button)
        button.style().polish(button)
        button.update()

    def _refresh_export_controls(self):
        if self._batch_refreshing:
            return
        self._batch_refreshing = True
        source_files = self._batch_source_files()
        try:
            self.batch_overrides_group.setVisible(self._current_export_mode() == EXPORT_MODE_BATCH)
            self.export_hint_label.setText(
                "Exports the graph currently shown in the main workspace."
                if self._current_export_mode() == EXPORT_MODE_CURRENT
                else "Batch export builds directly from the imported data source and the selections below."
            )
            self.export_run_btn.setText(
                "Export current graph" if self._current_export_mode() == EXPORT_MODE_CURRENT else "Run Batch Export"
            )
            self._set_button_role(
                self.export_run_btn,
                "primary" if self._current_export_mode() == EXPORT_MODE_CURRENT else "batch",
            )
            self.export_source_selector.setEnabled(self._current_export_mode() == EXPORT_MODE_BATCH and bool(self.files))

            category_options = self._available_batch_categories(source_files)
            category_checked = set(self._checked_list_values(self.batch_category_list)) & set(category_options)
            if not category_checked and category_options and self.batch_category_list.count() == 0:
                category_checked = set(self._default_batch_categories(source_files))
            self._set_checkable_list_items(self.batch_category_list, category_options, category_checked)
            self.batch_category_list.setEnabled(bool(source_files))
            self.batch_category_select_all_btn.setEnabled(bool(source_files) and bool(category_options))
            self.batch_category_clear_btn.setEnabled(bool(source_files) and bool(category_options))

            effective_categories = self._effective_batch_categories(source_files)
            ws_type_options = self._available_batch_ws_types(source_files=source_files, selected_categories=effective_categories)
            ws_checked = set(self._checked_list_values(self.batch_ws_type_list)) & set(ws_type_options)
            if not ws_checked and ws_type_options and self.batch_ws_type_list.count() == 0:
                ws_checked = set(self._default_batch_ws_types(categories=effective_categories, source_files=source_files))
            self._set_checkable_list_items(self.batch_ws_type_list, ws_type_options, ws_checked)
            self.batch_ws_box.setVisible(self._current_export_mode() == EXPORT_MODE_BATCH and "WebSocket" in effective_categories)
            self.batch_ws_type_list.setEnabled(self.batch_ws_box.isVisible() and bool(source_files))
            self.batch_ws_override.setEnabled(False)
            self.batch_ws_select_all_btn.setEnabled(self.batch_ws_box.isVisible() and bool(source_files) and bool(ws_type_options))
            self.batch_ws_clear_btn.setEnabled(self.batch_ws_box.isVisible() and bool(source_files) and bool(ws_type_options))

            effective_ws = self._effective_batch_ws_types(effective_categories, source_files)
            metric_options = self._available_batch_metrics(
                categories=effective_categories, ws_types=effective_ws, source_files=source_files
            )
            metric_checked = set(self._checked_list_values(self.batch_metric_list)) & set(metric_options)
            if not metric_checked and metric_options and self.batch_metric_list.count() == 0:
                metric_checked = set()
            self._set_checkable_list_items(self.batch_metric_list, metric_options, metric_checked)
            self.batch_metric_list.setEnabled(bool(source_files))
            self.batch_metric_select_all_btn.setEnabled(bool(source_files) and bool(metric_options))
            self.batch_metric_clear_btn.setEnabled(bool(source_files) and bool(metric_options))

            self.batch_plots_box.setVisible(bool(effective_categories))
            active_plot_categories = set()
            for category in effective_categories:
                section = self._ensure_batch_plot_section(category)
                plot_options = self._available_batch_plot_types_for_category(category, source_files=source_files)
                checked = set(self._checked_list_values(section["list"])) & set(plot_options)
                if not checked and plot_options and section["list"].count() == 0:
                    checked = set()
                self._set_checkable_list_items(section["list"], plot_options, checked)
                visible = bool(source_files) and bool(plot_options)
                section["box"].setVisible(visible)
                section["list"].setEnabled(visible)
                section["none_btn"].setEnabled(visible)
                section["all_btn"].setEnabled(visible)
                section["clear_btn"].setEnabled(visible)
                if visible:
                    active_plot_categories.add(category)

            for category, section in self.batch_plot_sections.items():
                if category in active_plot_categories:
                    continue
                section["box"].setVisible(False)
                section["list"].setEnabled(False)
                section["none_btn"].setEnabled(False)
                section["all_btn"].setEnabled(False)
                section["clear_btn"].setEnabled(False)

            if not self.batch_ws_box.isVisible():
                self._set_checkable_list_items(self.batch_ws_type_list, [], [])
            self._update_export_size_controls()
        finally:
            self._batch_refreshing = False

    def _format_labels_text(self, formats):
        return ", ".join(self._format_label(fmt) for fmt in formats)

    def _preview_request_title(self, request, include_category=True):
        parts = []
        if include_category:
            parts.append(request["category"])
        if request["category"] == "WebSocket" and request["ws_type"] not in (None, "", WS_TYPE_ALL):
            parts.append(request["ws_type"])
        parts.append(request["metric"])
        parts.append(request["plot_type"])
        return " | ".join(parts)

    def _current_export_target_preview(self, request, fmt):
        default_path = self._default_save_path(ext=fmt)
        image_path = self._sidecar_bundle_image_path(default_path) if self._title_mode_writes_sidecar() else default_path
        if self._title_mode_writes_sidecar():
            sidecar_preview = self._title_sidecar_path(image_path, fmt)
            if self._current_export_destination_mode() != EXPORT_DEST_ASK:
                return f"{image_path} (+ {os.path.basename(sidecar_preview)})"
            return f"{image_path} (+ {os.path.basename(sidecar_preview)}; file dialog confirms final name)"
        if self._current_export_destination_mode() != EXPORT_DEST_ASK:
            return default_path
        return f"{default_path} (file dialog confirms final name)"

    def _batch_export_path(self, batch_dir, request, fmt, ensure_dirs=True):
        category_slug = self._slugify(request["category"])
        folder = os.path.join(batch_dir, category_slug)
        if request["category"] == "WebSocket" and request["ws_type"] not in (None, "", WS_TYPE_ALL):
            folder = os.path.join(folder, self._slugify(request["ws_type"]))
        name_parts = [self._slugify(request["metric"])]
        if request["category"] == "WebSocket" and request["ws_type"] not in (None, "", WS_TYPE_ALL):
            name_parts.append(self._slugify(request["ws_type"]))
        name_parts.append(self._slugify(request["plot_type"]))
        name_parts.append(f"{len(request['files'])}bench")
        if fmt == ".png":
            name_parts.append(self._png_export_suffix().lstrip("-"))
        path = os.path.join(folder, "-".join([p for p in name_parts if p]) + fmt)
        path = self._sidecar_bundle_image_path(path)
        if ensure_dirs:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        return path

    def _batch_export_target_preview(self, request, fmt):
        root = os.path.join(self._export_root_directory(), "batch-<timestamp>")
        target = self._batch_export_path(root, request, fmt, ensure_dirs=False)
        if self._title_mode_writes_sidecar():
            sidecar_preview = self._title_sidecar_path(target, fmt)
            if self._current_export_destination_mode() == EXPORT_DEST_ASK:
                return f"{target} (+ {os.path.basename(sidecar_preview)}; base folder chosen when export starts)"
            return f"{target} (+ {os.path.basename(sidecar_preview)})"
        if self._current_export_destination_mode() == EXPORT_DEST_ASK:
            return f"{target} (base folder chosen when export starts)"
        return target

    def _build_export_plan(self):
        size_plan = self._resolved_export_size_plan()
        formats = self._selected_export_formats()
        mode = self._current_export_mode()
        sidecar_count = 1 if self._title_mode_writes_sidecar() else 0
        plan = {
            "mode": mode,
            "formats": list(formats),
            "size": size_plan,
            "requests": [],
            "stats": {"candidate_count": 0, "valid_count": 0, "output_count": 0},
            "ready": False,
            "message": "",
            "sample_target": None,
        }
        if not self.files:
            plan["message"] = "Load CSV files to prepare exports."
            return plan
        if not formats:
            plan["message"] = "Select at least one export format."
            return plan
        if mode == EXPORT_MODE_CURRENT:
            request = self._build_current_export_request()
            if not request["files"]:
                plan["message"] = "Current graph export needs at least one checked visible file."
                return plan
            if request.get("metric") in (None, "", METRIC_PLACEHOLDER):
                plan["message"] = "Select a metric before exporting."
                return plan
            pt = request.get("plot_type")
            if pt in (None, "", PLOT_TYPE_PLACEHOLDER) or pt not in HOME_PLOT_TYPE_OPTIONS:
                plan["message"] = "Select a plot type (Multi-line, Heatmap, or Bar) before exporting."
                return plan
            plan["requests"] = [request]
            plan["sample_target"] = self._current_export_target_preview(request, formats[0])
            plan["stats"] = {"candidate_count": 1, "valid_count": 1, "output_count": len(formats) + sidecar_count}
            plan["ready"] = True
            plan["message"] = "Current graph export is ready."
            return plan
        source_files = self._batch_source_files()
        categories = self._effective_batch_categories(source_files)
        ws_types = self._effective_batch_ws_types(categories, source_files)
        metrics = self._effective_batch_metrics(categories, ws_types, source_files)
        if not self._batch_source_files():
            plan["message"] = "Batch export needs at least one source file in the selected data source."
            return plan
        if not metrics:
            plan["message"] = "Select one or more metrics in the Batch Builder."
            return plan
        for cat in categories:
            if not self._effective_batch_plot_types_for_category(cat, source_files=source_files):
                plan["message"] = f"Select at least one plot type for “{cat}” (Multi-line, Heatmap, and/or Bar)."
                return plan
        requests, stats = self._build_batch_requests()
        plan["requests"] = requests
        plan["stats"] = stats
        if requests:
            plan["sample_target"] = self._batch_export_target_preview(requests[0], formats[0])
        if stats["valid_count"] == 0:
            plan["message"] = "No valid batch combinations. Adjust overrides or current plot context."
            return plan
        plan["ready"] = True
        plan["message"] = "Batch export plan is ready."
        return plan

    def _populate_export_plan_tree(self, plan):
        self.export_plan_tree.clear()
        requests = plan["requests"]
        if not requests:
            return
        formats_text = self._format_labels_text(plan["formats"])
        preview_requests = requests[:EXPORT_PREVIEW_LIMIT]
        if plan["mode"] == EXPORT_MODE_CURRENT:
            request = preview_requests[0]
            item = QTreeWidgetItem(
                [
                    self._preview_request_title(request, include_category=True),
                    str(len(request["files"])),
                    f"{len(plan['formats'])} file(s): {formats_text}",
                ]
            )
            self.export_plan_tree.addTopLevelItem(item)
            self.export_plan_tree.expandAll()
            return

        grouped = {}
        for request in preview_requests:
            group_key = request["category"]
            if request["category"] == "WebSocket" and request["ws_type"] not in (None, "", WS_TYPE_ALL):
                group_key = f"{request['category']} / {request['ws_type']}"
            parent = grouped.get(group_key)
            if parent is None:
                parent = QTreeWidgetItem([group_key, "", ""])
                grouped[group_key] = parent
                self.export_plan_tree.addTopLevelItem(parent)
            child = QTreeWidgetItem(
                [
                    self._preview_request_title(request, include_category=False),
                    str(len(request["files"])),
                    f"{len(plan['formats'])} file(s): {formats_text}",
                ]
            )
            parent.addChild(child)
        if len(requests) > len(preview_requests):
            self.export_plan_tree.addTopLevelItem(
                QTreeWidgetItem([f"... and {len(requests) - len(preview_requests)} more planned jobs", "", ""])
            )
        self.export_plan_tree.expandAll()

    def update_metric_options(self):
        visible = self.get_visible_files()
        if not visible:
            self.metric_selector.set_options([])
            return
        metrics = set(get_numeric_columns(self.headers[visible[0]]))
        for f in visible[1:]:
            metrics &= set(get_numeric_columns(self.headers[f]))
        metrics = sorted(metrics)
        old = self.metric_selector.currentText()
        self.metric_selector.set_options(metrics)
        if old in metrics:
            self.metric_selector.set_current(old)
        else:
            self.metric_selector.set_current(METRIC_PLACEHOLDER)

    def _live_plot_ready(self):
        if not self.files:
            return False
        if self.metric_selector.currentText() in (None, "", METRIC_PLACEHOLDER):
            return False
        pt = self.plot_type_selector.currentText()
        if pt in (None, "", PLOT_TYPE_PLACEHOLDER) or pt not in HOME_PLOT_TYPE_OPTIONS:
            return False
        return True

    def get_selected_files(self):
        visible = self.get_visible_files()
        checked = [self.file_listbox.item(i).data(Qt.UserRole)
                   for i in range(self.file_listbox.count())
                   if self.file_listbox.item(i).checkState() == Qt.Checked
                   and self.file_listbox.item(i).data(Qt.UserRole) in visible]
        return checked or visible

    def select_all_files(self):
        self.file_listbox.blockSignals(True)
        for i in range(self.file_listbox.count()):
            item = self.file_listbox.item(i)
            item.setCheckState(Qt.Checked)
            filepath = item.data(Qt.UserRole)
            if filepath:
                self.file_checked_state[filepath] = True
        self.file_listbox.blockSignals(False)
        self.file_listbox.selectAll()
        self._update_file_count_label()
        self._refresh_export_controls()
        self._update_export_preview()

    def deselect_all_files(self):
        self.file_listbox.blockSignals(True)
        for i in range(self.file_listbox.count()):
            item = self.file_listbox.item(i)
            item.setCheckState(Qt.Unchecked)
            filepath = item.data(Qt.UserRole)
            if filepath:
                self.file_checked_state[filepath] = False
        self.file_listbox.blockSignals(False)
        self.file_listbox.clearSelection()
        self._update_file_count_label()
        self._refresh_export_controls()
        self._update_export_preview()

    def _plot_websocket_heatmap(self, selected_files, metric, title_mode=None):
        """Build and draw a heatmap: rows = files, columns = unique x-axis values, color = metric."""
        data_rows = []
        row_labels = []
        all_x_ordered = []
        for f in selected_files:
            header = self.headers[f]
            rows = self.rows[f]
            typ = self.file_types[f]
            x, y, label = self.get_plot_data(header, rows, typ, metric, os.path.basename(f), filepath=f)
            if not x or not y:
                continue
            data_rows.append((x, y))
            row_labels.append(label or os.path.basename(f))
            all_x_ordered.extend(x)
        if not data_rows:
            self._heatmap_vals = []
            return
        # Unique x values in order of first appearance (or sorted if numeric)
        seen = set()
        x_unique = []
        for v in all_x_ordered:
            try:
                vf = float(v)
                if vf not in seen:
                    seen.add(vf)
                    x_unique.append(vf)
            except (TypeError, ValueError):
                if v not in seen:
                    seen.add(v)
                    x_unique.append(v)
        try:
            x_unique = sorted(x_unique)
        except TypeError:
            pass
        if not x_unique:
            self._heatmap_vals = []
            return
        x_to_col = {xv: j for j, xv in enumerate(x_unique)}
        n_rows = len(data_rows)
        n_cols = len(x_unique)
        matrix = np.full((n_rows, n_cols), np.nan)
        heatmap_vals = []
        for i, (x_list, y_list) in enumerate(data_rows):
            for xv, yv in zip(x_list, y_list):
                j = x_to_col.get(xv)
                if j is not None and yv is not None:
                    try:
                        matrix[i, j] = float(yv)
                        heatmap_vals.append(float(yv))
                    except (TypeError, ValueError):
                        pass
        self._heatmap_vals = heatmap_vals
        if not heatmap_vals:
            return
        base_style = self._current_plot_style()
        style = self._scaled_plot_style(base_style)
        im = self.ax.imshow(matrix, aspect="auto", cmap=style["heatmap_cmap"], interpolation="nearest")
        self.ax.set_xticks(np.arange(n_cols))
        self.ax.set_xticklabels([str(x_unique[j]) for j in range(n_cols)], rotation=45, ha="right")
        self.ax.set_yticks(np.arange(n_rows))
        self.ax.set_yticklabels(row_labels)
        first_file = selected_files[0]
        x_col = self.get_x_axis_column_name(self.headers[first_file], self.rows[first_file], "websocket", filepath=first_file)
        x_axis_label = XAXIS_DISPLAY_NAMES.get(x_col, x_col) if x_col else "Parameter"
        self.ax.set_xlabel(x_axis_label)
        self.ax.set_ylabel("Benchmark")
        titles = self._resolve_plot_titles(selected_files, metric, x_axis_label, heatmap=True)
        self._apply_plot_chrome(
            self._export_display_title(titles, title_mode=title_mode),
            0,
            has_colorbar=True,
            compact_title=(title_mode == EXPORT_TITLE_GRAPH),
        )
        # Use a separate fixed axes for colorbar so main ax is never shrunk (no shrink on repeated plot)
        cax = self.fig.add_axes(style["cbar_rect"])
        cbar = self.fig.colorbar(im, cax=cax, label=metric)
        cbar.ax.tick_params(labelsize=style["tick_size"], colors=style["tick_color"])
        cbar.outline.set_edgecolor(style["spine_color"])
        cbar.outline.set_linewidth(0.9)
        cbar.set_label(metric, color=TEXT, size=style["label_size"])

    def _clear_plot_artists(self):
        self.ax.clear()
        if getattr(self.fig, "_suptitle", None) is not None:
            self.fig._suptitle.remove()
        self.fig.legends.clear()
        if self._axes_leave_cid is not None:
            try:
                self.canvas.mpl_disconnect(self._axes_leave_cid)
            except Exception:
                pass
            self._axes_leave_cid = None
        # Remove every axes that is not the main plot (colorbar etc.) so none accumulate
        while True:
            extra = [ax for ax in self.fig.axes if ax is not self.ax]
            if not extra:
                break
            for ax in extra:
                ax.remove()
        self.ax.set_position(self._scaled_plot_style(self._current_plot_style())["axes_rect"])
        if hasattr(self, 'cursor') and self.cursor is not None:
            try:
                self.cursor.remove()
            except Exception:
                pass
            self.cursor = None
        if hasattr(self, 'bar_cursor') and self.bar_cursor is not None:
            try:
                self.bar_cursor.remove()
            except Exception:
                pass
            self.bar_cursor = None

    def _apply_plot_chrome(self, title, series_count, has_colorbar=False, compact_title=False):
        plot_kind = "bar" if self.ax.containers else "line"
        style = self._tuned_plot_style_for_series_count(
            self._scaled_plot_style(self._current_plot_style()),
            max(1, series_count),
            plot_kind=plot_kind,
        )
        handles, labels = self.ax.get_legend_handles_labels()
        legend_count = len(handles)
        ncol = self._legend_columns_for_plot(legend_count, style) if legend_count else 1
        legend_layout = self._legend_layout_for_plot(
            legend_count,
            ncol,
            style,
            has_colorbar=has_colorbar,
            plot_kind=plot_kind,
        )
        header_center_x = legend_layout["axes_rect"][0] + (legend_layout["axes_rect"][2] / 2.0)
        self.ax.set_position(legend_layout["axes_rect"])
        self.ax.set_facecolor(style["axes_facecolor"])
        self.fig.patch.set_facecolor(style["figure_facecolor"])
        for spine in self.ax.spines.values():
            spine.set_color(style["spine_color"])
            spine.set_linewidth(1.0)
        self.ax.tick_params(axis="both", labelsize=style["tick_size"], colors=style["tick_color"])
        self.ax.xaxis.label.set_size(style["label_size"])
        self.ax.yaxis.label.set_size(style["label_size"])
        self.ax.xaxis.label.set_color(TEXT)
        self.ax.yaxis.label.set_color(TEXT)
        title_size = style["label_size"] if compact_title else style["title_size"]
        title_weight = "normal" if compact_title else "semibold"
        title_artist = None
        if legend_layout["mode"] == "band":
            self.ax.set_title("")
            if title:
                title_artist = self.fig.suptitle(
                    title,
                    fontsize=title_size,
                    fontweight=title_weight,
                    color=TEXT,
                    x=header_center_x,
                    y=style["figure_title_y"],
                )
        else:
            self.ax.set_title(title, fontsize=title_size, fontweight=title_weight, color=TEXT, pad=style["title_pad"])
        if not legend_count:
            return
        legend_kwargs = dict(
            loc="upper center" if legend_layout["mode"] == "band" else "upper left",
            bbox_to_anchor=(header_center_x, style["figure_legend_y"]) if legend_layout["mode"] == "band" else (0.012, 0.995),
            bbox_transform=self.fig.transFigure if legend_layout["mode"] == "band" else self.ax.transAxes,
            ncol=ncol,
            framealpha=0.93,
            fontsize=style["legend_fontsize"],
            borderaxespad=0.0,
            columnspacing=style["legend_columnspacing"],
            handlelength=style["legend_handlelength"],
            labelspacing=style["legend_labelspacing"],
            facecolor=style["legend_facecolor"],
            edgecolor=style["legend_edgecolor"],
        )
        if legend_layout["mode"] == "band":
            legend_artist = self.fig.legend(handles, labels, **legend_kwargs)
            self.ax.set_position(
                self._fit_axes_below_header_band(
                    legend_layout["axes_rect"],
                    title_artist=title_artist,
                    legend_artist=legend_artist,
                )
            )
        else:
            self.ax.legend(handles, labels, **legend_kwargs)

    def _apply_series_legend(self, series_count, title, compact_title=False):
        self._apply_plot_chrome(title, series_count, compact_title=compact_title)

    def _attach_plot_interactivity(self, metric):
        lines = [line for line in self.ax.get_lines() if not str(line.get_label()).startswith("_")]
        bars = [bar for cont in self.ax.containers for bar in cont]
        series_count = max(
            len(lines),
            len({bar.get_gid() for bar in bars if bar.get_gid()}),
            1,
        )
        plot_kind = "line" if lines else "bar"
        style = self._tuned_plot_style_for_series_count(
            self._scaled_plot_style(self._current_plot_style()),
            series_count,
            plot_kind=plot_kind,
        )

        def reset_lines():
            for line in lines:
                self._apply_line_base_style(line, style)

        def reset_bars():
            for bar in bars:
                self._apply_bar_base_style(bar, style)

        try:
            if lines:
                self.cursor = mplcursors.cursor(
                    lines,
                    hover=True,
                    highlight=False,
                    annotation_kwargs={
                        'fontsize': style["annotation_fontsize"],
                        'arrowprops': dict(arrowstyle="->", color="#333", lw=1.2),
                        'bbox': dict(boxstyle="round,pad=0.2", fc=style["annotation_facecolor"], ec=style["annotation_edgecolor"], lw=0.8),
                    },
                )
                @self.cursor.connect("add")
                def on_add(sel):
                    reset_lines()
                    reset_bars()
                    for line in lines:
                        line.set_alpha(style["dim_alpha"])
                    sel.artist.set_linewidth(getattr(sel.artist, "_base_linewidth", style["line_width"]) + 1.6)
                    sel.artist.set_alpha(1.0)
                    sel.artist.set_markersize(getattr(sel.artist, "_base_markersize", style["marker_size"]) + 1.0)
                    sel.artist.set_zorder(style["line_hover_zorder"])
                    x_val = y_val = None
                    if getattr(sel, "target", None) is not None and len(sel.target) > 1:
                        x_val = sel.target[0]
                        y_val = sel.target[1]
                    annotation_lines = [sel.artist.get_label()]
                    if x_val is not None:
                        annotation_lines.append(f"x = {self._format_hover_value(x_val)}")
                    if y_val is not None:
                        annotation_lines.append(f"y = {self._format_hover_value(y_val)}")
                    sel.annotation.set_text("\n".join(annotation_lines))
                    for ann in self.ax.texts:
                        if ann is not sel.annotation:
                            ann.set_visible(False)
                    self.canvas.draw_idle()
                @self.cursor.connect("remove")
                def on_remove(_):
                    reset_lines()
                    for ann in self.ax.texts:
                        ann.set_visible(False)
                    self.canvas.draw_idle()

            if bars:
                self.bar_cursor = mplcursors.cursor(
                    bars,
                    hover=True,
                    highlight=False,
                    annotation_kwargs={
                        'fontsize': style["annotation_fontsize"],
                        'arrowprops': dict(arrowstyle="->", color="#333", lw=1.2),
                        'bbox': dict(boxstyle="round,pad=0.2", fc=style["annotation_facecolor"], ec=style["annotation_edgecolor"], lw=0.8),
                    },
                )
                @self.bar_cursor.connect("add")
                def on_bar_add(sel):
                    reset_lines()
                    reset_bars()
                    target = sel.artist
                    target.set_linewidth(style["bar_hover_linewidth"])
                    target.set_edgecolor(style["bar_hover_edgecolor"])
                    target.set_alpha(1.0)
                    target.set_zorder(style["bar_hover_zorder"])
                    x_val = target.get_x() + (target.get_width() / 2.0)
                    y_val = target.get_height()
                    label = target.get_gid() or ""
                    sel.annotation.set_text(
                        f"{label}\n"
                        f"x = {self._format_hover_value(x_val)}\n"
                        f"{metric} = {self._format_hover_value(y_val)}"
                    )
                    for ann in self.ax.texts:
                        if ann is not sel.annotation:
                            ann.set_visible(False)
                    self.canvas.draw_idle()
                @self.bar_cursor.connect("remove")
                def on_bar_remove(_):
                    reset_bars()
                    for ann in self.ax.texts:
                        ann.set_visible(False)
                    self.canvas.draw_idle()
        except Exception:
            pass

        def on_leave(_event):
            reset_lines()
            reset_bars()
            for ann in self.ax.texts:
                ann.set_visible(False)
            self.canvas.draw_idle()
        self._axes_leave_cid = self.canvas.mpl_connect('axes_leave_event', on_leave)

    def _render_plot(self, selected_files, metric, type_choice, enable_interactivity=True, title_mode=None):
        if not metric or metric == METRIC_PLACEHOLDER:
            return False
        if not selected_files:
            return False
        if type_choice in (None, "", PLOT_TYPE_PLACEHOLDER) or type_choice not in HOME_PLOT_TYPE_OPTIONS:
            return False
        is_websocket = selected_files and all(self.file_types.get(f) == "websocket" for f in selected_files)
        if type_choice == WS_PLOT_HEATMAP and not is_websocket:
            return False
        self._clear_plot_artists()

        all_vals = []
        n_series = max(1, len(selected_files))
        plot_kind = "bar" if type_choice == WS_PLOT_BAR else "line"
        style = self._tuned_plot_style_for_series_count(
            self._scaled_plot_style(self._current_plot_style()),
            n_series,
            plot_kind=plot_kind,
        )

        if type_choice == WS_PLOT_HEATMAP:
            self._plot_websocket_heatmap(selected_files, metric, title_mode=title_mode)
            self.canvas.draw()
            vals = getattr(self, "_heatmap_vals", None) or []
            if vals:
                s = f"{metric}: min={min(vals):.2f}, max={max(vals):.2f}, avg={sum(vals)/len(vals):.2f}"
                self.summary_label.setText(s)
                return True
            self.summary_label.setText("")
            return False

        # WebSocket bar plot: group all series by shared x categories (e.g., Num Clients)
        # so bars are aligned per category across servers.
        if is_websocket and type_choice == WS_PLOT_BAR:
            prepared = []
            for idx, f in enumerate(selected_files):
                header = self.headers[f]
                rows = self.rows[f]
                typ = self.file_types[f]
                x, y, label = self.get_plot_data(header, rows, typ, metric, os.path.basename(f), filepath=f)
                if x and y:
                    prepared.append((idx, label, x, y))
                    all_vals.extend(y)

            if not prepared:
                self.summary_label.setText("")
                return False

            # Build one shared sorted x-axis from all series so bars are truly grouped.
            x_unique = sorted(
                {
                    float(xv)
                    for _, _, x_vals, _ in prepared
                    for xv in x_vals
                    if isinstance(xv, (int, float, np.integer, np.floating))
                }
            )
            if not x_unique:
                self.summary_label.setText("")
                return False

            x_to_idx = {xv: i for i, xv in enumerate(x_unique)}
            base = np.arange(len(x_unique), dtype=float)
            # Keep websocket grouped bars compact and stable across series count.
            plotted_series = max(1, len(prepared))
            style = self._tuned_plot_style_for_series_count(
                self._scaled_plot_style(self._current_plot_style()),
                plotted_series,
                plot_kind=plot_kind,
            )
            group_width = 0.8
            bar_width = group_width / plotted_series

            for plotted_idx, (idx, label, x_vals, y_vals) in enumerate(prepared):
                series_style = self._series_style_for_index(idx, style)
                offset = (plotted_idx - (plotted_series - 1) / 2) * bar_width
                y_aligned = np.full(len(x_unique), np.nan, dtype=float)
                for xv, yv in zip(x_vals, y_vals):
                    if isinstance(xv, (int, float, np.integer, np.floating)):
                        x_key = float(xv)
                        if x_key in x_to_idx:
                            y_aligned[x_to_idx[x_key]] = float(yv)
                container = self.ax.bar(
                    base + offset,
                    y_aligned,
                    width=bar_width,
                    label=label,
                    color=series_style["color"],
                    alpha=style["bar_alpha"],
                    edgecolor=style["bar_edgecolor"],
                    linewidth=style["bar_linewidth"],
                    zorder=2 + idx,
                )
                for bar in container:
                    bar.set_gid(label)
                    bar._base_linewidth = style["bar_linewidth"]
                    bar._base_edgecolor = style["bar_edgecolor"]
                    bar._base_alpha = style["bar_alpha"]
                    bar._base_zorder = 2 + idx

            self.ax.set_xticks(base)
            self.ax.set_xticklabels(
                [str(int(v)) if float(v).is_integer() else str(v) for v in x_unique]
            )
            self.ax.set_xlim(-0.5, len(x_unique) - 0.5)
            self.ax.set_xlabel("Number of clients")
            self.ax.set_ylabel(metric)
            titles = self._resolve_plot_titles(prepared and selected_files or [], metric, "Number of clients")
            self._apply_series_legend(
                len(prepared),
                self._export_display_title(titles, title_mode=title_mode),
                compact_title=(title_mode == EXPORT_TITLE_GRAPH),
            )
            self.ax.grid(True, color=style["grid_color"], linewidth=style["grid_width"], alpha=0.9)
            self.canvas.draw()
            if enable_interactivity:
                self._attach_plot_interactivity(metric)

            if all_vals:
                s = f"{metric}: min={min(all_vals):.2f}, max={max(all_vals):.2f}, avg={sum(all_vals)/len(all_vals):.2f}"
                self.summary_label.setText(s)
            else:
                self.summary_label.setText("")
            return True

        for idx, f in enumerate(selected_files):
            header = self.headers[f]
            rows = self.rows[f]
            typ = self.file_types[f]
            x, y, label = self.get_plot_data(header, rows, typ, metric, os.path.basename(f), filepath=f)
            if x and y:
                series_style = self._series_style_for_index(idx, style)
                use_bar = type_choice == WS_PLOT_BAR
                if use_bar:
                    n_points = len(x)
                    if isinstance(x[0], (int, float, np.integer, np.floating)):
                        x_arr = np.array(x, dtype=float)
                        x_min, x_max = x_arr.min(), x_arr.max()
                        x_range = (x_max - x_min) if (x_max > x_min) else (x_max or 1.0)
                        slot_width = x_range / max(n_points, 1)
                        bar_width = max(slot_width * 0.4, x_range * 0.02)
                        bar_width = min(bar_width, slot_width * 0.9 / n_series)
                        bar_width *= getattr(self, "bar_width_scale", 1.0)
                        x_vals = x_arr + (idx - (n_series - 1) / 2) * bar_width
                    else:
                        slot_width = 1.0
                        bar_width = max(0.15, 0.8 / n_series)
                        bar_width *= getattr(self, "bar_width_scale", 1.0)
                        x_vals = np.arange(len(x)) + (idx - (n_series - 1) / 2) * bar_width
                        self.ax.set_xticks(np.arange(len(x)))
                        self.ax.set_xticklabels([str(v) for v in x])
                    container = self.ax.bar(
                        x_vals,
                        y,
                        width=bar_width,
                        label=label,
                        color=series_style["color"],
                        alpha=style["bar_alpha"],
                        edgecolor=style["bar_edgecolor"],
                        linewidth=style["bar_linewidth"],
                        zorder=2 + idx,
                    )
                    for bar in container:
                        bar.set_gid(label)
                        bar._base_linewidth = style["bar_linewidth"]
                        bar._base_edgecolor = style["bar_edgecolor"]
                        bar._base_alpha = style["bar_alpha"]
                        bar._base_zorder = 2 + idx
                    for xv, val in zip(x_vals, y):
                        if val == 0:
                            self.ax.plot(
                                [xv - bar_width/2, xv + bar_width/2],
                                [0, 0],
                                color=series_style["color"],
                                linewidth=max(1.0, round(style["line_width"] * 0.9, 2)),
                                alpha=0.85,
                                solid_capstyle='butt',
                                label="_nolegend_",
                                zorder=1,
                            )
                else:
                    lw = style["line_width"] * (1.0 + 0.026 * (idx % 6))
                    line, = self.ax.plot(
                        x,
                        y,
                        label=label,
                        color=series_style["color"],
                        marker=series_style["marker"],
                        linestyle=series_style["linestyle"],
                        linewidth=lw,
                        markersize=style["marker_size"],
                        markerfacecolor=series_style["markerfacecolor"],
                        markeredgecolor=series_style["markeredgecolor"],
                        markeredgewidth=series_style["markeredgewidth"],
                        fillstyle=series_style["fillstyle"],
                        zorder=2 + idx,
                    )
                    line._base_linewidth = lw
                    line._base_markersize = style["marker_size"]
                    line._base_alpha = 1.0
                    line._base_zorder = 2 + idx
                all_vals.extend(y)

        if selected_files:
            x_col = self.get_x_axis_column_name(self.headers[selected_files[0]], self.rows[selected_files[0]], self.file_types[selected_files[0]], filepath=selected_files[0])
            x_axis_label = XAXIS_DISPLAY_NAMES.get(x_col, x_col) if x_col else "Test parameter"
        else:
            x_axis_label = "Test parameter"
        self.ax.set_xlabel(x_axis_label)
        self.ax.set_ylabel(metric)
        titles = self._resolve_plot_titles(selected_files, metric, x_axis_label)
        self._apply_series_legend(
            len(selected_files),
            self._export_display_title(titles, title_mode=title_mode),
            compact_title=(title_mode == EXPORT_TITLE_GRAPH),
        )
        self.ax.grid(True, color=style["grid_color"], linewidth=style["grid_width"], alpha=0.9)
        self.canvas.draw()
        if all_vals:
            s = f"{metric}: min={min(all_vals):.2f}, max={max(all_vals):.2f}, avg={sum(all_vals)/len(all_vals):.2f}"
            self.summary_label.setText(s)
        else:
            self.summary_label.setText("")

        if not enable_interactivity:
            return True
        self._attach_plot_interactivity(metric)
        return True

    def plot_selected(self):
        if not self._live_plot_ready():
            self.last_plotted_request = None
            self._clear_plot_artists()
            self.canvas.draw()
            self.summary_label.setText("")
            self._update_export_preview()
            return
        selected_files = self.get_selected_files()
        metric = self.metric_selector.currentText()
        type_choice = self.plot_type_selector.currentText()
        plotted = self._render_plot(selected_files, metric, type_choice, enable_interactivity=True)
        if plotted:
            category = self.category_selector.currentText()
            self.last_plotted_request = {
                "category": category,
                "ws_type": self._current_request_ws_type(category, source_files=self.get_visible_files()),
                "metric": metric,
                "plot_style": self._current_plot_style_mode(),
                "plot_type": type_choice,
                "files": list(selected_files),
            }
        else:
            self.last_plotted_request = None
        self._update_export_preview()

    def get_plot_data(self, header, rows, typ, metric, label, filepath=None):
        if not rows:
            return [], [], label
        header_list = list(header) if hasattr(header, "__iter__") and not isinstance(header, dict) else list(header.keys()) if isinstance(header, dict) else []
        file_basename = os.path.splitext(os.path.basename(label))[0] if isinstance(label, str) else ""
        if header_list and "Container Name" in header_list and rows and _row_value(rows[0], "Container Name", header_list) not in (None, ""):
            container_name = str(_row_value(rows[0], "Container Name", header_list)).strip()
            if file_basename and file_basename != container_name and container_name in file_basename:
                label = file_basename
            else:
                label = container_name
        else:
            label = file_basename or label
        if typ == "websocket":
            subtype = self.file_ws_subtypes.get(filepath) if filepath and getattr(self, "file_ws_subtypes", None) else None
            xcol = websocket_xaxis_column(header_list, rows, subtype)
            if xcol:
                x = [safe_float(_row_value(r, xcol, header_list)) for r in rows]
                y = [safe_float(_row_value(r, metric, header_list)) for r in rows]
            else:
                x = list(range(1, len(rows) + 1))
                y = [safe_float(_row_value(r, metric, header_list)) for r in rows]
            if not x:
                x = list(range(1, len(rows) + 1))
            if not y:
                y = [0.0] * len(rows)
            return x, y, label
        if header_list and "Total Requests" in header_list:
            x = [safe_float(_row_value(r, "Total Requests", header_list)) for r in rows]
            y = [safe_float(_row_value(r, metric, header_list)) for r in rows]
        else:
            x = list(range(1, len(rows) + 1))
            y = [safe_float(_row_value(r, metric, header_list)) for r in rows]
        if not y:
            y = [0.0] * len(rows)
        return x, y, label

    def get_x_axis_column_name(self, header, rows, typ, filepath=None):
        if typ == "websocket":
            subtype = self.file_ws_subtypes.get(filepath) if filepath and getattr(self, 'file_ws_subtypes', None) else None
            xcol = websocket_xaxis_column(header, rows, subtype)
            if xcol:
                return xcol
            return "Test Parameter"
        if "Total Requests" in header:
            return "Total Requests"
        return "Test Parameter"

    def _default_save_stem(self):
        """Default path stem: graphs/<category>/<metric>[-<ws-subtype>]-<N>bench-<YYYYMMDD-HHMM>."""
        request = self.last_plotted_request if self.last_plotted_request else {}
        metric = request.get("metric") or self.metric_selector.currentText() or "graph"
        if metric == METRIC_PLACEHOLDER:
            metric = "graph"
        slug = re.sub(r"[^\w\s-]", "", metric).strip().lower()
        slug = re.sub(r"[-\s]+", "-", slug) or "graph"
        n = len(request.get("files") or self.get_selected_files())
        ts = datetime.now().strftime("%Y%m%d-%H%M")
        cat = (request.get("category") or self.category_selector.currentText() or "").strip()
        cat_lower = cat.lower().replace(" ", "")
        subtype_part = ""
        if cat_lower == "websocket" and getattr(self, "ws_type_selector", None):
            ws_type = request.get("ws_type") or self.ws_type_selector.currentText() or WS_TYPE_PLACEHOLDER
            acronym = WS_TYPE_SAVE_ACRONYM.get(ws_type, "all")
            subtype_part = f"-{acronym}"
        name = f"{slug}{subtype_part}-{n}bench-{ts}"
        root = self._export_root_directory()
        if cat and cat_lower not in ("all", "", BENCHMARK_TYPE_PLACEHOLDER.lower().replace(" ", "")):
            base = os.path.join(root, cat_lower)
        else:
            base = root
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, name)

    def _default_save_path(self, ext=".png", suffix=""):
        return self._path_for_export_format(self._default_save_stem(), ext, suffix=suffix)

    def _path_for_export_format(self, stem, fmt, suffix=None):
        suffix = self._png_export_suffix() if suffix is None and fmt == ".png" else (suffix or "")
        return f"{stem}{suffix}{fmt}"

    def _title_sidecar_path(self, filepath, fmt=None):
        stem, ext = os.path.splitext(filepath)
        fmt = fmt or ext.lower()
        if fmt == ".png":
            png_suffix = self._png_export_suffix()
            if png_suffix and stem.endswith(png_suffix):
                stem = stem[: -len(png_suffix)]
        return f"{stem}.title.txt"

    def _request_x_axis_label(self, request):
        files = request.get("files", [])
        if not files:
            return "Test parameter"
        first_file = files[0]
        if request.get("plot_type") == WS_PLOT_BAR and all(self.file_types.get(f) == "websocket" for f in files):
            return "Number of clients"
        x_col = self.get_x_axis_column_name(
            self.headers[first_file],
            self.rows[first_file],
            self.file_types[first_file],
            filepath=first_file,
        )
        return XAXIS_DISPLAY_NAMES.get(x_col, x_col) if x_col else "Test parameter"

    def _request_title_bundle(self, request):
        files = request.get("files", [])
        metric = request.get("metric", "Graph")
        is_heatmap = request.get("plot_type") == WS_PLOT_HEATMAP
        x_axis_label = self._request_x_axis_label(request)
        return self._resolve_plot_titles(files, metric, x_axis_label, heatmap=is_heatmap)

    def _title_sidecar_contents(self, request):
        titles = self._request_title_bundle(request)
        lines = [titles["compact"]]
        if titles["full"] != titles["compact"]:
            lines.extend(["", f"Detailed title: {titles['full']}"])
        lines.extend([
            "",
            f"Metric: {request.get('metric', '')}",
            f"Plot type: {request.get('plot_type', '')}",
            f"Category: {request.get('category', '')}",
        ])
        if request.get("ws_type"):
            lines.append(f"WebSocket subtype: {request.get('ws_type')}")
        lines.append(f"Benchmarks: {len(request.get('files', []))}")
        return "\n".join(lines).rstrip() + "\n"

    def _write_title_sidecar(self, filepath, request):
        try:
            with open(filepath, "w", encoding="utf-8") as handle:
                handle.write(self._title_sidecar_contents(request))
            return True
        except OSError as exc:
            QMessageBox.warning(self, "Could not save title file", f"Failed to save title sidecar:\n{filepath}\n\n{exc}")
            return False

    def _format_label(self, fmt):
        return fmt.lstrip(".").upper()

    def _graph_has_data(self):
        _has_data = getattr(self.ax, 'has_data', None)
        return _has_data() if callable(_has_data) else (len(self.ax.lines) + len(self.ax.containers)) > 0

    def _selected_png_dpi(self):
        try:
            return int(self.export_dpi_selector.currentText() or "300")
        except (TypeError, ValueError):
            return 300

    def _png_export_suffix(self):
        dpi_part = f"-{self._selected_png_dpi()}dpi"
        compress_part = "-compressed" if self.compress_png_checkbox.isChecked() else ""
        return f"{dpi_part}{compress_part}"

    def _export_save_kwargs(self, fmt, dpi=None):
        save_kwargs = {"format": fmt.lstrip(".")}
        if self.export_trim_whitespace_checkbox.isChecked():
            profile = self._figure_layout_profile()
            pad_inches = 0.1
            if profile == "single-column":
                pad_inches = 0.035
            elif profile == "full-width":
                pad_inches = 0.06
            save_kwargs.update({"bbox_inches": "tight", "pad_inches": pad_inches})
        if dpi is not None:
            save_kwargs["dpi"] = dpi
        return save_kwargs

    def _save_current_figure(self, filepath, fmt, size_inches=None):
        original_geometry = None
        if size_inches is not None:
            original_geometry = self._capture_figure_geometry()
            self.fig.set_size_inches(*size_inches, forward=False)
        try:
            parent = os.path.dirname(os.path.abspath(filepath))
            if parent:
                os.makedirs(parent, exist_ok=True)
            if fmt == ".png":
                ok = self._save_png(filepath, compressed=self.compress_png_checkbox.isChecked())
                if ok is False:
                    return False
                return True
            self.fig.savefig(filepath, **self._export_save_kwargs(fmt))
            return True
        finally:
            if original_geometry is not None:
                self._restore_figure_geometry(original_geometry)

    def _render_request_for_export(self, request, size_inches):
        if size_inches is not None:
            self.fig.set_size_inches(*size_inches, forward=False)
        plot_type = request.get("plot_type")
        if plot_type not in HOME_PLOT_TYPE_OPTIONS:
            return False
        plotted = self._render_plot(
            request.get("files", []),
            request.get("metric", METRIC_PLACEHOLDER),
            plot_type,
            enable_interactivity=False,
            title_mode=self._current_export_title_mode(),
        )
        self.canvas.draw()
        return plotted

    def _save_png(self, filepath, compressed=False):
        selected_dpi = self._selected_png_dpi()
        save_kwargs = self._export_save_kwargs(".png", dpi=selected_dpi)
        save_kwargs["pil_kwargs"] = {"optimize": True, "compress_level": 9}
        if not compressed:
            self.fig.savefig(filepath, **save_kwargs)
            return True

        try:
            from PIL import Image
        except ImportError:
            QMessageBox.warning(
                self,
                "Missing dependency",
                "Compressed PNG export requires Pillow. Run make setup or make install first.",
            )
            return False

        buf = BytesIO()
        self.fig.savefig(buf, **save_kwargs)
        buf.seek(0)

        with Image.open(buf) as img:
            if "A" in img.getbands():
                flattened = Image.new("RGB", img.size, "white")
                flattened.paste(img, mask=img.getchannel("A"))
            else:
                flattened = img.convert("RGB")
            dither_none = getattr(getattr(Image, "Dither", Image), "NONE", 0)
            compressed_img = flattened.quantize(colors=256, dither=dither_none)
            compressed_img.save(
                filepath,
                format="PNG",
                optimize=True,
                compress_level=9,
                dpi=(selected_dpi, selected_dpi),
            )
        return True

    def get_visible_files(self):
        filter_cat = self.category_selector.currentText()
        if filter_cat in (None, "", BENCHMARK_TYPE_PLACEHOLDER, "All"):
            base = list(self.files)
        else:
            base = [f for f in self.files if self.file_categories.get(f, "Unknown") == filter_cat]
        if filter_cat != "WebSocket":
            return base
        ws_type = self.ws_type_selector.currentText() if getattr(self, "ws_type_selector", None) else WS_TYPE_PLACEHOLDER
        if not ws_type or ws_type in (WS_TYPE_PLACEHOLDER, WS_TYPE_ALL):
            return base
        out = []
        for f in base:
            sub = self.file_ws_subtypes.get(f)
            if ws_type == WS_TYPE_CONCURRENCY and sub == WS_SUBTYPE_CONCURRENCY:
                out.append(f)
            elif ws_type == WS_TYPE_PAYLOAD and sub == WS_SUBTYPE_PAYLOAD:
                out.append(f)
            elif ws_type == WS_TYPE_BURST and sub == WS_SUBTYPE_BURST:
                out.append(f)
            elif ws_type == WS_TYPE_STREAM and sub == WS_SUBTYPE_STREAM:
                out.append(f)
            else:
                pass
        return out

    def _valid_metrics_for_files(self, files):
        if not files:
            return []
        metrics = set(get_numeric_columns(self.headers[files[0]]))
        for f in files[1:]:
            metrics &= set(get_numeric_columns(self.headers[f]))
        return sorted(metrics)

    def _filter_batch_files(self, source_files, category, ws_type=None):
        files = [f for f in source_files if self.file_categories.get(f) == category]
        if category != "WebSocket":
            return files
        if ws_type in (None, "", WS_TYPE_ALL):
            return files
        subtype_map = {
            WS_TYPE_CONCURRENCY: WS_SUBTYPE_CONCURRENCY,
            WS_TYPE_PAYLOAD: WS_SUBTYPE_PAYLOAD,
            WS_TYPE_BURST: WS_SUBTYPE_BURST,
            WS_TYPE_STREAM: WS_SUBTYPE_STREAM,
        }
        wanted = subtype_map.get(ws_type)
        return [f for f in files if self.file_ws_subtypes.get(f) == wanted]

    def _valid_plot_types_for_category(self, category, source_files=None):
        if category == "WebSocket":
            return list(HOME_PLOT_TYPE_OPTIONS)
        files = source_files or []
        if category in (None, "", BENCHMARK_TYPE_PLACEHOLDER, "All") and files and all(
            self.file_types.get(f) == "websocket" for f in files
        ):
            return list(HOME_PLOT_TYPE_OPTIONS)
        return [p for p in HOME_PLOT_TYPE_OPTIONS if p != WS_PLOT_HEATMAP]

    def _current_request_ws_type(self, category, source_files=None):
        if category != "WebSocket":
            return None
        return self.ws_type_selector.currentText()

    def _build_current_export_request(self):
        category = self.category_selector.currentText()
        ws_type = self._current_request_ws_type(category, source_files=self.get_visible_files())
        live_request = {
            "category": category,
            "ws_type": ws_type,
            "metric": self.metric_selector.currentText(),
            "plot_type": self.plot_type_selector.currentText(),
            "plot_style": self._current_plot_style_mode(),
            "files": list(self.get_selected_files()),
        }
        if self.last_plotted_request and self._live_plot_ready():
            cached = dict(self.last_plotted_request)
            if (
                cached.get("category") == live_request["category"]
                and cached.get("ws_type") == live_request["ws_type"]
                and cached.get("metric") == live_request["metric"]
                and cached.get("plot_type") == live_request["plot_type"]
                and cached.get("plot_style") == live_request["plot_style"]
                and list(cached.get("files", [])) == live_request["files"]
            ):
                return cached
        return live_request

    def _build_batch_requests(self):
        source_files = self._batch_source_files()
        categories = self._effective_batch_categories(source_files)
        ws_types = self._effective_batch_ws_types(categories, source_files)
        metrics = self._effective_batch_metrics(categories, ws_types, source_files)
        formats = self._selected_export_formats()
        if not source_files or not categories or not metrics or not formats:
            return [], {"candidate_count": 0, "valid_count": 0, "output_count": 0}
        requests = []
        candidate_count = 0
        seen = set()

        for category in categories:
            plot_candidates = [
                p for p in self._effective_batch_plot_types_for_category(category, source_files=source_files)
                if p in HOME_PLOT_TYPE_OPTIONS
            ]
            if category == "WebSocket":
                subtype_candidates = ws_types
            else:
                subtype_candidates = [None]
            for ws_type in subtype_candidates:
                files = self._filter_batch_files(source_files, category, ws_type)
                valid_metrics = set(self._valid_metrics_for_files(files))
                for metric in metrics:
                    for plot_type in plot_candidates:
                        candidate_count += 1
                        if not files or metric not in valid_metrics:
                            continue
                        key = (category, ws_type or "", metric, plot_type)
                        if key in seen:
                            continue
                        seen.add(key)
                        requests.append(
                            {
                                "category": category,
                                "ws_type": ws_type,
                                "metric": metric,
                                "plot_type": plot_type,
                                "files": list(files),
                            }
                        )
        return requests, {
            "candidate_count": candidate_count,
            "valid_count": len(requests),
            "output_count": len(requests) * (len(formats) + (1 if self._title_mode_writes_sidecar() else 0)),
        }

    def _update_export_preview(self, *_args):
        if self._batch_refreshing:
            return
        if not getattr(self, "export_plan_summary_label", None):
            return
        self._update_export_size_controls()
        size_default_label, size_resolved_label = self._resolved_export_size_text()
        self.export_size_source_label.setText(f"{size_default_label}\n{size_resolved_label}")
        plan = self._build_export_plan()
        source_label = self._current_export_source_mode() if plan["mode"] == EXPORT_MODE_BATCH else "Current visualization"
        self.export_plan_summary_label.setText(
            f"{plan['mode']}: {plan['stats']['valid_count']} graph job(s), "
            f"{plan['stats']['output_count']} output file(s), source {source_label}, "
            f"formats {self._format_labels_text(plan['formats']) or 'none'}, "
            f"title mode {self._current_export_title_mode().lower()}."
        )
        self.export_sample_path_label.setText(plan["sample_target"] or "A sample output path will appear here.")
        self._populate_export_plan_tree(plan)
        self.export_run_btn.setEnabled(plan["ready"])
        if plan["ready"]:
            dest_mode = self._current_export_destination_mode()
            if dest_mode == EXPORT_DEST_ASK:
                destination_label = "dialog-confirmed destination"
            elif dest_mode == EXPORT_DEST_PATH:
                destination_label = f"custom folder ({self._export_root_directory()})"
            else:
                destination_label = "graphs/ (next to the working directory)"
            self.export_status_label.setText(
                f"{plan['message']} Destination: {destination_label}. {size_resolved_label}"
            )
            return
        self.export_status_label.setText(plan["message"])

    def _snapshot_ui_state(self):
        return {
            "category": self.category_selector.currentText(),
            "ws_type": self.ws_type_selector.currentText(),
            "metric": self.metric_selector.currentText(),
            "plot_style": self._current_plot_style_mode(),
            "plot_type": self.plot_type_selector.currentText(),
            "file_checked_state": dict(self.file_checked_state),
            "had_graph": self._graph_has_data(),
            "summary_text": self.summary_label.text(),
            "last_plotted_request": dict(self.last_plotted_request) if self.last_plotted_request else None,
            "figure_geometry": self._capture_figure_geometry(),
        }

    def _restore_ui_state(self, snapshot):
        self.file_checked_state = dict(snapshot.get("file_checked_state", {}))
        self.last_plotted_request = dict(snapshot.get("last_plotted_request") or {}) or None
        self.plot_style_selector.set_current(snapshot.get("plot_style", PLOT_STYLE_COLORFUL))
        self.category_selector.set_current(snapshot.get("category", BENCHMARK_TYPE_PLACEHOLDER))
        self._on_filter_changed()
        self.ws_type_selector.set_current(snapshot.get("ws_type", WS_TYPE_PLACEHOLDER))
        self._on_filter_changed()
        self.plot_type_selector.set_current(snapshot.get("plot_type", PLOT_TYPE_PLACEHOLDER))
        self.metric_selector.set_current(snapshot.get("metric", METRIC_PLACEHOLDER))
        self.update_file_listbox_display()
        self._update_file_count_label()
        self._restore_figure_geometry(snapshot.get("figure_geometry"))
        if snapshot.get("had_graph"):
            request = snapshot.get("last_plotted_request") or {}
            if request:
                pt = request.get("plot_type", PLOT_TYPE_PLACEHOLDER)
                if pt not in HOME_PLOT_TYPE_OPTIONS:
                    pt = PLOT_TYPE_PLACEHOLDER
                plotted = self._render_plot(
                    request.get("files", []),
                    request.get("metric", METRIC_PLACEHOLDER),
                    pt,
                    enable_interactivity=True,
                )
                self.last_plotted_request = dict(request) if plotted else None
            else:
                self.plot_selected()
        else:
            self._clear_plot_artists()
            self.summary_label.setText(snapshot.get("summary_text", ""))
            self.canvas.draw()
        self._update_export_preview()

    def _slugify(self, text):
        slug = re.sub(r"[^\w\s-]", "", str(text or "")).strip().lower()
        return re.sub(r"[-\s]+", "-", slug) or "graph"

    def _batch_export_directory(self, root_dir=None):
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = root_dir if root_dir is not None else self._export_root_directory()
        path = os.path.join(base, f"batch-{ts}")
        os.makedirs(path, exist_ok=True)
        return path

    def _selected_file_dialog_filter(self, formats):
        mapping = {
            ".png": "PNG Image (*.png)",
            ".pdf": "PDF Document (*.pdf)",
            ".svg": "SVG Image (*.svg)",
        }
        filters = [mapping[fmt] for fmt in formats if fmt in mapping]
        return ";;".join(filters) if filters else "All Files (*)"

    def _paths_from_user_choice(self, filepath, formats):
        if not filepath:
            return None
        stem, ext = os.path.splitext(filepath)
        ext = ext.lower()
        if ext in formats:
            base_stem = stem
        elif ext in (".png", ".pdf", ".svg"):
            base_stem = stem
        else:
            base_stem = filepath
        paths = [(self._path_for_export_format(base_stem, fmt), fmt) for fmt in formats]
        if self._title_mode_writes_sidecar():
            paths = [(self._sidecar_bundle_image_path(p), fmt) for p, fmt in paths]
        return paths

    def _choose_current_export_targets(self, formats):
        default_path = self._default_save_path(ext=formats[0])
        if self._current_export_destination_mode() != EXPORT_DEST_ASK:
            stem, _ = os.path.splitext(default_path)
            paths = [(self._path_for_export_format(stem, fmt), fmt) for fmt in formats]
            if self._title_mode_writes_sidecar():
                paths = [(self._sidecar_bundle_image_path(p), fmt) for p, fmt in paths]
            return paths
        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "Save graph" if len(formats) == 1 else "Choose base name for export files",
            default_path,
            self._selected_file_dialog_filter(formats),
        )
        return self._paths_from_user_choice(filepath, formats)

    def _choose_batch_export_directory(self):
        if self._current_export_destination_mode() == EXPORT_DEST_ASK:
            start_dir = self._export_root_directory()
            root_dir = QFileDialog.getExistingDirectory(self, "Select folder for batch export", start_dir)
            if not root_dir:
                return None
            return self._batch_export_directory(root_dir)
        return self._batch_export_directory()

    def run_export(self):
        if self._current_export_mode() == EXPORT_MODE_BATCH:
            self.batch_export_graphs()
        else:
            self.export_graph()

    def export_graph(self):
        plan = self._build_export_plan()
        if not plan.get("ready"):
            QMessageBox.warning(self, "Export not ready", plan.get("message") or "Adjust export settings before exporting.")
            return
        targets = self._choose_current_export_targets(plan["formats"])
        if not targets:
            return
        export_size = plan["size"]["size_inches"]
        exported_paths = []
        title_paths = []
        snapshot = self._snapshot_ui_state()
        request = plan["requests"][0]
        try:
            plotted = self._render_request_for_export(request, export_size)
            if not plotted:
                QMessageBox.warning(self, "No graph", "Unable to render the current graph for export.")
                return
            for filepath, fmt in targets:
                ok = self._save_current_figure(filepath, fmt)
                if ok is False:
                    return
                exported_paths.append(filepath)
            if self._title_mode_writes_sidecar() and targets:
                sidecar_path = self._title_sidecar_path(targets[0][0], targets[0][1])
                ok = self._write_title_sidecar(sidecar_path, request)
                if ok is False:
                    return
                title_paths.append(sidecar_path)
        finally:
            self._restore_ui_state(snapshot)
        saved_total = len(exported_paths) + len(title_paths)
        self.export_status_label.setText(f"Last export: saved {saved_total} file(s).")
        saved_paths = exported_paths + title_paths
        QMessageBox.information(self, "Exported", "Saved to:\n" + "\n".join(saved_paths))

    def batch_export_graphs(self):
        plan = self._build_export_plan()
        if not plan.get("ready"):
            QMessageBox.warning(self, "Export not ready", plan.get("message") or "Adjust batch export settings before exporting.")
            return
        requests = plan["requests"]
        stats = plan["stats"]
        formats = plan["formats"]

        snapshot = self._snapshot_ui_state()
        batch_dir = self._choose_batch_export_directory()
        if not batch_dir:
            return
        export_size = plan["size"]["size_inches"]
        exported = 0
        cancelled = False
        per_request_outputs = len(formats) + (1 if self._title_mode_writes_sidecar() else 0)
        progress = QProgressDialog("Preparing batch export...", "Cancel", 0, max(stats["output_count"], 1), self)
        progress.setWindowTitle("Batch export")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        completed = 0

        try:
            for request in requests:
                if progress.wasCanceled():
                    cancelled = True
                    break
                progress.setLabelText(f"Rendering {self._preview_request_title(request, include_category=True)}")
                QApplication.processEvents()
                plotted = self._render_request_for_export(request, export_size)
                if not plotted:
                    completed += per_request_outputs
                    progress.setValue(min(completed, progress.maximum()))
                    continue
                for fmt in formats:
                    if progress.wasCanceled():
                        cancelled = True
                        break
                    filepath = self._batch_export_path(batch_dir, request, fmt)
                    progress.setLabelText(f"Saving {os.path.basename(filepath)}")
                    QApplication.processEvents()
                    ok = self._save_current_figure(filepath, fmt)
                    if ok is False:
                        return
                    exported += 1
                    completed += 1
                    progress.setValue(min(completed, progress.maximum()))
                if cancelled:
                    break
                if self._title_mode_writes_sidecar():
                    sample_target = self._batch_export_path(batch_dir, request, formats[0], ensure_dirs=True)
                    sidecar_path = self._title_sidecar_path(sample_target, formats[0])
                    progress.setLabelText(f"Saving {os.path.basename(sidecar_path)}")
                    QApplication.processEvents()
                    ok = self._write_title_sidecar(sidecar_path, request)
                    if ok is False:
                        return
                    exported += 1
                    completed += 1
                    progress.setValue(min(completed, progress.maximum()))
                if cancelled:
                    break
        finally:
            progress.close()
            self._restore_ui_state(snapshot)

        skipped = max(0, stats["output_count"] - exported)
        self.export_status_label.setText(
            f"Last batch: exported {exported}, skipped {skipped}. Folder: {batch_dir}"
        )
        if cancelled:
            QMessageBox.information(
                self,
                "Batch export cancelled",
                f"Exported {exported} files before cancellation.\nSkipped {skipped} planned outputs.\n\nPartial results in:\n{batch_dir}",
            )
            return
        QMessageBox.information(
            self,
            "Batch export complete",
            f"Exported {exported} files.\nSkipped {skipped} invalid or empty combinations.\n\nSaved to:\n{batch_dir}",
        )

    def show_help(self):
        msg = (
            "Benchmark Graph Generator\n\n"
            "• Select one or more CSV files (from results directories).\n"
            "• Auto-detects file type (HTTP, WebSocket, gRPC, etc).\n"
            "• Filter by category (Static, Dynamic, WebSocket, gRPC, etc).\n"
            "• WebSocket: subtype (Burst, Stream, Concurrency, Payload) is auto-detected from filename.\n"
            "• Plot types in Export Studio are generated per discovered benchmark category.\n"
            "• Home keeps data browsing, visualization controls, and the live canvas together.\n"
            "• Export Studio is available as its own tab and has its own file/folder import actions.\n"
            "• Export Studio controls formats, source files, PNG DPI, compression, whitespace trimming, title mode, size mode, destination, and batch planning.\n"
            "• Export size can match the live graph, use a named preset, or use a custom width/height.\n"
            "• Title mode can keep a compact title in the graph, omit titles, or write a .title.txt file next to each export.\n"
            "• Destination defaults to the graphs folder unless you switch to Ask every time.\n"
            "• Batch export can use all imported files directly, even when you are not visualizing a graph first.\n"
            "• Export Plan shows summary counts, visible per-job rows, and a sample output target before writing files.\n"
            "• Batch lists include All / Clear shortcuts so selection is explicit; plot types are chosen per benchmark category.\n"
            "• Summary stats (min, max, avg) appear in the workspace after plotting.\n"
            "• Double-click a file to plot. Use Plot button or change metric.\n"
            "• Select folder: recursively loads all CSVs from subfolders.\n"
        )
        QMessageBox.information(self, "Help", msg)

    def load_all_csvs_in_folder(self):
        start_dir = os.path.abspath("results") if os.path.isdir("results") else ""
        folder = QFileDialog.getExistingDirectory(self, "Select Folder Containing CSV Files", start_dir)
        if folder:
            csv_files = []
            for root, dirs, files in os.walk(folder):
                for f in files:
                    if f.lower().endswith('.csv'):
                        csv_files.append(os.path.join(root, f))
            if not csv_files:
                QMessageBox.warning(self, "No CSVs", "No CSV files found in the selected folder.")
                return
            self.add_files(csv_files)


def main():
    app = QApplication(sys.argv)
    win = BenchmarkGrapher()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
