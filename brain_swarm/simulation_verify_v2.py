"""
弱脑电信号叠加模拟验证 V2 —— 正确方法
==========================================
V1 发现滑动窗口叠加存在根本性问题:
  窗口之间时间偏移不同, 叠加平均时信号相位抵消, 而不是增强.
  这不是 ERP(事件相关电位)的标准做法.

正确方法 (两个): 
  方法A - 试次锁定叠加": 呈现N次相同的指令(如"想象右手"), 
           提取每次的EEG片段, 对齐到指令起始时刻做平均
  方法B - 频谱叠加": 对同一任务状态的多个不重叠时间段做功率谱平均
"""

import numpy as np
from scipy import signal
import os

SAMPLING_RATE = 250
TRIAL_DURATION = 2.0       # 每次试次 2 秒
TRIAL_SAMPLES = int(SAMPLING_RATE * TRIAL_DURATION)
T = np.arange(TRIAL_SAMPLES) / SAMPLING_RATE

np.random.seed(42)

print("=" * 70)
print("方法A: 试次锁定叠加 (Trial-locked Averaging) — 真正有效的ERP方法")
print("=" * 70)

# —— 模拟一次"想象右手"的运动想象 ——
# mu节律(10Hz)在运动想象时功率下降
# beta节律(22Hz)在运动想象时功率上升（beta rebound）
N_TRIALS = 100  # 重复100次

true_signal_rms_all = []
snr_before_all = []
snr_after_all = []

single_trials = np.zeros((N_TRIALS, TRIAL_SAMPLES))
noise_trials = np.zeros((N_TRIALS, TRIAL_SAMPLES))

for trial in range(N_TRIALS):
    # 真实脑电信号: mu节律抑制 + beta rebound
    # 假设基线mu振幅1.5μV, 想象时降到0.5μV
    # beta从0.3升到0.6μV
    mu_amp_base = 1.5
    mu_amp_active = 0.5
    beta_amp_base = 0.3
    beta_amp_active = 0.6

    # 模拟"事件相关去同步(ERD)"的时变模式
    # 前500ms是基线, 然后mu功率下降
    envelope = np.ones(TRIAL_SAMPLES)
    # 在0.5-1.5s之间mu抑制
    suppression_start = int(0.5 * SAMPLING_RATE)
    suppression_end = int(1.5 * SAMPLING_RATE)
    envelope[suppression_start:suppression_end] = 0.4
    # 平滑过渡
    from scipy.ndimage import gaussian_filter1d
    envelope = gaussian_filter1d(envelope, sigma=8)

    # mu节律 (10Hz)
    mu = mu_amp_base - (mu_amp_base - mu_amp_active) * (1 - envelope)
    mu_wave = mu * np.sin(2 * np.pi * 10.0 * T)

    # beta节律 (22Hz)
    beta = beta_amp_base + (beta_amp_active - beta_amp_base) * (1 - envelope)
    beta_wave = beta * np.sin(2 * np.pi * 22.0 * T)

    true_eeg = mu_wave + beta_wave
    true_signal_rms_all.append(np.sqrt(np.mean(true_eeg ** 2)))

    # 噪声
    noise_w = 8.0 * np.random.randn(TRIAL_SAMPLES)  # 白噪声
    noise_50 = 25.0 * np.sin(2 * np.pi * 50.0 * T + np.random.rand() * 2*np.pi)  # 工频
    noise_emg_raw = 10.0 * np.random.randn(TRIAL_SAMPLES)
    b_emg, a_emg = signal.butter(4, 30 / (SAMPLING_RATE/2), btype='high')
    noise_emg = signal.filtfilt(b_emg, a_emg, noise_emg_raw)
    noise = noise_w + noise_50 + noise_emg
    noise_trials[trial] = noise

    raw = true_eeg + noise
    single_trials[trial] = raw

    # 单次SNR
    snr_single = 10 * np.log10(np.var(true_eeg) / np.var(noise))
    snr_before_all.append(snr_single)

avg_true = np.mean([np.sin(2*np.pi*10*T)*1.5 + np.sin(2*np.pi*22*T)*0.3  # 基线
                    for _ in range(N_TRIALS)], axis=0)
# 实际平均信号
avg_signal = np.mean([np.array([mu_wave + beta_wave]) for _ in range(N_TRIALS)], axis=0)[0]
avg_raw = np.mean(single_trials, axis=0)
avg_noise = np.mean(noise_trials, axis=0)

residual_noise = avg_raw - avg_signal
avg_snr = 10 * np.log10(np.var(avg_signal) / np.var(residual_noise)) if np.var(residual_noise) > 1e-20 else 999

print(f"\n模拟参数:")
print(f"  试次数量: {N_TRIALS}")
print(f"  每次试次: {TRIAL_DURATION}s")
print(f"  总耗时: {N_TRIALS * (TRIAL_DURATION + 1.0):.0f}s (含间隔, 约5分钟)")
print(f"  脑电信号: 10Hz mu节律 + 22Hz beta节律")
print(f"  mu节律基线: 1.5μV, 想象时降至0.5μV")
print(f"  噪声: 白噪声(8μV) + 工频(25μV) + 肌电(10μV)")

avg_signal_rms_before = np.sqrt(np.mean(single_trials[0] ** 2))
avg_noise_rms_before = np.sqrt(np.mean(noise_trials[0] ** 2))

print(f"\n单次试次:")
print(f"  信号 RMS: {np.sqrt(np.var(true_eeg)):.4f} μV")
print(f"  噪声 RMS: {np.sqrt(np.var(noise_trials[0])):.4f} μV")
print(f"  SNR: {snr_before_all[0]:.2f} dB")

print(f"\n叠加 {N_TRIALS} 次后:")
print(f"  平均信号 RMS: {np.sqrt(np.var(avg_signal)):.4f} μV")
print(f"  残余噪声 RMS: {np.sqrt(np.var(residual_noise)):.4f} μV")
print(f"  SNR: {avg_snr:.2f} dB")
print(f"  理论噪声衰减: 1/√{N_TRIALS} = {1/np.sqrt(N_TRIALS):.4f}")
print(f"  SNR改善: {avg_snr - snr_before_all[0]:.1f} dB")
print(f"  理论SNR改善: {10*np.log10(N_TRIALS):.1f} dB")

print(f"\n  {'✅ 信号从噪声中成功恢复!' if avg_snr > 0 else '❌ 信号仍未恢复'}")
print(f"  SNR从{snr_before_all[0]:.1f}dB提升到{avg_snr:.1f}dB = {avg_snr - snr_before_all[0]:.0f}dB改善")

# —— 不同叠加次数的效果 ——
print(f"\n── 不同叠加次数的叠加效果 ──")
for n in [1, 5, 10, 20, 50, 100, 200]:
    avg_n = np.mean(single_trials[:n], axis=0)
    avg_true_n = np.mean([np.array([mu_wave + beta_wave]) for _ in range(n)], axis=0)[0]
    residual_n = avg_n - avg_true_n
    snr_n = 10 * np.log10(np.var(avg_true_n) / np.var(residual_n)) if np.var(residual_n) > 1e-20 else 999
    print(f"  叠加 {n:3d} 次: SNR = {snr_n:+6.1f} dB, 所需时间 ~{n * 3:.0f}s")

# —— 关键结论 ——
print(f"\n{'='*70}")
print("关键结论")
print(f"{'='*70}")
print("""
1. 试次锁定叠加是有效的, 但代价大:
   - 100次试次 = ~5分钟数据采集
   - 用户需要反复做"想象右手→休息→想象右手→休息"
   - 不能做到"持续想象, 滑动窗口平均"

2. 滑动窗口叠加 ( V1方法) 有根本性缺陷:
   - 窗口之间有时间偏移, 信号相位不同
   - 平均后信号彼此抵消而非增强
   - 这不是EEG文献中的做法

3. 真正可行的方案:
   - SSVEP: 频域1-2秒即可, 不需要时域叠加
   - FBCSP: 用空间滤波分离信号和噪声, 不依赖时域平均
   - 深度学习单次解码: EEGNet等模型可直接从单次试次解码

4. 你的文档中"滑动窗口叠加"的部分需要勘误:
   - 滑动窗口叠加 ≠ 信号平均增强
   - 平均的是同一时间点对齐的试次, 不是滑动窗口
""")

# 保存结果
output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "simulation_results_v2.txt")
with open(output_path, 'w', encoding='utf-8') as f:
    f.write("弱脑电信号叠加模拟验证 V2\n")
    f.write("=" * 60 + "\n\n")
    f.write(f"方法A: 试次锁定叠加\n")
    f.write(f"  试次数: {N_TRIALS}\n")
    f.write(f"  每次试次: {TRIAL_DURATION}s\n")
    f.write(f"  总耗时: ~5分钟\n\n")
    f.write(f"单次SNR: {snr_before_all[0]:.1f} dB\n")
    f.write(f"叠加后SNR: {avg_snr:.1f} dB\n")
    f.write(f"SNR改善: {avg_snr - snr_before_all[0]:.0f} dB\n")
    f.write(f"结论: {'有效' if avg_snr > 0 else '无效'}\n")

print(f"模拟结果已保存: {output_path}")