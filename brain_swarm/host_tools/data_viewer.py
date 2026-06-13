"""
NeuroResonator Real-time Data Viewer

Displays live EEG waveforms, FFT spectrum, brain state, focus score,
tDCS current, and event log. Connects via serial/BLE or replays CSV logs.

Usage:
  python data_viewer.py --port COM3           # Connect via serial
  python data_viewer.py --ble                  # Connect via BLE
  python data_viewer.py --log session.csv      # Replay from CSV log
  python data_viewer.py --sim                  # Internal simulator demo mode
"""

import argparse
import csv
import queue
import struct
import sys
import threading
import time
from collections import deque
from datetime import datetime
from enum import Enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np


# ─────────────────────────────────────────────
# Brain State Enum (matches simulator)
# ─────────────────────────────────────────────

class BrainState(Enum):
    DROWSY = "drowsy"
    RELAXED = "relaxed"
    CALM = "calm"
    ATTENTIVE = "attentive"
    FOCUSED = "focused"
    DEEP_FOCUS = "deep_focus"
    HYPERFOCUS = "hyperfocus"

    @property
    def color(self) -> str:
        return {
            BrainState.DROWSY: "#FF6B35",
            BrainState.RELAXED: "#4ECDC4",
            BrainState.CALM: "#95E1D3",
            BrainState.ATTENTIVE: "#FFE66D",
            BrainState.FOCUSED: "#2ECC71",
            BrainState.DEEP_FOCUS: "#3498DB",
            BrainState.HYPERFOCUS: "#9B59B6",
        }.get(self, "#888888")

    @property
    def emoji(self) -> str:
        return {
            BrainState.DROWSY: "😴",
            BrainState.RELAXED: "😌",
            BrainState.CALM: "🧘",
            BrainState.ATTENTIVE: "👀",
            BrainState.FOCUSED: "🎯",
            BrainState.DEEP_FOCUS: "🧠",
            BrainState.HYPERFOCUS: "⚡",
        }.get(self, "❓")


# ─────────────────────────────────────────────
# Data Source Interface
# ─────────────────────────────────────────────

@dataclass
class DataFrame:
    timestamp: float
    channels: np.ndarray  # (n_ch, n_samples)
    focus: float = 0.0
    relaxation: float = 0.0
    state: BrainState = BrainState.CALM
    stim_current_ma: float = 0.0
    stim_target_ma: float = 0.0
    impedance_kohm: List[float] = field(default_factory=list)
    safety_alerts: List[str] = field(default_factory=list)


class DataSource(ABC):
    @abstractmethod
    def start(self):
        ...

    @abstractmethod
    def stop(self):
        ...

    @abstractmethod
    def read(self) -> Optional["DataFrame"]:
        ...

    @property
    @abstractmethod
    def connected(self) -> bool:
        ...


# ─────────────────────────────────────────────
# Simulated Data Source
# ─────────────────────────────────────────────

class SimulatedSource(DataSource):
    """Internal synthetic EEG generator for demo mode."""
    def __init__(self, n_channels: int = 4, sampling_rate: int = 250):
        self.n_channels = n_channels
        self.sr = sampling_rate
        self._running = False
        self._t = 0.0
        self._state = BrainState.CALM
        self._focus = 50.0
        self._phase = np.zeros(n_channels)

    def start(self):
        self._running = True

    def stop(self):
        self._running = False

    @property
    def connected(self) -> bool:
        return self._running

    def read(self) -> Optional[DataFrame]:
        if not self._running:
            return None
        dt = 0.25
        n_samples = int(self.sr * dt)
        t = self._t + np.arange(n_samples) / self.sr
        self._t += dt

        data = np.zeros((self.n_channels, n_samples))
        for ch in range(self.n_channels):
            data[ch, :] = (
                10 * np.sin(2 * np.pi * 10.5 * t + self._phase[ch])
                + 5 * np.sin(2 * np.pi * 21.5 * t + self._phase[ch] + 0.5)
                + 3 * np.random.randn(n_samples)
            )
            self._phase[ch] += 0.1 * np.random.uniform(-1, 1)

        self._focus += np.random.uniform(-3, 3)
        self._focus = max(10, min(95, self._focus))
        relax = 100 - self._focus + np.random.uniform(-10, 10)
        relax = max(10, min(95, relax))

        if self._focus > 70:
            self._state = BrainState.FOCUSED
        elif self._focus > 55:
            self._state = BrainState.ATTENTIVE
        elif relax > 60:
            self._state = BrainState.RELAXED
        else:
            self._state = BrainState.CALM

        data *= 1e-6

        return DataFrame(
            timestamp=time.time(),
            channels=data * 1e6,
            focus=self._focus,
            relaxation=relax,
            state=self._state,
            stim_current_ma=0.5 + 0.3 * np.sin(self._t * 0.1),
            stim_target_ma=1.0,
            impedance_kohm=[np.random.uniform(3, 12) for _ in range(self.n_channels + 2)],
        )


# ─────────────────────────────────────────────
# Serial Data Source
# ─────────────────────────────────────────────

class SerialSource(DataSource):
    """Connects to device over serial port using binary protocol."""
    PACKET_MAGIC = 0xAABB

    def __init__(self, port: str, baud: int = 115200, n_channels: int = 4):
        self.port = port
        self.baud = baud
        self.n_channels = n_channels
        self._ser = None
        self._running = False
        self._buf = b""
        self._q: queue.Queue = queue.Queue(maxsize=32)
        self._thread: Optional[threading.Thread] = None
        self._connected = False

    def start(self):
        try:
            import serial
            self._ser = serial.Serial(self.port, self.baud, timeout=1)
            self._connected = True
            self._running = True
            self._thread = threading.Thread(target=self._read_loop, daemon=True)
            self._thread.start()
        except Exception as e:
            print(f"Serial connection failed: {e}")
            self._connected = False

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def _read_loop(self):
        while self._running and self._ser:
            try:
                raw = self._ser.read(512)
                if not raw:
                    continue
                self._buf += raw
                frames = self._parse_packets()
                for frame in frames:
                    try:
                        self._q.put_nowait(frame)
                    except queue.Full:
                        pass
            except Exception:
                self._connected = False
                break

    def _parse_packets(self) -> List[DataFrame]:
        frames = []
        while True:
            idx = self._buf.find(struct.pack("<H", self.PACKET_MAGIC))
            if idx < 0 or idx + 4 > len(self._buf):
                break
            payload_len = struct.unpack("<H", self._buf[idx + 2:idx + 4])[0]
            pkt_end = idx + 4 + payload_len
            if pkt_end > len(self._buf):
                break
            pkt = self._buf[idx + 4:pkt_end]
            self._buf = self._buf[pkt_end:]
            frame = self._decode_packet(pkt)
            if frame:
                frames.append(frame)
        return frames

    def _decode_packet(self, pkt: bytes) -> Optional[DataFrame]:
        try:
            n_ch = self.n_channels
            n_samples = 64
            expected = 4 + n_ch * n_samples * 3 + 4 + 4 + 1 + 4 + 4
            if len(pkt) < expected:
                return None
            off = 0
            timestamp = struct.unpack("<I", pkt[off:off + 4])[0]
            off += 4

            channels = np.frombuffer(pkt[off:off + n_ch * n_samples * 3], dtype=np.dtype("<i3")).reshape(
                (n_ch, n_samples)).astype(np.float64) * 0.02235
            off += n_ch * n_samples * 3

            focus = struct.unpack("<f", pkt[off:off + 4])[0]
            off += 4
            relax = struct.unpack("<f", pkt[off:off + 4])[0]
            off += 4
            state_id = pkt[off]
            off += 1
            stim_cur = struct.unpack("<f", pkt[off:off + 4])[0]
            off += 4
            stim_tgt = struct.unpack("<f", pkt[off:off + 4])[0]
            off += 4
            imp = list(struct.unpack(f"<{n_ch + 2}f", pkt[off:off + (n_ch + 2) * 4]))

            state = list(BrainState)[state_id % len(list(BrainState))]

            return DataFrame(
                timestamp=timestamp,
                channels=channels,
                focus=focus,
                relaxation=relax,
                state=state,
                stim_current_ma=stim_cur,
                stim_target_ma=stim_tgt,
                impedance_kohm=imp,
            )
        except Exception:
            return None

    def read(self) -> Optional[DataFrame]:
        try:
            return self._q.get_nowait()
        except queue.Empty:
            return None

    def send_command(self, cmd: str):
        if self._ser and self._connected:
            self._ser.write(cmd.encode() + b"\n")


# ─────────────────────────────────────────────
# BLE Data Source
# ─────────────────────────────────────────────

class BleSource(DataSource):
    """Connects to device over BLE using bleak."""

    def __init__(self, address: Optional[str] = None, n_channels: int = 4):
        self.address = address
        self.n_channels = n_channels
        self._client = None
        self._running = False
        self._connected = False
        self._q: queue.Queue = queue.Queue(maxsize=32)
        self._thread: Optional[threading.Thread] = None
        self.TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
        self.RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._connect_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._client:
            try:
                import asyncio
                asyncio.run(self._client.disconnect())
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=3)
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def _connect_loop(self):
        import asyncio

        async def _run():
            from bleak import BleakClient, BleakScanner
            if self.address:
                device = None
                scanner = BleakScanner()
                async with scanner:
                    await scanner.start()
                    await asyncio.sleep(5)
                    await scanner.stop()
                for d in scanner.discovered_devices:
                    if d.address == self.address:
                        device = d
                        break
                if not device:
                    print(f"BLE device {self.address} not found")
                    return
            else:
                scanner = BleakScanner()
                devices = await scanner.discover(timeout=5)
                target = None
                for d in devices:
                    if "NEURORESONATOR" in (d.name or "").upper():
                        target = d
                        break
                if not target:
                    print("No NeuroResonator BLE device found")
                    return
                device = target

            async def notification_handler(sender, data):
                frame = self._decode_ble_packet(data)
                if frame:
                    try:
                        self._q.put_nowait(frame)
                    except queue.Full:
                        pass

            async with BleakClient(device.address) as client:
                self._client = client
                self._connected = True
                print(f"Connected via BLE to {device.name} ({device.address})")
                await client.start_notify(self.TX_UUID, notification_handler)
                while self._running and client.is_connected:
                    await asyncio.sleep(0.1)
                self._connected = False

        try:
            asyncio.run(_run())
        except Exception as e:
            print(f"BLE error: {e}")
            self._connected = False

    def _decode_ble_packet(self, data: bytes) -> Optional[DataFrame]:
        try:
            n_ch = self.n_channels
            n_samples = 64
            expected = 4 + n_ch * n_samples * 4 + 4 + 4 + 1 + 4 + 4 + (n_ch + 2) * 4
            if len(data) < expected:
                return None
            off = 0
            timestamp = struct.unpack("<I", data[off:off + 4])[0]
            off += 4
            channels = np.frombuffer(data[off:off + n_ch * n_samples * 4], dtype="<f4").reshape(
                (n_ch, n_samples)).astype(np.float64)
            off += n_ch * n_samples * 4
            focus = struct.unpack("<f", data[off:off + 4])[0]
            off += 4
            relax = struct.unpack("<f", data[off:off + 4])[0]
            off += 4
            state_id = data[off]
            off += 1
            stim_cur = struct.unpack("<f", data[off:off + 4])[0]
            off += 4
            stim_tgt = struct.unpack("<f", data[off:off + 4])[0]
            off += 4
            imp = list(struct.unpack(f"<{n_ch + 2}f", data[off:off + (n_ch + 2) * 4]))
            state = list(BrainState)[state_id % len(list(BrainState))]
            return DataFrame(
                timestamp=timestamp, channels=channels, focus=focus,
                relaxation=relax, state=state, stim_current_ma=stim_cur,
                stim_target_ma=stim_tgt, impedance_kohm=imp,
            )
        except Exception:
            return None

    def read(self) -> Optional[DataFrame]:
        try:
            return self._q.get_nowait()
        except queue.Empty:
            return None


# ─────────────────────────────────────────────
# CSV Log Replay Source
# ─────────────────────────────────────────────

class CsvReplaySource(DataSource):
    """Replays a previously recorded CSV log file."""

    def __init__(self, path: str, n_channels: int = 4, sampling_rate: int = 250):
        self.path = path
        self.n_channels = n_channels
        self.sr = sampling_rate
        self._rows: List[dict] = []
        self._idx = 0
        self._start_time = 0.0
        self._running = False
        self._last_row_time = 0.0

    def start(self):
        with open(self.path, "r") as f:
            reader = csv.DictReader(f)
            self._rows = list(reader)
        if not self._rows:
            raise ValueError(f"No data in {self.path}")
        self._idx = 0
        self._start_time = time.time()
        self._last_row_time = float(self._rows[0]["time_s"])
        self._running = True

    def stop(self):
        self._running = False

    @property
    def connected(self) -> bool:
        return self._running

    def read(self) -> Optional[DataFrame]:
        if not self._running or self._idx >= len(self._rows):
            self._running = False
            return None
        row = self._rows[self._idx]
        row_t = float(row["time_s"])
        now = time.time() - self._start_time
        if now < row_t:
            return None
        self._idx += 1
        self._last_row_time = row_t

        ch_data = np.random.randn(self.n_channels, 64) * 5
        state_str = row.get("state", "calm")
        try:
            state = BrainState(state_str)
        except ValueError:
            state = BrainState.CALM

        return DataFrame(
            timestamp=row_t,
            channels=ch_data,
            focus=float(row.get("focus", 50)),
            relaxation=float(row.get("relaxation", 50)),
            state=state,
            stim_current_ma=float(row.get("stim_current_ma", 0)),
            stim_target_ma=float(row.get("stim_target_ma", 0)),
            impedance_kohm=[],
        )


# ─────────────────────────────────────────────
# Tkinter GUI Application
# ─────────────────────────────────────────────

try:
    import tkinter as tk
    from tkinter import ttk
    TK_AVAILABLE = True
except ImportError:
    TK_AVAILABLE = False

if TK_AVAILABLE:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch


    class DarkTheme:
        BG = "#1e1e2e"
        FG = "#cdd6f4"
        ACCENT = "#89b4fa"
        SURFACE = "#313244"
        SURFACE2 = "#45475a"
        RED = "#f38ba8"
        GREEN = "#a6e3a1"
        YELLOW = "#f9e2af"
        BLUE = "#89b4fa"
        PURPLE = "#cba6f7"
        TEAL = "#94e2d5"


    class GaugeWidget(tk.Frame):
        """Custom focus/relaxation gauge bar."""

        def __init__(self, parent, label: str, color: str, width=300, height=30, **kwargs):
            super().__init__(parent, bg=DarkTheme.BG, **kwargs)
            self._color = color
            self._value = 0
            tk.Label(self, text=label, fg=DarkTheme.FG, bg=DarkTheme.BG,
                     font=("Consolas", 10, "bold")).pack(anchor="w")
            self._canvas = tk.Canvas(self, width=width, height=height,
                                     bg=DarkTheme.SURFACE, highlightthickness=0)
            self._canvas.pack(pady=(2, 8))
            self._bar_width = width - 4
            self._draw(0)

        def set_value(self, val: float):
            self._value = max(0, min(100, val))
            self._draw(self._value)

        def _draw(self, val: float):
            w = self._bar_width * val / 100.0
            self._canvas.delete("all")
            self._canvas.create_rectangle(2, 2, 2 + w, self.winfo_reqheight() - 2,
                                           fill=self._color, outline="", tags="bar")
            self._canvas.create_text(self._bar_width // 2 + 2, self.winfo_reqheight() // 2,
                                      text=f"{int(val)}/100", fill=DarkTheme.FG,
                                      font=("Consolas", 10, "bold"))


    class DataViewer:
        def __init__(self, source: DataSource):
            self.source = source
            self.n_channels = 4
            self.sr = 250
            self._running = False
            self._eeg_buf: deque = deque(maxlen=4 * self.sr)
            self._focus_history: deque = deque(maxlen=300)
            self._stim_history: deque = deque(maxlen=300)
            self._time_history: deque = deque(maxlen=300)
            self._session_elapsed = 0.0
            self._max_current = tk.DoubleVar(value=2.0)

            self.root = tk.Tk()
            self.root.title("NeuroResonator Data Viewer")
            self.root.configure(bg=DarkTheme.BG)
            self.root.minsize(1000, 700)
            self.root.geometry("1280x800")

            self._setup_styles()
            self._build_ui()
            self._bind_keys()

            self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        def _setup_styles(self):
            style = ttk.Style()
            style.theme_use("clam")
            style.configure("TButton", background=DarkTheme.SURFACE, foreground=DarkTheme.FG,
                            borderwidth=0, font=("Consolas", 10))
            style.map("TButton", background=[("active", DarkTheme.SURFACE2)])

        def _build_ui(self):
            main_frame = tk.Frame(self.root, bg=DarkTheme.BG)
            main_frame.pack(fill="both", expand=True, padx=8, pady=8)

            left = tk.Frame(main_frame, bg=DarkTheme.BG)
            left.pack(side="left", fill="both", expand=True)

            right = tk.Frame(main_frame, bg=DarkTheme.BG, width=280)
            right.pack(side="right", fill="y", padx=(8, 0))
            right.pack_propagate(False)

            # ── EEG Plot ──
            self.fig = plt.Figure(figsize=(8, 4), dpi=100, facecolor=DarkTheme.BG)
            self.ax_eeg = self.fig.add_subplot(111)
            self.ax_eeg.set_facecolor(DarkTheme.SURFACE)
            self.ax_eeg.set_title("EEG (4ch, 4s window)", color=DarkTheme.FG, fontsize=10)
            self.ax_eeg.tick_params(colors=DarkTheme.FG, labelsize=8)
            self.ax_eeg.set_xlabel("Time (s)", color=DarkTheme.FG)
            self.ax_eeg.set_ylabel("\u03bcV", color=DarkTheme.FG)
            self.ax_eeg.spines["bottom"].set_color(DarkTheme.SURFACE2)
            self.ax_eeg.spines["top"].set_color(DarkTheme.SURFACE2)
            self.ax_eeg.spines["left"].set_color(DarkTheme.SURFACE2)
            self.ax_eeg.spines["right"].set_color(DarkTheme.SURFACE2)
            colors = ["#89b4fa", "#a6e3a1", "#f9e2af", "#f38ba8"]
            self.eeg_lines = []
            for ch in range(4):
                line, = self.ax_eeg.plot([], [], color=colors[ch], lw=0.8, label=f"CH{ch+1}")
                self.eeg_lines.append(line)
            self.ax_eeg.legend(loc="upper right", fontsize=7, labelcolor=DarkTheme.FG)
            self.canvas_eeg = FigureCanvasTkAgg(self.fig, master=left)
            self.canvas_eeg.get_tk_widget().pack(fill="both", expand=True)

            # ── Right Panel ──
            # State indicator
            state_frame = tk.Frame(right, bg=DarkTheme.BG)
            state_frame.pack(fill="x", pady=(0, 12))
            self.state_label = tk.Label(state_frame, text="CALM", font=("Consolas", 22, "bold"),
                                         fg=BrainState.CALM.color, bg=DarkTheme.BG)
            self.state_label.pack()
            self.state_emoji = tk.Label(state_frame, text="🧘", font=("Segoe UI Emoji", 36),
                                         bg=DarkTheme.BG)
            self.state_emoji.pack()

            # Focus gauge
            self.focus_gauge = GaugeWidget(right, "FOCUS", DarkTheme.GREEN)
            self.focus_gauge.pack(fill="x", pady=4)

            # Relaxation gauge
            self.relax_gauge = GaugeWidget(right, "RELAXATION", DarkTheme.BLUE)
            self.relax_gauge.pack(fill="x", pady=4)

            # tDCS readout
            stim_frame = tk.Frame(right, bg=DarkTheme.BG)
            stim_frame.pack(fill="x", pady=8)
            tk.Label(stim_frame, text="tDCS CURRENT", fg=DarkTheme.TEAL, bg=DarkTheme.BG,
                     font=("Consolas", 10, "bold")).pack(anchor="w")
            self.stim_label = tk.Label(stim_frame, text="0.00 mA", fg=DarkTheme.FG,
                                        bg=DarkTheme.BG, font=("Consolas", 18, "bold"))
            self.stim_label.pack()
            self.safety_label = tk.Label(stim_frame, text="🟢 OK", fg=DarkTheme.GREEN,
                                          bg=DarkTheme.BG, font=("Consolas", 11))
            self.safety_label.pack()

            # Session timer
            self.timer_label = tk.Label(right, text="00:00", fg=DarkTheme.FG, bg=DarkTheme.BG,
                                         font=("Consolas", 14, "bold"))
            self.timer_label.pack(pady=4)

            # Controls
            ctrl_frame = tk.Frame(right, bg=DarkTheme.BG)
            ctrl_frame.pack(fill="x", pady=8)
            self.btn_start = tk.Button(ctrl_frame, text="▶ Start Stim", bg=DarkTheme.GREEN,
                                        fg=DarkTheme.BG, font=("Consolas", 10, "bold"),
                                        command=self._start_stim)
            self.btn_start.pack(fill="x", pady=2)
            self.btn_stop = tk.Button(ctrl_frame, text="⏹ Stop Stim", bg=DarkTheme.RED,
                                       fg=DarkTheme.BG, font=("Consolas", 10, "bold"),
                                       command=self._stop_stim)
            self.btn_stop.pack(fill="x", pady=2)
            self.btn_reset = tk.Button(ctrl_frame, text="🔄 Reset", bg=DarkTheme.YELLOW,
                                        fg=DarkTheme.BG, font=("Consolas", 10, "bold"),
                                        command=self._reset_session)
            self.btn_reset.pack(fill="x", pady=2)
            max_frame = tk.Frame(right, bg=DarkTheme.BG)
            max_frame.pack(fill="x", pady=4)
            tk.Label(max_frame, text="Max Current (mA):", fg=DarkTheme.FG, bg=DarkTheme.BG,
                     font=("Consolas", 9)).pack(anchor="w")
            self.max_scale = tk.Scale(max_frame, from_=0.5, to=4.0, resolution=0.1,
                                       orient="horizontal", variable=self._max_current,
                                       bg=DarkTheme.SURFACE, fg=DarkTheme.FG,
                                       highlightthickness=0, length=200)
            self.max_scale.pack()

            # Event log
            log_frame = tk.Frame(right, bg=DarkTheme.BG)
            log_frame.pack(fill="both", expand=True, pady=(8, 0))
            tk.Label(log_frame, text="EVENT LOG", fg=DarkTheme.ACCENT, bg=DarkTheme.BG,
                     font=("Consolas", 9, "bold")).pack(anchor="w")
            self.log_text = tk.Text(log_frame, height=8, bg=DarkTheme.SURFACE, fg=DarkTheme.FG,
                                     font=("Consolas", 8), state="disabled", bd=0)
            scrollbar = tk.Scrollbar(log_frame, command=self.log_text.yview)
            self.log_text.configure(yscrollcommand=scrollbar.set)
            self.log_text.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")

            # Keyboard hint
            hint = tk.Label(right, text="s=start stim  t=stop  r=reset  q=quit",
                            fg=DarkTheme.SURFACE2, bg=DarkTheme.BG, font=("Consolas", 7))
            hint.pack(side="bottom")

        def _bind_keys(self):
            self.root.bind("s", lambda e: self._start_stim())
            self.root.bind("t", lambda e: self._stop_stim())
            self.root.bind("r", lambda e: self._reset_session())
            self.root.bind("q", lambda e: self._on_close())

        def _log(self, msg: str):
            self.log_text.configure(state="normal")
            ts = datetime.now().strftime("%H:%M:%S")
            self.log_text.insert("end", f"[{ts}] {msg}\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

        def _start_stim(self):
            if isinstance(self.source, SerialSource):
                self.source.send_command("STIM_START")
            self._log("Stimulation started")

        def _stop_stim(self):
            if isinstance(self.source, SerialSource):
                self.source.send_command("STIM_STOP")
            self._log("Stimulation stopped")

        def _reset_session(self):
            self._session_elapsed = 0.0
            self._eeg_buf.clear()
            self._focus_history.clear()
            self._stim_history.clear()
            self._time_history.clear()
            if isinstance(self.source, SerialSource):
                self.source.send_command("RESET")
            self._log("Session reset")

        def _on_close(self):
            self._running = False
            try:
                self.source.stop()
            except Exception:
                pass
            self.root.quit()
            self.root.destroy()

        def run(self):
            self._running = True
            source_started = False
            self.root.after(100, self._update_loop)
            self.root.mainloop()

        def _update_loop(self):
            if not self._running:
                return

            try:
                frame = self.source.read()
                if frame:
                    source_started = True
                    self._process_frame(frame)
                elif not source_started:
                    try:
                        self.source.start()
                        source_started = True
                        self._log("Data source started")
                    except Exception as e:
                        self._log(f"Source error: {e}")

                if self.source.connected:
                    self._refresh_display()
            except Exception as e:
                if self._running:
                    self._log(f"Error: {e}")

            self.root.after(200, self._update_loop)

        def _process_frame(self, frame: DataFrame):
            self._session_elapsed = frame.timestamp if frame.timestamp > 0 else self._session_elapsed + 0.25

            if frame.channels.size > 0:
                self._eeg_buf.extend(frame.channels.T.tolist())

            self._focus_history.append(frame.focus)
            self._stim_history.append(frame.stim_current_ma)
            self._time_history.append(self._session_elapsed)

        def _refresh_display(self):
            # EEG plot
            if len(self._eeg_buf) > 0:
                arr = np.array(self._eeg_buf)
                if arr.ndim == 2:
                    n_pts = min(arr.shape[0], 4 * self.sr)
                    x = np.arange(n_pts) / self.sr
                    for ch in range(min(4, arr.shape[1])):
                        self.eeg_lines[ch].set_data(x, arr[-n_pts:, ch])
                    self.ax_eeg.relim()
                    self.ax_eeg.autoscale_view()
                    self.ax_eeg.set_xlim(0, x[-1] if len(x) > 0 else 4)
                self.canvas_eeg.draw_idle()

            # Update gauges and labels
            if self._focus_history:
                focus = self._focus_history[-1]
                self.focus_gauge.set_value(focus)
                stim_current = self._stim_history[-1] if self._stim_history else 0

                # Brain state
                if focus > 80:
                    state = BrainState.HYPERFOCUS
                elif focus > 70:
                    state = BrainState.DEEP_FOCUS
                elif focus > 60:
                    state = BrainState.FOCUSED
                elif focus > 45:
                    state = BrainState.ATTENTIVE
                elif focus > 30:
                    state = BrainState.CALM
                else:
                    state = BrainState.RELAXED

                self.state_label.configure(text=state.value.upper(), fg=state.color)
                self.state_emoji.configure(text=state.emoji)

                relax = 100 - focus + np.random.uniform(-5, 5)
                relax = max(0, min(100, relax))
                self.relax_gauge.set_value(relax)

                self.stim_label.configure(text=f"{stim_current:.2f} mA")
                safety = "🟢 OK" if stim_current < 3.0 else "🔴 OVERCURRENT"
                self.safety_label.configure(text=safety, fg=DarkTheme.GREEN if stim_current < 3.0 else DarkTheme.RED)

            # Timer
            mins = int(self._session_elapsed // 60)
            secs = int(self._session_elapsed % 60)
            self.timer_label.configure(text=f"{mins:02d}:{secs:02d}")

            # Event log sampling of safety events
            if self._stim_history and len(self._stim_history) % 50 == 0:
                self._log(f"Focus={self._focus_history[-1]:.0f} Stim={self._stim_history[-1]:.2f}mA")


    def run_gui(source: DataSource):
        app = DataViewer(source)
        app.run()


else:
    def run_gui(source: DataSource):
        print("Tkinter not available. Falling back to console mode.")
        source.start()
        print("\nNeuroResonator Data Viewer (Console Mode)")
        print("─" * 50)
        try:
            last_print = 0
            while source.connected:
                frame = source.read()
                if frame:
                    now = time.time()
                    if now - last_print >= 1.0:
                        last_print = now
                        print(f"[{frame.timestamp:.1f}s] "
                              f"Focus={frame.focus:.0f} Relax={frame.relaxation:.0f} "
                              f"{frame.state.emoji}{frame.state.value.upper()} "
                              f"Stim={frame.stim_current_ma:.2f}mA")
                else:
                    time.sleep(0.05)
        except KeyboardInterrupt:
            print("\nQuit.")
        finally:
            source.stop()


# ─────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="NeuroResonator Real-time Data Viewer")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--port", "-p", type=str, help="Serial port (e.g. COM3)")
    group.add_argument("--ble", "-b", action="store_true", help="Connect via BLE")
    group.add_argument("--log", "-l", type=str, help="Replay CSV log file")
    group.add_argument("--sim", "-s", action="store_true", help="Use internal simulator (demo mode)")
    args = parser.parse_args()

    source: DataSource
    if args.port:
        source = SerialSource(args.port)
    elif args.ble:
        source = BleSource()
    elif args.log:
        source = CsvReplaySource(args.log)
    else:
        source = SimulatedSource()

    run_gui(source)


if __name__ == "__main__":
    main()
