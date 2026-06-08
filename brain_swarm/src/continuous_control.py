"""
纯脑信号连续比例控制模块 (Pure EEG Continuous Control)
========================================================

核心理念:
  不依赖 EOG/EMG/SSVEP 等非脑辅助信号,
  仅使用 mu 节律 (8-12Hz 感觉运动节律) 的功率变化做连续控制.

设计哲学 (Palmer Luckey 式):
  不完美但够用 → 让人脑自适应学习 → 大脑天生擅长控制mu节律

架构:
  ContinuousMuControl: mu节律功率 → 连续速度值 (-1 到 1)
  ImageryClickDetector: mu功率骤降尖峰 → 点击信号
  PureEEGPipeline: 两者整合的控制循环

通道:
  C3 (右脑) → 左侧身体运动想象 → "向左/加速"
  C4 (左脑) → 右侧身体运动想象 → "向右/减速"
  Cz (中央) → 双脚运动想象 → "点击/确认"

电极位置 (10-20 系统):
  C3: 左中央 (左侧身体感觉运动)
  C4: 右中央 (右侧身体感觉运动)
  Cz: 顶点 (双脚/全身感觉运动)

BOM成本:
  仅需要3个干电极(C3, C4, Cz) + 2个参考(A1, A2) + 1个DRL
  ADS1299 8通道中只用5个, 剩余3个可做冗余
"""

import numpy as np
from scipy import signal
from typing import Tuple, Optional, Dict, List
from dataclasses import dataclass, field
from collections import deque


@dataclass
class ContinuousControlConfig:
    """连续比例控制配置"""
    # 采样率
    sampling_rate: int = 250
    # 通道索引 (10-20系统)
    c3_channel: int = 0      # 右脑 mu 节律
    c4_channel: int = 1      # 左脑 mu 节律
    cz_channel: int = 2      # 中央 mu 节律
    # mu 节律频段 (8-12Hz, 也可扩展为 7-13Hz)
    mu_low: float = 8.0
    mu_high: float = 12.0
    # 控制参数
    update_rate: float = 30.0       # 控制更新率 (Hz), 目标每 ~33ms 输出一次
    smooth_factor: float = 0.3      # 指数平滑系数 (越小越平滑, 响应越慢)
    dead_zone: float = 0.05         # 死区 (避免微小漂移导致误动)
    max_speed: float = 1.0          # 最大输出速度
    # 基线自适应
    baseline_adapt_rate: float = 0.01  # 基线更新速度 (慢速跟踪趋势)
    baseline_window: float = 10.0      # 基线窗口 (秒)
    # 频谱分析参数
    fft_window_ms: float = 500        # FFT 窗口 (ms)
    fft_step_ms: float = 33           # FFT 步进 (ms) = ~30Hz 更新率
    # 通道顺序 (默认5通道: C3, C4, Cz, A1, A2, DRL)
    n_channels: int = 6


class ContinuousMuControl:
    """
    mu 节律连续比例控制器

    将 mu 节律 (8-12Hz) 的功率映射为连续速度值.
    mu 功率越高 (放松) → 速度接近 0
    mu 功率越低 (运动想象) → 速度越大

    大脑天生会通过神经反馈学会控制 mu 节律,
    不需要训练分类器 —— 这是最关键的设计决策.

    用法:
        control = ContinuousMuControl(config)
        while True:
            eeg_chunk = read_eeg()  # (n_channels, n_samples)
            speed = control.update(eeg_chunk)  # -1 到 1

            # 同时检测点击
            is_click, power = control.detect_click(eeg_chunk)
            if is_click:
                execute_click()
    """

    def __init__(self, config: Optional[ContinuousControlConfig] = None):
        self.config = config or ContinuousControlConfig()
        sr = self.config.sampling_rate

        # FFT 参数
        self._fft_samples = int(self.config.fft_window_ms * sr / 1000)
        self._fft_step = int(self.config.fft_step_ms * sr / 1000)

        # 预计算 FFT 频率轴
        self._fft_freqs = np.fft.rfftfreq(self._fft_samples, 1.0 / sr)

        # 获取 mu 节律的 FFT bin 范围
        self._mu_bins = np.where(
            (self._fft_freqs >= self.config.mu_low) &
            (self._fft_freqs <= self.config.mu_high)
        )[0]

        # 窗函数
        self._window = np.hanning(self._fft_samples)

        # 状态
        self._last_speed = 0.0
        self._baseline_power = None
        self._power_history = deque(maxlen=int(self.config.baseline_window * self.config.update_rate))
        self._speed_history = deque(maxlen=10)  # 用于平滑

        # 频谱缓冲
        self._c3_buffer = deque(maxlen=self._fft_samples)
        self._c4_buffer = deque(maxlen=self._fft_samples)
        self._cz_buffer = deque(maxlen=self._fft_samples)

        # 点击检测状态
        self._last_power = None
        self._power_slope_history = deque(maxlen=20)  # 用于计算功率变化率的基线

        # 统计
        self._update_count = 0
        self._mean_power = 0.5
        self._std_power = 0.15

    def _compute_mu_power(self, channel_data: np.ndarray) -> float:
        """
        计算 mu 节律功率

        使用 Welch 法: 对缓冲数据加窗 FFT, 取 mu 频段功率均值

        Args:
            channel_data: (n_samples,) 通道数据

        Returns:
            mu_power: mu 频段功率 (归一化)
        """
        n = len(channel_data)
        if n < self._fft_samples:
            return self._last_speed  # 数据不够, 返回上一次

        # 取最近的 fft_samples 个点
        data = channel_data[-self._fft_samples:]
        data = data * self._window[:len(data)]

        # FFT
        fft = np.abs(np.fft.rfft(data, n=self._fft_samples))
        fft_power = fft ** 2

        # mu 频段平均功率
        if len(self._mu_bins) > 0:
            mu_power = np.mean(fft_power[self._mu_bins])
        else:
            mu_power = np.mean(fft_power)

        # 归一化: log 变换使分布更正态
        mu_power = np.log(max(mu_power, 1e-10))

        return mu_power

    def feed(self, channel_data: np.ndarray, channel_idx: int):
        """
        喂入单通道数据到循环缓冲

        Args:
            channel_data: (n_samples,) 当前数据块
            channel_idx: 通道索引
        """
        if channel_idx == self.config.c3_channel:
            self._c3_buffer.extend(channel_data)
        elif channel_idx == self.config.c4_channel:
            self._c4_buffer.extend(channel_data)
        elif channel_idx == self.config.cz_channel:
            self._cz_buffer.extend(channel_data)

    def feed_all(self, eeg_chunk: np.ndarray):
        """喂入全部通道数据"""
        if eeg_chunk.ndim == 1:
            eeg_chunk = eeg_chunk[np.newaxis, :]

        for ch_idx in [self.config.c3_channel, self.config.c4_channel, self.config.cz_channel]:
            if ch_idx < eeg_chunk.shape[0]:
                self.feed(eeg_chunk[ch_idx], ch_idx)

    def update(self, eeg_chunk: Optional[np.ndarray] = None,
               channel: str = "c3") -> float:
        """
        更新输出速度

        Args:
            eeg_chunk: (n_channels, n_samples) EEG 数据块
            channel: "c3" (左侧控制) 或 "c4" (右侧控制) 或 "cz" (双脚)

        Returns:
            speed: -1 到 1 的连续速度值
                c3 通道: 正值 = 向左, 负值 = 向右(想象放松)
                c4 通道: 正值 = 向右, 负值 = 向左(想象放松)
        """
        if eeg_chunk is not None:
            self.feed_all(eeg_chunk)

        # 选择通道缓冲
        if channel == "c3":
            buffer = self._c3_buffer
        elif channel == "c4":
            buffer = self._c4_buffer
        elif channel == "cz":
            buffer = self._cz_buffer
        else:
            raise ValueError(f"未知通道: {channel}")

        if len(buffer) < self._fft_samples:
            return self._last_speed

        # 计算当前 mu 功率
        current_power = self._compute_mu_power(np.array(buffer))
        self._power_history.append(current_power)

        # 基线: 使用长时间窗口的中位数 (对尖峰鲁棒)
        if self._baseline_power is None and len(self._power_history) > 10:
            # 首次基线: 最近 10 秒的中位数
            base_window = min(len(list(self._power_history)),
                              int(self.config.baseline_window * self.config.update_rate))
            self._baseline_power = np.median(list(self._power_history)[-base_window:])
        elif self._baseline_power is not None:
            # 自适应基线: 指数慢速跟踪
            base_window = list(self._power_history)
            recent_median = np.median(base_window[-min(len(base_window), 50):])
            self._baseline_power = (1 - self.config.baseline_adapt_rate) * self._baseline_power + \
                                   self.config.baseline_adapt_rate * recent_median

        if self._baseline_power is None:
            return self._last_speed

        # mu 去同步: 功率下降 = 运动意图
        # mu 节律是"空闲节律"——当你不想运动时它最高
        # 当你想要运动时, mu 功率下降 (事件相关去同步, ERD)
        if self._baseline_power > 0:
            erd_ratio = (self._baseline_power - current_power) / self._baseline_power
        else:
            erd_ratio = 0.0

        # 映射到 -1 到 1
        speed = np.clip(erd_ratio, -1, 1)

        # 死区
        if abs(speed) < self.config.dead_zone:
            speed = 0.0

        # 指数平滑
        self._last_speed = self.config.smooth_factor * speed + \
                          (1 - self.config.smooth_factor) * self._last_speed

        # 限幅
        self._last_speed = np.clip(self._last_speed, -self.config.max_speed, self.config.max_speed)

        self._speed_history.append(self._last_speed)
        self._update_count += 1

        return self._last_speed

    def detect_click(self, eeg_chunk: Optional[np.ndarray] = None,
                     threshold: float = 2.5) -> Tuple[bool, float]:
        """
        检测"运动想象骤降尖峰" → 点击信号

        原理: 用户在持续运动想象中突然用力想象同一动作,
             mu 功率在 200-300ms 内出现超过 30% 的突然下降.

            这个骤降特征是纯脑的、自然的、无需训练的.
            它不是"分类出点击意图", 而是检测到 mu 功率的异常变化率.

        Args:
            eeg_chunk: EEG 数据块
            threshold: 检测阈值 (Z-score), 2.5-3.0 之间

        Returns:
            (is_click, power_drop_ratio)
        """
        if eeg_chunk is not None:
            self.feed_all(eeg_chunk)

        # 使用 C3 通道检测 (左右手都可以做骤降)
        buffer = self._c3_buffer
        if len(buffer) < self._fft_samples:
            return False, 0.0

        current_power = self._compute_mu_power(np.array(buffer))
        self._power_history.append(current_power)

        if self._last_power is None:
            self._last_power = current_power
            return False, 0.0

        # 功率变化率 (负值 = 下降)
        power_change = (current_power - self._last_power) / max(abs(self._last_power), 1e-10)

        # 更新功率变化率基线
        self._power_slope_history.append(power_change)
        self._last_power = current_power

        if len(self._power_slope_history) < 10:
            return False, 0.0

        # Z-score 异常检测
        slopes = np.array(list(self._power_slope_history))
        slope_mean = np.mean(slopes)
        slope_std = max(np.std(slopes), 1e-10)
        z_score = (power_change - slope_mean) / slope_std

        # 点击条件: 功率骤降 (大负值 Z-score)
        # 注意是 power_change 的大幅下降, 不是绝对值
        is_click = z_score < -threshold  # 异常大幅下降 = 点击

        return is_click, power_change

    def get_state(self) -> Dict:
        """获取当前状态 (用于调试/可视化)"""
        return {
            "speed": self._last_speed,
            "baseline_power": self._baseline_power,
            "update_count": self._update_count,
            "buffer_sizes": {
                "c3": len(self._c3_buffer),
                "c4": len(self._c4_buffer),
                "cz": len(self._cz_buffer),
            }
        }

    def reset(self):
        """重置所有状态"""
        self._last_speed = 0.0
        self._baseline_power = None
        self._power_history.clear()
        self._speed_history.clear()
        self._c3_buffer.clear()
        self._c4_buffer.clear()
        self._cz_buffer.clear()
        self._last_power = None
        self._power_slope_history.clear()
        self._update_count = 0


class ContinuousPipeline:
    """
    纯脑信号连续控制 Pipeline

    不做分类, 不做叠加平均, 不做模式切换.
    只用 mu 节律做连续比例控制 + 骤降尖峰做点击.

    用法:
        pipeline = ContinuousPipeline(config)
        control = pipeline.step(eeg_chunk)  # 每次新数据到达
        # control.speed: -1 到 1 的速度值
        # control.click: True/False 点击
    """

    @dataclass
    class ControlOutput:
        """控制输出"""
        speed: float = 0.0
        click: bool = False
        channel: str = "c3"
        mu_power: float = 0.0
        baseline: float = 0.0

    def __init__(self, config: Optional[ContinuousControlConfig] = None):
        self.config = config or ContinuousControlConfig()
        self.control = ContinuousMuControl(self.config)
        self._active_channel = "c3"  # 默认使用右手想象控制

    def step(self, eeg_chunk: np.ndarray) -> ControlOutput:
        """
        单步控制 (纯EEG, 无EOG/EMG)

        流程:
          1. mu 节律功率计算 (FFT, 30Hz 更新率)
          2. 基线自适应更新 (慢速跟踪)
          3. 去同步比率 → 速度
          4. 骤降尖峰检测 → 点击

        Args:
            eeg_chunk: (n_channels, n_samples) EEG 数据块

        Returns:
            ControlOutput
        """
        # 1. 连续速度
        speed = self.control.update(eeg_chunk, channel=self._active_channel)

        # 2. 点击检测
        is_click, power_change = self.control.detect_click(eeg_chunk)

        # 3. 获取状态信息
        state = self.control.get_state()

        return self.ControlOutput(
            speed=speed,
            click=is_click,
            channel=self._active_channel,
            mu_power=power_change,
            baseline=state.get("baseline_power", 0) or 0,
        )

    def switch_channel(self, channel: str):
        """切换控制通道"""
        assert channel in ("c3", "c4", "cz"), f"无效通道: {channel}"
        self._active_channel = channel

    def reset(self):
        """重置"""
        self.control.reset()
        self._active_channel = "c3"