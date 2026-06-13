import numpy as np
from scipy import signal
from typing import Tuple, Optional, Dict
from dataclasses import dataclass
from collections import deque
from enum import Enum


class BrainState(Enum):
    DEEP_SLEEP = "deep_sleep"
    LIGHT_SLEEP = "light_sleep"
    DROWSY = "drowsy"
    RELAXED = "relaxed"
    NEUTRAL = "neutral"
    FOCUSED = "focused"
    DEEP_FOCUS = "deep_focus"


@dataclass
class FocusReport:
    focus: float = 0.0
    relaxation: float = 0.0
    drowsiness: float = 0.0
    state: BrainState = BrainState.NEUTRAL
    theta_beta_ratio: float = 0.0
    alpha_power: float = 0.0
    delta_power: float = 0.0
    theta_power: float = 0.0
    beta_power: float = 0.0
    gamma_power: float = 0.0


class FocusDetector:
    BANDS = {
        "delta": (1.0, 4.0),
        "theta": (4.0, 8.0),
        "alpha": (8.0, 13.0),
        "beta": (13.0, 30.0),
        "gamma": (30.0, 45.0),
    }

    def __init__(
        self,
        sampling_rate: int = 250,
        fft_window: float = 2.0,
        update_rate: float = 4.0,
        smoothing: float = 0.3,
    ):
        self.sr = sampling_rate
        self._fft_n = int(fft_window * sampling_rate)
        self._step = int(sampling_rate / update_rate)
        self._freqs = np.fft.rfftfreq(self._fft_n, 1.0 / self.sr)
        self._win = np.hanning(self._fft_n)
        self._smoothing = smoothing
        self._band_bins = self._compute_band_bins()
        self._buf = deque(maxlen=self._fft_n)
        self._smoothed: Dict[str, float] = {b: 0.0 for b in self.BANDS}
        self._baselines: Dict[str, Optional[float]] = {b: None for b in self.BANDS}
        self._baseline_samples = 0
        self._baseline_interval = 100

    def _compute_band_bins(self):
        bins = {}
        for name, (low, high) in self.BANDS.items():
            mask = (self._freqs >= low) & (self._freqs <= high)
            bins[name] = np.where(mask)[0]
        return bins

    def _band_power(self, data: np.ndarray) -> Dict[str, float]:
        spec = np.abs(np.fft.rfft(data * self._win, n=self._fft_n)) ** 2
        powers = {}
        for name, bins in self._band_bins.items():
            if len(bins) == 0:
                powers[name] = 1e-10
            else:
                powers[name] = np.log(np.mean(spec[bins]) + 1e-10)
        return powers

    def _update_baselines(self, powers: Dict[str, float]):
        if self._baseline_samples < self._baseline_interval:
            for name in self.BANDS:
                if self._baselines[name] is None:
                    self._baselines[name] = powers[name]
                else:
                    alpha = 1.0 / (self._baseline_samples + 1)
                    self._baselines[name] = (1 - alpha) * self._baselines[name] + alpha * powers[name]
            self._baseline_samples += 1
        else:
            for name in self.BANDS:
                alpha = 0.01
                self._baselines[name] = (1 - alpha) * self._baselines[name] + alpha * powers[name]

    def feed(self, chunk: np.ndarray):
        if chunk.ndim == 1:
            self._buf.extend(chunk.tolist())
        else:
            self._buf.extend(chunk[0].tolist())

    def get_report(self) -> Optional[FocusReport]:
        if len(self._buf) < self._fft_n:
            return None

        data = np.array(self._buf)[-self._fft_n:]
        raw_powers = self._band_power(data)
        for name in self.BANDS:
            self._smoothed[name] = (
                self._smoothing * raw_powers[name] + (1 - self._smoothing) * self._smoothed[name]
            )
        self._update_baselines(self._smoothed)
        p = self._smoothed

        theta_beta = p["theta"] - p["beta"]
        focus_raw = max(0, min(100, 100 - (theta_beta + 5) * 10))
        alpha_rel = p["alpha"] - (self._baselines["alpha"] or p["alpha"])
        relax_raw = max(0, min(100, 50 + alpha_rel * 20))
        delta_rel = p["delta"] - (self._baselines["delta"] or p["delta"])
        drowsy_raw = max(0, min(100, (p["delta"] - p["beta"] + 5) * 10))

        if drowsy_raw > 60 and p["delta"] > p["theta"]:
            state = BrainState.DEEP_SLEEP
        elif drowsy_raw > 50:
            state = BrainState.LIGHT_SLEEP
        elif drowsy_raw > 35 and relax_raw > 60:
            state = BrainState.DROWSY
        elif relax_raw > 60 and focus_raw < 30:
            state = BrainState.RELAXED
        elif focus_raw > 70 and p["gamma"] > (self._baselines["gamma"] or p["gamma"]):
            state = BrainState.DEEP_FOCUS
        elif focus_raw > 55:
            state = BrainState.FOCUSED
        else:
            state = BrainState.NEUTRAL

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

    def reset_baseline(self):
        self._baselines = {b: None for b in self.BANDS}
        self._baseline_samples = 0

    def get_debug(self) -> dict:
        return {
            "buf_filled": len(self._buf),
            "buf_needed": self._fft_n,
            "baseline_samples": self._baseline_samples,
            "baselines": {k: round(v, 3) if v is not None else None for k, v in self._baselines.items()},
        }
