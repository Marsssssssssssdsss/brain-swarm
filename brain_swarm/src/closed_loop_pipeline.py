"""
闭环双向 BCI Pipeline (v3)

模式:
  [A] 侵入式仿真闭环 — 锋电位仿真 + tFUS + 沉浸感
  [B] 消费级专注度闭环 — EEG 专注度检测 + tDCS 刺激

Architecture:
                     +-----------+
                     |  大脑/皮肤  |
                     +-----+-----+
                           |
                Read path  |  Write path
                           |
            +--------------+--------------+
            |  EEG 采集    神经调控        |
            |  预处理      (tDCS/tACS)     |
            |  解码/状态检测 闭环更新参数  |
            +--------------+--------------+
"""

import time
import numpy as np
from typing import Optional

from config import PipelineConfig
from eeg_processor import EEGProcessor, EEGSimulator
from brain_decoder import create_decoder
from action_mapper import ActionMapper
from focus_detector import FocusDetector, FocusReport

from neural_sim.spike_sim import SpikeSimulator, SpikeTrainConfig
from neuromod.tfus import TFUSModulator, TFUSConfig
from neuromod.tdcs import TDCSModulator, TDCSConfig, StimMode
from experience.demo_experience import InvasiveExperience


class ClosedLoopPipeline:
    """闭环双向 BCI 主控"""

    def __init__(self, config: PipelineConfig):
        self.cfg = config

        # Read path (现有)
        self.eeg_proc = EEGProcessor(
            n_channels=config.eeg.n_channels,
            sampling_rate=config.eeg.sampling_rate,
            enable_wavelet=True
        )
        self.decoder = create_decoder(config.decoder)
        self.action_mapper = ActionMapper(
            action_file=config.action_file,
            smooth_count=config.smooth_count
        )
        self.eeg_sim = EEGSimulator(
            n_channels=config.eeg.n_channels,
            sampling_rate=config.eeg.sampling_rate,
            n_classes=config.decoder.n_classes
        )

        # Write path (新)
        self.modulator: Optional[TFUSModulator] = None
        if config.neuromod.enable:
            tfus_cfg = TFUSConfig(**config.neuromod.tfus)
            self.modulator = TFUSModulator(tfus_cfg)

        # Simulation (新)
        self.spike_sim: Optional[SpikeSimulator] = None
        if config.neural_sim.enable:
            spike_cfg = SpikeTrainConfig(
                n_neurons=config.neural_sim.n_neurons,
                sampling_rate=config.neural_sim.sampling_rate,
                duration=config.neural_sim.duration,
            )
            self.spike_sim = SpikeSimulator(spike_cfg)

        # Experience (新)
        self.experience = InvasiveExperience(enable_feedback=True)

        self._running = False
        self._command_history = []

    def run_closed_loop(self, duration: float = 30.0):
        """运行闭环读写循环"""
        self._running = True
        self.experience.print_manifesto()
        print(f"\n闭环系统启动 ({duration}秒)\n")

        start = time.time()
        loop_count = 0

        try:
            while self._running and (time.time() - start) < duration:
                # ---- Read path ----
                sim_data = self.eeg_sim.generate(0, 0.2)
                clean = self.eeg_proc.preprocess(sim_data)
                class_id, confidence = self.decoder.predict(clean)
                command = self.action_mapper.map(class_id, confidence)

                # ---- Simulation path (并行) ----
                if self.spike_sim:
                    spike_train = self.spike_sim.generate_train()
                    lfp = self.spike_sim.generate_lfp(spike_train)

                # ---- Write path ----
                if self.modulator and command:
                    modulated = self.modulator.simulate_stimulation(
                        clean, self.cfg.eeg.sampling_rate
                    )
                    update = self.modulator.closed_loop_update(
                        modulated, target_state="excite"
                    )

                # ---- Experience ----
                latency_ms = self.experience.measure_latency()
                self.experience.compute_immersion()

                if loop_count % 50 == 0:
                    print(f"  [{loop_count}] {self.experience.status_report()}")
                    if command:
                        print(f"    → 指令: {command.label} ({command.source})")

                loop_count += 1
                time.sleep(0.02)  # 50Hz 闭环更新

        except KeyboardInterrupt:
            print("\n用户中断")
        finally:
            self._shutdown()

    def _shutdown(self):
        self._running = False
        print(f"\n闭环系统停止")
        print(f"  循环次数: {len(self.experience._latencies)}")
        print(f"  平均延迟: {np.mean(self.experience._latencies) * 1000:.1f}ms")
        print(f"  最终沉浸度: {self.experience._immersion_score:.0f}/100")


class FocusLoopPipeline:
    """消费级专注度闭环 — EEG 检测 + tDCS 刺激"""

    def __init__(self, config: PipelineConfig):
        self.cfg = config
        self.eeg_proc = EEGProcessor(
            n_channels=config.eeg.n_channels,
            sampling_rate=config.eeg.sampling_rate,
            enable_wavelet=True
        )
        self.eeg_proc.build_filters()
        self.eeg_sim = EEGSimulator(
            n_channels=config.eeg.n_channels,
            sampling_rate=config.eeg.sampling_rate,
            n_classes=2
        )
        self.focus = FocusDetector(
            sampling_rate=config.eeg.sampling_rate,
            fft_window=config.focus_loop.fft_window,
            update_rate=config.focus_loop.update_rate,
            smoothing=config.focus_loop.smoothing,
        )
        tdcs_cfg = TDCSConfig(
            current_ma=1.0,
            closed_loop=True,
        )
        self.stim = TDCSModulator(tdcs_cfg)
        self._report_history = []
        self._running = False

    def run(self, duration: float = 60.0, simulate: bool = True):
        print("=" * 50)
        print("  专注度闭环系统")
        print("  纯 EEG 检测 → tDCS 反馈")
        print("=" * 50)
        print(f"  蒙太奇: {self.stim.cfg.anode} → {self.stim.cfg.cathode}")
        print(f"  最大电流: {self.stim.cfg.max_current}mA")
        print(f"  运行时长: {duration}s\n")

        self._running = True
        self.stim.start()
        start = time.time()
        loop = 0

        try:
            while self._running and (time.time() - start) < duration:
                if simulate:
                    chunk = self.eeg_sim.generate(0 if loop % 3 == 0 else 1, 0.25)
                    chunk += np.random.randn(*chunk.shape) * 0.3
                else:
                    chunk = np.zeros((self.cfg.eeg.n_channels, int(self.cfg.eeg.sampling_rate * 0.25)))

                clean = self.eeg_proc.preprocess(chunk)
                self.focus.feed(clean)
                report = self.focus.get_report()

                if report:
                    adj = self.stim.closed_loop_update(report.focus, report.relaxation)
                    self._report_history.append(report)

                    if loop % 20 == 0:
                        print(
                            f"  [{loop:3d}] 专注={report.focus:3d} "
                            f"放松={report.relaxation:3d} "
                            f"{report.state.value:12s} "
                            f"刺激={self.stim.state.current_ma:.2f}mA "
                            f"({adj['action']})"
                        )

                self.stim.step(dt=0.25)
                loop += 1
                time.sleep(0.02)

        except KeyboardInterrupt:
            print("\n用户中断")
        finally:
            self.stop()

    def stop(self):
        self._running = False
        self.stim.stop()
        if self._report_history:
            focuses = [r.focus for r in self._report_history]
            relaxes = [r.relaxation for r in self._report_history]
            print(f"\n会话统计:")
            print(f"  平均专注度: {np.mean(focuses):.0f}/100")
            print(f"  平均放松度: {np.mean(relaxes):.0f}/100")
            print(f"  刺激时长: {self.stim.state.session_time:.0f}s")
            print(f"  最大刺激: {self.stim.session_summary()['max_current']:.2f}mA")


def main():
    import sys
    config = PipelineConfig()

    if len(sys.argv) > 1 and sys.argv[1] == "focus":
        pipeline = FocusLoopPipeline(config)
        pipeline.run(duration=60.0, simulate=True)
    else:
        config.neural_sim.enable = True
        config.neuromod.enable = True
        pipeline = ClosedLoopPipeline(config)
        pipeline.run_closed_loop(duration=15.0)


if __name__ == "__main__":
    main()
