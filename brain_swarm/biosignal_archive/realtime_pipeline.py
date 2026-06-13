"""实时主控 Pipeline (v2 — 多模态集成)

核心设计:
  1. SSVEP 频域解码 (1-2秒/命令, 准确率~90%) — 主要控制通道
  2. FBCSP+LDA 运动想象 (0.5-1秒, 准确率~70%) — 备用控制通道
  3. EOG/EMG 生物信号 (实时检测, 可靠性极高) — 安全/辅助通道
  4. 试次锁定平均 (训练时使用) — 模型校准

EEG 采集 → 预处理 → 多模态融合解码 → 动作映射 → 集群控制
"""

import time
import numpy as np
from typing import Optional, Dict, Tuple
from collections import deque

from config import PipelineConfig
from eeg_processor import EEGProcessor, EEGSimulator
from brain_decoder import BaseDecoder, FBCSPDecoder, SimpleCNN, create_decoder
from ssvep_decoder import SSVEPDecoder
from biosignal import BiosignalDetector, TrialAverager
from action_mapper import ActionMapper, DroneCommand
from drone_controller import DroneSwarmController

import os


class BrainSwarmPipeline:
    """
    脑控无人机集群主控 Pipeline (v2)

    特色:
      - 多模态融合: SSVEP + EOG/EMG + 运动想象
      - 降级策略: 信号差时自动回退到 EOG/EMG 控制
      - 试次锁定训练: 用于训练 FBCSP/LDA 分类器

    用法:
        pipeline = BrainSwarmPipeline(config)
        pipeline.setup()
        pipeline.run(use_sim=True)
    """

    def __init__(self, config: PipelineConfig):
        self.config = config
        self.eeg_proc = EEGProcessor(
            n_channels=config.eeg.n_channels,
            sampling_rate=config.eeg.sampling_rate,
            enable_wavelet=True
        )
        self.decoder: Optional[BaseDecoder] = None
        self.ssvep_decoder: Optional[SSVEPDecoder] = None
        self.biosignal: Optional[BiosignalDetector] = None
        self.action_mapper: Optional[ActionMapper] = None
        self.swarm: Optional[DroneSwarmController] = None
        self.eeg_sim: Optional[EEGSimulator] = None

        # 运行状态
        self._running = False
        self._last_command_time = 0.0
        self._raw_buffer = deque(maxlen=config.eeg.sampling_rate * 5)

        # 命令平滑
        self._command_history = []
        self._smooth_count = config.smooth_count

        # 统计
        self.commands_executed = 0
        self.prediction_history = []

    def setup(self):
        """初始化所有组件"""
        print("=" * 60)
        print("  脑控无人机集群系统 v2 — 多模态融合")
        print("=" * 60)

        # 1. 构建 EEG 滤波器
        self.eeg_proc.build_filters(
            low_freq=self.config.eeg.low_freq,
            high_freq=self.config.eeg.high_freq,
            notch_freq=self.config.eeg.notch_freq
        )
        print(f"[OK] EEG 处理器 ({self.config.eeg.n_channels}通道, {self.config.eeg.sampling_rate}Hz)")

        # 2. 创建运动想象解码器 (主解码器)
        self.decoder = create_decoder(self.config.decoder)
        print(f"[OK] MI 解码器 ({self.config.decoder.model_type})")

        # 3. 创建 SSVEP 解码器 (如果启用)
        if self.config.ssvep.enable:
            self.ssvep_decoder = create_decoder(self.config.ssvep)
            n_cmds = len(self.config.ssvep.frequencies)
            print(f"[OK] SSVEP 解码器 ({n_cmds} 频率, {self.config.ssvep.window_duration}s)")

        # 4. 创建多模态生物信号检测器
        if self.config.biosignal.enable:
            self.biosignal = BiosignalDetector(
                sampling_rate=self.config.eeg.sampling_rate,
                blink_threshold=self.config.biosignal.blink_threshold,
                eye_move_threshold=self.config.biosignal.eye_move_threshold,
                jaw_clench_threshold=self.config.biosignal.jaw_clench_threshold,
            )
            print(f"[OK] 多模态检测器 (EOG眨眼+眼动 + EMG咬牙)")

        # 5. 加载预设动作
        self.action_mapper = ActionMapper(
            action_file=self.config.action_file,
            smooth_count=self.config.smooth_count
        )
        print(f"[OK] 动作映射器 ({len(self.action_mapper.actions)} 个命令)")

        # 6. 初始化无人机集群
        self.swarm = DroneSwarmController(
            n_drones=self.config.drone.n_drones,
            simulation=self.config.drone.simulation,
            default_height=self.config.drone.default_height,
            default_speed=self.config.drone.default_speed,
            safety_radius=self.config.drone.safety_radius
        )
        print(f"[OK] 无人机集群 ({self.config.drone.n_drones} 架, {'模拟' if self.config.drone.simulation else '真实'})")

        # 7. EEG 模拟器
        self.eeg_sim = EEGSimulator(
            n_channels=self.config.eeg.n_channels,
            sampling_rate=self.config.eeg.sampling_rate,
            n_classes=self.config.decoder.n_classes
        )
        print(f"\n系统就绪！\n")

    def fit_decoder(self, X: np.ndarray, y: np.ndarray, epochs: int = 50):
        """训练运动想象解码器"""
        if self.decoder is None:
            self.setup()

        print(f"训练 MI 解码器: {X.shape[0]} 样本")
        self.decoder.fit(X, y)
        print("训练完成！")

        os.makedirs(os.path.dirname(self.config.decoder.model_path), exist_ok=True)
        self.decoder.save(self.config.decoder.model_path)
        print(f"模型已保存: {self.config.decoder.model_path}")

    def process_chunk(self, raw_chunk: np.ndarray) -> Optional[DroneCommand]:
        """
        多模态融合处理一段 EEG 数据

        优先级: EOG/EMG (安全通道) > SSVEP (主控通道) > MI (备用通道)

        Args:
            raw_chunk: (n_channels, n_samples) 原始 EEG

        Returns:
            触发则返回 DroneCommand, 否则 None
        """
        # 1. 预处理
        clean = self.eeg_proc.preprocess(raw_chunk)

        # 2. EOG/EMG 检测 (最高优先级, 用于安全控制)
        if self.biosignal is not None:
            bio_result = self.biosignal.detect(raw_chunk)
            bio_type = bio_result["combined"]

            if bio_type == "jaw_clench":
                # 咬牙 → 紧急悬停 (安全通道)
                cmd = self.action_mapper.get_action_by_type("emergency_stop")
                if cmd:
                    cmd.source = "bio"
                return cmd

            elif bio_type == "eye_left" or bio_type == "eye_right":
                action = self.config.biosignal.eye_left_action if bio_type == "eye_left" else self.config.biosignal.eye_right_action
                cmd = self.action_mapper.get_action_by_type(action)
                if cmd:
                    cmd.source = "bio"
                return cmd

            elif bio_type == "blink":
                action = self.config.biosignal.blink_action
                cmd = self.action_mapper.get_action_by_type(action)
                if cmd:
                    cmd.source = "bio"
                return cmd

        # 3. SSVEP 解码 (主控通道, 如果启用)
        if self.ssvep_decoder is not None:
            cmd_id, confidence = self.ssvep_decoder.decode(clean)

            if cmd_id >= 0 and confidence >= self.config.ssvep.confidence_threshold:
                command = self.action_mapper.get_action(cmd_id)
                if command:
                    command.source = "ssvep"
                return command

        # 4. 运动想象解码 (备用通道)
        if self.decoder is not None and hasattr(self.decoder, 'predict'):
            try:
                class_id, confidence = self.decoder.predict(clean)

                # 命令平滑: 连续 N 次相同才触发
                self._command_history.append(class_id)
                if len(self._command_history) > self._smooth_count:
                    self._command_history.pop(0)

                if len(self._command_history) >= self._smooth_count:
                    # 检查是否连续相同
                    if len(set(self._command_history[-self._smooth_count:])) == 1:
                        command = self.action_mapper.map(class_id, confidence)
                        if command is not None and confidence >= 0.6:
                            command.source = "mi"
                            return command
            except RuntimeError:
                # 模型未训练
                pass

        return None

    def run(self, use_sim: bool = True, duration: float = 30.0, mode: str = "ssvep"):
        """
        运行主循环

        Args:
            use_sim: 模拟还是真实 EEG
            duration: 运行秒数
            mode: "ssvep" | "mi" | "hybrid"
        """
        self._running = True
        print(f"Pipeline 启动 ({'模拟' if use_sim else '真实'}, 模式={mode})\n")

        sampling_rate = self.config.eeg.sampling_rate
        chunk_samples = int(self.config.eeg.sampling_rate * 0.2)  # 200ms 每块
        chunk_duration = 0.2
        start_time = time.time()
        elapsed = 0.0
        ssvep_buffer = []

        try:
            while self._running and elapsed < duration:
                if use_sim:
                    sim_class = np.random.randint(0, self.config.decoder.n_classes)
                    raw_chunk = self.eeg_sim.generate(sim_class, 0.2, noise_level=0.3)
                else:
                    raw_chunk = np.zeros((self.config.eeg.n_channels, chunk_samples))

                # SSVEP 需要累积数据
                if mode == "ssvep" and self.ssvep_decoder is not None:
                    ssvep_buffer.append(raw_chunk)
                    total_samples = sum(b.shape[1] for b in ssvep_buffer)
                    ssvep_window = int(self.config.ssvep.window_duration * sampling_rate)

                    if total_samples >= ssvep_window:
                        # 拼接够 2 秒的数据
                        full = np.concatenate(ssvep_buffer, axis=1)
                        command = self.process_chunk(full)
                        ssvep_buffer = []  # 重置缓冲
                    else:
                        command = None
                else:
                    command = self.process_chunk(raw_chunk)

                # 执行命令
                if command is not None:
                    now = time.time()
                    if now - self._last_command_time >= self.config.min_command_interval:
                        result = self.swarm.execute(command)
                        self._last_command_time = now
                        self.commands_executed += 1
                        print(f"  [{self.commands_executed}] {command.source}: {result}")

                self.swarm.update(chunk_duration)
                elapsed = time.time() - start_time

                if int(elapsed) % 5 == 0 and int(elapsed) > 0:
                    if int(elapsed) != int(elapsed - chunk_duration * 2):
                        self.swarm.print_status()

                time.sleep(chunk_duration * 0.1)

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
            confidences = [p.get("confidence", 0) for p in self.prediction_history]
            print(f"  平均置信度: {np.mean(confidences):.3f}")

    def run_interactive(self):
        """交互式运行"""
        self._running = True

        print("\n交互模式：输入数字选择预设动作，输入 q 退出\n")
        actions = self.action_mapper.list_actions()
        for a in actions:
            print(f"  [{a['id']}] {a['label']} - {a['type']}")

        print("\n可用命令: 0-5 选择, 's' 状态, 'e' 紧急停止, 'b' 模拟眨眼, 'j' 模拟咬牙, 'q' 退出\n")

        try:
            while self._running:
                cmd = input(">>> ").strip().lower()

                if cmd == 'q':
                    break
                elif cmd == 's':
                    self.swarm.print_status()
                elif cmd == 'e':
                    print(self.swarm.emergency_stop())
                elif cmd == 'b':
                    print("  [模拟] 眨眼确认")
                    action = self.config.biosignal.blink_action
                    cmd = self.action_mapper.get_action_by_type(action)
                    if cmd:
                        cmd.source = "bio"
                        result = self.swarm.execute(cmd)
                        self.commands_executed += 1
                        print(f"  [{self.commands_executed}] {result}")
                elif cmd == 'j':
                    print("  [模拟] 咬牙紧急停止")
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