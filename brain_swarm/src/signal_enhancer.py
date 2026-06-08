"""信号增强器 —— 解决脑电信号衰减/弱信号问题

核心能力：
1. 信号质量实时评估（SNR、幅度、工频干扰比）
2. 伪迹自动检测与拒绝（肌肉伪迹、眼动、电极脱落）
3. 自适应阈值：根据用户基线动态调整触发灵敏度
4. 基线校准：学习用户静息态特征
5. 漂移补偿：跨会话和多时间尺度漂移修正
"""

import numpy as np
from scipy import signal
from typing import Tuple, Optional, Dict, List
from dataclasses import dataclass, field
from collections import deque
import time
import json
import os


# ─── 数据结构 ────────────────────────────────────────────

@dataclass
class SignalQualityReport:
    """信号质量评估报告"""
    snr_db: float = 0.0              # 信噪比 (dB)
    is_clean: bool = True            # 是否通过质量检查
    amplitude_ok: bool = True        # 幅度是否正常
    line_noise_ok: bool = True       # 工频干扰是否可接受
    saturation_ratio: float = 0.0    # 饱和比例 (0-1)
    contact_quality: int = 0         # 0=好, 1=一般, 2=差, 3=断开
    warnings: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    raw_score: float = 1.0           # 0=极差, 1=完美


@dataclass
class UserBaseline:
    """用户静息态基线"""
    attention_mean: float = 50.0
    attention_std: float = 15.0
    meditation_mean: float = 50.0
    meditation_std: float = 15.0
    alpha_power_mean: float = 0.0
    beta_power_mean: float = 0.0
    theta_power_mean: float = 0.0
    delta_power_mean: float = 0.0
    raw_eeg_rms: float = 0.0         # 原始EEG均方根
    calibrated: bool = False
    calibration_samples: int = 0
    calibration_time: float = 0.0


# ─── 伪迹检测器 ──────────────────────────────────────────

class ArtifactDetector:
    """伪迹自动检测：肌肉伪迹、眼动、电极脱落、饱和"""

    def __init__(
        self,
        amplitude_threshold: float = 150.0,     # μV，超过视为肌肉伪迹
        saturation_threshold: float = 0.95,      # 接近ADC上限比例
        kurtosis_threshold: float = 5.0,         # 峰度阈值
        dead_channel_std: float = 0.5,           # 死通道标准差阈值(μV)
    ):
        self.amplitude_threshold = amplitude_threshold
        self.saturation_threshold = saturation_threshold
        self.kurtosis_threshold = kurtosis_threshold
        self.dead_channel_std = dead_channel_std

    def detect_muscle_artifacts(self, segment: np.ndarray) -> Tuple[bool, float]:
        """
        检测肌肉伪迹：高频高幅信号

        Args:
            segment: (n_channels, n_samples) 或 (n_samples,) 的EEG片段

        Returns:
            (has_artifact, ratio_of_bad_samples)
        """
        if segment.ndim == 1:
            segment = segment[np.newaxis, :]

        total = segment.size
        bad = np.sum(np.abs(segment) > self.amplitude_threshold)
        ratio = bad / total
        return ratio > 0.05, ratio  # 超过5%采样点异常

    def detect_saturation(self, segment: np.ndarray) -> Tuple[bool, float]:
        """
        检测信号饱和（ADC溢出）
        表现为信号长时间卡在最大/最小值
        """
        if segment.ndim == 1:
            segment = segment[np.newaxis, :]

        n_channels, n_samples = segment.shape
        total_saturated = 0

        for ch in range(n_channels):
            ch_data = segment[ch]
            # 检测连续相同值（饱和特征）
            diffs = np.diff(ch_data)
            saturated = np.sum(np.abs(diffs) < 1e-6)
            total_saturated += saturated

        ratio = total_saturated / (n_channels * (n_samples - 1))
        return ratio > 0.1, ratio

    def detect_channel_dropout(self, segment: np.ndarray) -> Tuple[bool, np.ndarray]:
        """
        检测电极脱落/死通道

        Returns:
            (any_dropped, channel_mask) - mask中True表示该通道正常
        """
        if segment.ndim == 1:
            segment = segment[np.newaxis, :]

        n_channels = segment.shape[0]
        mask = np.ones(n_channels, dtype=bool)

        for ch in range(n_channels):
            ch_std = np.std(segment[ch])
            if ch_std < self.dead_channel_std:
                mask[ch] = False

        return not np.all(mask), mask

    def detect_blink_artifact(self, segment: np.ndarray, sampling_rate: int = 250) -> Tuple[bool, float]:
        """
        检测眼动/眨眼伪迹：前额通道出现大幅低频尖峰

        在TGAM单通道场景下特别重要——眨眼会严重干扰专注度读数
        """
        if segment.ndim > 1:
            segment = segment[0]  # TGAM只用第一通道

        # 计算峰度：眨眼会产生尖锐的尖峰
        mean = np.mean(segment)
        std = np.std(segment)
        if std < 1e-9:
            return False, 0.0

        n = len(segment)
        kurtosis = np.sum(((segment - mean) / std) ** 4) / n - 3  # 超额峰度

        # 高频能量比例：眨眼时高频能量突然增加
        freqs = np.fft.rfftfreq(len(segment), 1.0 / sampling_rate)
        fft = np.abs(np.fft.rfft(segment))
        high_band = fft[freqs > 20].sum()
        total_power = fft.sum()
        high_ratio = high_band / total_power if total_power > 0 else 0

        is_blink = kurtosis > self.kurtosis_threshold and high_ratio > 0.3
        return is_blink, kurtosis

    def full_check(self, segment: np.ndarray, sampling_rate: int = 250) -> Dict:
        """执行所有伪迹检测"""
        results = {}

        # 肌肉伪迹
        has_muscle, muscle_ratio = self.detect_muscle_artifacts(segment)
        results['muscle_artifact'] = {'detected': has_muscle, 'ratio': muscle_ratio}

        # 饱和检测
        has_sat, sat_ratio = self.detect_saturation(segment)
        results['saturation'] = {'detected': has_sat, 'ratio': sat_ratio}

        # 电极脱落
        has_dropout, ch_mask = self.detect_channel_dropout(segment)
        results['channel_dropout'] = {'detected': has_dropout, 'mask': ch_mask.tolist()}

        # 眨眼伪迹（TGAM单通道关键检测）
        has_blink, kurt = self.detect_blink_artifact(segment, sampling_rate)
        results['blink_artifact'] = {'detected': has_blink, 'kurtosis': kurt}

        results['any_artifact'] = any([
            results['muscle_artifact']['detected'],
            results['saturation']['detected'],
            results['channel_dropout']['detected'],
            results['blink_artifact']['detected'],
        ])

        return results


# ─── 信号质量评估器 ──────────────────────────────────────

class SignalQualityMonitor:
    """实时信号质量评估：SNR、工频干扰、接触质量"""

    def __init__(self, sampling_rate: int = 250, line_freq: float = 50.0):
        self.sampling_rate = sampling_rate
        self.line_freq = line_freq
        # 历史记录用于趋势分析
        self._snr_history = deque(maxlen=30)
        self._quality_history = deque(maxlen=30)
        self._degradation_counter = 0
        self._recovery_counter = 0

    def estimate_snr(self, data: np.ndarray, signal_band: Tuple[float, float] = (4, 40)) -> float:
        """
        估算信噪比 (dB)

        信号 = 目标频段功率，噪声 = 总功率 - 信号功率
        """
        if data.ndim > 1:
            data = data[0]  # 单通道评估

        n = len(data)
        freqs = np.fft.rfftfreq(n, 1.0 / self.sampling_rate)
        power = np.abs(np.fft.rfft(data)) ** 2

        # 信号频段
        sig_mask = (freqs >= signal_band[0]) & (freqs <= signal_band[1])
        signal_power = power[sig_mask].sum()

        # 总功率
        total_power = power.sum()
        noise_power = total_power - signal_power

        if noise_power < 1e-10:
            return 60.0  # 完美信号

        snr = 10 * np.log10(signal_power / noise_power)
        self._snr_history.append(snr)
        return max(-20, min(60, snr))  # 钳制在合理范围

    def estimate_line_noise_ratio(self, data: np.ndarray) -> float:
        """
        估计工频干扰比例 (0=无干扰, 1=全是工频)
        """
        if data.ndim > 1:
            data = data[0]

        n = len(data)
        freqs = np.fft.rfftfreq(n, 1.0 / self.sampling_rate)
        power = np.abs(np.fft.rfft(data)) ** 2

        # 工频 ± 2Hz
        line_mask = (freqs >= self.line_freq - 2) & (freqs <= self.line_freq + 2)
        line_power = power[line_mask].sum()
        total_power = power.sum()

        if total_power < 1e-10:
            return 0.0
        return line_power / total_power

    def assess_amplitude(self, data: np.ndarray) -> Tuple[bool, float, float]:
        """
        评估信号幅度是否正常
        
        正常EEG: 10-100 μV RMS
        TGAM输出范围: -2048 ~ 2047 (约 ±600 μV)
        
        Returns:
            (is_normal, rms_value, peak_to_peak)
        """
        if data.ndim > 1:
            data = data[0]

        rms = np.sqrt(np.mean(data ** 2))
        p2p = np.max(data) - np.min(data)

        # 正常范围：RMS > 5 (太低=死信号), < 500 (太高=饱和/伪迹)
        is_normal = 5 < rms < 500 and p2p > 10
        return is_normal, rms, p2p

    def get_quality_report(self, data: np.ndarray, tgam_signal_quality: int = 0) -> SignalQualityReport:
        """
        生成综合信号质量报告

        Args:
            data: 原始EEG片段数据
            tgam_signal_quality: TGAM硬件信号质量值 (0=好, 200=差)

        Returns:
            SignalQualityReport
        """
        report = SignalQualityReport()
        warnings = []
        suggestions = []

        # 1. SNR评估
        snr = self.estimate_snr(data)
        report.snr_db = snr

        if snr < 0:
            warnings.append(f"信噪比极低 ({snr:.1f} dB)")
            suggestions.append("检查电极是否贴合皮肤")
            suggestions.append("涂抹导电膏或使用生理盐水湿润电极")
        elif snr < 10:
            warnings.append(f"信噪比偏低 ({snr:.1f} dB)")
            suggestions.append("保持头部稳定，减少面部动作")

        # 2. 幅度检查
        amp_ok, rms, p2p = self.assess_amplitude(data)
        report.amplitude_ok = amp_ok
        if not amp_ok:
            if rms < 5:
                warnings.append("信号幅度过低")
                suggestions.append("检查电极与皮肤的接触，重新调整电极位置")
                suggestions.append("用酒精棉片清洁皮肤表面油脂")
                report.contact_quality = 3  # 断开
            else:
                warnings.append("信号幅度异常（可能饱和）")
                suggestions.append("降低放大器增益或检查ADC设置")
                report.saturation_ratio = 0.5

        # 3. 工频干扰
        line_ratio = self.estimate_line_noise_ratio(data)
        report.line_noise_ok = line_ratio < 0.3
        if line_ratio > 0.3:
            warnings.append(f"工频干扰过高 ({line_ratio:.1%})")
            suggestions.append("远离电源线和电器设备")
            suggestions.append("确保设备良好接地")
            suggestions.append("启用50Hz陷波滤波器")
        if line_ratio > 0.6:
            report.contact_quality = max(report.contact_quality, 2)

        # 4. TGAM硬件质量
        if tgam_signal_quality > 0:
            if tgam_signal_quality < 50:
                report.contact_quality = 1  # 一般
                suggestions.append("信号质量一般，尝试调整电极位置")
            elif tgam_signal_quality < 100:
                report.contact_quality = 2  # 差
                suggestions.append("信号质量差，请重新佩戴设备")
            else:
                report.contact_quality = 3  # 断开
                suggestions.append("检测不到有效信号，请检查设备连接")

        report.warnings = warnings
        report.suggestions = suggestions

        # 5. 综合评分
        quality_score = 1.0
        if snr < 0:
            quality_score -= 0.6
        elif snr < 10:
            quality_score -= 0.3
        if not amp_ok:
            quality_score -= 0.4
        if line_ratio > 0.3:
            quality_score -= 0.2
        if tgam_signal_quality > 100:
            quality_score -= 0.5
        elif tgam_signal_quality > 50:
            quality_score -= 0.25

        report.raw_score = max(0.0, quality_score)
        report.is_clean = report.raw_score >= 0.5

        # 趋势检测
        self._quality_history.append(report.raw_score)
        if len(self._quality_history) >= 5:
            recent = list(self._quality_history)[-5:]
            if np.mean(recent) < 0.4:
                self._degradation_counter += 1
                if self._degradation_counter > 3:
                    suggestions.append("⚠ 信号持续劣化，请检查设备状态")
            else:
                self._degradation_counter = max(0, self._degradation_counter - 1)

        report.suggestions = list(dict.fromkeys(suggestions))  # 去重保序
        return report


# ─── 基线校准器 ──────────────────────────────────────────

class BaselineCalibrator:
    """用户基线校准：学习静息态特征，用于自适应阈值"""

    def __init__(self, calibration_duration: float = 30.0):
        """
        Args:
            calibration_duration: 校准时长（秒）
        """
        self.calibration_duration = calibration_duration
        self._attention_samples: List[float] = []
        self._meditation_samples: List[float] = []
        self._raw_eeg_rms_samples: List[float] = []
        self._band_power_samples: Dict[str, List[float]] = {
            'delta': [], 'theta': [], 'alpha': [], 'beta': [], 'gamma': []
        }
        self._start_time: float = 0.0
        self._is_calibrating = False
        self._progress = 0.0  # 0.0 to 1.0
        self.baseline = UserBaseline()

    def start(self):
        """开始校准"""
        self._attention_samples.clear()
        self._meditation_samples.clear()
        self._raw_eeg_rms_samples.clear()
        for key in self._band_power_samples:
            self._band_power_samples[key].clear()
        self._start_time = time.time()
        self._is_calibrating = True
        self._progress = 0.0

    def add_sample(
        self,
        attention: float,
        meditation: float,
        raw_eeg_rms: float = 0.0,
        band_powers: Optional[Dict[str, float]] = None
    ):
        """添加一个采样点"""
        if not self._is_calibrating:
            return

        self._attention_samples.append(attention)
        self._meditation_samples.append(meditation)
        self._raw_eeg_rms_samples.append(raw_eeg_rms)

        if band_powers:
            for key, value in band_powers.items():
                if key in self._band_power_samples:
                    self._band_power_samples[key].append(value)

        elapsed = time.time() - self._start_time
        self._progress = min(1.0, elapsed / self.calibration_duration)

        # 是否完成
        if elapsed >= self.calibration_duration:
            self.finish()

    @property
    def progress(self) -> float:
        return self._progress

    @property
    def is_calibrating(self) -> bool:
        return self._is_calibrating

    def finish(self) -> UserBaseline:
        """完成校准，计算基线统计量"""
        if not self._attention_samples:
            self._is_calibrating = False
            return self.baseline

        baseline = UserBaseline()
        baseline.calibrated = True
        baseline.calibration_samples = len(self._attention_samples)
        baseline.calibration_time = time.time()

        # 注意力基线
        baseline.attention_mean = np.mean(self._attention_samples)
        baseline.attention_std = np.std(self._attention_samples)

        # 放松度基线
        baseline.meditation_mean = np.mean(self._meditation_samples)
        baseline.meditation_std = np.std(self._meditation_samples)

        # EEG RMS
        if self._raw_eeg_rms_samples:
            baseline.raw_eeg_rms = np.mean(self._raw_eeg_rms_samples)

        # 频段功率
        for key in self._band_power_samples:
            if self._band_power_samples[key]:
                setattr(baseline, f"{key}_power_mean",
                       np.mean(self._band_power_samples[key]))

        self._is_calibrating = False
        self._progress = 1.0
        self.baseline = baseline
        return baseline

    def save(self, filepath: str):
        """保存基线到文件"""
        data = {
            'attention_mean': self.baseline.attention_mean,
            'attention_std': self.baseline.attention_std,
            'meditation_mean': self.baseline.meditation_mean,
            'meditation_std': self.baseline.meditation_std,
            'alpha_power_mean': self.baseline.alpha_power_mean,
            'beta_power_mean': self.baseline.beta_power_mean,
            'theta_power_mean': self.baseline.theta_power_mean,
            'delta_power_mean': self.baseline.delta_power_mean,
            'raw_eeg_rms': self.baseline.raw_eeg_rms,
            'calibrated': self.baseline.calibrated,
            'calibration_samples': self.baseline.calibration_samples,
            'calibration_time': self.baseline.calibration_time,
        }
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls, filepath: str) -> 'BaselineCalibrator':
        """从文件加载基线"""
        calibrator = cls()
        if not os.path.exists(filepath):
            return calibrator

        with open(filepath, 'r') as f:
            data = json.load(f)

        baseline = UserBaseline()
        baseline.calibrated = data.get('calibrated', False)
        baseline.calibration_samples = data.get('calibration_samples', 0)
        baseline.calibration_time = data.get('calibration_time', 0)
        baseline.attention_mean = data.get('attention_mean', 50.0)
        baseline.attention_std = data.get('attention_std', 15.0)
        baseline.meditation_mean = data.get('meditation_mean', 50.0)
        baseline.meditation_std = data.get('meditation_std', 15.0)
        baseline.alpha_power_mean = data.get('alpha_power_mean', 0.0)
        baseline.beta_power_mean = data.get('beta_power_mean', 0.0)
        baseline.theta_power_mean = data.get('theta_power_mean', 0.0)
        baseline.delta_power_mean = data.get('delta_power_mean', 0.0)
        baseline.raw_eeg_rms = data.get('raw_eeg_rms', 0.0)
        calibrator.baseline = baseline
        return calibrator


# ─── 自适应阈值 ───────────────────────────────────────────

class AdaptiveThreshold:
    """
    自适应阈值控制器

    原理：每个人的静息态专注度不同（有人天生40，有人60）。
    根据用户基线自动计算合适的触发阈值，而非写死一个值。
    """

    def __init__(
        self,
        baseline_mean: float = 50.0,
        baseline_std: float = 15.0,
        z_score_threshold: float = 1.5,      # Z分数阈值（专注度超过均值多少个标准差）
        min_threshold: float = 40.0,          # 绝对最低阈值（防止基线太低）
        max_threshold: float = 90.0,          # 绝对最高阈值（防止基线太高）
        signal_quality_weight: float = 0.3,   # 信号质量对阈值的影响权重
    ):
        self.baseline_mean = baseline_mean
        self.baseline_std = baseline_std
        self.z_score_threshold = z_score_threshold
        self.min_threshold = min_threshold
        self.max_threshold = max_threshold
        self.signal_quality_weight = signal_quality_weight

    def compute_threshold(self, signal_quality_score: float = 1.0) -> float:
        """
        计算当前自适应阈值

        阈值 = baseline_mean + z_score * baseline_std
        信号差时提高阈值（更难触发，防止误触发）

        Args:
            signal_quality_score: 0~1, 当前信号质量评分

        Returns:
            当前有效的专注度触发阈值
        """
        # 基础阈值：基线均值 + N个标准差
        base_threshold = self.baseline_mean + self.z_score_threshold * self.baseline_std

        # 信号质量修正：质量越差，阈值越高（要求更强的专注信号才触发）
        quality_penalty = (1.0 - signal_quality_score) * 30  # 最多加30
        adjusted = base_threshold + self.signal_quality_weight * quality_penalty

        # 钳制
        adjusted = max(self.min_threshold, min(self.max_threshold, adjusted))
        return adjusted

    def update_baseline(self, mean: float, std: float):
        """平滑更新基线（适应长期变化）"""
        alpha = 0.1  # 学习率，缓慢适应
        self.baseline_mean = alpha * mean + (1 - alpha) * self.baseline_mean
        self.baseline_std = alpha * std + (1 - alpha) * self.baseline_std

    def classify_attention(self, attention: float, threshold: float) -> str:
        """
        将专注度分为三个等级

        Returns:
            'low', 'medium', 'high'
        """
        if attention >= threshold:
            return 'high'
        elif attention >= threshold * 0.75:
            return 'medium'
        return 'low'


# ─── 漂移补偿器 ──────────────────────────────────────────

class DriftCompensator:
    """
    漂移补偿器

    处理两个层面的漂移：
    1. 短期漂移：会话内电极阻抗变化的缓慢趋势
    2. 长期漂移：跨会话的用户状态变化（疲劳、适应、电极位置偏移）
    """

    def __init__(self, window_size: int = 100, sampling_rate: int = 250):
        self.window_size = window_size
        self.sampling_rate = sampling_rate
        self._short_term_buffer = deque(maxlen=window_size)
        self._long_term_trend = 0.0
        self._trend_alpha = 0.01  # 长期趋势学习率

    def detrend(self, value: float) -> float:
        """
        去除短期漂移：当前值 - 滑动窗口均值

        Args:
            value: 当前指标值（如专注度）

        Returns:
            去漂移后的值
        """
        self._short_term_buffer.append(value)

        if len(self._short_term_buffer) < 10:
            return value

        baseline = np.mean(list(self._short_term_buffer))
        detrended = value - baseline

        # 更新长期趋势
        self._long_term_trend = (
            (1 - self._trend_alpha) * self._long_term_trend
            + self._trend_alpha * baseline
        )

        return detrended

    def get_long_term_offset(self) -> float:
        """获取当前长期漂移偏移量（用于跨会话校准）"""
        return self._long_term_trend

    def reset(self):
        """重置短期缓冲区"""
        self._short_term_buffer.clear()


# ─── 综合信号增强器 ──────────────────────────────────────

class SignalEnhancer:
    """
    综合信号增强器——一站式解决所有信号衰减问题

    使用方式：
        enhancer = SignalEnhancer(sampling_rate=250)
        
        # 每个数据包到达时：
        quality_report = enhancer.evaluate(raw_data, tgam_quality)
        if quality_report.is_clean:
            threshold = enhancer.get_threshold()
            artifacts = enhancer.detect_artifacts(raw_data)
            if not artifacts['any_artifact']:
                # 信号OK，继续处理
                pass
    """

    def __init__(self, sampling_rate: int = 250, line_freq: float = 50.0):
        self.sampling_rate = sampling_rate
        self.quality_monitor = SignalQualityMonitor(sampling_rate, line_freq)
        self.artifact_detector = ArtifactDetector()
        self.calibrator = BaselineCalibrator()
        self.adaptive_threshold = AdaptiveThreshold()
        self.drift_compensator = DriftCompensator(sampling_rate=sampling_rate)

    def calibrate(self) -> BaselineCalibrator:
        """开始基线校准"""
        self.calibrator.start()
        return self.calibrator

    def load_baseline(self, filepath: str):
        """加载已保存的基线"""
        loaded = BaselineCalibrator.load(filepath)
        if loaded.baseline.calibrated:
            b = loaded.baseline
            self.calibrator.baseline = b
            self.adaptive_threshold.update_baseline(b.attention_mean, b.attention_std)

    def add_calibration_sample(self, attention: float, meditation: float,
                                raw_eeg_rms: float = 0.0):
        """添加校准采样点"""
        self.calibrator.add_sample(attention, meditation, raw_eeg_rms)

    def evaluate(self, raw_data: np.ndarray, tgam_quality: int = 0) -> SignalQualityReport:
        """综合信号质量评估"""
        return self.quality_monitor.get_quality_report(raw_data, tgam_quality)

    def detect_artifacts(self, segment: np.ndarray) -> Dict:
        """检测伪迹"""
        return self.artifact_detector.full_check(segment, self.sampling_rate)

    def get_threshold(self) -> float:
        """
        获取当前自适应阈值

        结合信号质量动态调整
        """
        # 用最近的质量评分
        if self.quality_monitor._quality_history:
            recent_quality = np.mean(list(self.quality_monitor._quality_history)[-5:])
        else:
            recent_quality = 1.0
        return self.adaptive_threshold.compute_threshold(recent_quality)

    def apply_drift_compensation(self, attention: float) -> float:
        """应用漂移补偿"""
        return self.drift_compensator.detrend(attention)

    def should_ignore_data(self, quality: SignalQualityReport, artifacts: Dict) -> Tuple[bool, str]:
        """
        判断是否应该忽略当前数据

        Returns:
            (should_ignore, reason)
        """
        if not quality.is_clean:
            return True, f"信号质量差 (评分: {quality.raw_score:.2f})"

        if quality.contact_quality >= 2:
            return True, f"电极接触不良 (等级: {quality.contact_quality})"

        if artifacts.get('any_artifact', False):
            parts = []
            if artifacts.get('muscle_artifact', {}).get('detected'):
                parts.append('肌肉伪迹')
            if artifacts.get('blink_artifact', {}).get('detected'):
                parts.append('眨眼伪迹')
            if artifacts.get('saturation', {}).get('detected'):
                parts.append('信号饱和')
            if artifacts.get('channel_dropout', {}).get('detected'):
                parts.append('电极脱落')
            return True, f"检测到伪迹: {', '.join(parts)}"

        return False, "OK"