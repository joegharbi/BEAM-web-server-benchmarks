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
    QStyleFactory, QFrame, QSplitter, QSizePolicy, QMenu, QAction,
)
from PyQt5.QtCore import Qt, QTimer, QEvent, QPoint
from PyQt5.QtGui import QFont, QKeySequence, QColor, QPalette

# --- Extensible category detection ---
# Path segment (lowercase) -> display name. Add new benchmark types here.
CATEGORY_PATH_PARTS = {"websocket": "WebSocket", "static": "Static", "dynamic": "Dynamic", "local": "Local", "grpc": "gRPC"}
# Filename prefix -> display name
CATEGORY_PREFIXES = {"ws-": "WebSocket", "st-": "Static", "dy-": "Dynamic", "grpc-": "gRPC"}
FONT_FAMILY = "Sans Serif"
FONT_SIZE = 12


def _make_light_palette():
    """Professional light palette for consistent appearance."""
    p = QPalette()
    p.setColor(QPalette.Window, QColor("#f5f5f5"))
    p.setColor(QPalette.WindowText, QColor("#2c3e50"))
    p.setColor(QPalette.Base, QColor("#ffffff"))
    p.setColor(QPalette.AlternateBase, QColor("#f0f0f0"))
    p.setColor(QPalette.Text, QColor("#2c3e50"))
    p.setColor(QPalette.Button, QColor("#e8e8e8"))
    p.setColor(QPalette.ButtonText, QColor("#2c3e50"))
    p.setColor(QPalette.Highlight, QColor("#3498db"))
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    return p


def _app_stylesheet():
    """Unified stylesheet: consistent font, colors, readable dropdowns."""
    font_pt = FONT_SIZE
    return f"""
        QWidget {{
            background-color: #f5f5f5;
            font-family: "{FONT_FAMILY}";
            font-size: {font_pt}pt;
        }}
        QLabel {{
            font-size: {font_pt}pt;
            color: #2c3e50;
        }}
        QPushButton {{
            font-size: {font_pt}pt;
            background-color: #e8e8e8;
            color: #2c3e50;
            border: 1px solid #d0d0d0;
            border-radius: 4px;
            padding: 6px 12px;
        }}
        QPushButton:hover {{
            background-color: #d8d8d8;
            border-color: #b0b0b0;
        }}
        QPushButton:pressed {{
            background-color: #c8c8c8;
        }}
        QPushButton:disabled {{
            background-color: #eeeeee;
            color: #999999;
        }}
        QGroupBox {{
            font-size: {font_pt}pt;
            font-weight: normal;
            border: 1px solid #d0d0d0;
            border-radius: 4px;
            margin-top: 8px;
            padding: 8px 8px 8px 8px;
            padding-top: 14px;
            background-color: #fafafa;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 8px;
            padding: 0 4px;
            color: #2c3e50;
        }}
        QComboBox {{
            font-size: {font_pt}pt;
            min-height: 26px;
            padding: 4px 10px;
            background-color: #ffffff;
            border: 1px solid #d0d0d0;
            border-radius: 4px;
        }}
        QComboBox::drop-down {{
            width: 20px;
            border: none;
        }}
        QComboBox QAbstractItemView {{
            font-size: {font_pt}pt;
            padding: 4px 8px;
            outline: none;
            selection-background-color: #3498db;
            selection-color: #ffffff;
            background-color: #ffffff;
            color: #2c3e50;
        }}
        QListWidget {{
            font-size: {font_pt}pt;
            background-color: #ffffff;
            border: 1px solid #d0d0d0;
            border-radius: 4px;
        }}
        QListWidget::item {{
            padding: 4px;
        }}
    """


class _DropUpComboBox(QComboBox):
    """QComboBox that uses QMenu as popup - floats and stays on screen (up/down/left/right)."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMaxVisibleItems(15)
        self._menu = None

    def showPopup(self):
        if self.count() == 0:
            return
        self._menu = QMenu(self)
        self._menu.setStyleSheet("""
            QMenu { font-size: 12pt; padding: 4px 8px; min-width: 180px; }
            QMenu::item { padding: 6px 24px; min-width: 160px; }
            QMenu::item:selected { background-color: #3498db; color: white; }
        """)
        self._menu.setMinimumWidth(max(200, self.width()))
        self._menu.setMaximumHeight(400)
        for i in range(self.count()):
            text = self.itemText(i)
            act = self._menu.addAction(text)
            act.setData(i)
        pos = self.mapToGlobal(QPoint(0, self.height()))
        screen = QApplication.desktop().availableGeometry(self)
        # Ensure menu fits: prefer opening above if near bottom
        menu_h = min(400, 36 * min(self.count(), 15))
        if pos.y() + menu_h > screen.bottom():
            pos.setY(self.mapToGlobal(QPoint(0, 0)).y() - menu_h)
        if pos.y() < screen.top():
            pos.setY(screen.top())
        menu_w = max(200, self.width(), 250)
        if pos.x() + menu_w > screen.right():
            pos.setX(screen.right() - menu_w - 10)
        if pos.x() < screen.left():
            pos.setX(screen.left())
        action = self._menu.exec_(pos)
        if action is not None:
            self.setCurrentIndex(action.data())
        self._menu = None


class _ComboDelegate(QStyledItemDelegate):
    """Draw combo items with readable colors on hover."""
    def paint(self, painter, option, index):
        option.palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
        option.palette.setColor(QPalette.Highlight, QColor("#3498db"))
        super().paint(painter, option, index)


# --- Helper Functions ---
def detect_csv_type(header):
    if "Test Type" in header or "Total Messages" in header:
        return "websocket"
    if "Type" in header and "Total Requests" in header:
        return "http"
    return "unknown"

def read_csv(filepath):
    with open(filepath, newline='') as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames
        rows = list(reader)
    return header, rows

def summarize_column(rows, col):
    vals = [float(r[col]) for r in rows if r.get(col) not in (None, '', 'NaN')]
    if not vals:
        return {'min': '-', 'max': '-', 'avg': '-'}
    return {'min': min(vals), 'max': max(vals), 'avg': sum(vals) / len(vals)}

def get_numeric_columns(header):
    numeric = []
    for h in header:
        if any(x in h.lower() for x in [
            "cpu", "mem", "latency", "throughput", "energy", "power",
            "requests", "messages", "samples", "rate", "size", "duration",
            "interval", "bursts", "time", "execution", "runtime"
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
        app.setStyleSheet(_app_stylesheet())
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)

        def _btn(text, slot, min_w=90):
            b = QPushButton(text, clicked=slot)
            b.setMinimumWidth(min_w)
            b.setMinimumHeight(26)
            return b

        splitter = QSplitter(Qt.Horizontal)

        # --- Left panel: Data (tall file list) + Plot + Save/Format/Help/Summary at bottom ---
        left_panel = QWidget()
        left_panel.setMinimumWidth(420)  # Keep buttons ("Select all", "Deselect all") fully readable
        left_layout = QVBoxLayout(left_panel)
        left_layout.setSpacing(8)

        data_group = QGroupBox("Data")
        data_layout = QVBoxLayout(data_group)
        data_layout.setSpacing(6)
        row1 = QHBoxLayout()
        row1.addWidget(_btn("Select files", self.browse_files))
        row1.addWidget(_btn("Select folder", self.load_all_csvs_in_folder))
        data_layout.addLayout(row1)
        row2 = QHBoxLayout()
        row2.addWidget(_btn("Clear all", self.clear_files, 80))
        self.select_all_btn = _btn("Select all", self.select_all_files, 95)
        self.select_all_btn.setEnabled(False)
        row2.addWidget(self.select_all_btn)
        self.deselect_all_btn = _btn("Deselect all", self.deselect_all_files, 100)
        self.deselect_all_btn.setEnabled(False)
        row2.addWidget(self.deselect_all_btn)
        data_layout.addLayout(row2)
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Filter:"))
        self.category_combo = _DropUpComboBox()
        self.category_combo.setItemDelegate(_ComboDelegate(self.category_combo))
        self.category_combo.addItem("All")
        self.category_combo.currentTextChanged.connect(self._on_filter_changed)
        row3.addWidget(self.category_combo)
        self.file_count_label = QLabel("0 loaded, 0 selected")
        row3.addWidget(self.file_count_label)
        data_layout.addLayout(row3)
        self.file_listbox = QListWidget()
        self.file_listbox.setSelectionMode(QListWidget.ExtendedSelection)
        self.file_listbox.setMinimumHeight(180)
        self.file_listbox.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.file_listbox.itemSelectionChanged.connect(self._on_selection_changed)
        self.file_listbox.itemDoubleClicked.connect(self.plot_selected)
        data_layout.addWidget(self.file_listbox, 1)
        left_layout.addWidget(data_group, 1)

        plot_group = QGroupBox("Plot")
        plot_layout = QVBoxLayout(plot_group)
        plot_layout.setSpacing(6)
        plot_layout.addWidget(QLabel("Metric:"))
        self.metric_combo = _DropUpComboBox()
        self.metric_combo.setItemDelegate(_ComboDelegate(self.metric_combo))
        self.metric_combo.currentTextChanged.connect(self.plot_selected)
        plot_layout.addWidget(self.metric_combo)
        pr = QHBoxLayout()
        pr.addWidget(QLabel("Type:"))
        self.plot_type_combo = _DropUpComboBox()
        self.plot_type_combo.setItemDelegate(_ComboDelegate(self.plot_type_combo))
        self.plot_type_combo.addItems(["Auto", "Bar", "Line"])
        self.plot_type_combo.currentTextChanged.connect(self.plot_selected)
        pr.addWidget(self.plot_type_combo)
        plot_btn = _btn("Plot", self.plot_selected)
        plot_btn.clicked.connect(self.plot_selected)
        pr.addWidget(plot_btn)
        plot_layout.addLayout(pr)
        left_layout.addWidget(plot_group)

        bottom_left = QFrame()
        bottom_left.setFrameShape(QFrame.NoFrame)
        bl_layout = QVBoxLayout(bottom_left)
        bl_layout.setSpacing(6)
        save_row = QHBoxLayout()
        save_row.addWidget(_btn("Save", self.export_graph))
        save_row.addWidget(QLabel("Format:"))
        self.format_combo = _DropUpComboBox()
        self.format_combo.setItemDelegate(_ComboDelegate(self.format_combo))
        self.format_combo.addItems(["PNG", "PDF", "SVG"])
        save_row.addWidget(self.format_combo)
        save_row.addWidget(_btn("Help", self.show_help))
        save_row.addStretch()
        bl_layout.addLayout(save_row)
        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet("color: #5a6c7d; padding: 4px; font-style: italic;")
        self.summary_label.setWordWrap(True)
        bl_layout.addWidget(self.summary_label)
        left_layout.addWidget(bottom_left)

        self.left_panel = left_panel
        splitter.addWidget(left_panel)

        # --- Right panel: Graph (resizable) ---
        right_panel = QWidget()
        right_panel.setMinimumWidth(450)  # Keep graph usable when user shrinks it
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        graph_group = QGroupBox("Graph")
        graph_layout = QVBoxLayout(graph_group)
        graph_layout.setContentsMargins(4, 4, 4, 4)
        top_row = QHBoxLayout()
        self.sidebar_toggle_btn = QPushButton("◀")
        self.sidebar_toggle_btn.setToolTip("Hide sidebar (full screen graph)")
        self.sidebar_toggle_btn.setFixedSize(32, 28)
        self.sidebar_toggle_btn.setStyleSheet("QPushButton { font-size: 14px; }")
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
        splitter.setSizes([420, 10000])

        main_layout.addWidget(splitter)

        QShortcut(QKeySequence("Ctrl+A"), self).activated.connect(self.select_all_files)
        QShortcut(QKeySequence(Qt.Key_Return), self).activated.connect(self.plot_selected)
        QShortcut(QKeySequence(Qt.Key_Enter), self).activated.connect(self.plot_selected)
        self.showMaximized()

    def _toggle_sidebar(self):
        visible = self.left_panel.isVisible()
        self.left_panel.setVisible(not visible)
        if visible:
            self.sidebar_toggle_btn.setText("☰")
            self.sidebar_toggle_btn.setToolTip("Show sidebar")
        else:
            self.sidebar_toggle_btn.setText("◀")
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
        self.update_file_listbox_display()
        self.update_metric_options()

    def _on_selection_changed(self):
        self._update_file_count_label()

    def _update_selection_buttons_state(self):
        has_items = self.file_listbox.count() > 0
        self.select_all_btn.setEnabled(has_items)
        self.deselect_all_btn.setEnabled(has_items)

    def _update_filter_combo(self):
        """Populate filter from loaded file categories (dynamic)."""
        cats = ["All"] + sorted(set(self.file_categories.values()))
        current = self.category_combo.currentText()
        self.category_combo.clear()
        self.category_combo.addItems(cats)
        if current in cats:
            self.category_combo.setCurrentText(current)
        elif cats:
            self.category_combo.setCurrentIndex(0)

    def _update_file_count_label(self):
        total = len(self.get_visible_files())
        selected = len(self.file_listbox.selectedItems())
        self.file_count_label.setText(f"{total} loaded, {selected} selected")

    def add_files(self, files):
        for f in files:
            if f not in self.files:
                try:
                    header, rows = read_csv(f)
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"Failed to read {f}: {e}")
                    continue
                typ = detect_csv_type(header)
                category = self.detect_file_category(f)
                self.files.append(f)
                self.file_types[f] = typ
                self.headers[f] = header
                self.rows[f] = rows
                self.file_categories[f] = category
        self._update_filter_combo()
        self.update_metric_options()
        self.update_file_listbox_display()

    def update_file_listbox_display(self):
        filter_cat = self.category_combo.currentText()
        self.file_listbox.clear()
        for f in self.files:
            if filter_cat == "All" or self.file_categories.get(f, "Unknown") == filter_cat:
                typ = self.file_types.get(f, "unknown")
                item = QListWidgetItem(os.path.basename(f) + f"  [{typ}]")
                item.setData(Qt.UserRole, f)
                self.file_listbox.addItem(item)
        self.file_listbox.selectAll()
        self._update_file_count_label()
        self._update_selection_buttons_state()

    def clear_files(self):
        self.files.clear()
        self.file_types.clear()
        self.headers.clear()
        self.rows.clear()
        self.file_categories.clear()
        self.file_listbox.clear()
        self.category_combo.clear()
        self.category_combo.addItem("All")
        self.metric_combo.clear()
        self.ax.clear()
        self.canvas.draw()
        self.summary_label.setText("")
        self.file_count_label.setText("0 loaded, 0 selected")
        self.select_all_btn.setEnabled(False)
        self.deselect_all_btn.setEnabled(False)

    def update_metric_options(self):
        self.metric_combo.blockSignals(True)
        try:
            visible = self.get_visible_files()
            if not visible:
                self.metric_combo.clear()
                return
            metrics = set(get_numeric_columns(self.headers[visible[0]]))
            for f in visible[1:]:
                metrics &= set(get_numeric_columns(self.headers[f]))
            metrics = sorted(metrics)
            old = self.metric_combo.currentText()
            self.metric_combo.clear()
            self.metric_combo.addItems(metrics)
            if old in metrics:
                self.metric_combo.setCurrentText(old)
            elif metrics:
                self.metric_combo.setCurrentIndex(0)
        finally:
            self.metric_combo.blockSignals(False)

    def get_selected_files(self):
        visible = self.get_visible_files()
        selected = self.file_listbox.selectedItems()
        if not selected:
            return visible
        paths = [item.data(Qt.UserRole) for item in selected if item.data(Qt.UserRole) in visible]
        return paths if paths else visible

    def select_all_files(self):
        self.file_listbox.selectAll()
        self._update_file_count_label()

    def deselect_all_files(self):
        self.file_listbox.clearSelection()
        self._update_file_count_label()

    def plot_selected(self):
        selected_files = self.get_selected_files()
        metric = self.metric_combo.currentText()
        if not metric:
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
        plot_type = self.plot_type_combo.currentText()
        n_bars = max(1, len(selected_files))
        bar_width = min(0.4, 0.8 / n_bars) if n_bars <= 3 else min(0.7, 2.4 / n_bars)
        bar_width *= getattr(self, 'bar_width_scale', 1.0)

        for idx, f in enumerate(selected_files):
            header = self.headers[f]
            rows = self.rows[f]
            typ = self.file_types[f]
            x, y, label = self.get_plot_data(header, rows, typ, metric, os.path.basename(f))
            if x and y:
                color = self.color_cycle[idx % len(self.color_cycle)]
                marker = self.marker_cycle[(idx // len(self.linestyle_cycle)) % len(self.marker_cycle)]
                linestyle = self.linestyle_cycle[idx % len(self.linestyle_cycle)]
                use_bar = (plot_type == "Bar") or (plot_type == "Auto" and typ == "websocket")
                if use_bar:
                    if isinstance(x[0], (int, float, np.integer, np.floating)):
                        x_vals = np.array(x) + (idx - (len(selected_files) - 1) / 2) * bar_width
                    else:
                        x_vals = np.arange(len(x)) + (idx - (len(selected_files) - 1) / 2) * bar_width
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
            x_axis_label = self.get_x_axis_column_name(self.headers[selected_files[0]], self.rows[selected_files[0]], self.file_types[selected_files[0]])
        else:
            x_axis_label = "Test Parameter"
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

        if self.ax.lines:
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

        if self.ax.containers:
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

    def get_plot_data(self, header, rows, typ, metric, label):
        file_basename = os.path.splitext(os.path.basename(label))[0] if isinstance(label, str) else ""
        if "Container Name" in header and rows and rows[0].get("Container Name"):
            container_name = str(rows[0]["Container Name"]).strip()
            if file_basename and file_basename != container_name and container_name in file_basename:
                label = file_basename
            else:
                label = container_name
        else:
            label = file_basename or label
        if typ == "websocket":
            for xkey in ["Num Clients", "Message Size (KB)", "Rate (msg/s)", "Bursts", "Duration (s)", "Interval (s)"]:
                if xkey in header:
                    x = [float(r[xkey]) if r.get(xkey) not in (None, '', 'NaN') else 0 for r in rows]
                    y = [float(r[metric]) if r.get(metric) not in (None, '', 'NaN') else 0 for r in rows]
                    return x, y, label
            x = list(range(1, len(rows) + 1))
            y = [float(r[metric]) if r.get(metric) not in (None, '', 'NaN') else 0 for r in rows]
            return x, y, label
        if "Total Requests" in header:
            x = [float(r["Total Requests"]) if r.get("Total Requests") not in (None, '', 'NaN') else 0 for r in rows]
            y = [float(r[metric]) if r.get(metric) not in (None, '', 'NaN') else 0 for r in rows]
            return x, y, label
        x = list(range(1, len(rows) + 1))
        y = [float(r[metric]) if r.get(metric) not in (None, '', 'NaN') else 0 for r in rows]
        return x, y, label

    def get_x_axis_column_name(self, header, rows, typ):
        if typ == "websocket":
            for xkey in ["Num Clients", "Message Size (KB)", "Rate (msg/s)", "Bursts", "Duration (s)", "Interval (s)"]:
                if xkey in header:
                    return xkey
        if "Total Requests" in header:
            return "Total Requests"
        return "Test Parameter"

    def _default_save_path(self):
        """Default path: graphs/<category>/<metric>-<N>bench-<YYYYMMDD-HHMM>.ext"""
        metric = self.metric_combo.currentText() or "graph"
        slug = re.sub(r"[^\w\s-]", "", metric).strip().lower()
        slug = re.sub(r"[-\s]+", "-", slug) or "graph"
        n = len(self.get_selected_files()) or len(self.get_visible_files()) or 0
        ts = datetime.now().strftime("%Y%m%d-%H%M")
        fmt = self.format_combo.currentText().lower()
        ext = ".png" if fmt == "png" else ".pdf" if fmt == "pdf" else ".svg"
        name = f"{slug}-{n}bench-{ts}{ext}"
        base = os.path.abspath("graphs")
        cat = self.category_combo.currentText().lower().replace(" ", "")
        if cat and cat != "all":
            base = os.path.join(base, cat)
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, name)

    def get_visible_files(self):
        filter_cat = self.category_combo.currentText()
        return [f for f in self.files if filter_cat == "All" or self.file_categories.get(f, "Unknown") == filter_cat]

    def export_graph(self):
        if not self.ax.has_data():
            QMessageBox.warning(self, "No graph", "No graph to export. Please plot something first.")
            return
        fmt = self.format_combo.currentText().lower()
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
            "• Filter by category (Static, Dynamic, WebSocket, gRPC, etc — extensible).\n"
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
