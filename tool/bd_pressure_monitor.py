#!/usr/bin/env python3
"""BD Pressure serial monitor with a responsive Tkinter interface."""

from __future__ import annotations

import argparse
import queue
import re
import threading
import time
import tkinter as tk
from collections import deque
from tkinter import messagebox, ttk

import serial
import serial.tools.list_ports
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure


DEFAULT_PORT = ""
DEFAULT_BAUD = 38400
MAX_POINTS = 10_000
DEFAULT_WINDOW_SECONDS = 5

BG = "#FFFFFF"
PANEL = "#FFFFFF"
CARD = "#F6F8FC"
CARD_HOVER = "#EAF0F8"
BORDER = "#DCE3EE"
ACCENT = "#2F80FF"
ACCENT_HOVER = "#1769E0"
GREEN = "#16A36A"
RED = "#FF5D6C"
YELLOW = "#C98500"
TEXT = "#172033"
MUTED = "#667085"
GRID = "#E4EAF2"

NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")

COMMAND_OPTIONS = (
    ("e;", "Endstop mode"),
    ("d;", "ADC data output via UART"),
    ("D;", "Disable ADC data output via UART"),
    ("l;", "PA mode"),
    ("i;", "Normal ADC"),
    ("I;", "Inverted ADC"),
)


class SerialWorker:
    """Own the serial port and perform all reads on one background thread."""

    def __init__(self, event_queue: queue.Queue):
        self.event_queue = event_queue
        self.serial_port: serial.Serial | None = None
        self.reader_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.write_lock = threading.Lock()
        self.connected = False

    def connect(self, port: str, baud: int) -> None:
        self.disconnect()
        self.serial_port = serial.Serial(
            port=port,
            baudrate=baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.1,
        )
        self.serial_port.reset_input_buffer()
        self.serial_port.reset_output_buffer()
        self.stop_event.clear()
        self.connected = True
        self.reader_thread = threading.Thread(
            target=self._read_loop,
            name="serial-reader",
            daemon=True,
        )
        self.reader_thread.start()

    def disconnect(self) -> None:
        self.stop_event.set()
        port = self.serial_port
        self.serial_port = None
        self.connected = False

        if port is not None:
            try:
                port.close()
            except serial.SerialException:
                pass

        thread = self.reader_thread
        self.reader_thread = None
        if thread and thread is not threading.current_thread():
            thread.join(timeout=0.5)

    def send(self, command: str) -> str:
        port = self.serial_port
        if not self.connected or port is None or not port.is_open:
            raise serial.SerialException("Serial port is not connected")

        command = command.strip()
        if not command:
            raise ValueError("Command is empty")
        if not command.endswith(";"):
            command += ";"

        payload = (command + "\n").encode("ascii")
        with self.write_lock:
            port.write(payload)
            port.flush()
        return command

    def _read_loop(self) -> None:
        buffer = bytearray()

        while not self.stop_event.is_set():
            port = self.serial_port
            if port is None:
                break

            try:
                chunk = port.read(max(1, port.in_waiting))
            except (serial.SerialException, OSError) as exc:
                if not self.stop_event.is_set():
                    self._put_event(("error", str(exc)))
                break

            if not chunk:
                continue

            buffer.extend(chunk)
            while b"\n" in buffer:
                raw_line, _, remainder = buffer.partition(b"\n")
                buffer = bytearray(remainder)
                text = raw_line.rstrip(b"\r").decode("ascii", errors="replace").strip()
                if text:
                    self._put_event(("line", time.monotonic(), text))

        self.connected = False

    def _put_event(self, event: tuple) -> None:
        try:
            self.event_queue.put_nowait(event)
        except queue.Full:
            try:
                self.event_queue.get_nowait()
                self.event_queue.put_nowait(event)
            except queue.Empty:
                pass


class SerialMonitorApp:
    def __init__(self, root: tk.Tk, initial_port: str, initial_baud: int, auto_connect: bool):
        self.root = root
        self.initial_port = initial_port
        self.initial_baud = initial_baud
        self.auto_connect = auto_connect

        self.events: queue.Queue = queue.Queue(maxsize=20_000)
        self.worker = SerialWorker(self.events)

        self.start_time = time.monotonic()
        self.time_data: deque[float] = deque(maxlen=MAX_POINTS)
        self.value_data: deque[float] = deque(maxlen=MAX_POINTS)
        self.response_lines: deque[str] = deque(maxlen=160)
        self.response_dirty = False
        self.paused = False
        self.last_plot_update = 0.0
        self.last_response_update = 0.0

        self.port_var = tk.StringVar(value=initial_port)
        self.baud_var = tk.StringVar(value=str(initial_baud))
        self.status_var = tk.StringVar(value="Disconnected")
        self.latest_var = tk.StringVar(value="--")
        self.min_var = tk.StringVar(value="--")
        self.max_var = tk.StringVar(value="--")
        self.points_var = tk.StringVar(value="0")
        self.window_var = tk.DoubleVar(value=DEFAULT_WINDOW_SECONDS)
        self.auto_scale_var = tk.BooleanVar(value=True)
        self.command_var = tk.StringVar()
        self.command_hint_var = tk.StringVar(value="Select a command or enter a custom command")
        self.command_items = [
            f"Send {command:<3}  {description}"
            for command, description in COMMAND_OPTIONS
        ]
        self.command_lookup = {
            display: (command, description)
            for display, (command, description) in zip(self.command_items, COMMAND_OPTIONS)
        }

        self._configure_window()
        self._configure_styles()
        self._build_interface()
        self.refresh_ports()
        self._bind_shortcuts()

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(40, self._poll_events)
        self.root.after(100, self._refresh_plot)

        if auto_connect:
            self.root.after(250, self._auto_connect)

    def _configure_window(self) -> None:
        self.root.title("BD Pressure Monitor")
        self.root.geometry("1280x760")
        self.root.minsize(960, 600)
        self.root.configure(bg=BG)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")

        style.configure(
            "Dark.TCombobox",
            fieldbackground=CARD,
            background=CARD,
            foreground=TEXT,
            arrowcolor=MUTED,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            padding=6,
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", CARD)],
            foreground=[("readonly", TEXT)],
            selectbackground=[("readonly", CARD)],
            selectforeground=[("readonly", TEXT)],
        )
        style.configure(
            "Dark.Horizontal.TScale",
            background=PANEL,
            troughcolor=CARD,
            bordercolor=PANEL,
            lightcolor=PANEL,
            darkcolor=PANEL,
        )
        style.configure(
            "Dark.TCheckbutton",
            background=PANEL,
            foreground=MUTED,
            indicatorbackground=CARD,
            indicatorforeground=ACCENT,
            focuscolor=PANEL,
        )
        style.map(
            "Dark.TCheckbutton",
            background=[("active", PANEL)],
            foreground=[("active", TEXT)],
        )

    def _build_interface(self) -> None:
        shell = tk.Frame(self.root, bg=BG)
        shell.pack(fill="both", expand=True, padx=18, pady=16)

        self._build_header(shell)

        content = tk.Frame(shell, bg=BG)
        content.pack(fill="both", expand=True, pady=(14, 0))
        content.grid_rowconfigure(0, weight=1)
        content.grid_columnconfigure(0, weight=4)
        content.grid_columnconfigure(1, weight=2)

        plot_card = self._card(content)
        plot_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        plot_card.grid_rowconfigure(1, weight=1)
        plot_card.grid_columnconfigure(0, weight=1)
        self._build_plot(plot_card)

        side = tk.Frame(content, bg=BG)
        side.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        side.grid_rowconfigure(1, weight=1)
        side.grid_columnconfigure(0, weight=1)
        self._build_stats(side)
        self._build_response(side)

        self._build_commands(shell)

    def _build_header(self, parent: tk.Widget) -> None:
        header = tk.Frame(parent, bg=BG)
        header.pack(fill="x")

        title_box = tk.Frame(header, bg=BG)
        title_box.pack(side="left")
        tk.Label(
            title_box,
            text="BD Pressure Monitor",
            bg=BG,
            fg=TEXT,
            font=("TkDefaultFont", 18, "bold"),
        ).pack(anchor="w")
        tk.Label(
            title_box,
            text="Live serial data acquisition and control",
            bg=BG,
            fg=MUTED,
            font=("TkDefaultFont", 9),
        ).pack(anchor="w", pady=(2, 0))

        controls = tk.Frame(header, bg=BG)
        controls.pack(side="right", fill="y")

        self.status_dot = tk.Label(controls, text="●", bg=BG, fg=RED, font=("TkDefaultFont", 13))
        self.status_dot.pack(side="left", padx=(0, 5))
        tk.Label(controls, textvariable=self.status_var, bg=BG, fg=MUTED).pack(
            side="left", padx=(0, 16)
        )

        self.port_combo = ttk.Combobox(
            controls,
            textvariable=self.port_var,
            width=18,
            state="readonly",
            style="Dark.TCombobox",
        )
        self.port_combo.pack(side="left", padx=(0, 6))

        self._button(controls, "↻", self.refresh_ports, width=3, bg=CARD).pack(
            side="left", padx=(0, 10)
        )

        baud_combo = ttk.Combobox(
            controls,
            textvariable=self.baud_var,
            values=("9600", "19200", "38400", "57600", "115200", "230400"),
            width=9,
            state="normal",
            style="Dark.TCombobox",
        )
        baud_combo.pack(side="left", padx=(0, 10))

        self.connect_button = self._button(
            controls,
            "Connect",
            self.toggle_connection,
            width=12,
            bg=ACCENT,
            active=ACCENT_HOVER,
        )
        self.connect_button.pack(side="left")

    def _build_plot(self, parent: tk.Widget) -> None:
        heading = tk.Frame(parent, bg=PANEL)
        heading.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))
        tk.Label(
            heading,
            text="Live waveform",
            bg=PANEL,
            fg=TEXT,
            font=("TkDefaultFont", 12, "bold"),
        ).pack(side="left")
        self.plot_state_label = tk.Label(heading, text="LIVE", bg=PANEL, fg=GREEN)
        self.plot_state_label.pack(side="right")

        figure = Figure(figsize=(8, 5), dpi=100, facecolor=PANEL)
        self.axes = figure.add_subplot(111)
        self.axes.set_facecolor(PANEL)
        self.axes.tick_params(colors=MUTED, labelsize=8)
        self.axes.xaxis.label.set_color(MUTED)
        self.axes.yaxis.label.set_color(MUTED)
        self.axes.set_xlabel("Time (s)")
        self.axes.set_ylabel("Value")
        self.axes.grid(True, color=GRID, alpha=0.8, linewidth=0.6)
        for spine in self.axes.spines.values():
            spine.set_color(BORDER)
        self.axes.set_xlim(0, DEFAULT_WINDOW_SECONDS)
        self.axes.set_ylim(4000, 6000)
        (self.plot_line,) = self.axes.plot([], [], color=ACCENT, linewidth=1.25)
        figure.tight_layout(pad=1.8)

        canvas_frame = tk.Frame(parent, bg=PANEL)
        canvas_frame.grid(row=1, column=0, sticky="nsew", padx=8)
        self.canvas = FigureCanvasTkAgg(figure, master=canvas_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        toolbar_host = tk.Frame(parent, bg=PANEL)
        toolbar_host.grid(row=2, column=0, sticky="ew", padx=10)
        toolbar = NavigationToolbar2Tk(self.canvas, toolbar_host, pack_toolbar=False)
        toolbar.config(background=PANEL)
        toolbar.update()
        toolbar.pack(side="left")

        plot_controls = tk.Frame(parent, bg=PANEL)
        plot_controls.grid(row=3, column=0, sticky="ew", padx=16, pady=(6, 14))
        tk.Label(plot_controls, text="Window", bg=PANEL, fg=MUTED).pack(side="left")
        ttk.Scale(
            plot_controls,
            from_=1,
            to=30,
            variable=self.window_var,
            orient="horizontal",
            style="Dark.Horizontal.TScale",
            length=170,
        ).pack(side="left", padx=(8, 6))
        self.window_label = tk.Label(plot_controls, text="5 s", bg=PANEL, fg=TEXT, width=5)
        self.window_label.pack(side="left", padx=(0, 14))
        ttk.Checkbutton(
            plot_controls,
            text="Auto Y scale",
            variable=self.auto_scale_var,
            style="Dark.TCheckbutton",
        ).pack(side="left")

        self.pause_button = self._button(plot_controls, "Pause", self.toggle_pause, width=9, bg=CARD)
        self.pause_button.pack(side="right", padx=(8, 0))
        self._button(plot_controls, "Clear", self.clear_data, width=9, bg=CARD).pack(side="right")

    def _build_stats(self, parent: tk.Widget) -> None:
        card = self._card(parent)
        card.grid(row=0, column=0, sticky="ew", pady=(0, 14))

        tk.Label(
            card,
            text="Signal summary",
            bg=PANEL,
            fg=TEXT,
            font=("TkDefaultFont", 11, "bold"),
        ).pack(anchor="w", padx=16, pady=(14, 10))

        stats = tk.Frame(card, bg=PANEL)
        stats.pack(fill="x", padx=12, pady=(0, 14))
        items = (
            ("LATEST", self.latest_var),
            ("MIN", self.min_var),
            ("MAX", self.max_var),
            ("POINTS", self.points_var),
        )
        for index, (label, variable) in enumerate(items):
            box = tk.Frame(stats, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
            box.grid(row=0, column=index, sticky="ew", padx=4)
            stats.grid_columnconfigure(index, weight=1)
            tk.Label(box, text=label, bg=CARD, fg=MUTED, font=("TkDefaultFont", 7)).pack(
                pady=(8, 2)
            )
            tk.Label(
                box,
                textvariable=variable,
                bg=CARD,
                fg=TEXT,
                font=("TkDefaultFont", 11, "bold"),
            ).pack(pady=(0, 8))

    def _build_response(self, parent: tk.Widget) -> None:
        card = self._card(parent)
        card.grid(row=1, column=0, sticky="nsew")
        card.grid_rowconfigure(1, weight=1)
        card.grid_columnconfigure(0, weight=1)

        heading = tk.Frame(card, bg=PANEL)
        heading.grid(row=0, column=0, sticky="ew", padx=16, pady=(13, 8))
        tk.Label(
            heading,
            text="Serial response",
            bg=PANEL,
            fg=TEXT,
            font=("TkDefaultFont", 11, "bold"),
        ).pack(side="left")
        self._button(heading, "Clear", self.clear_response, width=7, bg=CARD).pack(side="right")

        text_host = tk.Frame(card, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        text_host.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 10))
        scrollbar = ttk.Scrollbar(text_host, orient="vertical")
        scrollbar.pack(side="right", fill="y")
        self.response_text = tk.Text(
            text_host,
            bg=CARD,
            fg="#344054",
            insertbackground=TEXT,
            relief="flat",
            borderwidth=0,
            wrap="word",
            state="disabled",
            font=("TkFixedFont", 9),
            padx=10,
            pady=8,
            yscrollcommand=scrollbar.set,
        )
        self.response_text.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.response_text.yview)

        custom = tk.Frame(card, bg=PANEL)
        custom.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 14))
        custom.grid_columnconfigure(0, weight=1)
        self.command_combo = ttk.Combobox(
            custom,
            textvariable=self.command_var,
            values=self.command_items,
            state="normal",
            style="Dark.TCombobox",
            font=("TkFixedFont", 10),
        )
        self.command_combo.grid(row=0, column=0, sticky="ew", ipady=4, padx=(0, 8))
        self.command_combo.bind("<<ComboboxSelected>>", self._on_command_selected)
        self.command_combo.bind("<Return>", lambda _event: self.send_custom_command())
        self._button(custom, "Send", self.send_custom_command, width=8, bg=ACCENT).grid(
            row=0, column=1
        )
        tk.Label(
            custom,
            textvariable=self.command_hint_var,
            bg=PANEL,
            fg=MUTED,
            anchor="w",
            font=("TkDefaultFont", 8),
        ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))

    def _build_commands(self, parent: tk.Widget) -> None:
        card = self._card(parent)
        card.pack(fill="x", pady=(14, 0))

        title = tk.Frame(card, bg=PANEL)
        title.pack(fill="x", padx=16, pady=(12, 8))
        tk.Label(
            title,
            text="Quick commands",
            bg=PANEL,
            fg=TEXT,
            font=("TkDefaultFont", 11, "bold"),
        ).pack(side="left")
        tk.Label(title, text="Keyboard shortcuts available", bg=PANEL, fg=MUTED).pack(side="right")

        buttons = tk.Frame(card, bg=PANEL)
        buttons.pack(fill="x", padx=12, pady=(0, 14))
        commands = (
            ("Endstop", "e"),
            ("Raw on", "d"),
            ("Raw off", "D"),
            ("PA mode", "l"),
            ("Normal", "i"),
            ("Inverted", "I"),
            ("Set normal", "N"),
        )
        for index, (label, command) in enumerate(commands):
            button = self._button(
                buttons,
                f"{label}   {command};",
                lambda cmd=command: self.send_command(cmd),
                bg=CARD,
            )
            button.grid(row=0, column=index, sticky="ew", padx=4)
            buttons.grid_columnconfigure(index, weight=1)

        self._button(buttons, "Help   ?", self.show_help, bg=CARD).grid(
            row=0, column=len(commands), sticky="ew", padx=4
        )
        buttons.grid_columnconfigure(len(commands), weight=1)

    def _card(self, parent: tk.Widget) -> tk.Frame:
        return tk.Frame(parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)

    def _button(
        self,
        parent: tk.Widget,
        text: str,
        command,
        width: int = 0,
        bg: str = CARD,
        active: str = CARD_HOVER,
    ) -> tk.Button:
        foreground = "#FFFFFF" if bg in (ACCENT, RED) else TEXT
        return tk.Button(
            parent,
            text=text,
            command=command,
            width=width,
            bg=bg,
            activebackground=active,
            fg=foreground,
            activeforeground=foreground,
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            padx=10,
            pady=7,
        )

    def _bind_shortcuts(self) -> None:
        self.root.bind("<space>", lambda _event: self.toggle_pause())
        self.root.bind("<Control-l>", lambda _event: self.clear_data())
        self.root.bind("<F1>", lambda _event: self.show_help())

    def refresh_ports(self) -> None:
        discovered = sorted(
            {item.device for item in serial.tools.list_ports.comports()},
            key=str.casefold,
        )
        preferred = self.port_var.get().strip() or self.initial_port
        self.port_combo["values"] = discovered

        if preferred in discovered:
            self.port_var.set(preferred)
        elif discovered:
            self.port_var.set(discovered[0])
        else:
            self.port_var.set("")

        if self.worker.connected:
            self.port_combo.config(state="disabled")
            self.connect_button.config(state="normal")
        elif discovered:
            self.port_combo.config(state="readonly")
            self.connect_button.config(state="normal")
            if self.status_var.get() == "No serial port":
                self._set_connected_state(False)
        else:
            self.port_combo.config(state="disabled")
            self.connect_button.config(state="disabled")
            self.status_var.set("No serial port")
            self.status_dot.config(fg=YELLOW)

    def _auto_connect(self) -> None:
        if self.port_var.get().strip():
            self.connect()

    def toggle_connection(self) -> None:
        if self.worker.connected:
            self.disconnect()
        else:
            self.connect()

    def connect(self) -> None:
        port = self.port_var.get().strip()
        try:
            baud = int(self.baud_var.get().strip())
            if baud <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid baud rate", "Enter a positive integer baud rate.")
            return

        if not port:
            messagebox.showwarning(
                "No serial port",
                "No serial port was detected. Connect the device and click Refresh.",
            )
            return

        try:
            self.worker.connect(port, baud)
        except (serial.SerialException, OSError) as exc:
            self._set_connected_state(False)
            self._append_response(f"[ERROR] Cannot open {port}: {exc}")
            return

        self.start_time = time.monotonic()
        self.clear_data()
        self.clear_response()
        self._set_connected_state(True)
        self.port_combo.config(state="disabled")
        self._append_response(f"[CONNECTED] {port} @ {baud} bps")
        self.root.after(200, lambda: self.send_command("d"))

    def disconnect(self) -> None:
        was_connected = self.worker.connected
        self.worker.disconnect()
        self._set_connected_state(False)
        self.refresh_ports()
        if was_connected:
            self._append_response("[DISCONNECTED]")

    def _set_connected_state(self, connected: bool) -> None:
        if connected:
            self.status_var.set("Connected")
            self.status_dot.config(fg=GREEN)
            self.connect_button.config(text="Disconnect", bg=RED, activebackground="#FF7A87")
        else:
            self.status_var.set("Disconnected")
            self.status_dot.config(fg=RED)
            self.connect_button.config(text="Connect", bg=ACCENT, activebackground=ACCENT_HOVER)

    def send_command(self, command: str) -> None:
        try:
            sent = self.worker.send(command)
        except (serial.SerialException, OSError, ValueError) as exc:
            self._append_response(f"[ERROR] {exc}")
            return
        self._append_response(f"> {sent}")

    def send_custom_command(self) -> None:
        selection = self.command_var.get().strip()
        if selection:
            command, _description = self.command_lookup.get(selection, (selection, "Custom command"))
            self.send_command(command)
            self.command_var.set("")
            self.command_hint_var.set("Select a command or enter a custom command")

    def _on_command_selected(self, _event=None) -> None:
        selection = self.command_var.get()
        command_data = self.command_lookup.get(selection)
        if command_data:
            command, description = command_data
            self.command_var.set(command)
            self.command_hint_var.set(f"{command}  —  {description}")

    def _poll_events(self) -> None:
        processed = 0
        while processed < 5000:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            processed += 1

            if event[0] == "line":
                _, timestamp, line = event
                self._handle_serial_line(timestamp, line)
            elif event[0] == "error":
                _, error = event
                self._append_response(f"[SERIAL ERROR] {error}")
                self.worker.disconnect()
                self._set_connected_state(False)

        self.root.after(40, self._poll_events)

    def _handle_serial_line(self, timestamp: float, line: str) -> None:
        self._append_response(line)
        cleaned = line.replace(";", "").strip()
        if not NUMBER_RE.fullmatch(cleaned):
            return

        try:
            value = float(cleaned)
        except ValueError:
            return
        self.time_data.append(timestamp - self.start_time)
        self.value_data.append(value)

    def _append_response(self, line: str) -> None:
        self.response_lines.append(line)
        self.response_dirty = True

    def _update_response_widget(self) -> None:
        self.response_text.config(state="normal")
        self.response_text.delete("1.0", "end")
        self.response_text.insert("end", "\n".join(self.response_lines))
        self.response_text.see("end")
        self.response_text.config(state="disabled")
        self.response_dirty = False

    def clear_response(self) -> None:
        self.response_lines.clear()
        self.response_dirty = True

    def clear_data(self) -> None:
        self.time_data.clear()
        self.value_data.clear()
        self.latest_var.set("--")
        self.min_var.set("--")
        self.max_var.set("--")
        self.points_var.set("0")
        self.plot_line.set_data([], [])
        self.axes.set_xlim(0, max(1, self.window_var.get()))
        if not self.auto_scale_var.get():
            self.axes.set_ylim(4000, 6000)
        self.canvas.draw_idle()

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        self.pause_button.config(text="Resume" if self.paused else "Pause")
        self.plot_state_label.config(
            text="PAUSED" if self.paused else "LIVE",
            fg=YELLOW if self.paused else GREEN,
        )

    def _refresh_plot(self) -> None:
        now = time.monotonic()
        window = max(1.0, self.window_var.get())
        self.window_label.config(text=f"{window:.0f} s")

        if self.response_dirty and now - self.last_response_update >= 0.20:
            self._update_response_widget()
            self.last_response_update = now

        if not self.paused and self.value_data:
            times = list(self.time_data)
            values = list(self.value_data)
            latest_time = times[-1]
            x_min = max(0.0, latest_time - window)
            x_max = max(window, latest_time + window * 0.04)

            first_visible = 0
            for index in range(len(times) - 1, -1, -1):
                if times[index] < x_min:
                    first_visible = index + 1
                    break

            visible_times = times[first_visible:]
            visible_values = values[first_visible:]
            self.plot_line.set_data(visible_times, visible_values)
            self.axes.set_xlim(x_min, x_max)

            if self.auto_scale_var.get() and visible_values:
                low = min(visible_values)
                high = max(visible_values)
                span = high - low
                margin = max(1.0, span * 0.12, abs(high) * 0.002)
                self.axes.set_ylim(low - margin, high + margin)

            self.latest_var.set(f"{values[-1]:.2f}")
            self.min_var.set(f"{min(values):.2f}")
            self.max_var.set(f"{max(values):.2f}")
            self.points_var.set(str(len(values)))
            self.canvas.draw_idle()
            self.last_plot_update = now

        self.root.after(100, self._refresh_plot)

    def show_help(self) -> None:
        help_lines = (
            "Available commands:",
            "e;  Endstop / probe mode",
            "d;  Enable raw data output",
            "D;  Disable raw data output",
            "l;  Pressure Advance mode",
            "i;  Normal polarity",
            "I;  Inverted polarity",
            "N;  Use current data as normal",
            "",
            "Shortcuts:",
            "Space  Pause / resume plot",
            "Ctrl+L Clear plot",
            "F1     Show this help",
        )
        for line in help_lines:
            self._append_response(line)

    def close(self) -> None:
        self.worker.disconnect()
        self.root.destroy()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BD Pressure serial monitor")
    parser.add_argument("port", nargs="?", default=DEFAULT_PORT, help="serial port")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help="baud rate")
    parser.add_argument(
        "--no-auto-connect",
        action="store_true",
        help="start disconnected",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    root = tk.Tk()
    SerialMonitorApp(
        root,
        initial_port=args.port,
        initial_baud=args.baud,
        auto_connect=not args.no_auto_connect,
    )
    root.mainloop()


if __name__ == "__main__":
    main()
