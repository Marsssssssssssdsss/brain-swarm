"""聚焦超声 (tFUS) 神经调控仿真

tFUS 是当前最有潜力的非侵入式"写入"技术:
  - 毫米级空间精度 (比 tDCS 高 10x)
  - 可深达皮层下结构
  - 可兴奋或抑制, 取决于频率和占空比

这层仿真是在中国做"类侵入式"效果的关键技术栈。
"""

import numpy as np
from typing import Optional, Tuple
from dataclasses import dataclass


@dataclass
class TFUSConfig:
    frequency: float = 500e3        # 超声频率 (500kHz)
    prf: float = 1000.0             # 脉冲重复频率 (Hz)
    duty_cycle: float = 0.3         # 占空比 (30%)
    intensity: float = 1.0          # 空间峰值时间平均强度 (W/cm²)
    spot_size_mm: float = 2.0       # 焦点直径 (mm)
    target_depth_mm: float = 20.0   # 目标深度 (mm)
    n_elements: int = 256           # 换能器阵元数
    modulation_rate: float = 10.0   # 调制速率 (Hz)


class TFUSModulator:
    """聚焦超声调控仿真器

    模拟超声换能器阵列的声场分布和神经调控效果。
    """

    def __init__(self, config: TFUSConfig):
        self.cfg = config

    def compute_pressure_field(self, x: np.ndarray, y: np.ndarray, z: float) -> np.ndarray:
        """计算二维声场压力分布

        使用简化的瑞利-索末菲积分模拟。
        """
        k = 2 * np.pi * self.cfg.frequency / 1500  # 波数 (声速~1500m/s)
        r = np.sqrt(x ** 2 + y ** 2 + z ** 2)
        pressure = np.sin(k * r) / (r + 1e-6)
        return pressure * self.cfg.intensity

    def compute_neural_effect(self, pressure: np.ndarray) -> np.ndarray:
        """压力 → 神经元兴奋/抑制效果

        正压力 → 兴奋 (钠通道机械激活)
        负压力 → 抑制 (钾通道优先激活)
        """
        return np.tanh(pressure * 2.0)

    def simulate_stimulation(self, eeg_signal: np.ndarray, sampling_rate: int) -> np.ndarray:
        """模拟超声刺激对 EEG 的影响

        按照当前 tFUS 参数，将刺激伪迹叠加到 EEG 上，
        并按调制率产生周期性影响。
        """
        n = len(eeg_signal)
        t = np.arange(n) / sampling_rate
        envelope = 0.5 * (1 + np.sin(2 * np.pi * self.cfg.modulation_rate * t))
        artifact = envelope * self.cfg.intensity * 0.01
        return eeg_signal + artifact

    def closed_loop_update(self, current_eeg: np.ndarray, target_state: str) -> dict:
        """闭环更新调控参数

        根据当前脑状态调整超声参数以驱动到目标状态。
        """
        power = np.mean(current_eeg ** 2)
        if target_state == "excite":
            new_intensity = min(self.cfg.intensity * 1.1, 3.0)
        elif target_state == "suppress":
            new_intensity = max(self.cfg.intensity * 0.9, 0.1)
        else:
            new_intensity = self.cfg.intensity

        return {
            "previous_intensity": self.cfg.intensity,
            "new_intensity": new_intensity,
            "power": power,
            "target": target_state,
        }
