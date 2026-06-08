"""无人机集群控制接口

支持模拟模式（无硬件）和真实模式（Crazyflie / MAVLink）
"""

import time
import math
import numpy as np
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

from action_mapper import DroneCommand


@dataclass
class DroneState:
    """单架无人机状态"""
    drone_id: int
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    vz: float = 0.0
    yaw: float = 0.0
    battery: float = 100.0
    armed: bool = False


class DroneSwarmController:
    """
    无人机集群控制器

    职责：
    1. 管理多架无人机状态
    2. 执行预设动作（散开、聚合、跟随等）
    3. 模拟模式下用物理模型更新位置
    4. 真实模式下通过 API 发送指令
    """

    def __init__(
        self,
        n_drones: int,
        simulation: bool = True,
        default_height: float = 5.0,
        default_speed: float = 2.0,
        safety_radius: float = 5.0
    ):
        self.n_drones = n_drones
        self.simulation = simulation
        self.default_height = default_height
        self.default_speed = default_speed
        self.safety_radius = safety_radius

        self.drones: List[DroneState] = []
        self._init_drones()

        self._current_behavior: Optional[str] = None
        self._behavior_params: Dict = {}
        self._behavior_start_time: float = 0.0

        # 巡逻路线（默认圆形）
        self._patrol_route = self._generate_patrol_route(radius=10.0, n_points=8)

        # 起飞点
        self._home_position = (0.0, 0.0, 0.0)

    def _init_drones(self):
        """初始化所有无人机"""
        # 初始位置：排成一行
        for i in range(self.n_drones):
            state = DroneState(
                drone_id=i,
                x=i * 2.0 - self.n_drones,
                y=0.0,
                z=0.0,
                armed=True
            )
            self.drones.append(state)

    def _generate_patrol_route(self, radius: float, n_points: int) -> List[Tuple[float, float]]:
        """生成圆形巡逻路线"""
        route = []
        for i in range(n_points):
            angle = 2 * math.pi * i / n_points
            route.append((radius * math.cos(angle), radius * math.sin(angle)))
        return route

    def execute(self, command: DroneCommand) -> str:
        """
        执行预设动作

        Args:
            command: 预设动作指令

        Returns:
            String: 执行状态描述
        """
        behavior_type = command.behavior_type
        params = command.params

        self._current_behavior = behavior_type
        self._behavior_params = params
        self._behavior_start_time = time.time()

        if behavior_type == "scatter":
            return self._do_scatter(params)
        elif behavior_type == "assemble":
            return self._do_assemble(params)
        elif behavior_type == "follow":
            return self._do_follow(params)
        elif behavior_type == "hover":
            return self._do_hover(params)
        elif behavior_type == "return_home":
            return self._do_return_home(params)
        elif behavior_type == "patrol":
            return self._do_patrol(params)
        else:
            return f"未知动作类型: {behavior_type}"

    def _do_scatter(self, params: Dict) -> str:
        """散开：从中心向四周辐射"""
        spacing = params.get("spacing", 10.0)
        height = params.get("height", self.default_height)

        center_x = sum(d.x for d in self.drones) / self.n_drones
        center_y = sum(d.y for d in self.drones) / self.n_drones

        for i, drone in enumerate(self.drones):
            angle = 2 * math.pi * i / self.n_drones
            drone.x = center_x + spacing * math.cos(angle)
            drone.y = center_y + spacing * math.sin(angle)
            drone.z = height

        return f"散开: {self.n_drones} 架无人机间距 {spacing}m 辐射展开"

    def _do_assemble(self, params: Dict) -> str:
        """聚合：向中心收拢"""
        spacing = params.get("spacing", 2.0)
        height = params.get("height", self.default_height)

        center_x = sum(d.x for d in self.drones) / self.n_drones
        center_y = sum(d.y for d in self.drones) / self.n_drones

        for i, drone in enumerate(self.drones):
            angle = 2 * math.pi * i / self.n_drones
            drone.x = center_x + spacing * math.cos(angle)
            drone.y = center_y + spacing * math.sin(angle)
            drone.z = height

        return f"聚合: {self.n_drones} 架无人机收拢至间距 {spacing}m"

    def _do_follow(self, params: Dict) -> str:
        """跟随：保持编队向前移动"""
        formation = params.get("formation", "triangle")
        height = params.get("height", self.default_height)
        speed = params.get("speed", self.default_speed)

        if formation == "triangle":
            # 三角队形，领头在前面
            offsets = [(0, 0)]
            row = 1
            for i in range(1, self.n_drones):
                if i == 1:
                    offsets.append((-2, -3))
                elif i == 2:
                    offsets.append((2, -3))
                elif i == 3:
                    offsets.append((-4, -6))
                elif i == 4:
                    offsets.append((0, -6))
                elif i == 5:
                    offsets.append((4, -6))
                else:
                    offsets.append((0, -3 * (i - 2)))

            for i, drone in enumerate(self.drones):
                drone.x += offsets[i][0] if i < len(offsets) else 0
                drone.y += offsets[i][1] if i < len(offsets) else 0
                drone.z = height

        return f"跟随: {formation} 编队，速度 {speed}m/s"

    def _do_hover(self, params: Dict) -> str:
        """悬停"""
        height = params.get("height", self.default_height)

        for drone in self.drones:
            drone.z = height
            drone.vx = 0.0
            drone.vy = 0.0
            drone.vz = 0.0

        return f"悬停: 高度 {height}m"

    def _do_return_home(self, params: Dict) -> str:
        """返航"""
        height = params.get("height", self.default_height)

        home_x, home_y, home_z = self._home_position
        for drone in self.drones:
            drone.x = home_x
            drone.y = home_y
            drone.z = height

        return f"返航: 返回起飞点 (高度 {height}m)"

    def _do_patrol(self, params: Dict) -> str:
        """巡逻"""
        height = params.get("height", self.default_height)

        for i, drone in enumerate(self.drones):
            # 每架无人机从巡逻路线不同位置开始
            offset = i * len(self._patrol_route) // self.n_drones
            pt = self._patrol_route[offset % len(self._patrol_route)]
            drone.x = pt[0]
            drone.y = pt[1]
            drone.z = height

        return f"巡逻: 沿预设路线 {self.n_drones} 架无人机参与"

    def update(self, dt: float = 0.1):
        """更新无人机状态（模拟模式）"""
        if not self.simulation:
            return

        if self._current_behavior == "follow":
            speed = self._behavior_params.get("speed", self.default_speed)
            for drone in self.drones:
                drone.y += speed * dt

        elif self._current_behavior == "patrol":
            # 圆形巡逻
            elapsed = time.time() - self._behavior_start_time
            speed = self._behavior_params.get("speed", self.default_speed)
            angular_speed = speed / 10.0  # 角速度

            for i, drone in enumerate(self.drones):
                angle = elapsed * angular_speed + 2 * math.pi * i / self.n_drones
                radius = 10.0
                drone.x = radius * math.cos(angle)
                drone.y = radius * math.sin(angle)

    def get_swarm_state(self) -> Dict:
        """获取集群状态"""
        positions = [
            {"id": d.drone_id, "x": d.x, "y": d.y, "z": d.z,
             "armed": d.armed, "battery": d.battery}
            for d in self.drones
        ]

        return {
            "behavior": self._current_behavior,
            "drones": positions,
            "elapsed": time.time() - self._behavior_start_time if self._current_behavior else 0
        }

    def emergency_stop(self):
        """紧急停止所有无人机"""
        self._current_behavior = "hover"
        for drone in self.drones:
            drone.vx = 0.0
            drone.vy = 0.0
            drone.vz = 0.0
        return "紧急停止！所有无人机悬停"

    def print_status(self):
        """打印集群状态"""
        state = self.get_swarm_state()
        print(f"\n=== 集群状态 ({state['behavior'] or '空闲'}) ===")
        for d in state["drones"]:
            print(f"  无人机 {d['id']}: ({d['x']:.1f}, {d['y']:.1f}, {d['z']:.1f})m 电池:{d['battery']:.0f}%")