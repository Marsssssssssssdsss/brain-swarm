"""
多模态生物信号检测 —— EOG + EMG 辅助控制
============================================

核心洞察: 同一块 ADS1299 可以同时测量 EEG + EOG + EMG
      - EEG: 0.5-100Hz, 1-50 μV
      - EOG: 0-10Hz, 50-1000 μV (眼动信号比EEG大10-100倍)
      - EMG: 20-200Hz, 10-500 μV (肌电信号比EEG大5-50倍)

因此: 前额通道 (Fp1, Fp2) 天然包含 EOG
     颞肌附近通道 (T7, T8) 或咬牙时所有通道都包含 EMG
     这些信号比 EEG 容易检测得多, 可以作为"作弊码"补充控制.
"""

import numpy as np
from scipy import signal
from typing import Tuple, Optional, Dict
from enum import Enum


class BiosignalType(Enum):
    EOG_BLINK = "blink"           # 眨眼
    EOG_LEFT = "eye_left"         # 眼球左转
    EOG_RIGHT = "eye_right"       # 眼球右转
    EOG_UP = "eye_up"             # 眼球上转
    EMG_JAW = "jaw_clench"        # 咬牙
    EMG_BROW = "eyebrow_raise"    # 抬眉
    NONE = "none"                 # 无检测


class BiosignalDetector:
    """
    多模态生物信号检测器
    
    使用前额通道检测 EOG, 全通道检测 EMG.
    这些信号比 EEG 大 10-100 倍, 检测可靠性极高.
    
    用法:
        detector = BiosignalDetector(sampling_rate=250)
        result = detector.detect(eeg_chunk)
        # 返回: {eog: "blink", emg: "none", combined: "blink"}
    """

    def __init__(
        self,
        sampling_rate: int = 250,
        eog_channels: list = None,   # 前额通道索引 (默认 Fp1, Fp2 位置)
        emg_channels: list = None,   # 全通道
        blink_threshold: float = 3.0,
        eye_move_threshold: float = 2.0,
        jaw_clench_threshold: float = 5.0,
    ):
        self.sampling_rate = sampling_rate
        self.eog_channels = eog_channels or [0, 1]
        self.emg_channels = emg_channels or []
        self.blink_threshold = blink_threshold
        self.eye_move_threshold = eye_move_threshold
        self.jaw_threshold = jaw_clench_threshold

        # 构建 EOG 差分滤波 (0.1-10Hz 带通, EOG 的主要能量范围)
        nyquist = sampling_rate / 2.0
        self.eog_sos = signal.butter(2, [0.1 / nyquist, 10 / nyquist], btype='band', output='sos')

        # 构建 EMG 高通滤波 (> 20Hz)
        self.emg_sos = signal.butter(2, 20 / nyquist, btype='high', output='sos')

        # 基线
        self._blink_baseline = None
        self._eog_baseline = None
        self._jaw_baseline = None

        # 计数器 (用于防抖)
        self._last_detected = BiosignalType.NONE
        self._hold_count = 0
        self.hold_frames = 3  # 连续检测 3 次才确认

    def detect(self, eeg_chunk: np.ndarray) -> Dict[str, str]:
        """
        检测 EOG + EMG 信号

        Args:
            eeg_chunk: (n_channels, n_samples) EEG 数据

        Returns:
            {
                "eog": BiosignalType 值 (眨眼/眼动/无),
                "emg": BiosignalType 值 (咬牙/无),
                "combined": BiosignalType 值 (综合结果)
            }
        """
        n_channels, n_samples = eeg_chunk.shape

        # ─── EOG 检测 (前额通道差分) ──────────────
        eog_type = BiosignalType.NONE
        if len(self.eog_channels) >= 1:
            eog_signal = eeg_chunk[self.eog_channels[0]]

            # 滤波: EOG 在 0.1-10Hz
            eog_filtered = signal.sosfilt(self.eog_sos, eog_signal)

            # 眼电信号的特征:
            # - 眨眼: 大幅 > 100μV 的尖峰, 持续 100-200ms
            # - 眼动: 持续直流偏移 50-500μV
            eog_max = np.max(np.abs(eog_filtered))
            eog_std = np.std(eog_filtered)
            eog_mean = np.mean(eog_filtered)

            # 基线更新 (前 10 次用于建立基线)
            if self._eog_baseline is None:
                self._eog_baseline = eog_std
            else:
                self._eog_baseline = 0.9 * self._eog_baseline + 0.1 * eog_std

            baseline = max(self._eog_baseline, 1.0)  # 防止除零

            # 眨眼检测: 尖峰 + 快速返回基线
            # EOG 尖峰幅值 / 标准差 >> 阈值
            if eog_max / baseline > self.blink_threshold:
                # 判断是否为眨眼还是眼动
                # 眨眼: 正负对称, 快速 (< 300ms)
                # 眼动: 单方向偏移, 持续 (> 500ms)

                # 计算信号的斜率变化率
                diff = np.diff(eog_filtered)
                zero_crossing = len(np.where(diff[:-1] * diff[1:] <= 0)[0])

                if zero_crossing > 5:  # 多次过零 = 振荡 = 眨眼
                    eog_type = BiosignalType.EOG_BLINK
                elif eog_mean > eog_std * 1.5:
                    eog_type = BiosignalType.EOG_RIGHT
                elif eog_mean < -eog_std * 1.5:
                    eog_type = BiosignalType.EOG_LEFT

        # ─── EMG 检测 (全通道高频能量) ────────────
        emg_type = BiosignalType.NONE
        if len(self.emg_channels) > 0:
            emg_channels_list = self.emg_channels
        else:
            emg_channels_list = list(range(min(3, n_channels)))

        # 计算高频能量 (20-100Hz)
        total_hf_power = 0
        for ch in emg_channels_list:
            emg_filtered = signal.sosfilt(self.emg_sos, eeg_chunk[ch])
            total_hf_power += np.var(emg_filtered)

        hf_rms = np.sqrt(total_hf_power / len(emg_channels_list))

        # 基线更新
        if self._jaw_baseline is None:
            self._jaw_baseline = hf_rms
        else:
            self._jaw_baseline = 0.95 * self._jaw_baseline + 0.05 * hf_rms

        jaw_baseline = max(self._jaw_baseline, 0.1)

        # 咬牙检测: 高频能量突然增大
        if hf_rms / jaw_baseline > self.jaw_threshold:
            emg_type = BiosignalType.EMG_JAW

        # ─── 综合 ──────────────────────────────
        # 优先级: EMG > EOG 眼动 > EOG 眨眼
        if emg_type != BiosignalType.NONE:
            combined = emg_type
        elif eog_type in (BiosignalType.EOG_LEFT, BiosignalType.EOG_RIGHT, BiosignalType.EOG_UP):
            combined = eog_type
        elif eog_type == BiosignalType.EOG_BLINK:
            combined = eog_type
        else:
            combined = BiosignalType.NONE

        # 防抖: 只有连续检测到才确认
        if combined == self._last_detected:
            self._hold_count += 1
        else:
            self._hold_count = 0
            self._last_detected = combined

        final_type = self._last_detected if self._hold_count >= self.hold_frames else BiosignalType.NONE

        return {
            "eog": eog_type.value,
            "emg": emg_type.value,
            "combined": final_type.value,
        }


class TrialAverager:
    """
    试次锁定平均器 (Trial-locked Averaging)
    
    真正的 ERP 信号增强方法：对齐到指令起始时刻，取多次平均。
    这是 EEG 研究 40 年的标准方法。
    
    用法:
        averager = TrialAverager(trial_duration=2.0, sampling_rate=250)
        averager.start_trial("left_hand")  # 开始一次试次
        averager.feed(data_chunk)          # 持续喂入数据
        # ... 等 2 秒后 ...
        result = averager.end_trial()      # 结束试次, 保存
        # 重复多次后:
        avg = averager.get_average("left_hand")  # 获取平均 ERP
    """

    def __init__(self, trial_duration: float = 2.0, sampling_rate: int = 250):
        self.trial_duration = trial_duration
        self.trial_samples = int(trial_duration * sampling_rate)
        self.sampling_rate = sampling_rate

        # 当前试次
        self._current_trial: Optional[str] = None
        self._current_buffer = []
        self._current_start_time: Optional[float] = None

        # 已完成的试次 {label: [trials]}
        self._trials: Dict[str, list] = {}

        # 平均结果 {label: averaged_array}
        self._averaged: Dict[str, np.ndarray] = {}

    def start_trial(self, label: str):
        """开始一次新的试次"""
        self._current_trial = label
        self._current_buffer = []
        self._current_start_time = None

    def feed(self, data_chunk: np.ndarray):
        """喂入 EEG 数据"""
        if self._current_trial is None:
            return

        self._current_buffer.append(data_chunk)

    def end_trial(self) -> np.ndarray:
        """结束当前试次, 返回对齐后的数据"""
        if self._current_trial is None:
            raise RuntimeError("没有正在进行的试次")

        # 拼接缓冲
        if len(self._current_buffer) == 0:
            raise RuntimeError("没有数据")

        full_data = np.concatenate(self._current_buffer, axis=1) if len(self._current_buffer) > 1 else self._current_buffer[0]

        # 截取到固定长度
        if full_data.shape[1] > self.trial_samples:
            full_data = full_data[:, :self.trial_samples]
        elif full_data.shape[1] < self.trial_samples:
            # 补零
            pad = self.trial_samples - full_data.shape[1]
            full_data = np.pad(full_data, ((0, 0), (0, pad)))

        # 保存
        label = self._current_trial
        if label not in self._trials:
            self._trials[label] = []
        self._trials[label].append(full_data)

        # 重置
        self._current_trial = None
        self._current_buffer = []

        return full_data

    def get_trial_count(self, label: str) -> int:
        """获取某个标签的试次数"""
        return len(self._trials.get(label, []))

    def get_average(self, label: str) -> np.ndarray:
        """
        获取某个标签的叠加平均 ERP

        Returns:
            (n_channels, n_samples) 平均后的 ERP 波形
        """
        if label not in self._trials or len(self._trials[label]) == 0:
            raise ValueError(f"标签 '{label}' 没有数据")

        trials = np.array(self._trials[label])
        return np.mean(trials, axis=0)

    def get_all_averages(self) -> Dict[str, np.ndarray]:
        """获取所有标签的平均 ERP"""
        result = {}
        for label in self._trials:
            try:
                result[label] = self.get_average(label)
            except ValueError:
                pass
        return result

    def get_snr(self, label: str) -> float:
        """
        计算某个标签的 ERP 信噪比

        SNR = 信号功率 / 噪声功率
        信号 = 平均后的 ERP
        噪声 = 单次差异
        """
        if label not in self._trials or len(self._trials[label]) < 2:
            return 0.0

        trials = np.array(self._trials[label])
        averaged = np.mean(trials, axis=0)

        # 信号功率
        signal_power = np.var(averaged)

        # 噪声功率: 单次与平均的差异的方差
        residual = trials - averaged
        noise_power = np.mean(np.var(residual, axis=1))

        if noise_power < 1e-15:
            return float('inf')

        return float(10 * np.log10(signal_power / noise_power))

    def reset_trials(self, label: Optional[str] = None):
        """清除试次数据"""
        if label:
            self._trials.pop(label, None)
        else:
            self._trials = {}