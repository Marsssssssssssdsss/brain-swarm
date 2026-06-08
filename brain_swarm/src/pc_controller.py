"""脑控电脑 - 预设动作触发（含自适应阈值和信号衰减处理）

用 TGAM 脑电模块控制电脑：
- 专注度 > 自适应阈值 → 触发键盘快捷键
- 眨眼 → 切换动作模式
- 放松度 → 退出/取消

信号衰减处理：
- 基线校准：学习用户静息态特征，动态调整阈值
- 信号质量感知：信号差时拒绝触发，防止误操作
- 漂移补偿：消除电极阻抗变化带来的缓慢偏移
"""

import time
import os
from typing import Optional, Callable, List, Tuple
from dataclasses import dataclass
from collections import deque

from tgam_reader import TGAMData
from signal_enhancer import (
    AdaptiveThreshold, BaselineCalibrator, DriftCompensator,
    SignalQualityReport, SignalEnhancer, UserBaseline
)

# 尝试导入键盘库
try:
    import pyautogui
    HAS_PYAUTOGUI = True
except ImportError:
    HAS_PYAUTOGUI = False


# 基线文件路径
BASELINE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 
                              '..', 'data', 'user_baseline.json')


@dataclass
class BrainTrigger:
    """脑电触发条件"""
    name: str
    condition_desc: str
    # 触发条件：专注度阈值
    attention_min: int = 0
    attention_max: int = 100
    meditation_min: int = 0
    meditation_max: int = 100
    blink_min: int = 0  # 0 = 不需要眨眼
    # 平滑次数
    sustain_count: int = 3
    # 冷却时间（秒）
    cooldown: float = 1.5

    def check(self, attention: float, meditation: float, blink: int) -> bool:
        """检查是否满足触发条件"""
        # 眨眼检查
        if self.blink_min > 0:
            if blink < self.blink_min:
                return False
        # 专注度 + 放松度
        return (self.attention_min <= attention <= self.attention_max and
                self.meditation_min <= meditation <= self.meditation_max)


class BrainPcController:
    """
    脑控电脑控制器

    用 TGAM 的专注度 + 眨眼来控制电脑执行预设动作

    控制方案：
    - 不同专注度范围 → 不同的预设动作
    - 眨眼 → 确认/切换
    - 专注 > 自适应阈值 → 触发当前选中动作

    信号衰减处理：
    - 自适应阈值：根据用户基线动态调整，信号差时自动提高阈值
    - 基线校准：30秒学习用户静息态
    - 信号质量感知：信号差时拒绝触发
    """

    def __init__(self, use_adaptive_threshold: bool = True):
        # 预设动作列表（对应不同的专注度范围）
        self.actions: List[Tuple[str, str, str]] = [
            ("讲解模式", "打开 PPT + 专注展示", "win+shift+p"),
            ("编码模式", "打开 VS Code + 全屏", "win+shift+c"),
            ("浏览器", "打开 Chrome + 搜索", "win+shift+b"),
            ("终端", "打开命令窗口", "win+shift+t"),
            ("截屏", "截取当前屏幕", "win+shift+s"),
            ("暂停/恢复", "切换当前模式", "esc"),
        ]

        self.current_action_idx = 0
        self._attention_buffer: deque = deque(maxlen=10)
        self._trigger_buffer: deque = deque(maxlen=5)
        self._last_trigger_time = 0.0
        self._blink_count = 0
        self._last_blink_time = 0.0
        self._blink_window = []
        self._cooldown = 2.0
        self._mode = "idle"  # idle / calibrating / scanning / triggered

        # ─── 信号增强组件 ───
        self._use_adaptive = use_adaptive_threshold
        self._adaptive_threshold = AdaptiveThreshold()
        self._calibrator = BaselineCalibrator(calibration_duration=30.0)
        self._drift_compensator = DriftCompensator()
        self._signal_enhancer = SignalEnhancer()
        self._last_quality: Optional[SignalQualityReport] = None
        self._fixed_threshold = 70  # 传统固定阈值（不使用自适应时）
        self._current_threshold = 70  # 当前有效阈值
        self._baseline_loaded = False

        # 尝试加载已有基线
        self._try_load_baseline()

    def _try_load_baseline(self):
        """尝试加载已保存的用户基线"""
        if os.path.exists(BASELINE_FILE):
            try:
                self._calibrator = BaselineCalibrator.load(BASELINE_FILE)
                if self._calibrator.baseline.calibrated:
                    b = self._calibrator.baseline
                    self._adaptive_threshold.update_baseline(
                        b.attention_mean, b.attention_std
                    )
                    self._baseline_loaded = True
                    self._current_threshold = self._adaptive_threshold.compute_threshold()
                    print(f"[基线] 已加载用户基线: 专注度均值={b.attention_mean:.1f}, "
                          f"标准差={b.attention_std:.1f}, "
                          f"自适应阈值={self._current_threshold:.0f}")
            except Exception as e:
                print(f"[基线] 加载失败: {e}")

    def start_calibration(self):
        """开始基线校准"""
        self._mode = "calibrating"
        self._calibrator.start()
        print(f"\n{'='*50}")
        print("  基线校准开始")
        print("  请保持放松状态，不要刻意专注或思考")
        print(f"  校准时长: {self._calibrator.calibration_duration} 秒")
        print(f"{'='*50}\n")

    def get_calibration_progress(self) -> float:
        """获取校准进度 0.0~1.0"""
        return self._calibrator.progress

    @property
    def is_calibrating(self) -> bool:
        return self._mode == "calibrating"

    @property
    def current_threshold(self) -> float:
        return self._current_threshold

    @property
    def baseline_loaded(self) -> bool:
        return self._baseline_loaded

    def save_baseline(self):
        """保存基线到文件"""
        self._calibrator.save(BASELINE_FILE)
        print(f"[基线] 已保存到 {BASELINE_FILE}")

    def on_tgam_data(self, data: TGAMData):
        """处理 TGAM 数据，决定是否触发动作"""
        now = time.time()

        attention = data.attention
        meditation = data.meditation
        blink = data.blink

        # ─── 校准模式 ───
        if self._mode == "calibrating":
            # 计算原始EEG的RMS
            raw_eeg_rms = abs(data.raw_eeg) if data.raw_eeg else 0
            self._calibrator.add_sample(attention, meditation, raw_eeg_rms)

            if self._calibrator.progress >= 1.0:
                baseline = self._calibrator.baseline
                self._adaptive_threshold.update_baseline(
                    baseline.attention_mean, baseline.attention_std
                )
                self._current_threshold = self._adaptive_threshold.compute_threshold()
                self._mode = "scanning"
                self._baseline_loaded = True
                print(f"\n[基线] 校准完成!")
                print(f"  专注度: 均值={baseline.attention_mean:.1f}, "
                      f"标准差={baseline.attention_std:.1f}")
                print(f"  放松度: 均值={baseline.meditation_mean:.1f}, "
                      f"标准差={baseline.meditation_std:.1f}")
                print(f"  自适应阈值: {self._current_threshold:.0f}")
                print(f"{'='*50}\n")
                self.save_baseline()
            return  # 校准期间不触发动作

        # ─── 信号质量检查 ───
        # TGAM硬件信号质量
        if data.signal_quality > 100:
            # 信号极差，直接忽略
            self._last_quality = None
            return

        self._attention_buffer.append(attention)

        avg_attention = (sum(self._attention_buffer) /
                        len(self._attention_buffer) if self._attention_buffer else 0)

        # ─── 自适应阈值更新 ───
        if self._use_adaptive and self._baseline_loaded:
            # 信号质量越好，阈值越低（越容易触发）
            sig_quality = 1.0
            if data.signal_quality > 0:
                # TGAM signal_quality 0=好, 200=差
                sig_quality = max(0.0, 1.0 - data.signal_quality / 200.0)

            self._current_threshold = self._adaptive_threshold.compute_threshold(sig_quality)
        else:
            self._current_threshold = self._fixed_threshold

        # ─── 漂移补偿 ───
        compensated_attention = self._drift_compensator.detrend(avg_attention)

        # ─── 眨眼检测 ───
        if blink > 50:
            self._blink_window.append(now)
            self._blink_window = [t for t in self._blink_window if now - t < 1.0]

        # ─── 冷却检查 ───
        if now - self._last_trigger_time < self._cooldown:
            return

        # ─── 控制逻辑 ───

        # 双眨眼：切换动作
        if len(self._blink_window) >= 2:
            self._current_action_idx = (self._current_action_idx + 1) % len(self.actions)
            action = self.actions[self._current_action_idx]
            print(f"  [切换] → {action[0]}: {action[1]}")
            self._blink_window = []
            self._last_trigger_time = now

        # 高专注：执行当前动作（使用自适应阈值 + 补偿后专注度）
        elif compensated_attention >= self._current_threshold:
            self._trigger_buffer.append(True)
            if len(self._trigger_buffer) >= 3:
                self._execute_current_action()
                self._trigger_buffer.clear()
                self._last_trigger_time = now
        else:
            self._trigger_buffer.append(False)

    def get_status(self) -> dict:
        """获取当前控制器状态"""
        return {
            'mode': self._mode,
            'threshold': self._current_threshold,
            'adaptive': self._use_adaptive,
            'baseline_loaded': self._baseline_loaded,
            'calibration_progress': self.get_calibration_progress() if self._mode == 'calibrating' else 1.0,
            'current_action': self.actions[self.current_action_idx][0],
            'cooldown_active': (time.time() - self._last_trigger_time) < self._cooldown,
            'long_term_drift': self._drift_compensator.get_long_term_offset(),
        }

    def _execute_current_action(self):
        """执行当前选中的预设动作"""
        action = self.actions[self.current_action_idx]
        label, desc, shortcut = action
        print(f"\n  >>> 脑电触发: {label} - {desc}")
        print(f"  当前阈值: {self._current_threshold:.0f}")

        self._send_keyboard(shortcut)

    def _send_keyboard(self, shortcut: str):
        """发送键盘快捷键"""
        if not HAS_PYAUTOGUI:
            print(f"    [模拟] 按键: {shortcut}")
            print(f"    安装 pyautogui 以启用真实键盘控制: pip install pyautogui")
            return

        parts = shortcut.lower().split('+')

        try:
            if len(parts) == 1:
                pyautogui.press(parts[0])
            elif len(parts) == 2:
                pyautogui.hotkey(parts[0], parts[1])
            elif len(parts) == 3:
                pyautogui.hotkey(parts[0], parts[1], parts[2])

            print(f"    [真实] 已发送按键: {shortcut}")
        except Exception as e:
            print(f"    [错误] 按键发送失败: {e}")

    @staticmethod
    def press_key(key: str):
        """直接按键（基于输入键名）"""
        if HAS_PYAUTOGUI:
            pyautogui.press(key)

    @staticmethod
    def move_mouse(dx: int = 0, dy: int = 0):
        """移动鼠标（用专注度控制？）"""
        if HAS_PYAUTOGUI:
            pyautogui.moveRel(dx, dy, duration=0.1)

    @staticmethod
    def type_text(text: str):
        """输入文字"""
        if HAS_PYAUTOGUI:
            pyautogui.write(text, interval=0.05)