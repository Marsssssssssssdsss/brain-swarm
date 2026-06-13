"""锋电位仿真 — 模拟侵入式微电极阵列 (MEA) 记录

绕过人体侵入限制: 用统计模型生成高保真 spike 序列，
让非侵入式算法能在"侵入式级"信号上训练和验证。
"""

import numpy as np
from typing import Optional, List, Tuple
from dataclasses import dataclass, field


@dataclass
class SpikeTrainConfig:
    n_neurons: int = 32           # 模拟的神经元数量
    sampling_rate: int = 30000    # 侵入式典型采样率 (30kHz)
    duration: float = 1.0         # 单段时长 (秒)
    firing_rate_range: Tuple[float, float] = (0.5, 20.0)  # 放电率范围 (Hz)
    refractory_period: float = 0.002  # 不应期 (2ms)
    noise_floor: float = 0.1      # 背景噪声水平
    waveform_length: int = 48     # spike 波形采样点数 (~1.6ms @ 30kHz)


class SpikeSimulator:
    """锋电位序列仿真器"""

    def __init__(self, config: SpikeTrainConfig):
        self.cfg = config
        self._waveform_cache = {}

    def generate_train(self, firing_rates: Optional[np.ndarray] = None) -> np.ndarray:
        """生成多通道 spike 序列

        Args:
            firing_rates: (n_neurons,) 各神经元的平均放电率, None=随机

        Returns:
            (n_samples,) 多路复用的 spike 信号
        """
        n = self.cfg.n_neurons
        sr = self.cfg.sampling_rate
        n_samples = int(sr * self.cfg.duration)

        if firing_rates is None:
            low, high = self.cfg.firing_rate_range
            firing_rates = np.random.uniform(low, high, n)

        signal = np.zeros(n_samples)
        for neuron_idx in range(n):
            rate = firing_rates[neuron_idx]
            n_spikes = np.random.poisson(rate * self.cfg.duration)
            if n_spikes == 0:
                continue

            # 泊松放电 + 不应期
            spike_times = []
            for _ in range(n_spikes * 3):
                t = np.random.randint(0, n_samples - self.cfg.waveform_length)
                if not spike_times or t - spike_times[-1] > int(sr * self.cfg.refractory_period):
                    spike_times.append(t)
                    if len(spike_times) >= n_spikes:
                        break

            # 叠加 spike 波形
            waveform = self._get_waveform(neuron_idx)
            for t in spike_times:
                signal[t:t + len(waveform)] += waveform * (0.5 + np.random.random() * 0.5)

        # 加背景噪声
        signal += np.random.randn(n_samples) * self.cfg.noise_floor
        return signal

    def _get_waveform(self, neuron_idx: int) -> np.ndarray:
        """生成或缓存神经元特有的 spike 波形

        每个神经元的波形微不同 (幅值、宽度、形状)，
        模拟真实 MEA 记录中的"单元分离"特征。
        """
        if neuron_idx not in self._waveform_cache:
            wl = self.cfg.waveform_length
            t = np.linspace(0, 1, wl)
            amp = 1.0 + np.random.randn() * 0.2
            peak = 0.3 + np.random.random() * 0.3
            width = 0.05 + np.random.random() * 0.08
            # 双相波形 (典型的细胞外 spike)
            w = amp * (
                np.exp(-((t - peak) ** 2) / (2 * width ** 2))
                - 0.3 * np.exp(-((t - peak - 0.1) ** 2) / (2 * width ** 2))
            )
            self._waveform_cache[neuron_idx] = w
        return self._waveform_cache[neuron_idx]

    def generate_lfp(self, spike_train: np.ndarray) -> np.ndarray:
        """从 spike 序列提取 LFP (局部场电位)

        LFP 是低频成分 (< 300Hz)，反映突触输入而非输出。
        """
        from scipy import signal
        b, a = signal.butter(4, 300 / (self.cfg.sampling_rate / 2), btype='low')
        return signal.filtfilt(b, a, spike_train)

    def to_eeg_like(self, lfp: np.ndarray, target_sr: int = 250) -> np.ndarray:
        """将 LFP 降采样为"EEG 级"信号

        用途: 对比侵入式信号 vs 非侵入式信号的信息损失。
        """
        step = self.cfg.sampling_rate // target_sr
        return lfp[::step]
