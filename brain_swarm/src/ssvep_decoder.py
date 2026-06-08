"""
SSVEP 解码器 —— 实时脑控最实用的方案
========================================

原理: 屏幕显示多个以不同频率闪烁的方块,
     视觉皮层会产生相同频率的稳态视觉诱发电位 (SSVEP).
     通过 FFT 频谱分析, 1-2 秒即可锁定用户注视的目标.
     
优势:
  - 不需要训练 (用户注视闪烁方块即可)
  - 频域信噪比高, 不依赖时域平均
  - 6 个不同频率 = 6 个可靠命令, 准确率 ~90%
  - 只需 1-2 个枕叶通道 (Oz, O1, O2)

对标: 这是我们对比 EPOC X / 论文方案的真正优势领域
  - EPOC X: 不开箱送 SSVEP, 用户需要自己写
  - 论文: SSVEP 在 1-2 秒准确率 85-95%, 是 BCI 最成熟的范式
"""

import numpy as np
from scipy import signal
from typing import List, Tuple, Optional
from dataclasses import dataclass, field


@dataclass
class SSVEPConfig:
    """SSVEP 配置"""
    # 闪烁频率 (Hz) — 每个频率对应一个命令
    frequencies: List[float] = field(default_factory=lambda: [6.0, 6.67, 7.5, 8.57, 10.0, 12.0])
    # 命令标签 (与频率一一对应)
    command_labels: List[str] = field(default_factory=lambda: [
        "左转", "右转", "前进", "悬停", "返航", "紧急"
    ])
    # 采样率 (Hz)
    sampling_rate: int = 250
    # FFT 分析窗口长度 (秒)
    window_duration: float = 2.0
    # 检测阈值 (SNR 超过此值才认为检测到 SSVEP)
    snr_threshold: float = 3.0
    # 枕叶通道索引 (Oz, O1, O2 位置)
    occipital_channels: List[int] = field(default_factory=lambda: [0])
    # 谐波检测: 是否检测二次谐波 (2f, 3f)
    use_harmonics: bool = True
    harmonic_count: int = 2
    # 最小置信度
    confidence_threshold: float = 0.6


class SSVEPDecoder:
    """
    SSVEP 频域解码器
    
    用法:
        decoder = SSVEPDecoder(config)
        command_id, confidence = decoder.decode(eeg_chunk)
    """

    def __init__(self, config: Optional[SSVEPConfig] = None):
        self.config = config or SSVEPConfig()
        self._window_samples = int(self.config.window_duration * self.config.sampling_rate)
        self._freqs = self.config.frequencies

        # 预先计算谐波频率
        self._all_freqs = []
        self._fundamental_map = []  # 每个频率对应哪个命令
        for i, f in enumerate(self._freqs):
            self._fundamental_map.append(i)
            if self.config.use_harmonics:
                for h in range(2, self.config.harmonic_count + 1):
                    if h * f < self.config.sampling_rate / 2:  # 低于奈奎斯特频率
                        self._all_freqs.append(h * f)
                        self._fundamental_map.append(i)

        # 预计算 FFT 频率轴
        self._fft_freqs = np.fft.rfftfreq(self._window_samples, 1.0 / self.config.sampling_rate)

        # 用于频谱平滑的窗函数
        self._window = np.hanning(self._window_samples)

        # 在线频谱缓冲 (用于计算背景噪声)
        self._background_buffer = []

    def decode(self, eeg_chunk: np.ndarray) -> Tuple[int, float]:
        """
        解码 SSVEP 频率

        Args:
            eeg_chunk: (n_channels, n_samples) EEG 数据
                       通常应 >= window_samples 长度

        Returns:
            (command_id, confidence)
            command_id = -1 表示无检测 (低于阈值)
            confidence 范围 0-1
        """
        # 取枕叶通道
        occ_data = eeg_chunk[self.config.occipital_channels]
        n_occ = occ_data.shape[0]

        # 如果数据长度不够, 截断
        n_samples = min(occ_data.shape[1], self._window_samples)
        occ_data = occ_data[:, :n_samples]

        # 加窗
        occ_data = occ_data * self._window[:n_samples]

        # FFT
        fft = np.abs(np.fft.rfft(occ_data, n=self._window_samples, axis=1))
        # 取平均 (多个枕叶通道平均)
        fft_mean = np.mean(fft, axis=0)

        # 总功率
        total_power = np.sum(fft_mean)

        # 对每个候选频率计算 SNR
        snr_values = []
        for f in self._freqs:
            # 信号功率: 基频 + 谐波
            signal_power = 0.0
            for h in range(1, (self.config.harmonic_count + 1) if self.config.use_harmonics else 2):
                target_freq = h * f
                if target_freq >= self.config.sampling_rate / 2:
                    continue
                # 找最近的 FFT bin
                idx = np.argmin(np.abs(self._fft_freqs - target_freq))
                # 取该频率周围 3 个 bin 的总功率
                half_bin = 1
                start_idx = max(0, idx - half_bin)
                end_idx = min(len(self._fft_freqs), idx + half_bin + 1)
                signal_power += np.sum(fft_mean[start_idx:end_idx])

            # 背景噪声功率: 总功率 - 信号功率
            noise_power = max(total_power - signal_power, 1e-10)
            snr = signal_power / noise_power
            snr_values.append(snr)

        snr_values = np.array(snr_values)
        best_idx = np.argmax(snr_values)
        best_snr = snr_values[best_idx]

        # 置信度 = 归一化的 SNR
        # 用 softmax-like 转换
        if best_snr > self.config.snr_threshold:
            exp_snr = np.exp(snr_values - snr_values.max())
            confidence = float(exp_snr[best_idx] / exp_snr.sum())
        else:
            confidence = 0.0

        # 低于阈值则拒绝
        if confidence < self.config.confidence_threshold:
            return -1, confidence

        return best_idx, confidence

    def get_fft_spectrum(self, eeg_chunk: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        获取 FFT 频谱 (用于调试/可视化)

        Returns:
            (freqs, magnitude)
        """
        occ_data = eeg_chunk[self.config.occipital_channels]
        n_samples = min(occ_data.shape[1], self._window_samples)
        occ_data = occ_data[:, :n_samples]
        occ_data = occ_data * self._window[:n_samples]

        fft = np.abs(np.fft.rfft(occ_data, n=self._window_samples, axis=1))
        fft_mean = np.mean(fft, axis=0)

        return self._fft_freqs, fft_mean

    def update_background(self, eeg_chunk: np.ndarray):
        """
        更新背景噪声模型 (用于自适应阈值)
        """
        self._background_buffer.append(np.mean(np.abs(eeg_chunk)))
        if len(self._background_buffer) > 100:
            self._background_buffer.pop(0)