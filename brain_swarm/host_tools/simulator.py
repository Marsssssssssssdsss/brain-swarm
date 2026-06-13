"""
NeuroResonator Full Pipeline Simulator

Simulates the complete processing chain:
  1. ADS1299 EEG ADC (4ch, 24bit, 250Hz, synthetic signal generator)
  2. Band-pass filtering (0.5-40Hz)
  3. 512-point FFT with Hanning window
  4. 5-band power extraction per channel
  5. TFLite-like FocusDetector inference (heuristic fallback)
  6. Closed-loop rule engine
  7. tDCS current output + safety monitoring

Usage:
  python simulator.py                    # Default: 60s simulation with synthetic EEG
  python simulator.py --duration 300      # 5 minutes
  python simulator.py --focus-mode low    # Start in low focus state
  python simulator.py --output plot      # Show real-time plots
  python simulator.py --output csv       # Log to CSV file
"""

import argparse
import csv
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy import signal as scipy_signal


# ─────────────────────────────────────────────
# Brain State Enum (matches src/focus_detector.py)
# ─────────────────────────────────────────────

class BrainState(Enum):
    DROWSY = "drowsy"
    RELAXED = "relaxed"
    CALM = "calm"
    ATTENTIVE = "attentive"
    FOCUSED = "focused"
    DEEP_FOCUS = "deep_focus"
    HYPERFOCUS = "hyperfocus"


# ─────────────────────────────────────────────
# Synthetic EEG Generator
# ─────────────────────────────────────────────

@dataclass
class BandPowerProfile:
    delta: float = 1.0
    theta: float = 1.0
    alpha: float = 1.0
    beta: float = 1.0
    gamma: float = 1.0

    def as_array(self):
        return np.array([self.delta, self.theta, self.alpha, self.beta, self.gamma])


FOCUS_MODES: Dict[str, BandPowerProfile] = {
    "default":    BandPowerProfile(delta=2.0, theta=3.0, alpha=4.0, beta=3.0, gamma=1.5),
    "focused":    BandPowerProfile(delta=1.0, theta=1.5, alpha=2.5, beta=6.0, gamma=2.0),
    "drowsy":     BandPowerProfile(delta=6.0, theta=5.0, alpha=3.0, beta=1.0, gamma=0.5),
    "meditation": BandPowerProfile(delta=2.0, theta=2.0, alpha=8.0, beta=2.0, gamma=1.0),
    "hyperfocus": BandPowerProfile(delta=0.5, theta=1.0, alpha=2.0, beta=5.0, gamma=5.0),
}


class PinkNoiseGenerator:
    """1/f pink noise using Voss-McCartney algorithm."""
    def __init__(self, n_channels: int, n_rows: int = 16):
        self.n_channels = n_channels
        self.n_rows = n_rows
        self.pink = np.zeros((n_channels, n_rows))
        self.row_mask = 1 << np.arange(n_rows)

    def generate(self, n_samples: int) -> np.ndarray:
        out = np.zeros((self.n_channels, n_samples))
        for i in range(n_samples):
            white = np.random.randn(self.n_channels)
            self.pink += white[:, None] / self.n_rows
            for r in range(self.n_rows):
                if (i & self.row_mask[r]) == 0:
                    self.pink[:, r] = np.random.randn(self.n_channels)
            out[:, i] = self.pink.sum(axis=1) / np.sqrt(self.n_rows)
        return out


class EegSynthesizer:
    """
    Generates synthetic multi-channel EEG with configurable band powers,
    realistic noise, and smooth mode transitions.
    """
    BANDS = {
        "delta": (1.0, 4.0),
        "theta": (4.0, 8.0),
        "alpha": (8.0, 13.0),
        "beta":  (13.0, 30.0),
        "gamma": (30.0, 45.0),
    }
    BAND_NAMES = ["delta", "theta", "alpha", "beta", "gamma"]
    BAND_CENTERS = {"delta": 2.5, "theta": 6.0, "alpha": 10.5, "beta": 21.5, "gamma": 37.5}

    def __init__(self, n_channels: int = 4, sampling_rate: int = 250):
        self.n_channels = n_channels
        self.sr = sampling_rate
        self._mode: str = "default"
        self._profile = FOCUS_MODES["default"].as_array()
        self._target_profile = self._profile.copy()
        self._transition_speed = 0.005
        self._pink = PinkNoiseGenerator(n_channels)
        self._phase: Dict[str, float] = {b: np.random.uniform(0, 2 * np.pi) for b in self.BAND_NAMES}
        self._t = 0.0

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str, transition_speed: float = 0.005):
        if mode in FOCUS_MODES:
            self._mode = mode
            self._target_profile = FOCUS_MODES[mode].as_array()
            self._transition_speed = transition_speed

    def generate(self, n_samples: int) -> np.ndarray:
        t = self._t + np.arange(n_samples) / self.sr
        self._t += n_samples / self.sr

        self._profile += self._transition_speed * (self._target_profile - self._profile)

        raw = np.zeros((self.n_channels, n_samples))
        for ch in range(self.n_channels):
            for bi, band in enumerate(self.BAND_NAMES):
                amp = self._profile[bi] * (0.8 + 0.4 * np.random.uniform())
                freq = self.BAND_CENTERS[band] + 0.3 * np.random.uniform(-1, 1)
                raw[ch, :] += amp * np.sin(2 * np.pi * freq * t + self._phase[band] + ch * 0.3)
                self._phase[band] += 0.02 * np.random.uniform(-1, 1)

        pink = self._pink.generate(n_samples) * 0.3
        raw += pink

        gaussian = np.random.randn(self.n_channels, n_samples) * 10.0
        raw += gaussian

        mains = 5.0 * np.sin(2 * np.pi * 50 * t + self._phase.get("mains", 0))
        raw += mains
        self._phase["mains"] = self._phase.get("mains", 0) + 0.01

        raw = raw * 1e-6
        return raw


# ─────────────────────────────────────────────
# Signal Processing Block
# ─────────────────────────────────────────────

class SignalProcessor:
    """Band-pass filter + FFT + band power extraction."""
    def __init__(self, n_channels: int = 4, sampling_rate: int = 250):
        self.n_channels = n_channels
        self.sr = sampling_rate
        self.fft_n = 512
        self.window = np.hanning(self.fft_n)
        self._freqs = np.fft.rfftfreq(self.fft_n, 1.0 / self.sr)
        self._band_bins: Dict[str, np.ndarray] = {}
        self._build_filters()
        self._compute_band_bins()

    def _build_filters(self):
        nyquist = self.sr / 2.0
        self.bp_b, self.bp_a = scipy_signal.butter(4, [0.5 / nyquist, 40.0 / nyquist], btype="band")
        q = 30.0
        self.notch_b, self.notch_a = scipy_signal.iirnotch(50.0, q, self.sr)

    def _compute_band_bins(self):
        import sys, os; sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
        from src.focus_detector import FocusDetector
        self._band_bins = FocusDetector(self.sr)._compute_band_bins()

    def process(self, data: np.ndarray) -> Tuple[np.ndarray, Dict[str, np.ndarray]]:
        filtered = scipy_signal.filtfilt(self.bp_b, self.bp_a, data, axis=-1)
        filtered = scipy_signal.filtfilt(self.notch_b, self.notch_a, filtered, axis=-1)

        ch_powers: Dict[str, np.ndarray] = {b: np.zeros(self.n_channels) for b in
                                              ["delta", "theta", "alpha", "beta", "gamma"]}
        for ch in range(self.n_channels):
            segment = filtered[ch, -self.fft_n:]
            if len(segment) < self.fft_n:
                segment = np.pad(segment, (0, self.fft_n - len(segment)), "constant")
            spec = np.abs(np.fft.rfft(segment * self.window, n=self.fft_n)) ** 2
            for name, bins in self._band_bins.items():
                if len(bins) == 0:
                    ch_powers[name][ch] = 1e-10
                else:
                    ch_powers[name][ch] = np.log(np.mean(spec[bins]) + 1e-10)

        return filtered, ch_powers


# ─────────────────────────────────────────────
# Focus Detector (standalone, mirrors src/focus_detector.py)
# ─────────────────────────────────────────────

@dataclass
class FocusReport:
    focus: float = 0.0
    relaxation: float = 0.0
    drowsiness: float = 0.0
    state: BrainState = BrainState.CALM
    theta_beta_ratio: float = 0.0
    alpha_power: float = 0.0
    delta_power: float = 0.0
    theta_power: float = 0.0
    beta_power: float = 0.0
    gamma_power: float = 0.0


class FocusDetector:
    """Heuristic focus detector matching firmware logic."""

    def __init__(self, smoothing: float = 0.3):
        self._smoothing = smoothing
        self._smoothed: Dict[str, float] = {b: 0.0 for b in ["delta", "theta", "alpha", "beta", "gamma"]}

    def update(self, powers: Dict[str, np.ndarray]) -> FocusReport:
        avg_powers = {}
        for name in ["delta", "theta", "alpha", "beta", "gamma"]:
            val = float(np.mean(powers[name]))
            self._smoothed[name] = self._smoothing * val + (1 - self._smoothing) * self._smoothed[name]
            avg_powers[name] = self._smoothed[name]

        p = avg_powers
        theta_beta = p["theta"] - p["beta"]
        focus_raw = max(0, min(100, 100 - (theta_beta + 5) * 10))
        relax_raw = max(0, min(100, 50 + (p["alpha"] - p["delta"]) * 20))
        drowsy_raw = max(0, min(100, (p["delta"] - p["beta"] + 5) * 10))

        state: BrainState
        if drowsy_raw > 55 and p["theta"] > p["beta"]:
            state = BrainState.DROWSY
        elif relax_raw > 65 and focus_raw < 35:
            state = BrainState.RELAXED
        elif focus_raw > 55 and relax_raw > 55:
            state = BrainState.CALM
        elif 45 <= focus_raw <= 65 and relax_raw < 50:
            state = BrainState.ATTENTIVE
        elif focus_raw > 70 and p["gamma"] > p["beta"]:
            state = BrainState.HYPERFOCUS
        elif focus_raw > 65:
            state = BrainState.DEEP_FOCUS
        elif focus_raw > 50:
            state = BrainState.FOCUSED
        else:
            state = BrainState.CALM

        return FocusReport(
            focus=round(focus_raw),
            relaxation=round(relax_raw),
            drowsiness=round(drowsy_raw),
            state=state,
            theta_beta_ratio=round(theta_beta, 2),
            alpha_power=round(p["alpha"], 2),
            delta_power=round(p["delta"], 2),
            theta_power=round(p["theta"], 2),
            beta_power=round(p["beta"], 2),
            gamma_power=round(p["gamma"], 2),
        )


# ─────────────────────────────────────────────
# tDCS Modulator (standalone, mirrors src/neuromod/tdcs.py)
# ─────────────────────────────────────────────

@dataclass
class StimState:
    active: bool = False
    current_ma: float = 0.0
    target_current_ma: float = 0.0
    session_time: float = 0.0
    max_ramp_rate: float = 0.5


class TdcsModulator:
    """Closed-loop tDCS stimulator with ramp limiting and safety."""
    SAFETY_MAX_CURRENT = 2.0
    SAFETY_MAX_SESSION = 3600.0
    SAFETY_MAX_DOSE = 7.2

    def __init__(self, max_current: float = 2.0):
        self.state = StimState(max_ramp_rate=0.5)
        self._elapsed = 0.0
        self._total_dose = 0.0
        self._impedance_kohm = 5.0
        self._overcurrent = False
        self._history: List[dict] = []

    def start(self):
        self.state.active = True

    def stop(self):
        self.state.target_current_ma = 0.0

    def set_current(self, ma: float):
        self.state.target_current_ma = max(0, min(self.SAFETY_MAX_CURRENT, ma))

    def closed_loop_update(self, focus: float, relaxation: float) -> Dict:
        if not self.state.active:
            return {"current": 0.0, "action": "inactive"}
        if focus > 70 and self.state.current_ma < 0.5:
            self.set_current(0.5)
            return {"current": 0.5, "action": "focus_boost"}
        elif relaxation > 70 and focus < 30:
            self.set_current(0.0)
            return {"current": 0.0, "action": "relax_stop"}
        elif focus < 40 and self.state.current_ma > 0:
            self.set_current(max(0, self.state.current_ma - 0.3))
            return {"current": self.state.current_ma, "action": "reduce"}
        elif focus > 60 and self.state.current_ma > 0:
            return {"current": self.state.current_ma, "action": "maintain"}
        return {"current": self.state.current_ma, "action": "idle"}

    def step(self, dt: float = 1.0):
        if not self.state.active:
            self.state.current_ma = 0.0
            return

        self._elapsed += dt
        self.state.session_time += dt

        diff = self.state.target_current_ma - self.state.current_ma
        max_delta = self.state.max_ramp_rate * dt
        if abs(diff) > max_delta:
            diff = np.sign(diff) * max_delta
        self.state.current_ma += diff

        self.state.current_ma = max(0, min(self.SAFETY_MAX_CURRENT, self.state.current_ma))
        self._total_dose += self.state.current_ma * dt / 3600.0

        if self.state.current_ma > self.SAFETY_MAX_CURRENT:
            self._overcurrent = True

        self._history.append({
            "time": self.state.session_time,
            "current": self.state.current_ma,
            "target": self.state.target_current_ma,
        })

    def check_safety(self) -> List[str]:
        alerts = []
        if self._overcurrent:
            alerts.append("OVERCURRENT")
        if self._impedance_kohm > 20:
            alerts.append("HIGH_IMPEDANCE")
        if self.state.session_time > self.SAFETY_MAX_SESSION:
            alerts.append("SESSION_TIMEOUT")
        if self._total_dose > self.SAFETY_MAX_DOSE:
            alerts.append("MAX_DOSE_EXCEEDED")
        return alerts

    @property
    def status_str(self) -> str:
        alerts = self.check_safety()
        if not self.state.active:
            return "⏹ STOPPED"
        if alerts:
            return f"⚠ {' | '.join(alerts)}"
        return "🟢 OK"


# ─────────────────────────────────────────────
# Simulated Impedance Monitor
# ─────────────────────────────────────────────

class ImpedanceMonitor:
    """Simulates electrode-scalp impedance for safety monitoring."""
    def __init__(self, n_channels: int = 4):
        self.n_channels = n_channels
        self.impedances = np.random.uniform(3, 12, n_channels + 2)
        self.impedances[0] = np.random.uniform(4, 15)

    def step(self):
        noise = np.random.randn(self.n_channels + 2) * 0.3
        self.impedances = np.clip(self.impedances + noise, 1.0, 30.0)

    def check(self) -> Tuple[bool, List[float]]:
        return bool(np.all(self.impedances[:self.n_channels] < 20)), self.impedances[:self.n_channels].tolist()


# ─────────────────────────────────────────────
# Real-time Console Display
# ─────────────────────────────────────────────

STATE_LABELS = {
    BrainState.DROWSY: "[DRW]",
    BrainState.RELAXED: "[RLX]",
    BrainState.CALM: "[CLM]",
    BrainState.ATTENTIVE: "[ATT]",
    BrainState.FOCUSED: "[FCS]",
    BrainState.DEEP_FOCUS: "[DFC]",
    BrainState.HYPERFOCUS: "[HYP]",
}


def format_display(t: float, report: FocusReport, stim: TdcsModulator, impedance_ok: bool):
    sigil = STATE_LABELS.get(report.state, "[???]")
    imp = "STIM_OK" if impedance_ok else "STIM_FAIL"
    return (
        f"[{t:6.1f}s] "
        f"t/b={report.theta_beta_ratio:.2f} "
        f"a={report.alpha_power:.1f}dB "
        f"| {sigil} {report.state.value.upper()}({report.focus}) "
        f"RELAX({report.relaxation}) "
        f"| tDCS {stim.state.current_ma:+.2f}mA "
        f"| {imp}"
    )


# ─────────────────────────────────────────────
# CSV Logger
# ─────────────────────────────────────────────

class CsvLogger:
    def __init__(self, path: str):
        self.file = open(path, "w", newline="")
        self.writer = csv.writer(self.file)
        self.writer.writerow([
            "time_s", "focus", "relaxation", "drowsiness",
            "state", "theta_beta", "alpha_dB",
            "stim_current_ma", "stim_target_ma",
            "impedance_ok", "alerts",
        ])
        self.file.flush()

    def write(self, t: float, report: FocusReport, stim: TdcsModulator, impedance_ok: bool, alerts: str):
        self.writer.writerow([
            f"{t:.1f}", report.focus, report.relaxation, report.drowsiness,
            report.state.value, f"{report.theta_beta_ratio:.2f}", f"{report.alpha_power:.2f}",
            f"{stim.state.current_ma:.3f}", f"{stim.state.target_current_ma:.3f}",
            int(impedance_ok), alerts,
        ])
        self.file.flush()

    def close(self):
        self.file.close()


# ─────────────────────────────────────────────
# Matplotlib Real-time Plotter
# ─────────────────────────────────────────────

class RealtimePlotter:
    def __init__(self, n_channels: int = 4, sampling_rate: int = 250):
        plt = self._import_pyplot()
        if plt is None:
            raise ImportError("matplotlib not available")
        self.plt = plt
        self.n_channels = n_channels
        self.sr = sampling_rate
        self.fig, axes = plt.subplots(4, 1, figsize=(10, 8), sharex=False)
        self.ax_eeg = axes[0]
        self.ax_spec = axes[1]
        self.ax_focus = axes[2]
        self.ax_stim = axes[3]
        self.fig.tight_layout(pad=2.0)
        plt.ion()
        plt.show(block=False)

        self.eeg_lines = []
        colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728"]
        for ch in range(n_channels):
            line, = self.ax_eeg.plot([], [], color=colors[ch % len(colors)], label=f"CH{ch+1}")
            self.eeg_lines.append(line)
        self.ax_eeg.set_ylabel("\u03bcV")
        self.ax_eeg.legend(loc="upper right")
        self.ax_eeg.set_title("EEG 4-Channel (4s window)")

        self.spec_img = self.ax_spec.imshow(
            np.zeros((50, 100)), aspect="auto", origin="lower",
            cmap="inferno", vmin=-10, vmax=10,
            extent=[0, 100, 0, 50],
        )
        self.ax_spec.set_xlabel("Time (%)")
        self.ax_spec.set_ylabel("Hz")
        self.ax_spec.set_title("Spectrogram (CH1)")

        self.focus_bar = self.ax_focus.barh([0], [0], color="#2ca02c")
        self.ax_focus.set_xlim(0, 100)
        self.ax_focus.set_ylim(-0.5, 0.5)
        self.ax_focus.set_xlabel("Focus Score")
        self.ax_focus.set_title("Focus Score")
        self.focus_text = self.ax_focus.text(50, 0, "0", ha="center", va="center", fontsize=14, fontweight="bold")

        self.stim_line, = self.ax_stim.plot([], [], color="#d62728", lw=2)
        self.ax_stim.set_ylabel("mA")
        self.ax_stim.set_xlabel("Time (s)")
        self.ax_stim.set_title("tDCS Current")
        self.ax_stim.set_ylim(-0.1, 2.5)

        self._tdata: List[float] = []
        self._stimdata: List[float] = []
        self._spec_buf: List[np.ndarray] = []
        self._eeg_buf: List[np.ndarray] = []

    def _import_pyplot(self):
        try:
            import matplotlib
            matplotlib.use("TkAgg")
            import matplotlib.pyplot as plt
            return plt
        except ImportError:
            return None

    def update(self, t: float, raw_eeg: np.ndarray, filtered: np.ndarray, focus: float, stim_ma: float):
        self._tdata.append(t)
        self._stimdata.append(stim_ma)

        window = 4 * self.sr
        if self._tdata and len(self._tdata) > 0:
            display_n = min(filtered.shape[1], window)
            x = np.arange(display_n) / self.sr
            for ch in range(min(self.n_channels, len(self.eeg_lines))):
                self.eeg_lines[ch].set_data(x, filtered[ch, -display_n:] * 1e6)
            self.ax_eeg.relim()
            self.ax_eeg.autoscale_view()

        if self._tdata and len(self._tdata) > 0:
            seg = filtered[0, -256:]
            if len(seg) >= 32:
                f, t_spec, Sxx = scipy_signal.spectrogram(seg, fs=self.sr, nperseg=64)
                self._spec_buf.append(np.log10(Sxx.mean(axis=1) + 1e-10))
                if len(self._spec_buf) > 100:
                    self._spec_buf.pop(0)
                spec_arr = np.array(self._spec_buf).T
                self.spec_img.set_data(spec_arr)
                self.spec_img.set_extent([0, spec_arr.shape[1], f[0], f[-1]])

        self.focus_bar[0].set_width(focus)
        self.focus_text.set_text(str(int(focus)))

        if len(self._tdata) > 2:
            self.stim_line.set_data(self._tdata[-200:], self._stimdata[-200:])
            self.ax_stim.relim()
            self.ax_stim.autoscale_view()

        self.plt.pause(0.001)

    def close(self):
        self.plt.ioff()
        self.plt.close(self.fig)


# ─────────────────────────────────────────────
# Main Simulation Loop
# ─────────────────────────────────────────────

def run_simulation(args: argparse.Namespace):
    duration = args.duration
    mode = args.focus_mode
    n_channels = 4
    sr = 250

    synth = EegSynthesizer(n_channels=n_channels, sampling_rate=sr)
    synth.set_mode(mode)

    sig_proc = SignalProcessor(n_channels=n_channels, sampling_rate=sr)
    detector = FocusDetector(smoothing=0.3)
    stim = TdcsModulator(max_current=2.0)
    imp_mon = ImpedanceMonitor(n_channels=n_channels)

    csv_logger: Optional[CsvLogger] = None
    if args.output == "csv":
        csv_logger = CsvLogger(f"neuroresonator_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")

    plotter: Optional[RealtimePlotter] = None
    if args.output == "plot":
        try:
            plotter = RealtimePlotter(n_channels=n_channels, sampling_rate=sr)
        except ImportError:
            print("matplotlib not available; disabling plots")

    sample_buf = deque()
    report_interval = sr
    samples_since_report = 0

    stim.start()
    print(f"\nNeuroResonator Pipeline Simulator")
    print(f"  Mode: {mode}  Duration: {duration}s  Channels: {n_channels}  SR: {sr}Hz")
    print(f"  Output: {args.output}\n")

    start_time = time.time()
    last_display = 0.0

    try:
        while True:
            elapsed = time.time() - start_time
            if elapsed >= duration:
                break

            n_samples = int(sr * 0.05)
            chunk = synth.generate(n_samples)
            sample_buf.extend(chunk.T.tolist())

            sig_in = np.array(sample_buf).T if len(sample_buf) >= sig_proc.fft_n else chunk
            if sig_in.shape[1] >= sig_proc.fft_n:
                filtered, powers = sig_proc.process(sig_in)
            else:
                filtered = chunk
                powers = {b: np.zeros(n_channels) for b in ["delta","theta","alpha","beta","gamma"]}

            samples_since_report += n_samples
            if samples_since_report >= report_interval:
                samples_since_report = 0

                report = detector.update(powers)
                stim.closed_loop_update(report.focus, report.relaxation)
                stim.step(dt=1.0)
                imp_mon.step()
                imp_ok, _ = imp_mon.check()
                alerts = stim.check_safety()

                now = time.time()
                if now - last_display >= 0.8:
                    last_display = now
                    line = format_display(elapsed, report, stim, imp_ok)
                    alerts_str = ";".join(alerts) if alerts else ""
                    if alerts:
                        line += f" ⚠ {alerts_str}"
                    print(f"\r{' ' * 120}\r{line}", end="", flush=True)

                    if csv_logger:
                        csv_logger.write(elapsed, report, stim, imp_ok, alerts_str)

                    if plotter and len(sample_buf) >= sig_proc.fft_n:
                        raw_arr = np.array(sample_buf).T
                        plotter.update(elapsed, raw_arr[:, -sig_proc.fft_n:], filtered, report.focus, stim.state.current_ma)

            time.sleep(0.01)

    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")
    finally:
        stim.stop()
        print()
        if csv_logger:
            csv_logger.close()
            print(f"CSV log saved.")
        if plotter:
            plotter.close()
        print("Simulation complete.")


def main():
    parser = argparse.ArgumentParser(description="NeuroResonator Full Pipeline Simulator")
    parser.add_argument("--duration", "-d", type=float, default=60, help="Simulation duration in seconds (default: 60)")
    parser.add_argument("--focus-mode", "-m", type=str, default="default",
                        choices=list(FOCUS_MODES.keys()),
                        help="Initial focus mode (default: default)")
    parser.add_argument("--output", "-o", type=str, default="console",
                        choices=["console", "plot", "csv"],
                        help="Output mode: console text, matplotlib plot, or CSV log (default: console)")
    args = parser.parse_args()
    run_simulation(args)


if __name__ == "__main__":
    main()
