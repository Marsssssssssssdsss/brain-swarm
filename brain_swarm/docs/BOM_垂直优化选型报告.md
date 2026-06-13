# BOM: 垂直优化选型报告 — 消费级闭环比脑电刺激器

> 产品定位：纯 EEG 读 → 实时专注/放松检测 → tDCS/tACS 闭环写
> 目标售价 $249 (3× BOM)
> 竞品对位：Muse 2 ($249 只读) + Flow FL-100 ($459 开环写) + OpenBCI ($349 开发板)

---

## 1. EEG 模拟前端 (读) — ADS1299

| 参数 | 值 |
|------|-----|
| 选型 | ADS1299IPAGR (TI) |
| 通道数 | 8 |
| 分辨率 | 24-bit ΔΣ ADC |
| 输入噪声 | 1.0 μVpp (0.5-100Hz) |
| CMRR | -115 dB |
| 采样率 | 250 SPS (消费级够用) |
| 功耗 | 5 mW/ch (全部 8ch 约 40 mW) |
| 参考设计 | OpenBCI Cyton, Cerelog ESP-EEG |
| BOM | ~$25 |
| 采购 | LCSC C476817 |

**为什么是它：** 2025-2026 年新发表的 EEG 系统论文中 >70% 仍用 ADS1299。它不是最新的，但它是**噪声最低、通道最多、生态最好**的。MAX30001 功耗更低但只有单通道，不适合多通道心境状态检测。

---

## 2. 主控 MCU (算力+无线) — nRF5340 + ESP32-S3

### 主方案：双芯异构架构

```
┌─────────────────────────────────────────────────┐
│                  nRF5340 (低功耗域)               │
│  Cortex-M33 @128MHz (应用核)                     │
│  ├─ 传感器管理：ADS1299 SPI 读取                 │
│  ├─ BLE 5.4 手机通信                             │
│  ├─ 实时 FFT + 频带功率计算 (CMSIS-DSP)           │
│  └─ tDCS 电流控制 (DAC SPI)                      │
│  Cortex-M33 @64MHz (网络核)                       │
│  └─ BLE 协议栈隔离                               │
└─────────────────────────────────────────────────┘
                      ↑ SPI/I2C ↓
┌─────────────────────────────────────────────────┐
│                ESP32-S3 (高性能域)                │
│  Xtensa LX7 @240MHz                             │
│  ├─ TensorFlow Lite Micro 推理 (FocusDetector)  │
│  ├─ FFT + 模型推理 (向量扩展指令)                  │
│  ├─ WiFi OTA 固件更新 + 云端数据同步               │
│  └─ 大缓冲区 (16MB Flash, 8MB PSRAM)             │
└─────────────────────────────────────────────────┘
```

| 参数 | nRF5340 | ESP32-S3 |
|------|---------|----------|
| 核心 | Cortex-M33 双核 | Xtensa LX7 双核 |
| 频率 | 128MHz + 64MHz | 240MHz |
| Flash/RAM | 1MB / 512KB | 16MB / 8MB (PSRAM) |
| 无线 | BLE 5.4 (LE Audio, AoA/AoD) | WiFi 4 + BLE 5.0 |
| TFLite Micro | 支持 (CMSIS-NN) | 支持 (向量扩展) |
| 1D-CNN 推理 | ~100ms | <50ms |
| 休眠功耗 | 1 μA | 5 μA |
| 活跃功耗 | 50 μA/MHz | 110 μA/MHz |
| 安全 | CryptoCell-312, PSA L2 | 硬件加密加速器 |
| BOM | ~$5 | ~$2.80 |
| 合计 | **~$10** (含 Flash/PSRAM) | |

**为什么双芯：** nRF5340 负责"时刻在线"的低功耗传感器管理 + BLE 连接（重点是它的蓝牙 LE Audio 和极低功耗）。ESP32-S3 负责"需要时唤醒"的 AI 推理 + WiFi（重点是它的算力、生态、大内存）。单芯方案（单用 nRF5340 或 ESP32-S3）都会有明显短板。

---

## 3. tDCS/tACS 刺激器 (写) — 精密 DAC + Howland 恒流源

```
                    ┌──────────────────────┐
                    │    DAC8562 (TI)       │
                    │  16bit, 双通道, ±0.1% │
                    │  SPI 接口, 2.7-5.5V   │
                    └──────┬───────────────┘
                           │ Vout
                    ┌──────┴───────────────┐
                    │  Howland 恒流源       │
                    │  精密运放: OPA2189    │
                    │  电阻 0.1% 匹配        │
                    │  输出: ±2mA, 0.1μA 步进│
                    │  带宽: DC-1kHz(可tACS) │
                    └──────┬───────────────┘
                           │ 电极
                    ┌──────┴───────────────┐
                    │  安全隔离层           │
                    │  ISO7740 数字隔离     │
                    │  光耦 + 过流保护      │
                    │  RELAX 阻抗监测       │
                    └──────────────────────┘
```

| 参数 | 值 |
|------|-----|
| DAC | DAC8562 (TI, 16bit, 2ch, ±0.1% INL) |
| 运放 | OPA2189 (TI, 0.1μV/°C 漂移, 斩波稳零) |
| 电流范围 | 0 ~ ±2 mA (可编程) |
| 分辨率 | ~0.1 μA (16bit over ±2mA) |
| 隔离 | ISO7740 数字隔离 + 过流保护 PTC |
| 阻抗监测 | RELAX 方法 (100nA @ 100Hz 测试信号) |
| 支持刺激 | tDCS (DC), tACS (AC to 1kHz), tRNS (噪声) |
| BOM | ~$8 |

**参考设计：**
- Flow FL-100 (FDA cleared, 2024) — 同样 Howland 架构
- Soterix Medical 1x1 tDCS — 临床金标，恒流源 + RELAX
- Neuroelectrics StarStim — 多通道可编程刺激

**安全设计要点：**
1. **软件限流**：DAC 输出在固件层面限制最大值 (±2mA)
2. **硬件限流**：Howland 运放供电限幅，输出不可能超过 ±2.5mA
3. **过流保护**：PTC 自恢复保险丝 + 串联 22kΩ 限流电阻
4. **DC 阻断**：输出串联 1μF 电容（tACS 模式用）
5. **阻抗监测**：每次刺激前自动检测电极接触阻抗，>20kΩ 时禁止启动

---

## 4. 干电极 — 定制梳齿 Ag-AgCl

### 电极布局

```
          ┌─────────────────────────────────────┐
          │             额头                       │
          │   Fp1          Fp2                    │
          │   (专注)       (专注)                  │
          │      ┌────────────────┐               │
          │      │  刺激电极 (F3)   │  ← 阳极 tDCS │
          │      │  (阳极)         │               │
          │      └────────────────┘               │
          │                                       │
          │      运动皮层区域                       │
          │   C3              C4                   │
          │   (mu-备用)      (mu-备用)             │
          │                                       │
          │  ┌──────┐         ┌─────────┐          │
          │  │ A1 (参考)│      │ Fz (地) │          │
          │  └──────┘         └─────────┘          │
          │   耳夹              额中                │
          │                                       │
          │   ┌─────────────────────────┐          │
          │   │ 刺激回流电极 (Fp2)      │  ← 阴极  │
          │   │ (阴极)                  │          │
          └──────────────────────────────────────┘
```

### 电极方案对比

| 方案 | 阻抗 | 舒适度 | 寿命 | 成本 | 适合我们？ |
|------|------|--------|------|------|-----------|
| OpenBCI 梳齿 Ag-AgCl | ~50kΩ | 一般 | ~10次 | $1.67/个 | ✅ 原型阶段 |
| 凝胶电极 (Ambu) | <10kΩ | 差(需要洗头) | 1次 | $0.5/次 | ❌ 消费级不行 |
| CNT-PDMS 柔性 (2025论文) | ~30kΩ | 优 | >100次 | ~$3/个 | ⭐ 下一代 |
| 干式弹簧针 (Pogo pin) | >100kΩ | 差 | 长 | $0.6/个 | ❌ 阻抗太高 |
| 织物电极 (Muse 2) | ~80kΩ | 优 | 长 | ~$2/个 | ✅ 目标方案 |

**原型阶段 (Phase 0-1)：** 采购 OpenBCI 梳齿电极 + 导电耳夹
**量产阶段 (Phase 2+)：** 定制 CNT-PDMS 柔性干电极 + 织物头带一体化

### 通道配置

| 通道 | 位置 | 用途 | 必选？ |
|------|------|------|--------|
| CH1 | Fp1 | 专注/放松核心通道 (β/θ 比) | ✅ |
| CH2 | Fp2 | 专注/放松 + tDCS 阴极 | ✅ |
| CH3 | C3 | mu 节律控制 (备用) | ❌ |
| CH4 | C4 | mu 节律控制 (备用) | ❌ |
| REF | A1 (耳夹) | 参考电位 | ✅ |
| GND | Fz | 地 | ✅ |
| Stim+ | F3 (梳齿) | tDCS 阳极 | ✅ |
| Stim- | Fp2 (梳齿) | tDCS 阴极 | ✅ |

**电极总 BOM：~$10（原型）→ ~$5（量产）**

---

## 5. 边缘 AI — TensorFlow Lite Micro + CMSIS-NN

| 组件 | 详情 |
|------|------|
| 框架 | TensorFlow Lite Micro v2.17+ |
| DSP 库 | CMSIS-DSP v5.8 (FFT, 滤波) |
| NN 加速 | CMSIS-NN v5.8 (INT8 卷积优化) |
| 推理延迟 | <50ms (ESP32-S3) / ~100ms (nRF5340) |
| 模型大小 | ~4KB (量化 INT8 FocusDetector) |
| RAM 需求 | ~8KB 推理缓冲区 |

**模型架构：**
```
Input (4ch × 1s window @ 250Hz = 1000 features)
  → TimeDistributed Conv1D (8 filters, kernel=16, stride=4)
  → BatchNorm + ReLU
  → Global Average Pooling
  → Dense (16 units, ReLU)
  → Dense (8 units, Softmax)
→ 7 brain states + 1 noise state
```

**处理流程 (片上)：**
1. ADS1299 250Hz SPI → DMA → nRF5340 RAM
2. CMSIS-DSP FFT (512点, Hanning窗) → 5 频带功率
3. INT8 量化 → ESP32-S3 TFLite 推理
4. 卡尔曼滤波平滑 → 状态输出
5. 规则引擎 → tDCS 参数调整

**不需要额外 NPU。** ESP32-S3 的向量扩展指令已经够用。2027-2028 年如果 EnCharge EN100 成本降到 $5 以下可以考虑集成。

---

## 6. 电源管理 — BQ25120 + LiPo

```
                    ┌──────────────────────┐
    USB-C 5V ───────┤   BQ25120 (TI)       │
                    │  线性充电 300mA       │
                    │  运输模式 75nA        │
                    │  3 路 LDO 输出        │
                    │  I²C 可编程           │
                    ├──────────────────────┤
                    │  LDO1: 3.3V (MCU域)  │← 给 nRF5340 + ESP32-S3
                    │  LDO2: 5.0V (刺激域) │← 给 DAC + Howland
                    │  LDO3: 3.3V (模拟域) │← 给 ADS1299 (低噪声)
                    │  LDO3: 2.5V (ADC域)  │← 给 ADS1299 AVDD
                    └──────────────────────┘
                              │
                     ┌───────┴──────┐
                     │ 402030 LiPo  │
                     │ 400mAh, 3.7V │
                     │ 带保护板     │
                     └──────────────┘
```

| 参数 | 值 |
|------|-----|
| 充电 IC | BQ25120 (TI, 线性充电 + 3路 LDO) |
| 电池 | 402030 LiPo 400mAh (超薄, ~4mm) |
| 续航 (估算) | EEG 只读模式 ~8h / 闭环模式 ~4h |
| 充电时间 | ~1.5h (USB-C 300mA) |
| 运输功耗 | 75nA (BQ25120 运输模式) |
| BOM | ~$8 |

**功耗预算：**

| 域 | 组件 | 峰值功耗 | 平均功耗 |
|----|------|---------|---------|
| 模拟 | ADS1299 8ch | 40 mW | 40 mW |
| 低功耗数字 | nRF5340 BLE + SPI | 15 mW | 15 mW |
| 高性能数字 | ESP32-S3 TFLite (间歇) | 100 mW | 20 mW (25% duty) |
| 刺激 | DAC + Howland (间歇) | 10 mW | 5 mW (50% duty) |
| **总计** | | | **~80 mW** |
| **续航** | 400mAh / (80mW/3.7V) | | **~4.6h 闭环** |

---

## 7. 汇总 BOM

| # | 子系统 | 核心器件 | BOM ($) | 占总量 |
|---|--------|---------|---------|--------|
| 1 | EEG AFE | ADS1299IPAGR | $25 | 31% |
| 2 | 主控 + 无线 | nRF5340 + ESP32-S3 + Flash | $10 | 12% |
| 3 | tDCS 刺激 | DAC8562 + OPA2189 + 隔离 | $8 | 10% |
| 4 | 电极 | 定制 6ch 梳齿 Ag-AgCl + 耳夹 | $10 | 12% |
| 5 | 电源 | BQ25120 + 402030 LiPo 400mAh | $8 | 10% |
| 6 | PCB | 4层板 80×50mm (JLCPCB) | $10 | 12% |
| 7 | 无源 | R/C/L/接插件/ESD | $5 | 6% |
| 8 | 壳体 | 3D打印 SLA + 硅胶头带 | $5 | 6% |
| **总 BOM** | | | **$81** | 100% |
| **目标售价** | | 3× BOM | **$249** | |

### 与竞品 BOM 对比

| 产品 | 售价 | 估计 BOM | 功能 |
|------|------|----------|------|
| **本产品** | **$249** | **$81** | **EEG+tDCS 闭环, 8ch, BLE+WiFi** |
| Muse 2 | $249 | ~$40 | EEG 只读, 4ch, BLE |
| Flow FL-100 | $459 | ~$50 | tDCS 只写, 开环, 无 EEG |
| OpenBCI Cyton + 刺激 | $349 + $249 | ~$100 | 开发板, 不便携 |
| LIFTiD | $159 | ~$20 | tDCS 只写, 无 EEG |
| NeuroSky MindWave | $199 | ~$15 | EEG 只读, 1ch |

---

## 8. 开发阶段与成本

| 阶段 | 目标 | 时间 | 成本 |
|------|------|------|------|
| Phase 0 | 采购评估硬件 (OpenBCI, Muse 2, Flow) 基线测试 | 2周 | $500 |
| Phase 0.5 | 算法原型: PC 上录制 EEG → FocusDetector → tDCS 仿真 | 4周 | 纯软件 |
| Phase 1 | PCB v1 打样 + 焊接 + 基础固件 (EEG 读取/BLE 传输) | 6周 | $1,000 (3次迭代) |
| Phase 2 | 完整固件: 片上 FFT + TFLite + tDCS 闭环规则引擎 | 8周 | $2,000 |
| Phase 3 | 工业设计: 柔性干电极 + 织物头带 + 外壳量产模具 | 8周 | $10,000 |
| Phase 4 | 小批量 (100台) + 众测 + 迭代 | 12周 | $15,000 |
| Phase 5 | 规模化生产 + 渠道 + (可选) FDA CE 认证 | 24周 | $50,000+ |
| **总 (到 MVP)** | | **~24周** | **~$18,500** |
| **总 (到量产)** | | **~1年** | **~$80,000** |

---

## 9. 关键采购链接

| 器件 | 供应商 | 参考价 | 链接/编号 |
|------|--------|--------|-----------|
| ADS1299IPAGR | LCSC | ¥159 | C476817 |
| nRF5340-QFAA | LCSC | ¥38 | C5275490 |
| ESP32-S3-WROOM-1-N16R8 | LCSC | ¥18 | C529579 |
| DAC8562IDPWR | LCSC | ¥18 | C523324 |
| OPA2189IDR | LCSC | ¥8 | C528102 |
| BQ25120YFPR | LCSC | ¥12 | C538596 |
| 402030 LiPo 400mAh | AliExpress | ¥20 | 搜"402030 battery 400mAh" |
| OpenBCI 梳齿电极 | OpenBCI | $1.67/个 | 搜"OpenBCI Gold Cup Electrode" |
| 导电耳夹 | Taobao | ¥5 | 搜"导电耳夹 ECG" |
| ISO7740 | LCSC | ¥10 | C462792 |

---

## 10. 风险与未知

| 风险 | 影响 | 缓解 |
|------|------|------|
| 干电极阻抗不稳定 → 信号质量差 | 高 | Phase 0 先买 OpenBCI 梳齿测基线，验证可用 |
| tDCS 刺激伪迹干扰 EEG | 高 | 刺激间隙采 EEG (时分复用), 硬件 notch filter, 固件模板相减 |
| BLE 带宽不够 (4ch × 250Hz × 24bit = 24kbps) | 中 | 片上预处理后只传输状态 (7种 × 1Hz = 极低带宽) |
| 续航不足 4h | 中 | ESP32-S3 深度睡眠 (刺激间歇), 可增大电池到 600mAh |
| 用户期望管理 (tDCS 效果微妙) | 中 | 产品定位"辅助工具"而非"神奇的脑控", 引导用户做使用日志 |
| 中国 → 海外工厂转移 | 低 | 主板在 JLCPCB 打样 (中国), 组装可外包给深圳 (全球出货) |
| 专利风险 (Flow FL-100 闭环方法专利) | 低 | Flow 专利覆盖"抑郁症治疗", 我们的产品定位"健康辅助" |

---

## 附录 A：PCB 连接概图 (文字版)

```
nRF5340 ──── SPI ──── ADS1299 (EEG 8ch)
    │          │
    │          ├── Fp1 (CH1)
    │          ├── Fp2 (CH2)
    │          ├── C3  (CH3)
    │          ├── C4  (CH4)
    │          ├── A1  (REF)
    │          └── Fz  (GND)
    │
    ├── UART ──── ESP32-S3 (TFLite)
    │
    ├── SPI ──── DAC8562 ──── OPA2189 Howland ──── Stim+ (F3)
    │                                       └─── Stim- (Fp2)
    │
    ├── I²C ──── BQ25120 (电源管理)
    │
    └── BLE ──── 手机 App (状态显示 / 数据记录)
```

## 附录 B：固件架构 (文字版)

```
nRF5340 (传感器域):
  main():
    init_ble()                    // BLE 广播 + GATT 服务
    init_ads1299_spi(250Hz)       // SPI DMA 配置
    init_dac_spi()                // tDCS 控制
    init_power_management()       // I²C BQ25120
    
    loop():
      if ads1299_data_ready:
        dma_read_4ch()            // 4ch × 3bytes × 250Hz
        apply_notch_filter(50Hz)  // 工频陷波
        apply_bandpass(0.5-40Hz)  // 带通
        send_to_esp32(uart)

ESP32-S3 (AI域):
  loop():
    if uart_data_ready:
      receive_4ch_250ms_window    // 100 个采样点 × 4ch
      compute_fft_512()           // CMSIS-DSP
      extract_5_band_powers()     // δ, θ, α, β, γ
      tflite_inference()          // FocusDetector
      kalman_smooth()             // 状态平滑
      run_closed_loop_rules()     // 规则引擎 → tDCS 参数
      send_command_to_nrf5340()   // 电流值 + 模式
      send_state_to_phone(ble)    // 更新 App 显示
```

## 附录 C：tDCS 闭环规则引擎

```
输入: 连续专注度分数 S_focus [0,100], 放松度 S_relax [0,100]
输出: 刺激电流 I_mA, 刺激模式 mode

规则:
  if S_focus > 70:
    I = 0.5 + (S_focus - 70) * 0.025  // 0.5 ~ 1.25 mA
    mode = tDCS_ANODAL               // F3+ Fp2- 提升专注
  elif S_relax > 70:
    I = 1.0
    mode = tACS_10Hz                  // 10Hz α 节律同步
  elif S_focus < 40:
    I = 0.5                           // 微量支持
    mode = tDCS_ANODAL
  else:
    I = 0
    mode = IDLE                       // 不刺激，只监测

安全:
  if electrode_impedance > 20kΩ: stop_all()
  if session_time > 40min: stop_and_cool_down(20min)
  if I_change > 0.5mA/s: ramp_limit()  // 软启动/停止
  if any_error: safe_stop()           // 急停 + BLE 报警
```
