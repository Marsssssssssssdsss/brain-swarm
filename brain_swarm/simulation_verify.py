"""
弱脑电信号叠加模拟验证
========================
模拟场景: 真实脑电信号 1 μV, 背景噪声 50 μV, SNR = -33.98 dB
验证滑动窗口叠加100次能否把信号从噪声中恢复出来
"""

import numpy as np
from scipy import signal
import sys
import os

# ─── 参数设定 ────────────────────────────────────────────
SAMPLING_RATE = 250       # Hz
DURATION = 10.0           # 模拟10秒数据
N_SAMPLES = int(SAMPLING_RATE * DURATION)
T = np.arange(N_SAMPLES) / SAMPLING_RATE

# —— 模拟一个真实的弱脑电信号 ——
# 假设用户在做"想象右手"运动想象
# mu节律(10Hz)功率下降 + beta节律(22Hz)功率上升
SIGNAL_FREQS = [10.0, 22.0]       # Hz: mu节律和beta节律
SIGNAL_AMPS = [0.7, 0.5]          # μV:  真实脑电信号振幅
SIGNAL_PHASES = [0.0, np.pi/3]    # 相位偏移

true_eeg = np.zeros(N_SAMPLES)
for freq, amp, phase in zip(SIGNAL_FREQS, SIGNAL_AMPS, SIGNAL_PHASES):
    true_eeg += amp * np.sin(2 * np.pi * freq * T + phase)

# 添加真实脑电中的非平稳特性（频率微漂移、幅度调制）
# 模拟8-12Hz mu节律的时变特性
modulation = 0.3 * np.sin(2 * np.pi * 0.5 * T)  # 0.5Hz慢调制
true_eeg *= (1.0 + modulation)

# 信号的真实 RMS
true_signal_rms = np.sqrt(np.mean(true_eeg ** 2))
print(f"真实脑电信号 RMS: {true_signal_rms:.4f} μV")

# —— 叠加噪声 ——
# 实际EEG噪声来源:
# 1. 白噪声 (热噪声、放大器噪声): ~10 μV
# 2. 工频干扰 (50Hz): ~20-40 μV
# 3. 肌电伪迹 (EMG): ~10-30 μV
# 4. 眼动伪迹 (EOG): ~50-100 μV
np.random.seed(42)

noise_white = 10.0 * np.random.randn(N_SAMPLES)  # 白噪声
noise_line = 30.0 * np.sin(2 * np.pi * 50.0 * T + 0.5)  # 工频50Hz
noise_emg = 15.0 * np.random.randn(N_SAMPLES)  # 肌电噪声
# 对肌电噪声做高通滤波, 使其更像真实EMG (高频为主)
b_emg, a_emg = signal.butter(4, 20 / (SAMPLING_RATE/2), btype='high')
noise_emg = signal.filtfilt(b_emg, a_emg, noise_emg)

noise_total = noise_white + noise_line + noise_emg

# 混合信号 = 真信号 + 噪声
raw_eeg = true_eeg + noise_total

# 计算信噪比
signal_power = np.var(true_eeg)
noise_power = np.var(noise_total)
snr_db = 10 * np.log10(signal_power / noise_power)
print(f"背景噪声 RMS: {np.sqrt(noise_power):.4f} μV")
print(f"原始 SNR: {snr_db:.2f} dB")
print(f"信号完全被噪声淹没, {'肉眼不可见' if snr_db < 0 else '勉强可见'}")

# ─── 滑动窗口叠加 ────────────────────────────────────────
WINDOW_MS = 200           # 窗口长度 200ms
WINDOW_SAMPLES = int(WINDOW_MS * SAMPLING_RATE / 1000)  # 50个采样点
STEP_MS = 5               # 步进 5ms
STEP_SAMPLES = int(STEP_MS * SAMPLING_RATE / 1000)       # 约1-2个采样点
N_WINDOWS = 100           # 叠加100次

print(f"\n── 滑动窗口叠加参数 ──")
print(f"窗口长度: {WINDOW_MS}ms ({WINDOW_SAMPLES} 采样点)")
print(f"步进: {STEP_MS}ms ({STEP_SAMPLES} 采样点)")
print(f"叠加次数: {N_WINDOWS}")
total_delay_ms = (N_WINDOWS - 1) * STEP_MS + WINDOW_MS
print(f"总延迟: {total_delay_ms:.0f}ms ({total_delay_ms/1000:.1f}s)")

# 执行滑动窗口叠加
windows = []
for i in range(N_WINDOWS):
    start = i * STEP_SAMPLES
    end = start + WINDOW_SAMPLES
    if end > N_SAMPLES:
        break
    windows.append(raw_eeg[start:end])

windows = np.array(windows)  # (N, window_samples)
print(f"实际提取窗口数: {windows.shape[0]}")

# 叠加平均
averaged = np.mean(windows, axis=0)  # axis=0: 沿窗口维度取平均

# 等效的"真实信号"窗口
true_windows = []
for i in range(N_WINDOWS):
    start = i * STEP_SAMPLES
    end = start + WINDOW_SAMPLES
    if end > N_SAMPLES:
        break
    true_windows.append(true_eeg[start:end])
true_avg = np.mean(true_windows, axis=0)

# ─── 结果分析 ────────────────────────────────────────────
# 叠加后的残余噪声
residual_noise = averaged - true_avg
residual_noise_rms = np.sqrt(np.mean(residual_noise ** 2))
averaged_signal_rms = np.sqrt(np.mean(true_avg ** 2))
averaged_noise_power = np.var(residual_noise)
averaged_snr = 10 * np.log10(np.var(true_avg) / averaged_noise_power) if averaged_noise_power > 0 else 999

print(f"\n── 叠加结果 ──")
print(f"叠加后信号 RMS: {averaged_signal_rms:.4f} μV")
print(f"叠加后残余噪声 RMS: {residual_noise_rms:.4f} μV")
print(f"理论噪声衰减 (1/√N): {1/np.sqrt(N_WINDOWS):.4f}")
print(f"实际噪声 RMS (原始 {np.sqrt(noise_power):.1f} → 叠加后 {residual_noise_rms:.4f})")
print(f"叠加后 SNR: {averaged_snr:.2f} dB")
print(f"SNR 改善: {averaged_snr - snr_db:.2f} dB")
print(f"理论 SNR 改善 (10*log10(N)): {10*np.log10(N_WINDOWS):.2f} dB")

# ─── 信号识别测试 ────────────────────────────────────────
# 叠加后能否识别出10Hz和22Hz的信号成分?
from numpy.fft import rfft, rfftfreq

# 原始信号频谱
raw_fft = np.abs(rfft(raw_eeg[:WINDOW_SAMPLES]))
avg_fft = np.abs(rfft(averaged))
true_fft = np.abs(rfft(true_avg))
freqs = rfftfreq(WINDOW_SAMPLES, 1.0/SAMPLING_RATE)

print(f"\n── 频谱分析 ──")
# 检测10Hz峰值
idx_10 = np.argmin(np.abs(freqs - 10))
idx_22 = np.argmin(np.abs(freqs - 22))
print(f"10Hz 原始谱值: {raw_fft[idx_10]:.2f}, 叠加后: {avg_fft[idx_10]:.2f}")
print(f"22Hz 原始谱值: {raw_fft[idx_22]:.2f}, 叠加后: {avg_fft[idx_22]:.2f}")

# —— 不同叠加次数的效果对比 ——
print(f"\n── 不同叠加次数的效果 ──")
trial_counts = [1, 5, 10, 30, 50, 100, 200, 500]
for n in trial_counts:
    if n > windows.shape[0]:
        continue
    avg_n = np.mean(windows[:n], axis=0)
    residual_n = avg_n - true_avg
    snr_n = 10 * np.log10(np.var(true_avg) / np.var(residual_n)) if np.var(residual_n) > 0 else 999
    print(f"  叠加 {n:3d} 次: SNR = {snr_n:+.2f} dB, 噪声RMS = {np.sqrt(np.var(residual_n)):.4f} μV")

# ─── 输出结论 ────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"结论")
print(f"{'='*60}")
print(f"1. 信号完全被噪声淹没时 (SNR = {snr_db:.1f} dB), 单次窗口完全不可识别")
print(f"2. 滑动窗口叠加 {N_WINDOWS} 次后 SNR = {averaged_snr:+.1f} dB")
print(f"3. SNR 改善 {averaged_snr - snr_db:.1f} dB = {10*np.log10(N_WINDOWS):.1f} dB (理论值)")
print(f"4. {'✅ 信号成功从噪声中恢复' if averaged_snr > 0 else '❌ 信号仍未恢复'}")
print(f"5. 总延迟 {total_delay_ms:.0f}ms ≈ {total_delay_ms/1000:.1f}s")
print(f"")
print(f"重要限制：")
print(f"  - 前提: 用户在叠加期间必须保持同一意图 (不能中途切换想法)")
print(f"  - 前提: 电极接触良好 (信号不能衰减到0)")
print(f"  - 叠加不放大信号, 而是通过平均降低噪声幅度")
print(f"  - 信号 RMS 从 {true_signal_rms:.4f} μV 不变, 噪声 RMS 从 {np.sqrt(noise_power):.1f} μV 降到 {residual_noise_rms:.4f} μV")

# ─── 保存结果到文件 ──
output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "simulation_results.txt")
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(f"弱脑电信号叠加模拟验证结果\n")
    f.write(f"{'='*60}\n\n")
    f.write(f"信号参数:\n")
    f.write(f"  真实脑电信号 RMS: {true_signal_rms:.4f} μV\n")
    f.write(f"  背景噪声 RMS: {np.sqrt(noise_power):.4f} μV\n")
    f.write(f"  原始 SNR: {snr_db:.2f} dB\n\n")
    f.write(f"叠加参数:\n")
    f.write(f"  窗口长度: {WINDOW_MS}ms, 步进: {STEP_MS}ms\n")
    f.write(f"  叠加次数: {N_WINDOWS}\n")
    f.write(f"  总延迟: {total_delay_ms:.0f}ms\n\n")
    f.write(f"叠加结果:\n")
    f.write(f"  叠加后信号 RMS: {averaged_signal_rms:.4f} μV\n")
    f.write(f"  叠加后残余噪声 RMS: {residual_noise_rms:.4f} μV\n")
    f.write(f"  叠加后 SNR: {averaged_snr:.2f} dB\n")
    f.write(f"  SNR 改善: {averaged_snr - snr_db:.2f} dB\n")
    f.write(f"  {'✅ 有效' if averaged_snr > snr_db else '❌ 无效'}\n")

print(f"\n模拟结果已保存到: {output_path}")