"""
Benchmark Graph Generator — PyQt5 GUI for plotting CSV benchmark results.
Extensible: add categories via CATEGORY_PATH_PARTS and CATEGORY_PREFIXES.
"""
import os
import csv
import sys
import re
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
    QScrollArea,
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

# Single design system: one border, one hover, one pressed for all controls
BORDER = "1px solid #ced4da"
BORDER_RADIUS = "4px"
BG_CONTROL = "#ffffff"
BG_HOVER = "#f1f3f5"
BG_PRESSED = "#e9ecef"
BG_DISABLED = "#e9ecef"
TEXT = "#212529"
TEXT_DISABLED = "#adb5bd"
BORDER_HOVER = "#adb5bd"
SELECTION_BG = "#e9ecef"
SELECTION_TEXT = "#212529"


def _make_light_palette():
    p = QPalette()
    p.setColor(QPalette.Window, QColor("#f8f9fa"))
    p.setColor(QPalette.WindowText, QColor(TEXT))
    p.setColor(QPalette.Base, QColor(BG_CONTROL))
    p.setColor(QPalette.AlternateBase, QColor(BG_HOVER))
    p.setColor(QPalette.Text, QColor(TEXT))
    p.setColor(QPalette.Button, QColor(BG_CONTROL))
    p.setColor(QPalette.ButtonText, QColor(TEXT))
    p.setColor(QPalette.Highlight, QColor(SELECTION_BG))
    p.setColor(QPalette.HighlightedText, QColor(SELECTION_TEXT))
    return p


def _app_stylesheet():
    """One design system: all buttons and controls share the same colors, size, hover, pressed."""
    f = FONT_SIZE
    h = CONTROL_HEIGHT
    return f"""
        QWidget {{ font-family: "{FONT_FAMILY}"; font-size: {f}pt; background-color: #f8f9fa; }}
        QLabel {{ font-size: {f}pt; color: {TEXT}; }}

        QPushButton, QToolButton {{
            font-size: {f}pt;
            background-color: {BG_CONTROL};
            color: {TEXT};
            border: {BORDER};
            border-radius: {BORDER_RADIUS};
            padding: 4px 12px;
            min-height: {h}px;
            max-height: {h}px;
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
        QToolButton {{ text-align: left; }}
        QToolButton::menu-indicator {{ width: 14px; border: none; }}

        QMenu {{
            font-size: {f}pt;
            background-color: {BG_CONTROL};
            border: {BORDER};
            border-radius: {BORDER_RADIUS};
            padding: 2px 0;
        }}
        QMenu::item {{ padding: 4px 16px; color: {TEXT}; }}
        QMenu::item:selected {{ background-color: {SELECTION_BG}; color: {SELECTION_TEXT}; }}

        QGroupBox {{
            font-size: {f}pt;
            font-weight: normal;
            border: {BORDER};
            border-radius: {BORDER_RADIUS};
            margin-top: 6px;
            padding: 8px;
            padding-top: 12px;
            background-color: {BG_CONTROL};
        }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: {TEXT}; }}

        QComboBox {{
            font-size: {f}pt;
            min-height: {h}px;
            max-height: {h}px;
            padding: 4px 12px;
            background-color: {BG_CONTROL};
            color: {TEXT};
            border: {BORDER};
            border-radius: {BORDER_RADIUS};
        }}
        QComboBox::drop-down {{ width: 18px; border: none; }}
        QComboBox QAbstractItemView {{
            font-size: {f}pt;
            padding: 4px 8px;
            background-color: {BG_CONTROL};
            color: {TEXT};
            selection-background-color: {SELECTION_BG};
            selection-color: {SELECTION_TEXT};
            border: {BORDER};
        }}

        QListWidget {{
            font-size: {f}pt;
            background-color: {BG_CONTROL};
            color: {TEXT};
            border: {BORDER};
            border-radius: {BORDER_RADIUS};
            outline: none;
        }}
        QListWidget::item {{
            padding: 4px 8px;
            border: none;
            outline: none;
        }}
        QListWidget::item:selected {{
            background-color: {SELECTION_BG};
            color: {SELECTION_TEXT};
            padding: 4px 8px;
            border: none;
            outline: none;
        }}

        QSplitter::handle {{
            width: 10px;
            background: #dee2e6;
            border-left: 1px solid #ced4da;
            border-right: 1px solid #ced4da;
        }}
        QSplitter::handle:hover {{
            background: #ced4da;
        }}

        QPushButton#sidebarToggleBtn {{
            min-width: 38px;
            max-width: 38px;
            padding: 4px;
            font-size: 18px;
            line-height: 1;
        }}

        QScrollArea {{ border: none; background: transparent; }}
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

# WebSocket CSV: test subtypes and x-axis column names (from measure_websocket.py + run_benchmarks.sh)
WS_SUBTYPE_CONCURRENCY = "concurrency_sweep"
WS_SUBTYPE_PAYLOAD = "payload_sweep"
WS_SUBTYPE_BURST = "burst"
WS_SUBTYPE_STREAM = "stream"
WS_XAXIS_COLUMNS = ["Num Clients", "Message Size (KB)", "Rate (msg/s)", "Bursts", "Duration (s)", "Interval (s)"]
WS_TYPE_ALL = "All"
WS_TYPE_CONCURRENCY = "Concurrency sweep"
WS_TYPE_PAYLOAD = "Payload sweep"
WS_TYPE_BURST_STREAM = "Burst / Stream"
WS_TYPE_OPTIONS = [WS_TYPE_ALL, WS_TYPE_CONCURRENCY, WS_TYPE_PAYLOAD, WS_TYPE_BURST_STREAM]
BENCHMARK_TYPE_PLACEHOLDER = "Benchmark type"
WS_TYPE_PLACEHOLDER = "WebSocket type"
# Short acronyms for WebSocket subtype in save filenames (avoids overwriting when saving different subtypes in the same second)
WS_TYPE_SAVE_ACRONYM = {
    WS_TYPE_CONCURRENCY: "conc",
    WS_TYPE_PAYLOAD: "pay",
    WS_TYPE_BURST_STREAM: "burst",
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
    if "_concurrency_sweep" in base:
        return WS_SUBTYPE_CONCURRENCY
    if "_payload_sweep" in base:
        return WS_SUBTYPE_PAYLOAD
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
        self.file_types = {}
        self.headers = {}
        self.rows = {}
        self.file_categories = {}
        self.file_ws_subtypes = {}
        self.color_cycle = []
        for cmap_name in ['tab20', 'tab20b', 'tab20c', 'Set1', 'Set2', 'Set3', 'Dark2', 'Paired', 'Accent', 'Pastel1', 'Pastel2']:
            cmap = plt.get_cmap(cmap_name)
            self.color_cycle.extend([cmap(i) for i in range(cmap.N)])
        self.color_cycle = list(dict.fromkeys(self.color_cycle))
        self.marker_cycle = ['o', 's', 'D', '^', 'v', 'P', '*', 'X', 'h', '+', 'x', '|', '_', '1', '2', '3', '4', '8']
        self.linestyle_cycle = ['-', '--', '-.', ':']
        self.bar_width_scale = 5.0
        self.cursor = None
        self.bar_cursor = None
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
        def _btn(text, slot, min_w=None):
            w = min_w if min_w is not None else BTN_MIN_W
            b = QPushButton(text, clicked=slot)
            b.setMinimumWidth(w)
            b.setMinimumHeight(CONTROL_HEIGHT)
            b.setMaximumHeight(CONTROL_HEIGHT)
            b.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
            return b

        GAP = 6
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(10)
        splitter.setChildrenCollapsible(False)

        left_panel = QWidget()
        left_panel.setMinimumWidth(380)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setSpacing(GAP)

        data_group = QGroupBox("Data")
        data_layout = QVBoxLayout(data_group)
        data_layout.setSpacing(GAP)
        row1 = QHBoxLayout()
        row1.setSpacing(GAP)
        row1.addWidget(_btn("Select files", self.browse_files))
        row1.addWidget(_btn("Select folder", self.load_all_csvs_in_folder))
        row1.addStretch()
        data_layout.addLayout(row1)
        row2 = QHBoxLayout()
        row2.setSpacing(GAP)
        row2.addWidget(_btn("Clear all", self.clear_files))
        self.select_all_btn = _btn("Select all", self.select_all_files)
        self.select_all_btn.setEnabled(False)
        row2.addWidget(self.select_all_btn)
        self.deselect_all_btn = _btn("Deselect all", self.deselect_all_files)
        self.deselect_all_btn.setEnabled(False)
        row2.addWidget(self.deselect_all_btn)
        row2.addStretch()
        data_layout.addLayout(row2)
        row3 = QHBoxLayout()
        row3.setSpacing(GAP)
        self.category_selector = MenuSelectorWidget(placeholder=BENCHMARK_TYPE_PLACEHOLDER, parent=self)
        self.category_selector.set_options([])
        self.category_selector.option_chosen.connect(lambda t: QTimer.singleShot(0, self._on_filter_changed))
        row3.addWidget(self.category_selector)
        self.file_count_label = QLabel("0 loaded, 0 selected")
        row3.addWidget(self.file_count_label, 1)
        data_layout.addLayout(row3)
        self.ws_type_row = QWidget()
        row_ws = QHBoxLayout(self.ws_type_row)
        row_ws.setContentsMargins(0, 0, 0, 0)
        row_ws.setSpacing(GAP)
        self.ws_type_selector = MenuSelectorWidget(placeholder=WS_TYPE_PLACEHOLDER, parent=self)
        self.ws_type_selector.set_options([WS_TYPE_ALL, WS_TYPE_CONCURRENCY, WS_TYPE_PAYLOAD, WS_TYPE_BURST_STREAM])
        self.ws_type_selector.option_chosen.connect(lambda t: QTimer.singleShot(0, self._on_filter_changed))
        row_ws.addWidget(self.ws_type_selector)
        row_ws.addStretch()
        self.ws_type_row.setVisible(False)
        data_layout.addWidget(self.ws_type_row)
        self.file_listbox = CheckableFileListWidget(self)
        self.file_listbox.setSelectionMode(QListWidget.ExtendedSelection)
        self.file_listbox.setMinimumHeight(140)
        self.file_listbox.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.file_listbox.itemSelectionChanged.connect(self._on_selection_changed)
        self.file_listbox.itemChanged.connect(self._on_item_changed)
        self.file_listbox.itemDoubleClicked.connect(self.plot_selected)
        data_layout.addWidget(self.file_listbox, 1)
        left_layout.addWidget(data_group, 1)

        plot_group = QGroupBox("Plot")
        plot_layout = QVBoxLayout(plot_group)
        plot_layout.setSpacing(GAP)
        plot_layout.setContentsMargins(8, 10, 8, 8)
        plot_layout.addWidget(QLabel("Metric:"))
        self.metric_selector = MenuSelectorWidget(placeholder="(select metric)", parent=self)
        self.metric_selector.set_options([])
        self.metric_selector.option_chosen.connect(lambda t: QTimer.singleShot(0, self.plot_selected))
        plot_layout.addWidget(self.metric_selector)
        pr = QHBoxLayout()
        pr.setSpacing(GAP)
        pr.addWidget(QLabel("Type:"))
        self.plot_type_selector = MenuSelectorWidget(placeholder="Auto", parent=self)
        self.plot_type_selector.set_options(["Auto", "Bar", "Line"])
        self.plot_type_selector.option_chosen.connect(lambda t: QTimer.singleShot(0, self.plot_selected))
        pr.addWidget(self.plot_type_selector)
        pr.addWidget(_btn("Plot", self.plot_selected))
        pr.addStretch()
        plot_layout.addLayout(pr)
        left_layout.addWidget(plot_group)

        bottom_left = QFrame()
        bottom_left.setFrameShape(QFrame.NoFrame)
        bl_layout = QVBoxLayout(bottom_left)
        bl_layout.setSpacing(GAP)
        save_row = QHBoxLayout()
        save_row.setSpacing(GAP)
        save_row.addWidget(_btn("Save", self.export_graph))
        save_row.addWidget(QLabel("Format:"))
        self.format_combo = QComboBox()
        self.format_combo.setItemDelegate(_ComboDelegate(self.format_combo))
        self.format_combo.addItems(["PNG", "PDF", "SVG"])
        self.format_combo.setMinimumWidth(BTN_MIN_W)
        save_row.addWidget(self.format_combo)
        save_row.addWidget(_btn("Help", self.show_help))
        save_row.addStretch()
        bl_layout.addLayout(save_row)
        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet(f"color: {TEXT_DISABLED}; padding: 2px; font-size: {FONT_SIZE}pt; font-style: italic;")
        self.summary_label.setWordWrap(True)
        bl_layout.addWidget(self.summary_label)
        left_layout.addWidget(bottom_left)

        scroll = QScrollArea()
        scroll.setWidget(left_panel)
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(360)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.left_panel = scroll
        splitter.addWidget(scroll)

        # --- Right panel: Graph (resizable) ---
        right_panel = QWidget()
        right_panel.setMinimumWidth(450)  # Keep graph usable when user shrinks it
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        graph_group = QGroupBox("Graph")
        graph_layout = QVBoxLayout(graph_group)
        graph_layout.setContentsMargins(2, 2, 2, 2)
        top_row = QHBoxLayout()
        self.sidebar_toggle_btn = QPushButton("\u25c0")  # ◀ (black left-pointing triangle)
        self.sidebar_toggle_btn.setObjectName("sidebarToggleBtn")
        self.sidebar_toggle_btn.setToolTip("Hide sidebar (full screen graph)")
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
        right_layout.addWidget(graph_group)
        splitter.addWidget(right_panel)

        # Initial split: left at minimum readable width, graph gets the rest
        splitter.setSizes([400, 10000])

        main_layout.addWidget(splitter)
        central.setStyleSheet(_app_stylesheet())

        QShortcut(QKeySequence("Ctrl+A"), self).activated.connect(self.select_all_files)
        QShortcut(QKeySequence(Qt.Key_Return), self).activated.connect(self.plot_selected)
        QShortcut(QKeySequence(Qt.Key_Enter), self).activated.connect(self.plot_selected)
        self.showMaximized()

    def _toggle_sidebar(self):
        visible = self.left_panel.isVisible()
        self.left_panel.setVisible(not visible)
        if visible:
            self.sidebar_toggle_btn.setText("\u25b6")   # ▶ (black right-pointing triangle) = show panel
            self.sidebar_toggle_btn.setToolTip("Show sidebar")
        else:
            self.sidebar_toggle_btn.setText("\u25c0")  # ◀ (black left-pointing triangle) = hide panel
            self.sidebar_toggle_btn.setToolTip("Hide sidebar (full screen graph)")

    def browse_files(self):
        start_dir = os.path.abspath("results") if os.path.isdir("results") else ""
        files, _ = QFileDialog.getOpenFileNames(self, "Select CSV files", start_dir, "CSV Files (*.csv)")
        if files:
            self.add_files(files)

    def detect_file_category(self, filepath):
        """Detect category from path using CATEGORY_PREFIXES and CATEGORY_PATH_PARTS."""
        base = os.path.basename(filepath).lower()
        for prefix, cat in CATEGORY_PREFIXES.items():
            if base.startswith(prefix):
                return cat
        path_lower = filepath.lower()
        for part, cat in CATEGORY_PATH_PARTS.items():
            if part in path_lower:
                return cat
        return "Unknown"

    def _on_filter_changed(self):
        cat = self.category_selector.currentText()
        if getattr(self, "ws_type_row", None):
            self.ws_type_row.setVisible(cat == "WebSocket")
            if cat != "WebSocket" and getattr(self, "ws_type_selector", None):
                self.ws_type_selector.set_current(WS_TYPE_PLACEHOLDER)
        self.update_file_listbox_display()
        self.update_metric_options()

    def _on_selection_changed(self):
        self._update_file_count_label()

    def _on_item_changed(self, item):
        """Update file count when a checkbox is toggled."""
        self._update_file_count_label()

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
        self._update_filter_combo()
        self.update_metric_options()
        self.update_file_listbox_display()

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
            item.setCheckState(Qt.Checked)
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
        self.file_listbox.clear()
        self.category_selector.set_options([BENCHMARK_TYPE_PLACEHOLDER])
        self.category_selector.set_current(BENCHMARK_TYPE_PLACEHOLDER)
        if getattr(self, "ws_type_row", None):
            self.ws_type_row.setVisible(False)
        if getattr(self, "ws_type_selector", None):
            self.ws_type_selector.set_current(WS_TYPE_PLACEHOLDER)
        self.metric_selector.set_options([])
        self.ax.clear()
        self.canvas.draw()
        self.summary_label.setText("")
        self.file_count_label.setText("0 loaded, 0 selected")
        self.select_all_btn.setEnabled(False)
        self.deselect_all_btn.setEnabled(False)

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
        elif metrics:
            self.metric_selector.set_current(metrics[0])

    def get_selected_files(self):
        visible = self.get_visible_files()
        checked = [self.file_listbox.item(i).data(Qt.UserRole)
                   for i in range(self.file_listbox.count())
                   if self.file_listbox.item(i).checkState() == Qt.Checked
                   and self.file_listbox.item(i).data(Qt.UserRole) in visible]
        if not checked:
            return visible
        return checked

    def select_all_files(self):
        self.file_listbox.blockSignals(True)
        for i in range(self.file_listbox.count()):
            self.file_listbox.item(i).setCheckState(Qt.Checked)
        self.file_listbox.blockSignals(False)
        self.file_listbox.selectAll()
        self._update_file_count_label()

    def deselect_all_files(self):
        self.file_listbox.blockSignals(True)
        for i in range(self.file_listbox.count()):
            self.file_listbox.item(i).setCheckState(Qt.Unchecked)
        self.file_listbox.blockSignals(False)
        self.file_listbox.clearSelection()
        self._update_file_count_label()

    def plot_selected(self):
        selected_files = self.get_selected_files()
        metric = self.metric_selector.currentText()
        if not metric or metric == "(select metric)":
            return
        if not selected_files:
            return
        self.ax.clear()
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

        all_vals = []
        plot_type = self.plot_type_selector.currentText()
        n_series = max(1, len(selected_files))

        for idx, f in enumerate(selected_files):
            header = self.headers[f]
            rows = self.rows[f]
            typ = self.file_types[f]
            x, y, label = self.get_plot_data(header, rows, typ, metric, os.path.basename(f), filepath=f)
            if x and y:
                color = self.color_cycle[idx % len(self.color_cycle)]
                marker = self.marker_cycle[(idx // len(self.linestyle_cycle)) % len(self.marker_cycle)]
                linestyle = self.linestyle_cycle[idx % len(self.linestyle_cycle)]
                use_bar = (plot_type == "Bar") or (plot_type == "Auto" and typ == "websocket")
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
                    self.ax.bar(x_vals, y, width=bar_width, label=label, color=color, alpha=0.85)
                    for xv, val in zip(x_vals, y):
                        if val == 0:
                            self.ax.plot([xv - bar_width/2, xv + bar_width/2], [0, 0], color=color, linewidth=2, alpha=0.85, solid_capstyle='butt')
                else:
                    self.ax.plot(x, y, label=label, color=color, marker=marker, linestyle=linestyle, linewidth=2, markersize=7)
                all_vals.extend(y)

        if selected_files:
            x_col = self.get_x_axis_column_name(self.headers[selected_files[0]], self.rows[selected_files[0]], self.file_types[selected_files[0]], filepath=selected_files[0])
            x_axis_label = XAXIS_DISPLAY_NAMES.get(x_col, x_col) if x_col else "Test parameter"
        else:
            x_axis_label = "Test parameter"
        self.ax.set_title(f"{metric} vs. {x_axis_label}")
        self.ax.set_xlabel(x_axis_label)
        self.ax.set_ylabel(metric)
        self.ax.legend(loc='best', framealpha=0.85)
        self.ax.grid(True)
        self.canvas.draw()
        if all_vals:
            s = f"{metric}: min={min(all_vals):.2f}, max={max(all_vals):.2f}, avg={sum(all_vals)/len(all_vals):.2f}"
            self.summary_label.setText(s)
        else:
            self.summary_label.setText("")

        try:
            if self.ax.lines and len(self.ax.lines) > 0:
                self.cursor = mplcursors.cursor(self.ax.lines, hover=True, highlight=False,
                    annotation_kwargs={'fontsize': 9, 'arrowprops': dict(arrowstyle="->", color="#333", lw=1.2),
                        'bbox': dict(boxstyle="round,pad=0.2", fc="#f7f7f7", ec="#333", lw=0.8)})
                @self.cursor.connect("add")
                def on_add(sel):
                    for line in self.ax.get_lines():
                        line.set_linewidth(2)
                        line.set_alpha(0.7)
                    sel.artist.set_linewidth(4)
                    sel.artist.set_alpha(1.0)
                    sel.annotation.set_text(sel.artist.get_label())
                    for ann in self.ax.texts:
                        if ann is not sel.annotation:
                            ann.set_visible(False)
                @self.cursor.connect("remove")
                def on_remove(_):
                    for line in self.ax.get_lines():
                        line.set_linewidth(2)
                        line.set_alpha(1.0)
                    for ann in self.ax.texts:
                        ann.set_visible(False)
                    self.canvas.draw_idle()

            if self.ax.containers and len(self.ax.containers) > 0:
                self.bar_cursor = mplcursors.cursor(self.ax.containers, hover=True, highlight=False,
                    annotation_kwargs={'fontsize': 9, 'arrowprops': dict(arrowstyle="->", color="#333", lw=1.2),
                        'bbox': dict(boxstyle="round,pad=0.2", fc="#f7f7f7", ec="#333", lw=0.8)})
                @self.bar_cursor.connect("add")
                def on_bar_add(sel):
                    target_label = sel.artist.get_label() if hasattr(sel.artist, 'get_label') else None
                    if target_label:
                        for cont in self.ax.containers:
                            for bar in cont:
                                if (bar.get_label() if hasattr(bar, 'get_label') else None) == target_label:
                                    bar.set_linewidth(3)
                                    bar.set_edgecolor('#d62728')
                                    bar.set_alpha(1.0)
                    sel.annotation.set_text(target_label or '')
                    for ann in self.ax.texts:
                        if ann is not sel.annotation:
                            ann.set_visible(False)
                @self.bar_cursor.connect("remove")
                def on_bar_remove(_):
                    for cont in self.ax.containers:
                        for bar in cont:
                            bar.set_linewidth(0.5)
                            bar.set_edgecolor('black')
                            bar.set_alpha(0.85)
                    for ann in self.ax.texts:
                        ann.set_visible(False)
                    self.canvas.draw_idle()
        except Exception:
            pass

        def on_leave(event):
            for line in self.ax.get_lines():
                line.set_linewidth(2)
                line.set_alpha(1.0)
            for cont in self.ax.containers:
                for bar in cont:
                    bar.set_linewidth(0.5)
                    bar.set_edgecolor('black')
                    bar.set_alpha(0.85)
            for ann in self.ax.texts:
                ann.set_visible(False)
            self.canvas.draw_idle()
        self.canvas.mpl_connect('axes_leave_event', on_leave)

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

    def _default_save_path(self):
        """Default path: graphs/<category>/<metric>[-<ws-subtype>]-<N>bench-<YYYYMMDD-HHMM>.ext.
        WebSocket subtype (conc/pay/burst/all) in filename avoids overwriting when saving different subtypes in the same second."""
        metric = self.metric_selector.currentText() or "graph"
        if metric == "(select metric)":
            metric = "graph"
        slug = re.sub(r"[^\w\s-]", "", metric).strip().lower()
        slug = re.sub(r"[-\s]+", "-", slug) or "graph"
        n = len(self.get_selected_files()) or len(self.get_visible_files()) or 0
        ts = datetime.now().strftime("%Y%m%d-%H%M")
        fmt = self.format_combo.currentText().strip().lower()
        ext = ".png" if fmt == "png" else ".pdf" if fmt == "pdf" else ".svg"
        cat = (self.category_selector.currentText() or "").strip()
        cat_lower = cat.lower().replace(" ", "")
        subtype_part = ""
        if cat_lower == "websocket" and getattr(self, "ws_type_selector", None):
            ws_type = self.ws_type_selector.currentText() or WS_TYPE_PLACEHOLDER
            acronym = WS_TYPE_SAVE_ACRONYM.get(ws_type, "all")
            subtype_part = f"-{acronym}"
        name = f"{slug}{subtype_part}-{n}bench-{ts}{ext}"
        base = os.path.abspath("graphs")
        if cat and cat_lower not in ("all", "", BENCHMARK_TYPE_PLACEHOLDER.lower().replace(" ", "")):
            base = os.path.join(base, cat_lower)
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, name)

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
            elif ws_type == WS_TYPE_BURST_STREAM and sub in (WS_SUBTYPE_BURST, WS_SUBTYPE_STREAM, None):
                out.append(f)
            else:
                pass
        return out

    def export_graph(self):
        _has_data = getattr(self.ax, 'has_data', None)
        has_data = _has_data() if callable(_has_data) else (len(self.ax.lines) + len(self.ax.containers)) > 0
        if not has_data:
            QMessageBox.warning(self, "No graph", "No graph to export. Please plot something first.")
            return
        fmt = self.format_combo.currentText().strip().lower()
        if fmt == "png":
            filetypes, defaultext, save_kwargs = "PNG Image (*.png)", ".png", {"dpi": 300}
        elif fmt == "pdf":
            filetypes, defaultext, save_kwargs = "PDF Document (*.pdf)", ".pdf", {}
        elif fmt == "svg":
            filetypes, defaultext, save_kwargs = "SVG Image (*.svg)", ".svg", {}
        else:
            filetypes, defaultext, save_kwargs = "PNG Image (*.png)", ".png", {"dpi": 300}
        default_path = self._default_save_path()
        filepath, _ = QFileDialog.getSaveFileName(self, "Save graph", default_path, filetypes)
        if filepath:
            if not filepath.lower().endswith(defaultext):
                filepath = filepath + defaultext
            self.fig.savefig(filepath, bbox_inches='tight', pad_inches=0.1, format=fmt, **save_kwargs)
            QMessageBox.information(self, "Exported", f"Graph exported to {filepath}")

    def show_help(self):
        msg = (
            "Benchmark Graph Generator\n\n"
            "• Select one or more CSV files (from results directories).\n"
            "• Auto-detects file type (HTTP, WebSocket, gRPC, etc).\n"
            "• Filter by category (Static, Dynamic, WebSocket, gRPC, etc).\n"
            "• WebSocket files show subtype: Burst, Stream, Concurrency, Payload.\n"
            "• Choose a metric to plot (latency, throughput, CPU, etc).\n"
            "• Overlay/combine results from multiple files.\n"
            "• Export graphs as PNG, PDF, or SVG (Save button below).\n"
            "• Summary stats (min, max, avg) shown below the graph.\n"
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
