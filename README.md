# Brain-Swarm 脑控无人机集群系统

脑机接口(BCI)脑控无人机集群系统 —— 基于 EEG 信号的实时采集、处理、解码与无人机控制。

## 项目结构

```
brain_swarm/
├── src/                    # 核心源码
│   ├── tgam_reader.py      # TGAM EEG 芯片数据读取
│   ├── signal_enhancer.py  # 信号增强（基线校准、自适应阈值、伪迹检测）
│   ├── eeg_processor.py    # EEG 信号处理（滤波、特征提取）
│   ├── brain_decoder.py    # 脑电解码器
│   ├── action_mapper.py    # 动作映射
│   ├── pc_controller.py    # 电脑控制（键盘/鼠标）
│   ├── drone_controller.py # 无人机控制
│   ├── realtime_pipeline.py# 实时处理流水线
│   ├── config.py           # 配置文件
│   └── preset_actions.json # 预设动作配置
├── docs/                   # 技术文档
├── demo.py                 # 主演示程序
├── tgam_demo.py            # TGAM 芯片演示
└── requirements.txt        # 依赖
```

## 快速开始

1. 安装依赖：
```bash
pip install -r requirements.txt
```

2. 连接 TGAM EEG 芯片，运行：
```bash
python brain_swarm/demo.py
```

## 技术栈

- **硬件**: TGAM EEG 芯片 (NeuroSky)
- **信号处理**: MNE-Python, SciPy, NumPy
- **机器学习**: PyTorch, scikit-learn
- **控制**: pyautogui (PC控制), pyzmq (实时通信)