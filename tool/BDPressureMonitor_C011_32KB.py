#!/usr/bin/env python3
"""BD Pressure serial monitor with a responsive Tkinter interface."""

from __future__ import annotations

import argparse
import queue
import re
import struct
import threading
import time
import tkinter as tk
from collections import deque
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

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

STM32_FLASH_BASE = 0x08000000
STM32C011_PID = 0x443
STM32_BOOT_BAUD = 115200
STM32_ACK = 0x79
STM32_NACK = 0x1F
STM32C011_VARIANTS = {
    "STM32C011x6 — 32 KB": 32 * 1024,
}


class STM32BootloaderError(RuntimeError):
    """Raised when the STM32 ROM bootloader rejects or times out."""


def _xor_bytes(data: bytes) -> int:
    checksum = 0
    for value in data:
        checksum ^= value
    return checksum


def load_firmware_image(filename: str) -> tuple[int, bytes]:
    """Load a BIN or Intel HEX image and return (base_address, data)."""

    path = Path(filename)
    suffix = path.suffix.lower()

    if suffix == ".bin":
        data = path.read_bytes()
        if not data:
            raise ValueError("The firmware file is empty.")
        return STM32_FLASH_BASE, data

    if suffix != ".hex":
        raise ValueError("Only .bin and Intel HEX (.hex) firmware files are supported.")

    memory: dict[int, int] = {}
    address_base = 0
    eof_seen = False

    for line_number, text in enumerate(path.read_text(encoding="ascii").splitlines(), start=1):
        text = text.strip()
        if not text:
            continue
        if not text.startswith(":"):
            raise ValueError(f"HEX line {line_number}: missing ':' prefix.")

        try:
            record = bytes.fromhex(text[1:])
        except ValueError as exc:
            raise ValueError(f"HEX line {line_number}: invalid hexadecimal data.") from exc

        if len(record) < 5:
            raise ValueError(f"HEX line {line_number}: record is too short.")
        if (sum(record) & 0xFF) != 0:
            raise ValueError(f"HEX line {line_number}: checksum mismatch.")

        count = record[0]
        if len(record) != count + 5:
            raise ValueError(f"HEX line {line_number}: invalid byte count.")

        offset = (record[1] << 8) | record[2]
        record_type = record[3]
        payload = record[4 : 4 + count]

        if record_type == 0x00:
            absolute = address_base + offset
            for index, value in enumerate(payload):
                address = absolute + index
                previous = memory.get(address)
                if previous is not None and previous != value:
                    raise ValueError(
                        f"HEX line {line_number}: conflicting data at 0x{address:08X}."
                    )
                memory[address] = value
        elif record_type == 0x01:
            eof_seen = True
            break
        elif record_type == 0x02:
            if count != 2:
                raise ValueError(f"HEX line {line_number}: invalid segment address.")
            address_base = int.from_bytes(payload, "big") << 4
        elif record_type == 0x04:
            if count != 2:
                raise ValueError(f"HEX line {line_number}: invalid linear address.")
            address_base = int.from_bytes(payload, "big") << 16
        elif record_type in (0x03, 0x05):
            continue
        else:
            raise ValueError(
                f"HEX line {line_number}: unsupported record type 0x{record_type:02X}."
            )

    if not eof_seen:
        raise ValueError("HEX file does not contain an EOF record.")
    if not memory:
        raise ValueError("HEX file does not contain firmware data.")

    start = min(memory)
    end = max(memory) + 1
    data = bytearray([0xFF]) * (end - start)
    for address, value in memory.items():
        data[address - start] = value
    return start, bytes(data)


def validate_firmware_image(base_address: int, data: bytes, flash_size: int) -> bytes:
    """Validate address/range and pad the image for STM32C0 double-word writes."""

    flash_end = STM32_FLASH_BASE + flash_size
    if base_address < STM32_FLASH_BASE or base_address + len(data) > flash_end:
        raise ValueError(
            f"Firmware range 0x{base_address:08X}-0x{base_address + len(data) - 1:08X} "
            f"is outside the selected STM32C011 flash range "
            f"0x{STM32_FLASH_BASE:08X}-0x{flash_end - 1:08X}."
        )
    if base_address % 8:
        raise ValueError("Firmware start address must be aligned to 8 bytes.")

    if base_address == STM32_FLASH_BASE and len(data) >= 8:
        initial_sp, reset_handler = struct.unpack_from("<II", data)
        valid_sp = 0x20000000 <= initial_sp <= 0x20001800
        reset_address = reset_handler & ~1
        valid_reset = bool(reset_handler & 1) and STM32_FLASH_BASE <= reset_address < flash_end
        if not valid_sp or not valid_reset:
            raise ValueError(
                "The firmware vector table is not valid for STM32C011 "
                f"(SP=0x{initial_sp:08X}, Reset=0x{reset_handler:08X})."
            )

    padding = (-len(data)) % 8
    if padding:
        data += b"\xFF" * padding
    return data


class STM32UARTBootloader:
    """Minimal AN3155 UART programmer for the STM32C011 ROM bootloader."""

    def __init__(self, port: str, log_callback=None, progress_callback=None):
        self.port_name = port
        self.log_callback = log_callback or (lambda _message: None)
        self.progress_callback = progress_callback or (lambda _value: None)
        self.serial_port: serial.Serial | None = None
        self.supported_commands: set[int] = set()

    def _log(self, message: str) -> None:
        self.log_callback(message)

    def _progress(self, value: float) -> None:
        self.progress_callback(max(0.0, min(100.0, value)))

    def open(self) -> None:
        self.serial_port = serial.Serial(
            port=self.port_name,
            baudrate=STM32_BOOT_BAUD,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_EVEN,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.2,
            write_timeout=2,
        )
        self.serial_port.reset_input_buffer()
        self.serial_port.reset_output_buffer()

    def close(self) -> None:
        if self.serial_port is not None:
            try:
                self.serial_port.close()
            finally:
                self.serial_port = None

    def _read_exact(self, count: int, timeout: float, stage: str) -> bytes:
        port = self.serial_port
        if port is None:
            raise STM32BootloaderError("Bootloader serial port is not open.")

        deadline = time.monotonic() + timeout
        result = bytearray()
        while len(result) < count and time.monotonic() < deadline:
            chunk = port.read(count - len(result))
            if chunk:
                result.extend(chunk)
        if len(result) != count:
            raise STM32BootloaderError(f"Timeout while waiting for {stage}.")
        return bytes(result)

    def _write(self, data: bytes) -> None:
        port = self.serial_port
        if port is None:
            raise STM32BootloaderError("Bootloader serial port is not open.")
        port.write(data)
        port.flush()

    def _wait_ack(self, stage: str, timeout: float = 2.0) -> None:
        response = self._read_exact(1, timeout, stage)[0]
        if response == STM32_ACK:
            return
        if response == STM32_NACK:
            raise STM32BootloaderError(f"STM32 returned NACK during {stage}.")
        raise STM32BootloaderError(
            f"Unexpected response 0x{response:02X} during {stage}."
        )

    def sync(self, retries: int = 5) -> None:
        port = self.serial_port
        if port is None:
            raise STM32BootloaderError("Bootloader serial port is not open.")

        for attempt in range(1, retries + 1):
            self._log(f"Bootloader synchronization attempt {attempt}/{retries}...")
            self._write(b"\x7F")
            response = port.read(1)
            if response and response[0] in (STM32_ACK, 0x00):
                time.sleep(0.05)
                return
            port.reset_input_buffer()
            time.sleep(0.2)

        raise STM32BootloaderError(
            "Bootloader synchronization failed. Hold BOOT high, reset or power on "
            f"the STM32C011, then release BOOT. Port: {self.port_name}; "
            f"{STM32_BOOT_BAUD} baud; even parity."
        )

    def _send_command(self, command: int, stage: str) -> None:
        self._write(bytes((command, command ^ 0xFF)))
        self._wait_ack(stage)

    def _send_address(self, address: int, stage: str) -> None:
        encoded = address.to_bytes(4, "big")
        self._write(encoded + bytes((_xor_bytes(encoded),)))
        self._wait_ack(stage)

    def get_bootloader_info(self) -> tuple[int, set[int]]:
        self._send_command(0x00, "Get command")
        payload_length = self._read_exact(1, 2.0, "Get response length")[0] + 1
        payload = self._read_exact(payload_length, 2.0, "Get response")
        self._wait_ack("Get completion")
        if not payload:
            raise STM32BootloaderError("Empty Get response.")
        version = payload[0]
        self.supported_commands = set(payload[1:])
        return version, self.supported_commands

    def get_product_id(self) -> int:
        self._send_command(0x02, "Get ID command")
        length = self._read_exact(1, 2.0, "Get ID length")[0] + 1
        identifier = self._read_exact(length, 2.0, "Get ID response")
        self._wait_ack("Get ID completion")
        if len(identifier) < 2:
            raise STM32BootloaderError("Invalid product ID response.")
        return int.from_bytes(identifier[-2:], "big")

    def mass_erase(self) -> None:
        if 0x44 in self.supported_commands:
            self._send_command(0x44, "Extended Erase command")
            self._write(b"\xFF\xFF\x00")
            self._wait_ack("mass erase", timeout=35.0)
            return
        if 0x43 in self.supported_commands:
            self._send_command(0x43, "Erase command")
            self._write(b"\xFF\x00")
            self._wait_ack("mass erase", timeout=35.0)
            return
        raise STM32BootloaderError("This bootloader does not report an erase command.")

    def write_memory(self, address: int, data: bytes) -> None:
        if not 1 <= len(data) <= 256:
            raise ValueError("Write block must contain 1-256 bytes.")
        self._send_command(0x31, "Write Memory command")
        self._send_address(address, "write address")
        packet = bytes((len(data) - 1,)) + data
        self._write(packet + bytes((_xor_bytes(packet),)))
        self._wait_ack("flash programming", timeout=5.0)

    def read_memory(self, address: int, count: int) -> bytes:
        if not 1 <= count <= 256:
            raise ValueError("Read block must contain 1-256 bytes.")
        self._send_command(0x11, "Read Memory command")
        self._send_address(address, "read address")
        length = count - 1
        self._write(bytes((length, length ^ 0xFF)))
        self._wait_ack("read length")
        return self._read_exact(count, 3.0, "flash verification data")

    def go(self, address: int) -> None:
        self._send_command(0x21, "Go command")
        self._send_address(address, "application start address")

    def program(self, base_address: int, data: bytes) -> None:
        try:
            self._progress(0)
            self._log(f"Opening {self.port_name} at {STM32_BOOT_BAUD} baud, 8E1...")
            self.open()
            self._log("Synchronizing with STM32 ROM bootloader...")
            self.sync(retries=5)
            self._progress(3)

            version, commands = self.get_bootloader_info()
            self._log(
                f"Bootloader version {version >> 4}.{version & 0x0F}; "
                f"commands: {' '.join(f'{item:02X}' for item in sorted(commands))}"
            )

            product_id = self.get_product_id()
            self._log(f"Detected product ID: 0x{product_id:03X}")
            if product_id != STM32C011_PID:
                raise STM32BootloaderError(
                    f"Expected STM32C011 PID 0x{STM32C011_PID:03X}, "
                    f"but detected 0x{product_id:03X}. Flash was not erased."
                )
            for required in (0x31, 0x11):
                if required not in commands:
                    raise STM32BootloaderError(
                        f"Required bootloader command 0x{required:02X} is unavailable."
                    )

            self._progress(7)
            self._log("Erasing STM32C011 main flash...")
            self.mass_erase()
            self._progress(12)

            total = len(data)
            self._log(
                f"Programming {total} bytes at 0x{base_address:08X} "
                "in 256-byte blocks..."
            )
            for offset in range(0, total, 256):
                block = data[offset : offset + 256]
                self.write_memory(base_address + offset, block)
                self._progress(12 + 53 * (offset + len(block)) / total)

            self._log("Reading flash back for verification...")
            for offset in range(0, total, 256):
                expected = data[offset : offset + 256]
                actual = self.read_memory(base_address + offset, len(expected))
                if actual != expected:
                    difference = next(
                        index
                        for index, (left, right) in enumerate(zip(expected, actual))
                        if left != right
                    )
                    failed_address = base_address + offset + difference
                    raise STM32BootloaderError(
                        f"Verification failed at 0x{failed_address:08X}: "
                        f"expected 0x{expected[difference]:02X}, "
                        f"read 0x{actual[difference]:02X}."
                    )
                self._progress(65 + 33 * (offset + len(expected)) / total)

            self._log("Verification passed. Starting the application...")
            self.go(base_address)
            self._progress(100)
            self._log(
                "STM32C011 programming completed successfully. "
                "Power cycle the device if it does not restart automatically."
            )
        finally:
            self.close()


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
        self.flash_dialog: tk.Toplevel | None = None
        self.flash_thread: threading.Thread | None = None
        self.flash_busy = False
        self.flash_port_var = tk.StringVar()
        self.flash_variant_var = tk.StringVar(value="STM32C011x6 — 32 KB")
        self.flash_file_var = tk.StringVar()
        self.flash_file_info_var = tk.StringVar(value="Select a .bin or .hex firmware file")
        self.flash_status_var = tk.StringVar(value="Ready")
        self.flash_progress_var = tk.DoubleVar(value=0)
        self.flash_port_combo: ttk.Combobox | None = None
        self.flash_variant_combo: ttk.Combobox | None = None
        self.flash_browse_button: tk.Button | None = None
        self.flash_program_button: tk.Button | None = None
        self.flash_log_text: tk.Text | None = None

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
        style.configure(
            "Flash.Horizontal.TProgressbar",
            troughcolor=CARD,
            background=ACCENT,
            bordercolor=BORDER,
            lightcolor=ACCENT,
            darkcolor=ACCENT,
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

        title_row = tk.Frame(header, bg=BG)
        title_row.pack(fill="x")

        title_box = tk.Frame(title_row, bg=BG)
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

        status_box = tk.Frame(title_row, bg=BG)
        status_box.pack(side="right", fill="y")

        self.status_dot = tk.Label(
            status_box,
            text="●",
            bg=BG,
            fg=RED,
            font=("TkDefaultFont", 13),
        )
        self.status_dot.pack(side="left", padx=(0, 5))
        tk.Label(status_box, textvariable=self.status_var, bg=BG, fg=MUTED).pack(
            side="left"
        )

        controls = self._card(header)
        controls.pack(fill="x", pady=(12, 0))

        tk.Label(controls, text="Port", bg=PANEL, fg=MUTED).pack(
            side="left", padx=(12, 7)
        )
        self.port_combo = ttk.Combobox(
            controls,
            textvariable=self.port_var,
            width=18,
            state="readonly",
            style="Dark.TCombobox",
        )
        self.port_combo.pack(side="left", pady=8)

        self._button(controls, "↻", self.refresh_ports, width=3, bg=CARD).pack(
            side="left", padx=(6, 14), pady=8
        )

        tk.Label(controls, text="Baud", bg=PANEL, fg=MUTED).pack(
            side="left", padx=(0, 7)
        )

        baud_combo = ttk.Combobox(
            controls,
            textvariable=self.baud_var,
            values=("9600", "19200", "38400", "57600", "115200", "230400"),
            width=9,
            state="normal",
            style="Dark.TCombobox",
        )
        baud_combo.pack(side="left", pady=8)

        self.connect_button = self._button(
            controls,
            "Connect",
            self.toggle_connection,
            width=12,
            bg=ACCENT,
            active=ACCENT_HOVER,
        )
        self.connect_button.pack(side="right", padx=(8, 12), pady=8)

        self._button(
            controls,
            "Flash STM32C011",
            self.open_flash_dialog,
            width=17,
            bg=ACCENT,
            active=ACCENT_HOVER,
        ).pack(side="right", pady=8)

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

    @staticmethod
    def _detected_ports() -> list[str]:
        return sorted(
            {item.device for item in serial.tools.list_ports.comports()},
            key=str.casefold,
        )

    def open_flash_dialog(self) -> None:
        if self.flash_dialog is not None and self.flash_dialog.winfo_exists():
            self.flash_dialog.deiconify()
            self.flash_dialog.lift()
            self.flash_dialog.focus_force()
            return

        dialog = tk.Toplevel(self.root)
        self.flash_dialog = dialog
        dialog.title("STM32C011 Firmware Flash")
        dialog.geometry("760x680")
        dialog.minsize(680, 600)
        dialog.configure(bg=BG)
        dialog.transient(self.root)
        dialog.protocol("WM_DELETE_WINDOW", self._close_flash_dialog)

        shell = tk.Frame(dialog, bg=BG)
        shell.pack(fill="both", expand=True, padx=18, pady=16)

        tk.Label(
            shell,
            text="STM32C011 Firmware Flash",
            bg=BG,
            fg=TEXT,
            font=("TkDefaultFont", 17, "bold"),
        ).pack(anchor="w")
        tk.Label(
            shell,
            text="STM32 ROM UART bootloader · PID 0x443 · 115200 baud · 8E1",
            bg=BG,
            fg=MUTED,
            font=("TkDefaultFont", 9),
        ).pack(anchor="w", pady=(2, 12))

        settings = self._card(shell)
        settings.pack(fill="x")
        settings.grid_columnconfigure(1, weight=1)

        tk.Label(settings, text="Port", bg=PANEL, fg=MUTED).grid(
            row=0, column=0, sticky="w", padx=(14, 10), pady=(14, 7)
        )
        self.flash_port_combo = ttk.Combobox(
            settings,
            textvariable=self.flash_port_var,
            state="readonly",
            style="Dark.TCombobox",
        )
        self.flash_port_combo.grid(
            row=0, column=1, sticky="ew", padx=(0, 8), pady=(14, 7)
        )
        self._button(
            settings,
            "Refresh",
            self._refresh_flash_ports,
            width=8,
            bg=CARD,
        ).grid(row=0, column=2, padx=(0, 14), pady=(14, 7))

        tk.Label(settings, text="Device", bg=PANEL, fg=MUTED).grid(
            row=1, column=0, sticky="w", padx=(14, 10), pady=7
        )
        self.flash_variant_combo = ttk.Combobox(
            settings,
            textvariable=self.flash_variant_var,
            values=tuple(STM32C011_VARIANTS),
            state="readonly",
            style="Dark.TCombobox",
        )
        self.flash_variant_combo.grid(
            row=1, column=1, columnspan=2, sticky="ew", padx=(0, 14), pady=7
        )
        self.flash_variant_combo.bind("<<ComboboxSelected>>", self._update_flash_file_info)

        tk.Label(settings, text="Firmware", bg=PANEL, fg=MUTED).grid(
            row=2, column=0, sticky="w", padx=(14, 10), pady=7
        )
        firmware_entry = tk.Entry(
            settings,
            textvariable=self.flash_file_var,
            bg=CARD,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            highlightthickness=1,
        )
        firmware_entry.grid(
            row=2, column=1, sticky="ew", padx=(0, 8), pady=7, ipady=7
        )
        self.flash_browse_button = self._button(
            settings,
            "Browse...",
            self._choose_flash_file,
            width=8,
            bg=CARD,
        )
        self.flash_browse_button.grid(row=2, column=2, padx=(0, 14), pady=7)

        tk.Label(
            settings,
            textvariable=self.flash_file_info_var,
            bg=PANEL,
            fg=MUTED,
            anchor="w",
            justify="left",
            font=("TkDefaultFont", 8),
        ).grid(row=3, column=1, columnspan=2, sticky="ew", padx=(0, 14), pady=(0, 14))

        instruction = tk.Frame(
            shell,
            bg="#FFF8E8",
            highlightbackground="#F2D38C",
            highlightthickness=1,
        )
        instruction.pack(fill="x", pady=(12, 0))
        tk.Label(
            instruction,
            text=(
                "Before programming: 1) hold BOOT high; 2) reset or power on; "
                "3) release BOOT; 4) click Program.\n"
                "USART1 uses PA9/PA10; on WLCSP12, SO8N, TSSOP20 and UFQFN20, "
                "the pins are remapped to PA11/PA12."
            ),
            bg="#FFF8E8",
            fg="#7A4B00",
            justify="left",
            anchor="w",
            wraplength=690,
        ).pack(fill="x", padx=12, pady=10)

        progress_card = self._card(shell)
        progress_card.pack(fill="x", pady=(12, 0))
        progress_head = tk.Frame(progress_card, bg=PANEL)
        progress_head.pack(fill="x", padx=14, pady=(12, 7))
        tk.Label(
            progress_head,
            text="Programming status",
            bg=PANEL,
            fg=TEXT,
            font=("TkDefaultFont", 10, "bold"),
        ).pack(side="left")
        tk.Label(
            progress_head,
            textvariable=self.flash_status_var,
            bg=PANEL,
            fg=MUTED,
        ).pack(side="right")
        ttk.Progressbar(
            progress_card,
            variable=self.flash_progress_var,
            maximum=100,
            style="Flash.Horizontal.TProgressbar",
        ).pack(fill="x", padx=14, pady=(0, 12))

        log_card = self._card(shell)
        log_card.pack(fill="both", expand=True, pady=(12, 0))
        log_card.grid_rowconfigure(1, weight=1)
        log_card.grid_columnconfigure(0, weight=1)
        tk.Label(
            log_card,
            text="Flash log",
            bg=PANEL,
            fg=TEXT,
            font=("TkDefaultFont", 10, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 6))
        self.flash_log_text = tk.Text(
            log_card,
            bg=CARD,
            fg="#344054",
            relief="flat",
            state="disabled",
            font=("TkFixedFont", 9),
            height=8,
            padx=9,
            pady=7,
        )
        self.flash_log_text.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 12))

        footer = tk.Frame(shell, bg=BG)
        footer.pack(fill="x", pady=(12, 0))
        self._button(
            footer,
            "Close",
            self._close_flash_dialog,
            width=10,
            bg=CARD,
        ).pack(side="right", padx=(8, 0))
        self.flash_program_button = self._button(
            footer,
            "Program STM32C011",
            self._start_flash,
            width=19,
            bg=ACCENT,
            active=ACCENT_HOVER,
        )
        self.flash_program_button.pack(side="right")

        self._refresh_flash_ports()
        self._update_flash_file_info()

    def _refresh_flash_ports(self) -> None:
        if self.flash_busy or self.flash_port_combo is None:
            return
        ports = self._detected_ports()
        preferred = self.flash_port_var.get().strip() or self.port_var.get().strip()
        self.flash_port_combo["values"] = ports
        if preferred in ports:
            self.flash_port_var.set(preferred)
        elif ports:
            self.flash_port_var.set(ports[0])
        else:
            self.flash_port_var.set("")
        self.flash_port_combo.config(state="readonly" if ports else "disabled")

    def _choose_flash_file(self) -> None:
        if self.flash_busy:
            return
        filename = filedialog.askopenfilename(
            parent=self.flash_dialog,
            title="Select STM32C011 firmware",
            filetypes=(
                ("STM32 firmware", "*.bin *.hex"),
                ("Binary firmware", "*.bin"),
                ("Intel HEX firmware", "*.hex"),
                ("All files", "*.*"),
            ),
        )
        if filename:
            self.flash_file_var.set(filename)
            self._update_flash_file_info()

    def _update_flash_file_info(self, _event=None) -> None:
        filename = self.flash_file_var.get().strip()
        if not filename:
            self.flash_file_info_var.set("Select a .bin or .hex firmware file")
            return
        try:
            base_address, data = load_firmware_image(filename)
            flash_size = STM32C011_VARIANTS[self.flash_variant_var.get()]
            padded = validate_firmware_image(base_address, data, flash_size)
        except (OSError, ValueError, KeyError) as exc:
            self.flash_file_info_var.set(f"Invalid firmware: {exc}")
            return
        self.flash_file_info_var.set(
            f"Base 0x{base_address:08X} · File {len(data)} bytes · "
            f"Program {len(padded)} bytes · Limit {flash_size // 1024} KB"
        )

    def _start_flash(self) -> None:
        if self.flash_busy:
            return
        port = self.flash_port_var.get().strip()
        if not port or port not in self._detected_ports():
            messagebox.showwarning(
                "No serial port",
                "Select a serial port currently detected by the computer.",
                parent=self.flash_dialog,
            )
            self._refresh_flash_ports()
            return

        filename = self.flash_file_var.get().strip()
        try:
            flash_size = STM32C011_VARIANTS[self.flash_variant_var.get()]
            base_address, data = load_firmware_image(filename)
            data = validate_firmware_image(base_address, data, flash_size)
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror("Invalid firmware", str(exc), parent=self.flash_dialog)
            return

        confirmed = messagebox.askyesno(
            "Confirm STM32C011 programming",
            (
                f"Device: {self.flash_variant_var.get()}\n"
                f"Port: {port}\n"
                f"Firmware: {Path(filename).name}\n"
                f"Program size: {len(data)} bytes\n\n"
                "This operation performs a full main-flash erase. Continue?"
            ),
            parent=self.flash_dialog,
        )
        if not confirmed:
            return

        if self.worker.connected:
            self.disconnect()

        self._clear_flash_log()
        self.flash_progress_var.set(0)
        self.flash_status_var.set("Programming...")
        self._set_flash_busy(True)

        self.flash_thread = threading.Thread(
            target=self._run_flash,
            args=(port, base_address, data),
            name="stm32c011-programmer",
            daemon=True,
        )
        self.flash_thread.start()

    def _run_flash(self, port: str, base_address: int, data: bytes) -> None:
        programmer = STM32UARTBootloader(
            port,
            log_callback=lambda message: self._put_flash_event(("flash_log", message)),
            progress_callback=lambda value: self._put_flash_event(("flash_progress", value)),
        )
        try:
            programmer.program(base_address, data)
        except Exception as exc:
            self._put_flash_event(("flash_error", str(exc)))
        else:
            self._put_flash_event(
                (
                    "flash_done",
                    "Programming and verification completed. "
                    "Power cycle the device if it does not restart automatically.",
                )
            )

    def _put_flash_event(self, event: tuple) -> None:
        try:
            self.events.put_nowait(event)
        except queue.Full:
            try:
                self.events.get_nowait()
                self.events.put_nowait(event)
            except queue.Empty:
                pass

    def _set_flash_busy(self, busy: bool) -> None:
        self.flash_busy = busy
        if self.flash_port_combo is not None:
            self.flash_port_combo.config(state="disabled" if busy else "readonly")
        if self.flash_variant_combo is not None:
            self.flash_variant_combo.config(state="disabled" if busy else "readonly")
        if self.flash_browse_button is not None:
            self.flash_browse_button.config(state="disabled" if busy else "normal")
        if self.flash_program_button is not None:
            self.flash_program_button.config(state="disabled" if busy else "normal")
        if not busy:
            self._refresh_flash_ports()

    def _append_flash_log(self, message: str) -> None:
        text_widget = self.flash_log_text
        if text_widget is None or not text_widget.winfo_exists():
            return
        text_widget.config(state="normal")
        text_widget.insert("end", message + "\n")
        text_widget.see("end")
        text_widget.config(state="disabled")

    def _clear_flash_log(self) -> None:
        text_widget = self.flash_log_text
        if text_widget is None or not text_widget.winfo_exists():
            return
        text_widget.config(state="normal")
        text_widget.delete("1.0", "end")
        text_widget.config(state="disabled")

    def _finish_flash(self, success: bool, message: str) -> None:
        self.flash_thread = None
        self._set_flash_busy(False)
        self.flash_status_var.set("Completed" if success else "Failed")
        self._append_flash_log(message)
        if success:
            self.flash_progress_var.set(100)
            messagebox.showinfo("STM32C011", message, parent=self.flash_dialog)
        else:
            messagebox.showerror("STM32C011 programming failed", message, parent=self.flash_dialog)
        self.root.after(500, self.refresh_ports)

    def _close_flash_dialog(self) -> None:
        if self.flash_busy:
            messagebox.showwarning(
                "Programming in progress",
                "Wait for STM32C011 programming and verification to finish.",
                parent=self.flash_dialog,
            )
            return
        if self.flash_dialog is not None:
            self.flash_dialog.destroy()
        self.flash_dialog = None
        self.flash_port_combo = None
        self.flash_variant_combo = None
        self.flash_browse_button = None
        self.flash_program_button = None
        self.flash_log_text = None

    def refresh_ports(self) -> None:
        discovered = self._detected_ports()
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
            elif event[0] == "flash_log":
                _, message = event
                self._append_flash_log(message)
                self.flash_status_var.set(message)
            elif event[0] == "flash_progress":
                _, value = event
                self.flash_progress_var.set(value)
            elif event[0] == "flash_done":
                _, message = event
                self._finish_flash(True, message)
            elif event[0] == "flash_error":
                _, message = event
                self._finish_flash(False, message)

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
        if self.flash_busy:
            messagebox.showwarning(
                "Programming in progress",
                "Wait for STM32C011 programming and verification to finish.",
                parent=self.flash_dialog or self.root,
            )
            return
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
