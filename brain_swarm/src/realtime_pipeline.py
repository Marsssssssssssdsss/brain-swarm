"""实时主控 Pipeline

脑控无人机集群系统的核心循环：
EEG 采集 → 预处理 → 解码 → 动作映射 → 集群控制
"""

import time
import numpy as np
from typing import Optional
from collections import deque

from config import PipelineConfig
from eeg_processor import EEGProcessor, EEGSimulator
from brain_decoder import BaseDecoder, FBCSPDecoder, SimpleCNN, create_decoder
from action_mapper import ActionMapper, DroneCommand
from drone_controller import DroneSwarmController


class BrainSwarmPipeline:
    """
    脑控无人机集群主控 Pipeline

    用法:
        pipeline = BrainSwarmPipeline(config)
        pipeline.setup()           # 初始化
        pipeline.run(use_sim=True) # 运行（模拟模式）
    """

    def __init__(self, config: PipelineConfig):
        self.config = config
        self.eeg_proc = EEGProcessor(
            n_channels=config.eeg.n_channels,
            sampling_rate=config.eeg.sampling_rate
        )
        self.decoder: Optional[BaseDecoder] = None
        self.action_mapper: Optional[ActionMapper] = None
        self.swarm: Optional[DroneSwarmController] = None
        self.eeg_sim: Optional[EEGSimulator] = None

        # 运行状态
        self._running = False
        self._last_command_time = 0.0
        self._raw_buffer = deque(maxlen=config.eeg.sampling_rate * 5)  # 5 秒缓冲

        # 统计
        self.commands_executed = 0
        self.prediction_history = []

    def setup(self):
        """初始化所有组件"""
        print("=" * 50)
        print("  脑控无人机集群系统 - BrainSwarm")
        print("=" * 50)

        # 1. 构建 EEG 滤波器
        self.eeg_proc.build_filters(
            low_freq=self.config.eeg.low_freq,
            high_freq=self.config.eeg.high_freq,
            notch_freq=self.config.eeg.notch_freq
        )
        print(f"[OK] EEG 处理器就绪 ({self.config.eeg.n_channels}通道, {self.config.eeg.sampling_rate}Hz)")

        # 2. 创建解码器
        self.decoder = create_decoder(self.config.decoder)
        print(f"[OK] 解码器就绪 ({self.config.decoder.model_type})")

        # 3. 加载预设动作
        self.action_mapper = ActionMapper(
            action_file=self.config.action_file,
            smooth_count=self.config.smooth_count
        )
        print(f"[OK] 动作映射器就绪 ({len(self.action_mapper.actions)} 个预设动作)")

        # 4. 初始化无人机集群
        self.swarm = DroneSwarmController(
            n_drones=self.config.drone.n_drones,
            simulation=self.config.drone.simulation,
            default_height=self.config.drone.default_height,
            default_speed=self.config.drone.default_speed,
            safety_radius=self.config.drone.safety_radius
        )
        print(f"[OK] 无人机集群就绪 ({self.config.drone.n_drones} 架, {'模拟' if self.config.drone.simulation else '真实'}模式)")

        # 5. EEG 模拟器
        self.eeg_sim = EEGSimulator(
            n_channels=self.config.eeg.n_channels,
            sampling_rate=self.config.eeg.sampling_rate,
            n_classes=self.config.decoder.n_classes
        )

        print(f"\n系统就绪！等待指令...\n")

    def fit_decoder(self, X: np.ndarray, y: np.ndarray, epochs: int = 50):
        """训练解码器"""
        if self.decoder is None:
            self.setup()

        print(f"训练解码器: {X.shape[0]} 个样本, {X.shape[1]} 通道, {X.shape[2]} 采样点")
        self.decoder.fit(X, y)
        print("训练完成！")

        # 保存模型
        os.makedirs(os.path.dirname(self.config.decoder.model_path), exist_ok=True)
        self.decoder.save(self.config.decoder.model_path)
        print(f"模型已保存至: {self.config.decoder.model_path}")

    def process_chunk(self, raw_chunk: np.ndarray) -> Optional[DroneCommand]:
        """
        处理一段 EEG 数据

        Args:
            raw_chunk: (n_channels, n_samples) 原始 EEG 数据

        Returns:
            如果触发动作，返回 DroneCommand；否则 None
        """
        # 1. 预处理
        clean = self.eeg_proc.preprocess(raw_chunk)

        # 2. 解码
        if self.decoder is None:
            return None

        class_id, confidence = self.decoder.predict(clean)

        self.prediction_history.append({
            "time": time.time(),
            "class_id": class_id,
            "confidence": confidence
        })

        # 3. 动作映射
        command = self.action_mapper.map(class_id, confidence)

        return command

    def run(self, use_sim: bool = True, duration: float = 30.0):
        """
        运行主循环

        Args:
            use_sim: 是否使用模拟 EEG 数据
            duration: 运行时长（秒）
        """
        self._running = True
        print(f"Pipeline 启动 ({'模拟' if use_sim else '真实'}模式, {duration}秒)\n")

        # 采样参数
        sampling_rate = self.config.eeg.sampling_rate
        chunk_samples = int(self.config.decoder.window_size * sampling_rate)
        chunk_duration = self.config.decoder.window_size

        start_time = time.time()
        elapsed = 0.0

        try:
            while self._running and elapsed < duration:
                # 获取 EEG 数据
                if use_sim:
                    # 模拟：随机选择一个动作类别
                    sim_class = np.random.randint(0, self.config.decoder.n_classes)
                    raw_chunk = self.eeg_sim.generate(
                        sim_class, chunk_duration, noise_level=0.3
                    )
                else:
                    # TODO: 从真实 EEG 设备读取
                    raw_chunk = np.zeros((self.config.eeg.n_channels, chunk_samples))

                # 处理
                command = self.process_chunk(raw_chunk)

                # 执行
                if command is not None:
                    now = time.time()
                    # 检查最小间隔
                    if now - self._last_command_time >= self.config.min_command_interval:
                        result = self.swarm.execute(command)
                        self._last_command_time = now
                        self.commands_executed += 1
                        print(f"  [{self.commands_executed}] {result}")

                # 更新无人机状态
                self.swarm.update(chunk_duration)

                # 状态显示
                elapsed = time.time() - start_time
                if int(elapsed) % 5 == 0 and int(elapsed) > 0 and int(elapsed) != int(elapsed - chunk_duration * 2):
                    self.swarm.print_status()

                time.sleep(chunk_duration * 0.1)  # 模拟实时处理

        except KeyboardInterrupt:
            print("\n用户中断")

        finally:
            self.stop()

    def stop(self):
        """停止 Pipeline"""
        self._running = False
        if self.swarm:
            self.swarm.emergency_stop()
            self.swarm.print_status()

        print(f"\nPipeline 已停止")
        print(f"  执行指令数: {self.commands_executed}")
        if self.prediction_history:
            confidences = [p["confidence"] for p in self.prediction_history]
            print(f"  平均置信度: {np.mean(confidences):.3f}")

    def run_interactive(self):
        """交互式运行：监听键盘输入模拟脑电指令"""
        self._running = True

        print("\n交互模式：输入数字选择预设动作，输入 q 退出\n")
        actions = self.action_mapper.list_actions()
        for a in actions:
            print(f"  [{a['id']}] {a['label']} - {a['type']}")

        print("\n可用命令: 0-5 选择动作, 's' 查看状态, 'e' 紧急停止, 'q' 退出\n")

        try:
            while self._running:
                cmd = input(">>> ").strip().lower()

                if cmd == 'q':
                    break
                elif cmd == 's':
                    self.swarm.print_status()
                elif cmd == 'e':
                    print(self.swarm.emergency_stop())
                elif cmd.isdigit():
                    class_id = int(cmd)
                    action = self.action_mapper.get_action(class_id)
                    if action:
                        result = self.swarm.execute(action)
                        self.commands_executed += 1
                        print(f"  [{self.commands_executed}] {result}")
                    else:
                        print(f"  无效动作: {class_id}")
                else:
                    print("  未知命令")

                self.swarm.update(dt=0.5)

        except KeyboardInterrupt:
            pass

        finally:
            self.stop()


import os