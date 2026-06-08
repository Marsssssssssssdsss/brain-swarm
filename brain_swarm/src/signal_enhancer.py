"""信号增强器 —— 解决脑电信号衰减/弱信号问题

核心能力：
1. 信号质量实时评估（SNR、幅度、工频干扰比）
2. 伪迹自动检测与拒绝（肌肉伪迹、眼动、电极脱落）
3. 自适应阈值：根据用户基线动态调整触发灵敏度
4. 基线校准：学习用户静息态特征
5. 漂移补偿：跨会话和多时间尺度漂移修正
6. FBCSP 特征提取 + LDA 分类器 (替代纯阈值方法)
7. 小波变换去噪 (论文级伪迹去除)
"""

import numpy as np
from scipy import signal, linalg
from typing import Tuple, Optional, Dict, List
from dataclasses import dataclass, field
from collections import deque
import time
import json
import os
import warnings
from scipy.signal import butter, sosfilt, filtfilt


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

    def wavelet_denoise(self, data: np.ndarray, wavelet_name: str = "db4",
                        level: int = 5, threshold_mode: str = "soft",
                        threshold_scale: float = 1.0) -> np.ndarray:
        """
        小波变换去噪 (对标论文级伪迹去除方法)

        原理: 将EEG信号分解到小波域, 对小尺度(高频)系数做阈值处理,
        保留大尺度(低频)系数, 再重构信号.
        相比FIR/IIR滤波, 小波去噪能在去除噪声的同时更好地保留信号瞬态特征.

        Args:
            data: (n_channels, n_samples) 或 (n_samples,) EEG数据
            wavelet_name: 小波基名称 (db4/sym8/coif5 等)
            level: 分解层数
            threshold_mode: 'soft' 或 'hard'
            threshold_scale: 阈值缩放系数 (越大去噪越强)

        Returns:
            去噪后的数据, 形状与输入相同
        """
        try:
            import pywt
        except ImportError:
            # 没有pywt库则返回原数据
            return data

        if data.ndim == 1:
            data = data[np.newaxis, :]

        n_channels, n_samples = data.shape
        denoised = np.zeros_like(data)

        for ch in range(n_channels):
            # 小波分解
            coeffs = pywt.wavedec(data[ch], wavelet_name, level=level)

            # 估计噪声标准差 (使用第一层细节系数的中位数)
            sigma = np.median(np.abs(coeffs[-1])) / 0.6745
            threshold = sigma * np.sqrt(2 * np.log(n_samples)) * threshold_scale

            # 对除最后一层近似系数外的所有细节系数做阈值处理
            coeffs_thresholded = [coeffs[0]]  # 保留近似系数
            for i in range(1, len(coeffs)):
                if threshold_mode == "soft":
                    coeffs_thresholded.append(
                        pywt.threshold(coeffs[i], threshold, mode='soft')
                    )
                else:
                    coeffs_thresholded.append(
                        pywt.threshold(coeffs[i], threshold, mode='hard')
                    )

            # 重构
            denoised[ch] = pywt.waverec(coeffs_thresholded, wavelet_name)[:n_samples]

        if denoised.shape[0] == 1:
            return denoised[0]
        return denoised

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


# ─── FBCSP 特征提取 + LDA 分类器 ────────────────────────
# FBCSP (Filter Bank Common Spatial Patterns):
# 1. 将EEG信号滤波到多个频段 (滤波器组)
# 2. 每个频段执行CSP (Common Spatial Patterns) 寻找最大化两类方差差异的空间滤波器
# 3. 提取log-variance特征
# 4. LDA (Linear Discriminant Analysis) 分类
# 这是BCI Competition IV 2a 上最经典的基线方法, 4类平均准确率 ~67-73%

class CommonSpatialPattern:
    """
    Common Spatial Patterns (CSP) 空间滤波器

    CSP寻找一组空间滤波器, 使得一类信号的方差最大化, 另一类最小化.
    适用于二分类运动想象 (如左手vs右手).
    """

    def __init__(self, n_filters: int = 4):
        """
        Args:
            n_filters: 保留的空间滤波器对数 (每类保留n_filters个, 共2*n_filters个)
        """
        self.n_filters = n_filters
        self.filters_: Optional[np.ndarray] = None
        self.patterns_: Optional[np.ndarray] = None
        self.fitted = False

    def fit(self, X: np.ndarray, y: np.ndarray):
        """
        训练CSP空间滤波器

        Args:
            X: (n_trials, n_channels, n_samples) 训练数据
            y: (n_trials,) 标签 (0 或 1)
        """
        classes = np.unique(y)
        if len(classes) != 2:
            raise ValueError(f"CSP需要二分类, 收到 {len(classes)} 类")

        # 计算每类的协方差矩阵
        covs = []
        for cls in classes:
            trials = X[y == cls]
            n_trials = trials.shape[0]
            # 每个trial的协方差矩阵
            trial_covs = []
            for i in range(n_trials):
                trial = trials[i]
                trial = trial - trial.mean(axis=-1, keepdims=True)
                cov = trial @ trial.T / (trial.shape[1] - 1)
                trial_covs.append(cov)
            covs.append(np.mean(trial_covs, axis=0))

        R1, R2 = covs[0], covs[1]
        R = R1 + R2

        # 广义特征值分解
        try:
            eigenvalues, eigenvectors = linalg.eigh(R1, R)
        except linalg.LinAlgError:
            # 添加正则项保证数值稳定性
            R += np.eye(R.shape[0]) * 1e-10
            eigenvalues, eigenvectors = linalg.eigh(R1, R)

        # 按绝对值降序排列
        idx = np.argsort(np.abs(eigenvalues))[::-1]
        eigenvalues = eigenvalues[idx]
        eigenvectors = eigenvectors[:, idx]

        # 选择头尾各n_filters个滤波器
        selected = np.concatenate([
            idx[:self.n_filters],
            idx[-self.n_filters:]
        ])
        self.filters_ = eigenvectors[:, selected]
        self.patterns_ = linalg.inv(self.filters_.T)
        self.fitted = True
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        """
        提取CSP特征 (log-variance)

        Args:
            X: (n_trials, n_channels, n_samples) 或 (n_channels, n_samples)

        Returns:
            features: (n_trials, 2*n_filters) log-variance特征
        """
        if not self.fitted:
            raise RuntimeError("CSP未训练, 请先调用fit()")

        if X.ndim == 2:
            X = X[np.newaxis, ...]

        n_trials = X.shape[0]
        n_filters = self.filters_.shape[1]

        features = np.zeros((n_trials, n_filters))
        for i in range(n_trials):
            # 空间滤波
            projected = self.filters_.T @ X[i]
            # log-variance特征
            var = np.var(projected, axis=1)
            # 归一化并取log
            var_sum = var.sum()
            if var_sum > 0:
                var = var / var_sum
            # 避免log(0)
            var = np.clip(var, 1e-10, None)
            features[i] = np.log(var)

        return features

    def fit_transform(self, X: np.ndarray, y: np.ndarray) -> np.ndarray:
        """训练并提取特征"""
        self.fit(X, y)
        return self.transform(X)


class FilterBankCSP:
    """
    Filter Bank Common Spatial Patterns (FBCSP)

    将EEG信号分解到多个频段, 每个频段独立运行CSP, 拼接所有特征.
    BCI Competition IV 2a 标准方法.

    频段示例: [(4,8), (8,12), (12,16), (16,20), (20,24), (24,28), (28,32), (32,36)] Hz
    """

    def __init__(
        self,
        bands: List[Tuple[float, float]] = None,
        n_filters: int = 4,
        sampling_rate: int = 250,
    ):
        """
        Args:
            bands: 频段列表 [(low, high), ...]
            n_filters: 每个频段保留的CSP滤波器对数
            sampling_rate: 采样率 (Hz)
        """
        if bands is None:
            bands = [
                (4, 8), (8, 12), (12, 16), (16, 20),
                (20, 24), (24, 28), (28, 32), (32, 36)
            ]
        self.bands = bands
        self.n_filters = n_filters
        self.sampling_rate = sampling_rate
        self.csp_models: List[CommonSpatialPattern] = []
        self._filters_built = False
        self.fitted = False

    def _build_filters(self):
        """为每个频段构建带通滤波器"""
        self._band_filters = []
        nyquist = self.sampling_rate / 2.0
        for low, high in self.bands:
            if high >= nyquist:
                high = nyquist - 0.5
            sos = butter(4, [low / nyquist, high / nyquist], btype='band', output='sos')
            self._band_filters.append(sos)
        self._filters_built = True

    def fit(self, X: np.ndarray, y: np.ndarray):
        """
        训练FBCSP

        Args:
            X: (n_trials, n_channels, n_samples) 训练数据
            y: (n_trials,) 标签
        """
        if not self._filters_built:
            self._build_filters()

        self.csp_models = []
        for band_idx, (low, high) in enumerate(self.bands):
            # 频段滤波
            X_filtered = np.zeros_like(X)
            for i in range(X.shape[0]):
                for ch in range(X.shape[1]):
                    X_filtered[i, ch] = sosfilt(
                        self._band_filters[band_idx], X[i, ch]
                    )

            # 该频段运行CSP
            csp = CommonSpatialPattern(n_filters=self.n_filters)
            csp.fit(X_filtered, y)
            self.csp_models.append(csp)

        self.fitted = True
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        """
        提取FBCSP特征

        Args:
            X: (n_trials, n_channels, n_samples)

        Returns:
            features: (n_trials, n_bands * 2 * n_filters) 拼接特征
        """
        if not self.fitted:
            raise RuntimeError("FBCSP未训练, 请先调用fit()")
        if not self._filters_built:
            self._build_filters()

        all_features = []
        for band_idx in range(len(self.bands)):
            # 频段滤波
            X_filtered = np.zeros_like(X)
            for i in range(X.shape[0]):
                for ch in range(X.shape[1]):
                    X_filtered[i, ch] = sosfilt(
                        self._band_filters[band_idx], X[i, ch]
                    )

            # 提取CSP特征
            features = self.csp_models[band_idx].transform(X_filtered)
            all_features.append(features)

        return np.concatenate(all_features, axis=1)

    def fit_transform(self, X: np.ndarray, y: np.ndarray) -> np.ndarray:
        """训练并提取特征"""
        self.fit(X, y)
        return self.transform(X)


class LDAClassifier:
    """
    Linear Discriminant Analysis (LDA) 分类器

    用于FBCSP特征的分类. 相比普通LDA实现:
    - 支持多分类 (One-vs-Rest)
    - 添加正则项处理高维小样本问题
    - 输出类别概率
    """

    def __init__(self, shrinkage: float = 1e-6):
        """
        Args:
            shrinkage: 正则化系数 (防止协方差矩阵奇异)
        """
        self.shrinkage = shrinkage
        self.means_: List[np.ndarray] = []
        self.prior_: List[float] = []
        self.cov_inv_: Optional[np.ndarray] = None
        self.classes_: Optional[np.ndarray] = None
        self.fitted = False

    def fit(self, X: np.ndarray, y: np.ndarray):
        """
        训练LDA

        Args:
            X: (n_samples, n_features)
            y: (n_samples,) 标签
        """
        self.classes_ = np.unique(y)
        n_classes = len(self.classes_)
        n_features = X.shape[1]

        # 计算每类均值
        self.means_ = []
        self.prior_ = []
        for cls in self.classes_:
            X_cls = X[y == cls]
            self.means_.append(X_cls.mean(axis=0))
            self.prior_.append(X_cls.shape[0] / X.shape[0])

        # 计算类内协方差矩阵 (pooled)
        cov = np.zeros((n_features, n_features))
        for cls in self.classes_:
            X_cls = X[y == cls]
            centered = X_cls - X_cls.mean(axis=0)
            cov += centered.T @ centered
        cov /= (X.shape[0] - n_classes)

        # 正则化
        cov += self.shrinkage * np.eye(n_features)

        # 求逆
        self.cov_inv_ = linalg.inv(cov)
        self.fitted = True
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        """预测类别"""
        scores = self.decision_function(X)
        return self.classes_[scores.argmax(axis=1)]

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """
        预测各类别概率

        Args:
            X: (n_samples, n_features)

        Returns:
            proba: (n_samples, n_classes) 概率分布
        """
        scores = self.decision_function(X)
        # softmax 转概率
        scores = scores - scores.max(axis=1, keepdims=True)
        exp_scores = np.exp(scores)
        proba = exp_scores / exp_scores.sum(axis=1, keepdims=True)
        return proba

    def decision_function(self, X: np.ndarray) -> np.ndarray:
        """计算判别分数"""
        if not self.fitted:
            raise RuntimeError("LDA未训练")

        n_samples = X.shape[0]
        n_classes = len(self.classes_)
        scores = np.zeros((n_samples, n_classes))

        for i in range(n_classes):
            mean = self.means_[i]
            # 线性判别函数: w·x + b
            w = self.cov_inv_ @ mean
            b = -0.5 * (mean @ self.cov_inv_ @ mean) + np.log(self.prior_[i])
            scores[:, i] = X @ w + b

        return scores


class FBCSPDecoder:
    """
    FBCSP + LDA 完整解码器

    一站式完成: 频段滤波 → CSP特征提取 → LDA分类
    支持在线 (predict_one) 和离线 (fit/predict) 模式
    """

    def __init__(
        self,
        n_classes: int = 4,
        n_channels: int = 8,
        sampling_rate: int = 250,
        bands: Optional[List[Tuple[float, float]]] = None,
        n_filters: int = 4,
        confidence_threshold: float = 0.7,
    ):
        self.n_classes = n_classes
        self.n_channels = n_channels
        self.sampling_rate = sampling_rate
        self.n_filters = n_filters
        self.confidence_threshold = confidence_threshold

        if bands is None:
            bands = [
                (4, 8), (8, 12), (12, 16), (16, 20),
                (20, 24), (24, 28), (28, 32), (32, 36)
            ]
        self.bands = bands

        # OvR (One-vs-Rest) 策略: 对每个类别训练一个二分类FBCSP+LDA
        self._binary_classifiers: List[Tuple[FilterBankCSP, LDAClassifier]] = []
        self.fitted = False

    def fit(self, X: np.ndarray, y: np.ndarray):
        """
        训练多分类FBCSP+LDA (OvR策略)

        Args:
            X: (n_trials, n_channels, n_samples) 训练数据
            y: (n_trials,) 标签 (0 到 n_classes-1)
        """
        self._binary_classifiers = []
        for cls in range(self.n_classes):
            # 构建二分类标签: 当前类 vs 其他所有类
            y_binary = np.where(y == cls, 1, 0)

            # 检查是否有正样本
            if y_binary.sum() == 0:
                warnings.warn(f"类别 {cls} 没有训练样本")
                self._binary_classifiers.append((None, None))
                continue

            # FBCSP + LDA
            fbcsp = FilterBankCSP(
                bands=self.bands,
                n_filters=self.n_filters,
                sampling_rate=self.sampling_rate,
            )
            lda = LDAClassifier()
            features = fbcsp.fit_transform(X, y_binary)
            lda.fit(features, y_binary)

            self._binary_classifiers.append((fbcsp, lda))

        self.fitted = True
        return self

    def predict_one(self, trial: np.ndarray) -> Tuple[int, float]:
        """
        单次试次预测 (在线模式)

        Args:
            trial: (n_channels, n_samples) 单次EEG试次

        Returns:
            (predicted_class, confidence)
        """
        if trial.ndim == 2:
            trial = trial[np.newaxis, ...]  # (1, n_channels, n_samples)

        # 对每个OvR分类器获取概率
        probas = np.zeros(self.n_classes)
        for cls in range(self.n_classes):
            fbcsp, lda = self._binary_classifiers[cls]
            if fbcsp is None or lda is None:
                probas[cls] = 0.0
                continue
            features = fbcsp.transform(trial)
            proba = lda.predict_proba(features)
            # 取"属于该类"的概率 (OvR二分类的class 1)
            probas[cls] = proba[0, 1]

        predicted = probas.argmax()
        confidence = probas[predicted]

        # 置信度低于阈值则拒绝 (认为不可靠)
        if confidence < self.confidence_threshold:
            return -1, confidence

        return predicted, confidence

    def predict(self, X: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        批量预测

        Args:
            X: (n_trials, n_channels, n_samples)

        Returns:
            (predictions, confidences)
        """
        predictions = []
        confidences = []
        for i in range(X.shape[0]):
            pred, conf = self.predict_one(X[i])
            predictions.append(pred)
            confidences.append(conf)

        return np.array(predictions), np.array(confidences)


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
                
    FBCSP 解码器集成:
        enhancer = SignalEnhancer(sampling_rate=250, enable_decoder=True)
        enhancer.decoder.fit(X_train, y_train)  # 训练FBCSP+LDA
        pred, conf = enhancer.decoder.predict_one(trial)  # 在线预测
    """

    def __init__(self, sampling_rate: int = 250, line_freq: float = 50.0,
                 enable_decoder: bool = False,
                 n_classes: int = 6, n_channels: int = 8):
        self.sampling_rate = sampling_rate
        self.quality_monitor = SignalQualityMonitor(sampling_rate, line_freq)
        self.artifact_detector = ArtifactDetector()
        self.calibrator = BaselineCalibrator()
        self.adaptive_threshold = AdaptiveThreshold()
        self.drift_compensator = DriftCompensator(sampling_rate=sampling_rate)
        self.enable_decoder = enable_decoder
        self.decoder = None
        if enable_decoder:
            self.decoder = FBCSPDecoder(
                n_classes=n_classes,
                n_channels=n_channels,
                sampling_rate=sampling_rate,
                confidence_threshold=0.7,
            )

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