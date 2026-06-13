import numpy as np
from typing import Tuple, Optional, Dict
from scipy import signal


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

        self._current_trial: Optional[str] = None
        self._current_buffer = []
        self._current_start_time: Optional[float] = None

        self._trials: Dict[str, list] = {}

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

        if len(self._current_buffer) == 0:
            raise RuntimeError("没有数据")

        full_data = np.concatenate(self._current_buffer, axis=1) if len(self._current_buffer) > 1 else self._current_buffer[0]

        if full_data.shape[1] > self.trial_samples:
            full_data = full_data[:, :self.trial_samples]
        elif full_data.shape[1] < self.trial_samples:
            pad = self.trial_samples - full_data.shape[1]
            full_data = np.pad(full_data, ((0, 0), (0, pad)))

        label = self._current_trial
        if label not in self._trials:
            self._trials[label] = []
        self._trials[label].append(full_data)

        self._current_trial = None
        self._current_buffer = []

        return full_data

    def get_trial_count(self, label: str) -> int:
        """获取某个标签的试次数"""
        return len(self._trials.get(label, []))

    def get_average(self, label: str) -> np.ndarray:
        """获取某个标签的叠加平均 ERP"""
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
        """计算某个标签的 ERP 信噪比"""
        if label not in self._trials or len(self._trials[label]) < 2:
            return 0.0

        trials = np.array(self._trials[label])
        averaged = np.mean(trials, axis=0)

        signal_power = np.var(averaged)
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
