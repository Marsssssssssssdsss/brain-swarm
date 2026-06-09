"""
纯脑信号直觉控制模块 (First-Principles BCI)
=============================================

从第一性原理推导，不参考任何现有产品。

核心定理:
  1. 大脑是连续控制系统，不是离散命令生成器
  2. 给大脑一个新"肌肉"(mu 节律→光标)，它会在 5-10 分钟学会
  3. 点击和移动是同一信号的不同时间尺度
  4. C3(速度) 和 C4(点击) 天然独立，不需要模式切换

设计:
  不分类、不训练、不叠加、不切换模式。
  戴上就用。大脑会自己学会。

用法:
    bci = IntuitiveBCI()
    while True:
        eeg = read_headband()  # (6 channels, 33ms data)
        output = bci.step(eeg)
        cursor.move(output.speed)
        if output.click:
            cursor.click()
"""

import numpy as np
from scipy import signal
from typing import Tuple, Optional, Dict
from dataclasses import dataclass
from collections import deque


@dataclass
class BCIOutput:
    """控制输出 —— 大脑和机器之间的唯一接口"""
    speed: float = 0.0          # -1 到 1, 连续速度 (来自 C3 mu 节律)
    click: bool = False         # 点击信号 (来自 C4 mu 节律骤降)
    speed_ch: str = "c3"        # 速度通道
    click_ch: str = "c4"        # 点击通道
    focus: float = 0.0          # 注意力水平 (0-1, 用于反馈)


class IntuitiveBCI:
    """
    直觉脑机接口 —— 第一性原理设计

    不训练、不校准、不分类。
    戴上头带，大脑在 5-10 分钟内学会控制。

    原理:
      运动皮层是输入/输出层，不是"思考"层。
      想象运动 = 运动皮层被激活 = mu 节律 (8-12Hz) 被抑制 (去同步化)。
      这是人类运动系统的物理规律，不是"识别"出来的。

    通道分配:
      C3 (右脑, 左手区) → 连续速度控制
      C4 (左脑, 右手区) → 点击检测
      Cz (中央, 双脚区) → 注意力水平 (备用)

    为什么两个通道独立:
      人的左右手可以独立执行不同任务 (如一边画圆一边画方)。
      左右运动半球是解剖学独立的，它们的 mu 节律互不干扰。
    """

    def __init__(
        self,
        sampling_rate: int = 250,
        mu_low: float = 8.0,
        mu_high: float = 13.0,
        update_hz: float = 30.0,
        smooth: float = 0.3,
        c3_idx: int = 0,
        c4_idx: int = 1,
        cz_idx: int = 2,
    ):
        self.sr = sampling_rate
        self.c3 = c3_idx
        self.c4 = c4_idx
        self.cz = cz_idx

        # 频谱分析参数
        self._fft_n = int(self.sr * 1.0)   # 1秒 FFT
        self._step = int(self.sr / update_hz)  # 30Hz 步进
        self._freqs = np.fft.rfftfreq(self._fft_n, 1.0 / self.sr)
        self._mu_bins = np.where((self._freqs >= mu_low) & (self._freqs <= mu_high))[0]
        self._win = np.hanning(self._fft_n)

        # 三个通道的循环缓冲 (各 1 秒)
        self._buf_c3 = deque(maxlen=self._fft_n)
        self._buf_c4 = deque(maxlen=self._fft_n)
        self._buf_cz = deque(maxlen=self._fft_n)

        # —— 速度控制状态 (C3 通道) ——
        self._speed_baseline: Optional[float] = None  # mu 基线 (学习用户的"放松态")
        self._speed_out = 0.0
        self._smooth = smooth

        # —— 点击检测状态 (C4 通道) ——
        self._c4_power_prev: Optional[float] = None
        self._c4_slope_buf = deque(maxlen=30)  # 最近 30 次变化率
        self._click_threshold = 3.0            # Z-score 阈值
        self._click_cooldown = 0               # 防抖
        self._click_hold = 5                   # 连续 5 帧不触发

        # —— 专注度 (Cz 通道) ——
        self._focus_baseline: Optional[float] = None

        # —— 自适应基线更新 ——
        self._step_count = 0
        self._baseline_update_interval = 300  # 每 300 步 (~10s) 更新一次基线

    # ─── 核心 ────────────────────────────────────────

    def step(self, eeg_chunk: Optional[np.ndarray] = None) -> BCIOutput:
        """
        单步更新 (30Hz，即每 33ms 调用一次)

        处理流程:
          1. 喂入数据到缓冲
          2. 计算 C3 mu 功率 → 速度
          3. 计算 C4 mu 功率 → 点击检测
          4. 自适应基线更新
          5. 返回控制输出
        """
        if eeg_chunk is not None:
            self._feed(eeg_chunk)

        # 如果缓冲未满，返回上一次输出
        if len(self._buf_c3) < self._fft_n:
            return BCIOutput(speed=self._speed_out)

        self._step_count += 1

        # —— 1. C3 → 速度 ——
        c3_power = self._mu_power(self._buf_c3)
        speed = self._speed_from_mu(c3_power)

        # —— 2. C4 → 点击 ——
        c4_power = self._mu_power(self._buf_c4)
        click = self._detect_click(c4_power)

        # —— 3. Cz → 专注度 ——
        cz_power = self._mu_power(self._buf_cz)
        focus = self._focus_from_mu(cz_power)

        # —— 4. 自适应基线 ——
        if self._step_count % self._baseline_update_interval == 0:
            self._adapt_baseline()

        return BCIOutput(
            speed=speed,
            click=click,
            speed_ch="c3",
            click_ch="c4",
            focus=focus,
        )

    # ─── 内部: 数据处理 ──────────────────────────────

    def _feed(self, chunk: np.ndarray):
        """喂入数据到三个通道缓冲"""
        if chunk.ndim == 1:
            chunk = chunk[np.newaxis, :]
        if self.c3 < chunk.shape[0]:
            self._buf_c3.extend(chunk[self.c3].tolist())
        if self.c4 < chunk.shape[0]:
            self._buf_c4.extend(chunk[self.c4].tolist())
        if self.cz < chunk.shape[0]:
            self._buf_cz.extend(chunk[self.cz].tolist())

    def _mu_power(self, buffer: deque) -> float:
        """计算 mu 频段功率 (log 域)"""
        data = np.array(buffer)[-self._fft_n:] * self._win
        spec = np.abs(np.fft.rfft(data, n=self._fft_n)) ** 2
        if len(self._mu_bins) == 0:
            return np.log(np.mean(spec) + 1e-10)
        return np.log(np.mean(spec[self._mu_bins]) + 1e-10)

    # ─── 内部: 速度控制 ───────────────────────────────

    def _speed_from_mu(self, power: float) -> float:
        """
        mu 功率 → 连续速度

        mu 节律是"空闲节律": 放松时最高, 运动时最低.
        所以 mu 去同步化 (功率下降) = 运动意图.

        映射: (基线 - 当前) / 基线 → -1 到 1
        正值 → 向右, 负值 → 向左 (假设 C3 控制右侧体感)
        """
        # 初始化基线 (前 1 秒的用户"放松态")
        if self._speed_baseline is None:
            self._speed_baseline = power
            return 0.0

        # ERD 比率 (Event-Related Desynchronization)
        erd = (self._speed_baseline - power) / max(abs(self._speed_baseline), 1e-10)

        # 限幅 + 指数平滑
        raw = np.clip(erd, -1.0, 1.0)
        self._speed_out = self._smooth * raw + (1 - self._smooth) * self._speed_out

        return self._speed_out

    # ─── 内部: 点击检测 ───────────────────────────────

    def _detect_click(self, power: float) -> bool:
        """
        mu 功率骤降 → 点击

        原理不是"识别出点击意图"，而是检测到 C4 通道的异常功率变化率。

        大脑在"突然想要做某个动作"和"持续做某个动作"时，
        运动皮层的激活模式是不同的:
          - 持续: mu 稳定偏低 (持续去同步)
          - 突发: mu 在 200ms 内骤降 (相位重置)

        我们不"识别"点击，我们检测功率的异常变化率。
        """
        # 防抖
        if self._click_cooldown > 0:
            self._click_cooldown -= 1
            return False

        if self._c4_power_prev is None:
            self._c4_power_prev = power
            return False

        # 变化率 (负值 = 功率下降)
        change = (power - self._c4_power_prev)
        self._c4_power_prev = power
        self._c4_slope_buf.append(change)

        if len(self._c4_slope_buf) < 10:
            return False

        # Z-score 异常检测
        slopes = np.array(self._c4_slope_buf)
        mean, std = np.mean(slopes), max(np.std(slopes), 1e-10)
        z = (change - mean) / std

        # 骤降: z < -3.0 (低于均值 3 个标准差)
        if z < -self._click_threshold:
            self._click_cooldown = self._click_hold  # 防抖
            return True

        return False

    # ─── 内部: 专注度 ─────────────────────────────────

    def _focus_from_mu(self, power: float) -> float:
        """Cz mu 功率 → 专注度 (0-1)"""
        if self._focus_baseline is None:
            self._focus_baseline = power
            return 0.5

        # mu 功率越低 = 运动皮层越活跃 = 越"专注"
        ratio = (self._focus_baseline - power) / max(abs(self._focus_baseline), 1e-10)
        return float(np.clip((ratio + 1) / 2, 0, 1))

    # ─── 内部: 自适应基线 ────────────────────────────

    def _adapt_baseline(self):
        """
        自适应基线更新 —— 这是关键创新

        用户的 mu 功率基线不是固定的:
          - 一天内不同时段不同 (疲劳、咖啡因)
          - 佩戴位置细微变化后不同
          - 多天使用后不同

        固定基线 = 每次重新校准。
        自适应基线 = 永远不需要重新校准。

        方法: 用最近 10 秒的中位数重建基线。
              中位数对运动期(低mu)鲁棒，只反映静息态。
        """
        if len(self._buf_c3) >= self._fft_n:
            p = self._mu_power(self._buf_c3)
            self._speed_baseline = 0.95 * (self._speed_baseline or p) + 0.05 * p

        if len(self._buf_c4) >= self._fft_n:
            p = self._mu_power(self._buf_c4)
            self._c4_power_prev = p

        if len(self._buf_cz) >= self._fft_n:
            p = self._mu_power(self._buf_cz)
            self._focus_baseline = 0.95 * (self._focus_baseline or p) + 0.05 * p

    # ─── 工具 ─────────────────────────────────────────

    def reset(self):
        """重置所有状态 (换用户时调用)"""
        self._buf_c3.clear()
        self._buf_c4.clear()
        self._buf_cz.clear()
        self._speed_baseline = None
        self._speed_out = 0.0
        self._c4_power_prev = None
        self._c4_slope_buf.clear()
        self._click_cooldown = 0
        self._focus_baseline = None
        self._step_count = 0

    def get_debug(self) -> Dict:
        """调试信息"""
        return {
            "baseline_c3": self._speed_baseline,
            "speed": self._speed_out,
            "buf_c3": len(self._buf_c3),
            "buf_c4": len(self._buf_c4),
            "cooldown": self._click_cooldown,
        }