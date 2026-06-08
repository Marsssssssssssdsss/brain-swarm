"""系统配置"""

import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict
import os

# 项目根目录
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")
MODEL_DIR = os.path.join(DATA_DIR, "models")
BASELINE_DIR = os.path.join(DATA_DIR, "baselines")


@dataclass
class EEGConfig:
    """EEG 采集配置"""
    n_channels: int = 8
    sampling_rate: int = 250       # Hz
    low_freq: float = 0.5          # 带通滤波下限 (0.5Hz 保留慢皮层电位SCP)
    high_freq: float = 100.0       # 带通滤波上限 (100Hz 包含gamma频段信息)
    notch_freq: float = 50.0       # 陷波滤波器频率


@dataclass
class SignalEnhancementConfig:
    """信号增强配置 —— 解决信号衰减问题"""
    # 基线校准 (BCI研究标准: 2-5分钟基线, 30秒方差过大会导致Z-score阈值不可靠)
    enable_baseline_calibration: bool = True
    calibration_duration: float = 120.0     # 校准时长（秒）— 从30秒修正为2分钟

    # 小波变换去噪 (对标论文级伪迹去除方法)
    enable_wavelet_denoising: bool = True
    wavelet_name: str = "db4"               # 小波基: db4, sym8, coif5 等
    wavelet_level: int = 5                  # 分解层数
    wavelet_threshold_mode: str = "soft"    # 阈值模式: soft | hard
    wavelet_threshold_scale: float = 1.0    # 阈值缩放系数

    # 贝叶斯自适应阈值 (替代纯Z-score方法)
    enable_bayesian_threshold: bool = False
    bayesian_prior_mean: float = 50.0
    bayesian_prior_std: float = 15.0
    baseline_file: str = os.path.join(BASELINE_DIR, "user_baseline.json")

    # 自适应阈值
    enable_adaptive_threshold: bool = True
    z_score_threshold: float = 1.5          # 触发阈值 = 基线均值 + N*标准差
    min_threshold: float = 40.0             # 绝对最低阈值
    max_threshold: float = 90.0             # 绝对最高阈值
    signal_quality_weight: float = 0.3      # 信号质量对阈值的影响权重

    # 伪迹检测
    enable_artifact_rejection: bool = True
    amplitude_threshold: float = 150.0      # μV, 肌肉伪迹阈值
    kurtosis_threshold: float = 5.0          # 眨眼伪迹峰度阈值
    dead_channel_std: float = 0.5            # 死通道标准差阈值

    # 信号质量评估
    enable_quality_monitoring: bool = True
    min_acceptable_snr: float = 0.0          # 最低可接受SNR (dB)
    max_line_noise_ratio: float = 0.3        # 最大工频干扰比例
    tgam_quality_threshold: int = 100        # TGAM硬件质量阈值（超过则忽略）

    # 漂移补偿
    enable_drift_compensation: bool = True
    drift_window_size: int = 100             # 短期漂移窗口大小

    # 自动恢复
    auto_recovery_enabled: bool = True
    recovery_cooldown: float = 5.0           # 恢复冷却时间（秒）


@dataclass
class DecoderConfig:
    """解码器配置"""
    model_type: str = "fbcsp_lda"  # fbcsp_lda | eegnet | csbrain
    n_classes: int = 6             # 预设动作数量 (实际可靠控制: 4-6类, 14类不现实)
    # 时间窗口
    window_size: float = 1.0       # 每段分析窗口长度（秒）
    window_stride: float = 0.5     # 窗口滑动步长（秒）
    # FBCSP 参数
    fbcsp_bands: List[tuple] = field(default_factory=lambda: [
        (4, 8), (8, 12), (12, 16), (16, 20), (20, 24), (24, 28), (28, 32), (32, 36)
    ])
    fbcsp_m: int = 4               # CSP每频段保留的空间滤波器对数
    # 滑动窗口叠加参数
    sliding_window_count: int = 100     # 叠加窗口数
    sliding_window_step_ms: float = 5.0 # 窗口步进(ms)
    # 置信度阈值 (超过此值才触发命令, 防止误触发)
    confidence_threshold: float = 0.7   # 0-1
    # EEGNet 参数
    eegnet_dropout: float = 0.5
    # 模型路径
    model_path: str = os.path.join(MODEL_DIR, "brain_decoder.pkl")


@dataclass
class DroneConfig:
    """无人机集群配置"""
    n_drones: int = 3
    simulation: bool = True
    connection_uris: List[str] = field(default_factory=list)
    default_height: float = 1.0
    default_speed: float = 0.5
    safety_radius: float = 5.0


@dataclass
class SSVEPConfig:
    """SSVEP 频域解码配置 (最实用的实时方案)"""
    frequencies: List[float] = field(default_factory=lambda: [6.0, 6.67, 7.5, 8.57, 10.0, 12.0])
    command_labels: List[str] = field(default_factory=lambda: ["左转", "右转", "前进", "悬停", "返航", "紧急"])
    window_duration: float = 2.0          # FFT分析窗口(秒)
    snr_threshold: float = 3.0            # 检测阈值
    occipital_channels: List[int] = field(default_factory=lambda: [0])
    use_harmonics: bool = True
    harmonic_count: int = 2
    confidence_threshold: float = 0.6
    enable: bool = False                  # 默认关闭, 用户可选择开启


@dataclass
class BiosignalConfig:
    """多模态生物信号配置 (EOG眨眼/眼动 + EMG咬牙)"""
    enable: bool = True
    blink_threshold: float = 3.0
    eye_move_threshold: float = 2.0
    jaw_clench_threshold: float = 5.0
    # 按键映射
    blink_action: str = "confirm"
    eye_left_action: str = "prev"
    eye_right_action: str = "next"
    jaw_clench_action: str = "emergency_stop"


@dataclass
class ContinuousControlConfig:
    """纯脑信号连续比例控制配置 (mu节律)"""
    sampling_rate: int = 250
    c3_channel: int = 0
    c4_channel: int = 1
    cz_channel: int = 2
    mu_low: float = 8.0
    mu_high: float = 12.0
    smooth_factor: float = 0.3
    dead_zone: float = 0.05
    max_speed: float = 1.0
    baseline_adapt_rate: float = 0.01
    baseline_window: float = 10.0
    fft_window_ms: float = 500
    fft_step_ms: float = 33
    click_threshold: float = 2.5
    n_channels: int = 6


@dataclass
class PipelineConfig:
    """实时 Pipeline 配置"""
    eeg: EEGConfig = field(default_factory=EEGConfig)
    decoder: DecoderConfig = field(default_factory=DecoderConfig)
    ssvep: SSVEPConfig = field(default_factory=SSVEPConfig)
    biosignal: BiosignalConfig = field(default_factory=BiosignalConfig)
    continuous: ContinuousControlConfig = field(default_factory=ContinuousControlConfig)
    drone: DroneConfig = field(default_factory=DroneConfig)
    signal_enhancement: SignalEnhancementConfig = field(default_factory=SignalEnhancementConfig)
    # 预设动作配置文件
    action_file: str = os.path.join(ROOT_DIR, "src", "preset_actions.json")
    # 平滑参数：连续 N 次预测相同才触发
    smooth_count: int = 3
    # 两次指令间最小间隔（秒）
    min_command_interval: float = 2.0