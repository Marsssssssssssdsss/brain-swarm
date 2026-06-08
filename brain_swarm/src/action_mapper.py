"""预设动作映射系统

将脑电分类结果映射为无人机集群指令
"""

import json
import os
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field


@dataclass
class DroneCommand:
    """无人机集群指令"""
    action_id: str
    action_label: str
    behavior_type: str
    params: Dict[str, Any] = field(default_factory=dict)


class ActionMapper:
    """
    预设动作映射器

    职责：
    1. 加载预设动作定义
    2. 将解码器输出的 class_id 映射为具体的无人机集群指令
    3. 支持平滑滤波（连续 N 次预测相同才触发）
    """

    def __init__(self, action_file: str, smooth_count: int = 3):
        self.smooth_count = smooth_count
        self.actions: Dict[int, DroneCommand] = {}
        self._prediction_buffer: List[int] = []
        self._current_action: Optional[int] = None
        self._load_actions(action_file)

    def _load_actions(self, action_file: str):
        """加载预设动作配置"""
        if not os.path.exists(action_file):
            raise FileNotFoundError(f"动作配置文件不存在: {action_file}")

        with open(action_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        for action in data.get("actions", []):
            cmd = DroneCommand(
                action_id=action["id"],
                action_label=action["label"],
                behavior_type=action["drone_behavior"]["type"],
                params={k: v for k, v in action["drone_behavior"].items() if k != "type"}
            )
            self.actions[action["class_index"]] = cmd

        print(f"已加载 {len(self.actions)} 个预设动作: {[a.action_label for a in self.actions.values()]}")

    def map(self, class_id: int, confidence: float) -> Optional[DroneCommand]:
        """
        将解码结果映射为指令

        使用平滑滤波：连续 smooth_count 次预测相同才触发

        Args:
            class_id: 解码器预测的类别
            confidence: 置信度

        Returns:
            如果触发，返回 DroneCommand；否则返回 None
        """
        self._prediction_buffer.append(class_id)

        # 保持缓冲区大小
        if len(self._prediction_buffer) > self.smooth_count:
            self._prediction_buffer = self._prediction_buffer[-self.smooth_count:]

        # 检查是否连续 N 次预测相同
        if len(self._prediction_buffer) < self.smooth_count:
            return None

        if all(p == class_id for p in self._prediction_buffer):
            # 避免重复触发同一动作
            if class_id == self._current_action:
                return None

            self._current_action = class_id
            if class_id in self.actions:
                cmd = self.actions[class_id]
                return cmd

        return None

    def reset_buffer(self):
        """重置缓冲区"""
        self._prediction_buffer = []
        self._current_action = None

    def get_action(self, class_id: int) -> Optional[DroneCommand]:
        """直接获取动作（不经过平滑）"""
        return self.actions.get(class_id)

    def list_actions(self) -> List[Dict]:
        """列出所有可用动作"""
        return [
            {
                "id": cmd.action_id,
                "label": cmd.action_label,
                "type": cmd.behavior_type,
                "params": cmd.params
            }
            for cmd in self.actions.values()
        ]