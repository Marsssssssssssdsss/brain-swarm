"""脑控无人机集群 - 演示脚本

演示完整流程：
1. 生成模拟 EEG 数据
2. 训练解码器
3. 运行实时 Pipeline
4. 交互模式
"""

import sys
import os

# 加载项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.config import PipelineConfig, EEGConfig, DecoderConfig, DroneConfig
from src.eeg_processor import EEGSimulator, EEGProcessor
from src.realtime_pipeline import BrainSwarmPipeline


def demo_training():
    """演示 1：训练解码器"""
    print("=" * 50)
    print("  演示 1：训练脑信号解码器")
    print("=" * 50)

    config = PipelineConfig()
    pipeline = BrainSwarmPipeline(config)
    pipeline.setup()

    # 生成模拟训练数据：每类 100 个样本
    n_samples_per_class = 100
    n_classes = 6
    sampling_rate = config.eeg.sampling_rate
    window_size = config.decoder.window_size
    n_samples_per_window = int(window_size * sampling_rate)

    sim = EEGSimulator(
        n_channels=config.eeg.n_channels,
        sampling_rate=sampling_rate,
        n_classes=n_classes
    )

    X = []
    y = []
    processor = EEGProcessor(n_channels=config.eeg.n_channels, sampling_rate=sampling_rate)
    processor.build_filters()

    print(f"生成训练数据: {n_samples_per_class} 样本/类 × {n_classes} 类")

    for class_id in range(n_classes):
        for _ in range(n_samples_per_class):
            raw = sim.generate(class_id, duration=window_size, noise_level=0.3)
            clean = processor.preprocess(raw)
            X.append(clean)
            y.append(class_id)

    X = np.array(X)
    y = np.array(y)

    print(f"总数据量: {X.shape}")

    # 训练
    pipeline.fit_decoder(X, y, epochs=30)

    # 验证
    correct = 0
    n_test = 50
    for class_id in range(n_classes):
        for _ in range(n_test):
            raw = sim.generate(class_id, duration=window_size, noise_level=0.4)
            clean = processor.preprocess(raw)
            pred, conf = pipeline.decoder.predict(clean)
            if pred == class_id:
                correct += 1

    accuracy = correct / (n_classes * n_test) * 100
    print(f"\n验证准确率: {accuracy:.1f}% ({correct}/{n_classes * n_test})")

    return pipeline


def demo_pipeline():
    """演示 2：运行实时 Pipeline"""
    print("\n" + "=" * 50)
    print("  演示 2：实时脑控 Pipeline")
    print("=" * 50)

    config = PipelineConfig()
    pipeline = BrainSwarmPipeline(config)
    pipeline.setup()

    # 快速训练
    import numpy as np
    from src.eeg_processor import EEGSimulator, EEGProcessor

    sim = EEGSimulator(
        n_channels=config.eeg.n_channels,
        sampling_rate=config.eeg.sampling_rate,
        n_classes=config.decoder.n_classes
    )

    X, y = [], []
    n_samples_per_class = 50
    window_size = config.decoder.window_size
    n_samples_per_window = int(window_size * config.eeg.sampling_rate)

    processor = EEGProcessor(n_channels=config.eeg.n_channels, sampling_rate=config.eeg.sampling_rate)
    processor.build_filters()

    for class_id in range(config.decoder.n_classes):
        for _ in range(n_samples_per_class):
            raw = sim.generate(class_id, duration=window_size, noise_level=0.3)
            clean = processor.preprocess(raw)
            X.append(clean)
            y.append(class_id)

    pipeline.fit_decoder(np.array(X), np.array(y), epochs=20)

    # 运行
    pipeline.run(use_sim=True, duration=15.0)


def demo_interactive():
    """演示 3：交互模式"""
    print("\n" + "=" * 50)
    print("  演示 3：交互模式")
    print("=" * 50)

    config = PipelineConfig()
    pipeline = BrainSwarmPipeline(config)
    pipeline.setup()
    pipeline.run_interactive()


if __name__ == "__main__":
    import numpy as np

    print("脑控无人机集群 - BrainSwarm 演示\n")
    print("选择一个演示：")
    print("  1. 训练解码器 + 验证")
    print("  2. 实时 Pipeline（模拟数据）")
    print("  3. 交互模式（键盘模拟脑电）")
    print("  4. 全部运行")

    choice = input("\n请输入选项 (1-4): ").strip()

    if choice == '1':
        demo_training()
    elif choice == '2':
        demo_pipeline()
    elif choice == '3':
        demo_interactive()
    elif choice == '4':
        demo_training()
        demo_pipeline()
        demo_interactive()
    else:
        print("无效选项")