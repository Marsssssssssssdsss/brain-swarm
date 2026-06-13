"""
Electrode Impedance Calibration Tool

Measures and validates electrode contact impedance for all channels.
Used during device setup before each use.

Usage:
  python calibration.py --port COM3
  python calibration.py --sim  # Simulated calibration for testing
"""

import argparse
import csv
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

import numpy as np


# ─────────────────────────────────────────────
# Electrode Configuration
# ─────────────────────────────────────────────

ELECTRODE_LAYOUT = [
    ("Fp1",  "CH1",  "EEG"),
    ("Fp2",  "CH2",  "EEG"),
    ("C3",   "CH3",  "EEG"),
    ("C4",   "CH4",  "EEG"),
    ("A1",   "REF",  "Reference"),
    ("Fz",   "GND",  "Ground"),
    ("F3",   "STIM+","Stimulation Anode"),
    ("Fp2",  "STIM-","Stimulation Cathode"),
]

IMPEDANCE_PASS_THRESHOLD = 20.0  # kΩ (pass if below this)
IMPEDANCE_WARN_THRESHOLD = 15.0  # kΩ


@dataclass
class ChannelMeasurement:
    name: str
    label: str
    role: str
    impedance_kohm: float = 0.0
    passed: bool = False
    warning: bool = False


class ImpedanceMeasurementSource:
    """Abstract base for impedance measurement sources."""

    def connect(self) -> bool:
        raise NotImplementedError

    def disconnect(self):
        raise NotImplementedError

    def measure_channel(self, channel_index: int) -> float:
        raise NotImplementedError

    def measure_all(self, n_channels: int) -> List[float]:
        return [self.measure_channel(i) for i in range(n_channels)]


class SimulatedImpedanceSource(ImpedanceMeasurementSource):
    """Simulates impedance measurement for testing without hardware."""

    def __init__(self):
        self._baseline = np.array([
            np.random.uniform(3, 18) for _ in range(len(ELECTRODE_LAYOUT))
        ])

    def connect(self) -> bool:
        print("Simulated impedance source connected.")
        return True

    def disconnect(self):
        pass

    def measure_channel(self, channel_index: int) -> float:
        time.sleep(0.3)
        noise = np.random.randn() * 0.5
        val = self._baseline[channel_index] + noise
        if np.random.random() < 0.05:
            val += np.random.uniform(10, 30)
        return max(0.5, val)

    def measure_all(self, n_channels: int) -> List[float]:
        results = []
        for i in range(n_channels):
            results.append(self.measure_channel(i))
        return results


class SerialImpedanceSource(ImpedanceMeasurementSource):
    """Measures impedance via serial command interface."""

    def __init__(self, port: str, baud: int = 115200):
        self.port = port
        self.baud = baud
        self._ser = None

    def connect(self) -> bool:
        try:
            import serial
            self._ser = serial.Serial(self.port, self.baud, timeout=5)
            self._ser.write(b"AT+IMPEDANCE\r\n")
            resp = self._ser.read(100)
            return b"OK" in resp
        except Exception as e:
            print(f"Serial connection failed: {e}")
            return False

    def disconnect(self):
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass

    def measure_channel(self, channel_index: int) -> float:
        if not self._ser:
            return 999.0
        cmd = f"AT+IMP={channel_index}\r\n".encode()
        self._ser.write(cmd)
        resp = self._ser.read(50).decode(errors="replace").strip()
        try:
            return float(resp.split("=")[-1].split(",")[0])
        except (ValueError, IndexError):
            return 999.0


# ─────────────────────────────────────────────
# Calibration Display
# ─────────────────────────────────────────────

def print_table(measurements: List[ChannelMeasurement]):
    header = f"{'Electrode':<10} {'Channel':<8} {'Role':<22} {'Impedance':<12} {'Status':<12}"
    sep = "─" * len(header)
    print(f"\n{header}")
    print(sep)

    for m in measurements:
        imp_str = f"{m.impedance_kohm:.1f} k\u03a9"
        if m.passed:
            status = "\u2705 PASS"
        elif m.warning:
            status = "\u26a0\ufe0f WARNING"
        else:
            status = "\u274c FAIL (>20k\u03a9)"

        print(f"{m.name:<10} {m.label:<8} {m.role:<22} {imp_str:<12} {status:<12}")

    print(sep)


def print_verdict(measurements: List[ChannelMeasurement]) -> str:
    passed = sum(1 for m in measurements if m.passed)
    warned = sum(1 for m in measurements if m.warning)
    failed = sum(1 for m in measurements if not m.passed and not m.warning)

    print(f"\n  \u2705 Passed: {passed}/{len(measurements)}")
    if warned:
        print(f"  \u26a0\ufe0f  Warnings: {warned}")
    if failed:
        print(f"  \u274c Failed: {failed}")

    if failed > 0:
        verdict = "POOR"
        color = "\033[91m"
        print(f"\n  {color}\u26a0\ufe0f  VERDICT: POOR \u2014 Adjust electrodes and retry.\033[0m")
    elif warned > 0:
        verdict = "NEEDS_ADJUSTMENT"
        color = "\033[93m"
        print(f"\n  {color}\u26a0\ufe0f  VERDICT: NEEDS_ADJUSTMENT \u2014 Some electrodes need attention.\033[0m")
    else:
        verdict = "GOOD"
        color = "\033[92m"
        print(f"\n  {color}\u2705 VERDICT: GOOD \u2014 All electrodes have good contact.\033[0m")

    return verdict


def export_csv(measurements: List[ChannelMeasurement], filepath: str):
    with open(filepath, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Electrode", "Channel", "Role", "Impedance_kOhm", "Passed", "Warning"])
        for m in measurements:
            writer.writerow([m.name, m.label, m.role, f"{m.impedance_kohm:.2f}", m.passed, m.warning])
    print(f"\nResults exported to {filepath}")


# ─────────────────────────────────────────────
# Serial Plot (ASCII impedance over time)
# ─────────────────────────────────────────────

class ImpedancePlotter:
    """Real-time ASCII impedance trend for electrode settling observation."""

    def __init__(self, n_channels: int = 8, width: int = 60, height: int = 12):
        self.n_channels = n_channels
        self.width = width
        self.height = height
        self._history: List[List[float]] = []

    def add_measurement(self, impedances: List[float]):
        self._history.append(impedances)
        if len(self._history) > self.width:
            self._history.pop(0)

    def display(self):
        if len(self._history) < 2:
            return
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.write("Impedance Trend (k\u03a9) - Electrode Settling\n")
        sys.stdout.write("─" * (self.width + 20) + "\n")

        arr = np.array(self._history)
        for ch in range(min(self.n_channels, arr.shape[1])):
            vals = arr[:, ch]
            label = f"{ELECTRODE_LAYOUT[ch][0]:<6}"
            line = self._render_line(vals)
            sys.stdout.write(f"{label} |{line}|\n")

        sys.stdout.write("─" * (self.width + 20) + "\n")
        sys.stdout.write(f"Time: {len(self._history)} measurements\n")
        sys.stdout.flush()

    def _render_line(self, vals: np.ndarray) -> str:
        if vals.max() == vals.min():
            return " " * self.width
        scaled = ((vals - vals.min()) / (vals.max() - vals.min() + 1e-10) * (self.height - 1)).astype(int)
        out = []
        for s in scaled:
            out.append(" ▁▂▃▄▅▆▇█"[min(s, 7)])
        return "".join(out)


# ─────────────────────────────────────────────
# Main Calibration Routine
# ─────────────────────────────────────────────

def run_calibration(source: ImpedanceMeasurementSource, continuous: bool = False, export: Optional[str] = None):
    if not source.connect():
        print("Failed to connect to device.")
        return False

    n_channels = len(ELECTRODE_LAYOUT)
    print(f"\nNeuroResonator Electrode Impedance Calibration")
    print(f"  {n_channels} electrodes  |  Pass: <{IMPEDANCE_PASS_THRESHOLD}k\u03a9  "
          f"Warn: <{IMPEDANCE_WARN_THRESHOLD}k\u03a9\n")
    print("Measuring impedances...")

    plotter = ImpedancePlotter(n_channels=n_channels)

    try:
        if continuous:
            print("Continuous monitoring mode. Press Ctrl+C to stop.\n")
            while True:
                raw_impedances = source.measure_all(n_channels)
                measurements = _build_measurements(raw_impedances)
                plotter.add_measurement(raw_impedances)
                plotter.display()
                print_table(measurements)
                print_verdict(measurements)
                time.sleep(1.0)
        else:
            raw_impedances = source.measure_all(n_channels)
            measurements = _build_measurements(raw_impedances)

            print_table(measurements)
            verdict = print_verdict(measurements)

            if export:
                export_csv(measurements, export)

            if verdict == "POOR":
                try:
                    import winsound
                    winsound.Beep(800, 500)
                    winsound.Beep(600, 500)
                except ImportError:
                    print("\a\a")

            return verdict != "POOR"

    except KeyboardInterrupt:
        print("\nCalibration interrupted.")
        return False
    finally:
        source.disconnect()


def _build_measurements(raw_impedances: List[float]) -> List[ChannelMeasurement]:
    measurements = []
    for i, (name, label, role) in enumerate(ELECTRODE_LAYOUT):
        imp = raw_impedances[i] if i < len(raw_impedances) else 999.0
        measurements.append(ChannelMeasurement(
            name=name,
            label=label,
            role=role,
            impedance_kohm=imp,
            passed=imp < IMPEDANCE_WARN_THRESHOLD,
            warning=IMPEDANCE_WARN_THRESHOLD <= imp < IMPEDANCE_PASS_THRESHOLD,
        ))
    return measurements


def main():
    parser = argparse.ArgumentParser(description="Electrode Impedance Calibration Tool")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--port", "-p", type=str, help="Serial port (e.g. COM3)")
    group.add_argument("--sim", "-s", action="store_true", help="Simulated calibration for testing")
    parser.add_argument("--continuous", "-c", action="store_true", help="Continuous monitoring mode")
    parser.add_argument("--export", "-e", type=str, help="Export results to CSV file")
    args = parser.parse_args()

    if args.sim:
        source = SimulatedImpedanceSource()
    elif args.port:
        source = SerialImpedanceSource(args.port)
    else:
        print("No source specified. Use --port COM3 or --sim. Defaulting to --sim.")
        source = SimulatedImpedanceSource()

    success = run_calibration(source, continuous=args.continuous, export=args.export)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
