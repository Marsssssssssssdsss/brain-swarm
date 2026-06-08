"""EEG 信号预处理模块

负责：带通滤波、陷波滤波、公共平均参考、伪迹去除、分段、信号质量评估
"""

import numpy as np
from scipy import signal
from typing import Tuple, Optional, Dict, List
from collections import deque

from signal_enhancer import ArtifactDetector, SignalQualityMonitor, SignalQualityReport


class EEGProcessor:
    """EEG 信号实时预处理"""

    def __init__(self, n_channels: int = 8, sampling_rate: int = 250):
        self.n_channels = n_channels
        self.sampling_rate = sampling_rate
        self._filters_built = False

        # 信号增强组件
        self.artifact_detector = ArtifactDetector()
        self.quality_monitor = SignalQualityMonitor(sampling_rate)
        self._last_quality: Optional[SignalQualityReport] = None
        self._rejected_count = 0
        self._total_count = 0

    @property
    def rejection_rate(self) -> float:
        """伪迹拒绝率"""
        if self._total_count == 0:
            return 0.0
        return self._rejected_count / self._total_count

    @property
    def last_quality(self) -> Optional[SignalQualityReport]:
        """最后一次信号质量评估"""
        return self._last_quality

    def build_filters(
        self,
        low_freq: float = 1.0,
        high_freq: float = 50.0,
        notch_freq: float = 50.0
    ):
        """构建滤波器系数"""
        nyquist = self.sampling_rate / 2.0

        # 带通滤波器 (FIR)
        self.bp_b, self.bp_a = signal.butter(
            4,
            [low_freq / nyquist, high_freq / nyquist],
            btype='band'
        )

        # 陷波滤波器（去除50Hz工频干扰）
        q = 30.0
        self.notch_b, self.notch_a = signal.iirnotch(
            notch_freq, q, self.sampling_rate
        )

        self._filters_built = True

    def apply_bandpass(self, data: np.ndarray) -> np.ndarray:
        """带通滤波"""
        if not self._filters_built:
            self.build_filters()
        return signal.filtfilt(self.bp_b, self.bp_a, data, axis=-1)

    def apply_notch(self, data: np.ndarray) -> np.ndarray:
        """陷波滤波"""
        if not self._filters_built:
            self.build_filters()
        return signal.filtfilt(self.notch_b, self.notch_a, data, axis=-1)

    def apply_car(self, data: np.ndarray) -> np.ndarray:
        """公共平均参考 (Common Average Reference)"""
        avg = data.mean(axis=0, keepdims=True)
        return data - avg

    def preprocess(self, raw_chunk: np.ndarray, reject_artifacts: bool = True) -> np.ndarray:
        """
        完整预处理流水线

        Args:
            raw_chunk: (n_channels, n_samples) 原始 EEG 数据
            reject_artifacts: 是否自动拒绝伪迹段

        Returns:
            (n_channels, n_samples) 预处理后的 EEG 数据
        """
        data = raw_chunk.astype(np.float64)

        # 1. 去直流分量
        data = data - data.mean(axis=-1, keepdims=True)

        # 2. 带通滤波
        data = self.apply_bandpass(data)

        # 3. 陷波滤波
        data = self.apply_notch(data)

        # 4. 公共平均参考
        data = self.apply_car(data)

        return data

    def preprocess_with_quality(
        self,
        raw_chunk: np.ndarray,
        reject_artifacts: bool = True
    ) -> Tuple[np.ndarray, SignalQualityReport, bool]:
        """
        完整预处理 + 信号质量评估 + 伪迹拒绝

        Args:
            raw_chunk: (n_channels, n_samples) 原始 EEG 数据
            reject_artifacts: 是否自动拒绝伪迹段

        Returns:
            (processed_data, quality_report, was_rejected)
        """
        self._total_count += 1

        # 预处理
        data = self.preprocess(raw_chunk, reject_artifacts=False)

        # 信号质量评估
        quality = self.quality_monitor.get_quality_report(data)
        self._last_quality = quality

        # 伪迹检测
        artifacts = self.artifact_detector.full_check(data, self.sampling_rate)

        # 判断是否拒绝
        rejected = False
        if reject_artifacts:
            if not quality.is_clean:
                rejected = True
            elif artifacts['any_artifact']:
                rejected = True

        if rejected:
            self._rejected_count += 1
            # 返回全零数据标记为无效
            return np.zeros_like(data), quality, True

        return data, quality, False

    def segment_with_quality(
        self,
        data: np.ndarray,
        window_size: float,
        window_stride: float,
        reject_artifacts: bool = True
    ) -> Tuple[np.ndarray, List[bool], List[SignalQualityReport]]:
        """
        滑动窗口分段 + 逐段质量评估

        Returns:
            (segments, valid_mask, quality_reports)
        """
        n_samples = int(window_size * self.sampling_rate)
        stride = int(window_stride * self.sampling_rate)
        total = data.shape[-1]

        if total < n_samples:
            return np.array([]), [], []

        segments = []
        valid_mask = []
        quality_reports = []

        start = 0
        while start + n_samples <= total:
            segment = data[:, start:start + n_samples]
            processed, quality, rejected = self.preprocess_with_quality(
                segment, reject_artifacts
            )
            segments.append(processed)
            quality_reports.append(quality)
            valid_mask.append(not rejected)
            start += stride

        if len(segments) == 0:
            return np.array([]), [], []

        return np.stack(segments, axis=0), valid_mask, quality_reports

    def segment(
        self,
        data: np.ndarray,
        window_size: float,
        window_stride: float
    ) -> np.ndarray:
        """
        滑动窗口分段

        Args:
            data: (n_channels, n_samples) 连续数据
            window_size: 窗口长度（秒）
            window_stride: 滑动步长（秒）

        Returns:
            (n_segments, n_channels, n_samples_per_segment) 分段数据
        """
        n_samples = int(window_size * self.sampling_rate)
        stride = int(window_stride * self.sampling_rate)
        total = data.shape[-1]

        if total < n_samples:
            return np.array([])

        segments = []
        start = 0
        while start + n_samples <= total:
            segments.append(data[:, start:start + n_samples])
            start += stride

        if len(segments) == 0:
            return np.array([])

        return np.stack(segments, axis=0)

    def extract_band_power(
        self,
        data: np.ndarray,
        bands: list
    ) -> np.ndarray:
        """
        提取各频段功率（用于快速特征分析）

        Args:
            data: (n_channels, n_samples) 或 (n_segments, n_channels, n_samples)
            bands: [(low, high), ...] 频段列表

        Returns:
            频段功率特征
        """
        if data.ndim == 2:
            data = data[np.newaxis, ...]

        n_segments, n_channels, n_samples = data.shape
        n_bands = len(bands)
        features = np.zeros((n_segments, n_channels * n_bands))

        freqs = np.fft.rfftfreq(n_samples, 1.0 / self.sampling_rate)

        for seg_idx in range(n_segments):
            for ch_idx in range(n_channels):
                fft_vals = np.abs(np.fft.rfft(data[seg_idx, ch_idx])) ** 2
                for band_idx, (low, high) in enumerate(bands):
                    mask = (freqs >= low) & (freqs <= high)
                    if mask.any():
                        features[seg_idx, ch_idx * n_bands + band_idx] = (
                            fft_vals[mask].mean()
                        )

        return features


class EEGSimulator:
    """模拟 EEG 数据生成器（用于无硬件时测试 Pipeline）"""

    def __init__(
        self,
        n_channels: int = 8,
        sampling_rate: int = 250,
        n_classes: int = 6
    ):
        self.n_channels = n_channels
        self.sampling_rate = sampling_rate
        self.n_classes = n_classes

        # 为每个类别生成不同的"模板"信号
        np.random.seed(42)
        self.templates = {}
        t = np.linspace(0, 1, sampling_rate)
        for i in range(n_classes):
            # 每个类别有不同的频率特征
            freq = 8 + i * 3  # 8, 11, 14, 17, 20, 23 Hz
            base = np.sin(2 * np.pi * freq * t)
            # 每个通道有不同的相位偏移
            self.templates[i] = np.array([
                base * (1.0 + 0.3 * np.sin(ch * 0.5))
                for ch in range(n_channels)
            ])

    def generate(
        self,
        class_id: int,
        duration: float = 1.0,
        noise_level: float = 0.5
    ) -> np.ndarray:
        """
        生成模拟 EEG 数据

        Args:
            class_id: 类别标签
            duration: 数据时长（秒）
            noise_level: 噪声强度

        Returns:
            (n_channels, n_samples) 模拟 EE 数据
        """
        n_samples = int(duration * self.sampling_rate)
        template = self.templates[class_id]

        # 叠加噪声
        noise = noise_level * np.random.randn(self.n_channels, n_samples)

        # 循环模板填充
        repeats = n_samples // template.shape[1] + 1
        signal_data = np.tile(template, (1, repeats))[:, :n_samples]

        return signal_data + noise

    def generate_sequence(self, class_ids: list, duration_per: float = 1.0):
        """生成连续序列，每个 class_id 持续 duration_per 秒"""
        chunks = []
        for cid in class_ids:
            chunks.append(self.generate(cid, duration_per))
        return np.concatenate(chunks, axis=-1)