import numpy as np
from typing import Optional, Dict
from dataclasses import dataclass
from enum import Enum


class StimMode(Enum):
    tDCS = "tdcs"
    tACS = "tacs"


@dataclass
class TDCSConfig:
    mode: StimMode = StimMode.tDCS
    current_ma: float = 1.0
    min_current: float = 0.0
    max_current: float = 2.0
    ramp_time: float = 30.0
    anode: str = "F3"
    cathode: str = "Fp2"
    tacs_frequency: float = 10.0
    closed_loop: bool = True


@dataclass
class StimState:
    active: bool = False
    current_ma: float = 0.0
    target_current_ma: float = 0.0
    ramp_progress: float = 0.0
    elapsed: float = 0.0
    session_time: float = 0.0


class TDCSModulator:
    FOCUS_MONTAGE = {
        "anode": "F3",
        "cathode": "Fp2",
        "target": "左背外侧前额叶 (DLPFC)",
        "effect": "提升专注力、工作记忆",
    }

    RELAX_MONTAGE = {
        "anode": "Fp1",
        "cathode": "T4",
        "target": "前额叶-颞叶回路",
        "effect": "促进放松、降低焦虑",
    }

    def __init__(self, config: TDCSConfig):
        self.cfg = config
        self.state = StimState()
        self._session_log: list = []

    def start(self):
        if self.state.active:
            return
        self.state.active = True
        self.state.elapsed = 0.0
        self.state.current_ma = 0.0
        self.state.target_current_ma = self.cfg.current_ma
        self.state.ramp_progress = 0.0

    def stop(self):
        self.state.active = False
        self.state.target_current_ma = 0.0

    def set_current(self, current_ma: float):
        clamped = max(self.cfg.min_current, min(self.cfg.max_current, current_ma))
        self.state.target_current_ma = clamped

    def step(self, dt: float = 1.0) -> StimState:
        if not self.state.active:
            self.state.current_ma = 0.0
            return self.state

        self.state.elapsed += dt
        self.state.session_time += dt

        if self.state.target_current_ma > self.state.current_ma:
            self.state.ramp_progress = min(1.0, self.state.elapsed / self.cfg.ramp_time)
            self.state.current_ma = self.state.target_current_ma * self.state.ramp_progress
        elif self.state.target_current_ma < self.state.current_ma:
            ramp_down = self.cfg.ramp_time * 0.5
            progress = min(1.0, self.state.elapsed / ramp_down)
            self.state.current_ma = self.state.current_ma * (1 - progress)
        else:
            self.state.ramp_progress = 1.0

        if self.state.current_ma < 0.01:
            self.state.current_ma = 0.0
            if self.state.target_current_ma == 0.0:
                self.state.active = False

        self._session_log.append({
            "time": self.state.session_time,
            "current": round(self.state.current_ma, 3),
            "target": round(self.state.target_current_ma, 3),
            "mode": self.cfg.mode.value,
        })
        return self.state

    def closed_loop_update(self, focus: float, relaxation: float) -> Dict:
        if not self.cfg.closed_loop:
            return {"current": self.state.current_ma, "action": "disabled"}

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

    def status(self) -> dict:
        return {
            "active": self.state.active,
            "current_ma": round(self.state.current_ma, 2),
            "target_ma": round(self.state.target_current_ma, 2),
            "ramp_progress": round(self.state.ramp_progress, 2),
            "session_time": round(self.state.session_time, 1),
            "mode": self.cfg.mode.value,
            "montage": f"{self.cfg.anode} → {self.cfg.cathode}",
        }

    def session_summary(self) -> dict:
        if not self._session_log:
            return {"sessions": 0}
        return {
            "sessions": 1,
            "total_time": round(self.state.session_time, 1),
            "max_current": max(s["current"] for s in self._session_log),
            "avg_current": round(np.mean([s["current"] for s in self._session_log]), 3),
        }
