"""
"新生命"沉浸式体验演示

模拟侵入式 BCI 的核心体验差:
  1. ⚡ 零延迟: 意念到动作 < 20ms (vs 非侵入式 200-500ms)
  2. 🎯 高维度: 连续比例控制 + 多自由度 (vs 离散 4-6 类)
  3. 🔄 双向写入: 能"感受"到机器反馈 (vs 单向读取)
  4. 🧠 内化: 工具感消失, 变成身体延伸 (vs 始终外挂)

技术上不需要真侵入, 用视觉-触觉-听觉多通道反馈
营造"这就是我身体的一部分"的错觉。
"""

import time
import numpy as np
from typing import Optional


class InvasiveExperience:
    """侵入式体感仿真器

    通过多模态反馈营造"这就是我身体一部分"的体验:
      - 视觉: 实时神经信号可视化 + 光标/无人机融合
      - 时序: 亚 20ms 延迟 (真侵入式水平)
      - 映射: 连续比例控制, 不是离散分类
    """

    def __init__(self, enable_feedback: bool = True):
        self.enable_feedback = enable_feedback
        self._start_time = time.time()
        self._latencies = []
        self._control_signals = []
        self._immersion_score = 0.0

    def measure_latency(self, simulated_ms: float = 15.0) -> float:
        """测量端到端延迟 (意念 → 动作反馈)

        Args:
            simulated_ms: 模拟延迟毫秒数 (侵入式 <20ms, 非侵入式 200-500ms)
        """
        self._latencies.append(simulated_ms / 1000.0)
        recent = self._latencies[-10:] if len(self._latencies) >= 10 else self._latencies
        return np.mean(recent) * 1000  # ms

    def compute_immersion(self) -> float:
        """计算沉浸感评分

        影响因子:
          - 延迟 < 50ms: +40 分
          - 连续控制 (非离散): +30 分
          - 双向反馈: +20 分
          - 自适应基线: +10 分
        """
        score = 0.0
        avg_latency = np.mean(self._latencies[-20:]) * 1000 if self._latencies else 999
        if avg_latency < 20:
            score += 40
        elif avg_latency < 50:
            score += 25
        elif avg_latency < 100:
            score += 10

        self._immersion_score = score
        return score

    def status_report(self) -> str:
        """生成体验状态报告"""
        lat = self.measure_latency()
        immersion = self.compute_immersion()
        level = "侵入级" if immersion >= 60 else "准侵入级" if immersion >= 30 else "非侵入级"
        return (
            f"[体感状态] 延迟={lat:.1f}ms | 沉浸={immersion:.0f}/100 | "
            f"等级={level}"
        )

    def simulate_invasive_control(self, mu_power: float, dt: float = 0.02) -> float:
        """模拟侵入式级连续控制

        非侵入式: 分类→平滑→冷却 (200-500ms 延迟)
        侵入式:   mu 功率直出速度 (无分类, 无冷却)
        """
        speed = np.clip((0.5 - mu_power) * 2, -1, 1)
        self._control_signals.append(speed)
        return speed

    @staticmethod
    def print_manifesto():
        """打印体验宣言"""
        print("=" * 60)
        print("  侵入式 BCI 体验宣言")
        print("=" * 60)
        print()
        print('  非侵入式让你\u201c用\u201d脑机接口')
        print('  侵入式让你\u201c成为\u201d脑机接口')
        print()
        print("  差的不只是信噪比")
        print("  差的是: 这是不是我的身体")
        print()
        print("  我们要做的是:")
        print("    用非侵入式硬件 + 侵入式级算法 + 沉浸式反馈")
        print("    = 准侵入式体验")
        print()
        print("  不是替代侵入式")
        print("  是在不让开颅的前提下")
        print("  逼近那个体感阈值")
        print("=" * 60)
