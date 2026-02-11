import os
import csv
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.backends._backend_tk import NavigationToolbar2Tk  # linter: correct import for toolbar
import mplcursors  # Always use mplcursors for hover
import numpy as np

# --- Constants for CSV Types ---
WEBSOCKET_HEADERS = [
    "Container Name", "Test Type", "Num CPUs", "Total Messages", "Successful Messages", "Failed Messages", "Execution Time (s)", "Messages/s", "Throughput (MB/s)",
    "Avg Latency (ms)", "Min Latency (ms)", "Max Latency (ms)", "Total Energy (J)", "Avg Power (W)", "Samples", "Avg CPU (%)", "Peak CPU (%)", "Total CPU (%)",
    "Avg Mem (MB)", "Peak Mem (MB)", "Total Mem (MB)", "Pattern", "Num Clients", "Message Size (KB)", "Rate (msg/s)", "Bursts", "Interval (s)", "Duration (s)"
]
HTTP_HEADERS = [
    "Container Name", "Type", "Num CPUs", "Total Requests", "Successful Requests", "Failed Requests", "Execution Time (s)", "Requests/s", "Total Energy (J)", "Avg Power (W)", "Samples", "Avg CPU (%)", "Peak CPU (%)", "Total CPU (%)", "Avg Mem (MB)", "Peak Mem (MB)", "Total Mem (MB)"
]

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
    return {
        'min': min(vals),
        'max': max(vals),
        'avg': sum(vals)/len(vals)
    }

def get_numeric_columns(header):
    # Heuristic: columns with numbers in sample data
    numeric = []
    for h in header:
        if any(x in h.lower() for x in [
            "cpu",
            "mem",
            "latency",
            "throughput",
            "energy",
            "power",
            "requests",
            "messages",
            "samples",
            "rate",
            "size",
            "duration",
            "interval",
            "bursts",
            # Include time-based metrics like "Execution Time (s)"
            "time",
            "execution",
            "runtime"
        ]):
            numeric.append(h)
    return numeric

# --- Main GUI Class ---
class BenchmarkGrapher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Web Server Benchmark Graph Generator")
        self.geometry("1100x700")
        try:
            self.state('zoomed')  # Start maximized (Windows/Linux)
        except Exception:
            self.attributes('-zoomed', True)  # Fallback for some Linux/others
        self.configure(bg="#f7f7f7")
        self.files = []
        self.file_types = {}
        self.headers = {}
        self.rows = {}
        self.file_categories = {}  # Track category (static/dynamic/websocket/local) for each file
        self.selected_metric = tk.StringVar()
        self.selected_files = []
        self.plot_type = tk.StringVar(value="Auto")
        self.category_filter = tk.StringVar(value="All")
        # Use a large, colorblind-friendly palette
        self.color_cycle = []
        for cmap_name in ['tab20', 'tab20b', 'tab20c', 'Set1', 'Set2', 'Set3', 'Dark2', 'Paired', 'Accent', 'Pastel1', 'Pastel2']:
            cmap = plt.get_cmap(cmap_name)
            self.color_cycle.extend([cmap(i) for i in range(cmap.N)])
        # Remove duplicates and keep only visually distinct colors
        self.color_cycle = list(dict.fromkeys(self.color_cycle))
        # Large set of marker shapes
        self.marker_cycle = ['o', 's', 'D', '^', 'v', 'P', '*', 'X', 'h', '+', 'x', '|', '_', '1', '2', '3', '4', '8', '<', '>', '.', ',', 'H', 'd', 'p']
        # Line styles for extra distinction
        self.linestyle_cycle = ['-', '--', '-.', ':']
        self.legend_alpha = 1.0
        # Make bars thicker in bar charts
        self.bar_width_scale = 5.0
        self.init_ui()
        # Global Ctrl+A binding for select all in file listbox
        self.bind_all('<Control-a>', self.global_ctrl_a_select_all)
        self.bind_all('<Control-A>', self.global_ctrl_a_select_all)

    def init_ui(self):
        # --- File Selection ---
        file_frame = tk.Frame(self, bg="#f7f7f7")
        file_frame.pack(fill=tk.X, padx=10, pady=5)
        tk.Label(file_frame, text="Select CSV files:", bg="#f7f7f7", font=("Arial", 12, "bold")).pack(side=tk.LEFT)
        tk.Button(file_frame, text="Browse...", command=self.browse_files, font=("Arial", 11)).pack(side=tk.LEFT, padx=5)
        tk.Button(file_frame, text="Load All CSVs in Folder...", command=self.load_all_csvs_in_folder, font=("Arial", 11)).pack(side=tk.LEFT, padx=5)
        tk.Button(file_frame, text="Clear", command=self.clear_files, font=("Arial", 11)).pack(side=tk.LEFT, padx=5)

        # Place Select All button and category filter above the file listbox
        select_all_frame = tk.Frame(self, bg="#f7f7f7")
        select_all_frame.pack(fill=tk.X, padx=10, pady=(0, 0))
        self.select_all_btn = tk.Button(select_all_frame, text="Select All", command=self.select_all_files, font=("Arial", 10), state=tk.DISABLED)
        self.select_all_btn.pack(side=tk.LEFT, anchor='w')
        tk.Label(select_all_frame, text="Filter by category:", bg="#f7f7f7", font=("Arial", 10)).pack(side=tk.LEFT, padx=(20, 5))
        self.category_combo = ttk.Combobox(select_all_frame, textvariable=self.category_filter, state="readonly", width=15, font=("Arial", 10))
        self.category_combo['values'] = ["All", "Static", "Dynamic", "WebSocket", "Local"]
        self.category_combo.set("All")
        self.category_combo.pack(side=tk.LEFT, padx=5)
        self.category_combo.bind('<<ComboboxSelected>>', lambda e: self.update_file_listbox_display())

        self.file_listbox = tk.Listbox(file_frame, selectmode=tk.MULTIPLE, width=80, height=3, font=("Arial", 10))
        self.file_listbox.pack(side=tk.LEFT, padx=10)
        self.file_listbox.bind('<Double-1>', lambda e: self.plot_selected())
        # Ctrl+A to select all
        self.file_listbox.bind('<Control-a>', self.select_all_files)
        self.file_listbox.bind('<Control-A>', self.select_all_files)
        # Ensure Listbox gets focus on mouse enter or click
        self.file_listbox.bind('<Enter>', lambda e: self.file_listbox.focus_set())
        self.file_listbox.bind('<Button-1>', lambda e: self.file_listbox.focus_set())
        self.file_listbox.bind('<FocusIn>', lambda e: self.file_listbox.focus_set())
        # Optional: Right-click context menu for Select All
        self.file_listbox.bind('<Button-3>', self.show_file_listbox_menu)
        self.file_listbox_menu = tk.Menu(self.file_listbox, tearoff=0)
        self.file_listbox_menu.add_command(label="Select All", command=self.select_all_files)
        # Drag-and-drop support can be added with tkinterDnD2 if desired in the future.

        # --- Metric Selection ---
        metric_frame = tk.Frame(self, bg="#f7f7f7")
        metric_frame.pack(fill=tk.X, padx=10, pady=5)
        tk.Label(metric_frame, text="Metric to plot:", bg="#f7f7f7", font=("Arial", 12, "bold")).pack(side=tk.LEFT)
        self.metric_combo = ttk.Combobox(metric_frame, textvariable=self.selected_metric, state="readonly", width=40, font=("Arial", 11))
        self.metric_combo.pack(side=tk.LEFT, padx=5)
        self.metric_combo.bind('<<ComboboxSelected>>', lambda e: self.plot_selected())

        # --- Plot Type Selection ---
        plot_type_frame = tk.Frame(self, bg="#f7f7f7")
        plot_type_frame.pack(fill=tk.X, padx=10, pady=5)
        tk.Label(plot_type_frame, text="Plot Type:", bg="#f7f7f7", font=("Arial", 12, "bold")).pack(side=tk.LEFT)
        self.plot_type_combo = ttk.Combobox(plot_type_frame, textvariable=self.plot_type, state="readonly", width=20, font=("Arial", 11))
        self.plot_type_combo.pack(side=tk.LEFT, padx=5)
        self.plot_type_combo.bind('<<ComboboxSelected>>', lambda e: self.plot_selected())
        self.plot_type_combo['values'] = ["Auto", "Bar", "Line"]
        self.plot_type_combo.set("Auto")

        # --- Graph Area ---
        self.fig, self.ax = plt.subplots(figsize=(8, 5))
        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Add matplotlib navigation toolbar for zoom/pan
        self.toolbar = NavigationToolbar2Tk(self.canvas, self)
        self.toolbar.update()
        self.toolbar.pack(fill=tk.X, padx=10, pady=(0, 5))

        # --- Save Button ---
        save_frame = tk.Frame(self, bg="#f7f7f7")
        save_frame.pack(fill=tk.X, padx=10, pady=(0, 5))
        self.save_button = tk.Button(save_frame, text="Save Graph", command=self.export_graph, font=("Arial", 11))
        self.save_button.pack(side=tk.LEFT)
        tk.Label(save_frame, text="Format:", bg="#f7f7f7", font=("Arial", 10)).pack(side=tk.LEFT, padx=(20, 5))
        self.export_format = tk.StringVar(value="PNG")
        self.format_combo = ttk.Combobox(save_frame, textvariable=self.export_format, state="readonly", width=10, font=("Arial", 10))
        self.format_combo['values'] = ["PNG", "PDF", "SVG"]
        self.format_combo.set("PNG")
        self.format_combo.pack(side=tk.LEFT, padx=5)

        # --- Summary Area ---
        summary_frame = tk.Frame(self, bg="#f7f7f7")
        summary_frame.pack(fill=tk.X, padx=10, pady=5)
        tk.Label(summary_frame, text="Summary:", bg="#f7f7f7", font=("Arial", 12, "bold")).pack(side=tk.LEFT)
        self.summary_text = tk.Text(summary_frame, height=4, width=120, font=("Arial", 10))
        self.summary_text.pack(side=tk.LEFT, padx=10)
        self.summary_text.config(state=tk.DISABLED)

        # --- Help Button ---
        tk.Button(self, text="Help", command=self.show_help, font=("Arial", 11)).pack(side=tk.RIGHT, padx=10, pady=5)

    def browse_files(self):
        files = filedialog.askopenfilenames(title="Select CSV files", filetypes=[("CSV Files", "*.csv")])
        if files:
            self.add_files(files)

    # Drag-and-drop support removed for compatibility with standard Tkinter.
    # def drop_files(self, event):
    #     files = self.tk.splitlist(event.data)
    #     self.add_files(files)

    def detect_file_category(self, filepath):
        """Detect category (static/dynamic/websocket/local) from file path."""
        path_lower = filepath.lower()
        if 'websocket' in path_lower or 'ws-' in os.path.basename(filepath).lower():
            return "WebSocket"
        elif 'static' in path_lower or 'st-' in os.path.basename(filepath).lower():
            return "Static"
        elif 'dynamic' in path_lower or 'dy-' in os.path.basename(filepath).lower():
            return "Dynamic"
        elif 'local' in path_lower:
            return "Local"
        return "Unknown"

    def add_files(self, files):
        for f in files:
            if f not in self.files:
                try:
                    header, rows = read_csv(f)
                except Exception as e:
                    messagebox.showerror("Error", f"Failed to read {f}: {e}")
                    continue
                typ = detect_csv_type(header)
                category = self.detect_file_category(f)
                self.files.append(f)
                self.file_types[f] = typ
                self.headers[f] = header
                self.rows[f] = rows
                self.file_categories[f] = category
                self.file_listbox.insert(tk.END, os.path.basename(f) + f"  [{typ}]")
        self.update_metric_options()
        self.update_file_listbox_display()
        # Enable Select All button if files are present
        if self.files:
            self.select_all_btn.config(state=tk.NORMAL)
        else:
            self.select_all_btn.config(state=tk.DISABLED)

    def update_file_listbox_display(self):
        """Update file listbox display based on category filter."""
        filter_cat = self.category_filter.get()
        self.file_listbox.delete(0, tk.END)
        for f in self.files:
            if filter_cat == "All" or self.file_categories.get(f, "Unknown") == filter_cat:
                typ = self.file_types.get(f, "unknown")
                self.file_listbox.insert(tk.END, os.path.basename(f) + f"  [{typ}]")

    def clear_files(self):
        self.files.clear()
        self.file_types.clear()
        self.headers.clear()
        self.rows.clear()
        self.file_categories.clear()
        self.file_listbox.delete(0, tk.END)
        self.metric_combo.set("")
        self.ax.clear()
        self.canvas.draw()
        self.summary_text.config(state=tk.NORMAL)
        self.summary_text.delete(1.0, tk.END)
        self.summary_text.config(state=tk.DISABLED)
        # Disable Select All button when no files
        self.select_all_btn.config(state=tk.DISABLED)

    def update_metric_options(self):
        # Show only metrics common to all selected files
        if not self.files:
            self.metric_combo['values'] = []
            return
        metrics = set(get_numeric_columns(self.headers[self.files[0]]))
        for f in self.files[1:]:
            metrics &= set(get_numeric_columns(self.headers[f]))
        metrics = sorted(metrics)
        self.metric_combo['values'] = metrics
        if metrics:
            self.metric_combo.set(metrics[0])
        else:
            self.metric_combo.set("")

    def plot_selected(self):
        selected_indices = self.file_listbox.curselection()
        filter_cat = self.category_filter.get()
        # Map listbox indices to actual file paths (respecting category filter)
        visible_files = [f for f in self.files if filter_cat == "All" or self.file_categories.get(f, "Unknown") == filter_cat]
        if not selected_indices:
            selected_files = visible_files  # Default: all visible files
        else:
            selected_files = [visible_files[i] for i in selected_indices if i < len(visible_files)]
        metric = self.selected_metric.get()
        if not metric:
            messagebox.showwarning("No metric selected", "Please select a metric to plot.")
            return
        self.ax.clear()
        # Remove old mplcursors cursor if it exists
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
        summary_lines = []
        plot_type = self.plot_type.get()
        # For grouped bar chart
        n_bars = max(1, len(selected_files))
        if n_bars <= 3:
            bar_width = min(0.4, 0.8 / n_bars)
        else:
            bar_width = min(0.7, 2.4 / n_bars)
        # Apply global scaling to make bars a bit thicker
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
                # Determine plot type
                if plot_type == "Auto":
                    use_bar = (typ == "websocket")
                elif plot_type == "Bar":
                    use_bar = True
                else:
                    use_bar = False
                if use_bar:
                    # Grouped bars: offset x positions for each file
                    if isinstance(x[0], (int, float, np.integer, np.floating)):
                        x_vals = np.array(x) + (idx - (len(selected_files)-1)/2) * bar_width
                    else:
                        # For categorical x, convert to range
                        x_vals = np.arange(len(x)) + (idx - (len(selected_files)-1)/2) * bar_width
                        self.ax.set_xticks(np.arange(len(x)))
                        self.ax.set_xticklabels([str(val) for val in x])
                    self.ax.bar(x_vals, y, width=bar_width, label=label, color=color,
                        alpha=0.85)
                    # Draw a thin colored line for zero bars
                    for xv, val in zip(x_vals, y):
                        if val == 0:
                            self.ax.plot([xv - bar_width/2, xv + bar_width/2], [0, 0], color=color, linewidth=2, alpha=0.85, solid_capstyle='butt')
                else:
                    self.ax.plot(x, y, label=label, color=color, marker=marker, linestyle=linestyle, linewidth=2, markersize=7)
                stats = summarize_column(rows, metric)
                summary_lines.append(f"{label}: min={stats['min']}, max={stats['max']}, avg={stats['avg']}")
        # Use dynamic x-axis label based on the first selected file
        if selected_files:
            x_axis_label = self.get_x_axis_column_name(self.headers[selected_files[0]], self.rows[selected_files[0]], self.file_types[selected_files[0]])
        else:
            x_axis_label = "Test Parameter"
        # More specific title using actual x-axis column name
        self.ax.set_title(f"{metric} vs. {x_axis_label}")
        self.ax.set_xlabel(x_axis_label)
        self.ax.set_ylabel(metric)
        self.ax.legend(loc='best', framealpha=0.85)
        self.ax.grid(True)
        self.canvas.draw()
        self.summary_text.config(state=tk.NORMAL)
        self.summary_text.delete(1.0, tk.END)
        self.summary_text.insert(tk.END, "\n".join(summary_lines))
        self.summary_text.config(state=tk.DISABLED)

        # Add hover interactivity with mplcursors for lines
        self.cursor = mplcursors.cursor(self.ax.lines, hover=True, highlight=False,
            annotation_kwargs={
                'fontsize': 9,
                'arrowprops': dict(arrowstyle="->", color="#333", lw=1.2),
                'bbox': dict(boxstyle="round,pad=0.2", fc="#f7f7f7", ec="#333", lw=0.8)
            })
        @self.cursor.connect("add")
        def on_add(sel):
            # Reset all lines
            for line in self.ax.get_lines():
                line.set_linewidth(2)
                line.set_alpha(0.7)
            # Highlight only the hovered line
            sel.artist.set_linewidth(4)
            sel.artist.set_alpha(1.0)
            sel.annotation.set_text(sel.artist.get_label())
            # Only one annotation at a time
            for ann in self.ax.texts:
                if ann is not sel.annotation:
                    ann.set_visible(False)
        @self.cursor.connect("remove")
        def on_remove(sel):
            # Reset all lines
            for line in self.ax.get_lines():
                line.set_linewidth(2)
                line.set_alpha(1.0)
            # Hide all annotations
            for ann in self.ax.texts:
                ann.set_visible(False)
            self.canvas.draw_idle()

        # Add hover interactivity with mplcursors for bars
        if self.ax.containers:
            self.bar_cursor = mplcursors.cursor(self.ax.containers, hover=True, highlight=False,
                annotation_kwargs={
                    'fontsize': 9,
                    'arrowprops': dict(arrowstyle="->", color="#333", lw=1.2),
                    'bbox': dict(boxstyle="round,pad=0.2", fc="#f7f7f7", ec="#333", lw=0.8)
                })
            @self.bar_cursor.connect("add")
            def on_bar_add(sel):
                # Reset all bars
                for cont in self.ax.containers:
                    for bar in cont:
                        bar.set_linewidth(0.5)
                        bar.set_edgecolor('black')
                        bar.set_alpha(0.85)
                # Highlight all bars in the same group/series as the hovered bar
                target_label = sel.artist.get_label() if hasattr(sel.artist, 'get_label') else None
                if target_label:
                    for cont in self.ax.containers:
                        for bar in cont:
                            bar_label = bar.get_label() if hasattr(bar, 'get_label') else None
                            if bar_label == target_label:
                                bar.set_linewidth(3)
                                bar.set_edgecolor('#d62728')
                                bar.set_alpha(1.0)
                # Show annotation for the hovered bar
                sel.annotation.set_text(target_label or '')
                # Only one annotation at a time
                for ann in self.ax.texts:
                    if ann is not sel.annotation:
                        ann.set_visible(False)
            @self.bar_cursor.connect("remove")
            def on_bar_remove(sel):
                # Reset all bars
                for cont in self.ax.containers:
                    for bar in cont:
                        bar.set_linewidth(0.5)
                        bar.set_edgecolor('black')
                        bar.set_alpha(0.85)
                # Hide all annotations
                for ann in self.ax.texts:
                    ann.set_visible(False)
                self.canvas.draw_idle()

        # Reset highlights and hide annotation when mouse leaves axes
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
        # Use clean label: "Container Name" from first row if present, else filename without .csv.
        # If filename has a suffix (e.g. _concurrency_sweep, _payload_sweep), use it so multiple
        # CSVs for the same container get distinct legend entries when plotted together.
        file_basename = os.path.splitext(os.path.basename(label))[0] if isinstance(label, str) else ""
        if "Container Name" in header and rows and rows[0].get("Container Name"):
            container_name = str(rows[0]["Container Name"]).strip()
            # Distinct label when this file is a sweep/variant (e.g. ws-phoenix-1-8_concurrency_sweep)
            if file_basename and file_basename != container_name and container_name in file_basename:
                label = file_basename
            else:
                label = container_name
        else:
            label = file_basename or label
        # Heuristics for x-axis:
        if typ == "websocket":
            # Prefer Num Clients, Message Size, Rate, Bursts, Duration, etc.
            for xkey in ["Num Clients", "Message Size (KB)", "Rate (msg/s)", "Bursts", "Duration (s)", "Interval (s)"]:
                if xkey in header:
                    x = [float(r[xkey]) if r.get(xkey) not in (None, '', 'NaN') else 0 for r in rows]
                    y = [float(r[metric]) if r.get(metric) not in (None, '', 'NaN') else 0 for r in rows]
                    return x, y, label
            # Fallback for websocket
            x = list(range(1, len(rows)+1))
            y = [float(r[metric]) if r.get(metric) not in (None, '', 'NaN') else 0 for r in rows]
            return x, y, label
        # HTTP: Prefer Total Requests
        if "Total Requests" in header:
            x = [float(r["Total Requests"]) if r.get("Total Requests") not in (None, '', 'NaN') else 0 for r in rows]
            y = [float(r[metric]) if r.get(metric) not in (None, '', 'NaN') else 0 for r in rows]
            return x, y, label
        # Fallback: row index
        x = list(range(1, len(rows)+1))
        y = [float(r[metric]) if r.get(metric) not in (None, '', 'NaN') else 0 for r in rows]
        return x, y, label

    def get_x_axis_column_name(self, header, rows, typ):
        """Get a meaningful name for the x-axis based on the data being plotted."""
        if typ == "websocket":
            # Check for common WebSocket test parameters
            for xkey in ["Num Clients", "Message Size (KB)", "Rate (msg/s)", "Bursts", "Duration (s)", "Interval (s)"]:
                if xkey in header:
                    return xkey
        # Check for common HTTP test parameters
        if "Total Requests" in header:
            return "Total Requests"
        # If no obvious parameter found, return a generic label
        return "Test Parameter"

    def export_graph(self):
        if not self.ax.has_data():
            messagebox.showwarning("No graph", "No graph to export. Please plot something first.")
            return
        fmt = self.export_format.get().lower()
        if fmt == "png":
            filetypes = [("PNG Image", "*.png")]
            defaultext = ".png"
            save_kwargs = {"dpi": 300}
        elif fmt == "pdf":
            filetypes = [("PDF Document", "*.pdf")]
            defaultext = ".pdf"
            save_kwargs = {}
        elif fmt == "svg":
            filetypes = [("SVG Image", "*.svg")]
            defaultext = ".svg"
            save_kwargs = {}
        else:
            filetypes = [("PNG Image", "*.png")]
            defaultext = ".png"
            save_kwargs = {"dpi": 300}
        file = filedialog.asksaveasfilename(defaultextension=defaultext, filetypes=filetypes)
        if file:
            # Ensure file extension matches format
            if not file.lower().endswith(defaultext):
                file = file + defaultext
            # Save with tight layout to trim white spaces
            self.fig.savefig(file, bbox_inches='tight', pad_inches=0.1, format=fmt, **save_kwargs)
            messagebox.showinfo("Exported", f"Graph exported to {file}")

    def show_help(self):
        msg = (
            "Web Server Benchmark Graph Generator\n\n"
            "- Select one or more CSV files (from results directories).\n"
            "- The tool auto-detects file type (HTTP, WebSocket, etc).\n"
            "- Filter files by category (Static, Dynamic, WebSocket, Local).\n"
            "- Choose a metric to plot (latency, throughput, CPU, etc).\n"
            "- Overlay/combine results from multiple files.\n"
            "- Export graphs as PNG, PDF, or SVG.\n"
            "- Summary stats (min, max, avg) shown below the graph.\n"
            "- Double-click a file to plot.\n"
            "- Load All CSVs in Folder: recursively loads all CSVs from subfolders.\n"
            "- Handles all result types (static, dynamic, local, websocket).\n"
            "- If a metric is missing in a file, it is skipped.\n"
            "- Graph title shows specific x-axis parameter (e.g. 'Messages/s vs. Num Clients').\n"
        )
        messagebox.showinfo("Help", msg)

    def select_all_files(self, event=None):
        self.file_listbox.select_set(0, tk.END)
        return 'break'

    def global_ctrl_a_select_all(self, event=None):
        # Only select all if file_listbox has focus, and avoid KeyError if dialog is open
        try:
            focus_widget = self.file_listbox.focus_get()
            if focus_widget is self.file_listbox:
                self.select_all_files()
                return 'break'
        except Exception:
            pass

    def show_file_listbox_menu(self, event):
        try:
            self.file_listbox_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.file_listbox_menu.grab_release()

    def load_all_csvs_in_folder(self):
        folder = filedialog.askdirectory(title="Select Folder Containing CSV Files")
        if folder:
            csv_files = []
            # Recursively find all CSV files in folder and subfolders
            for root, dirs, files in os.walk(folder):
                for f in files:
                    if f.lower().endswith('.csv'):
                        csv_files.append(os.path.join(root, f))
            if not csv_files:
                messagebox.showwarning("No CSVs", "No CSV files found in the selected folder.")
                return
            self.add_files(csv_files)

if __name__ == "__main__":
    app = BenchmarkGrapher()
    app.mainloop()
