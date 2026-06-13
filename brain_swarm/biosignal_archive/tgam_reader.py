"""TGAM 脑电模块串口读取与解析

TGAM (NeuroSky 神念科技) 是最便宜的单通道脑电方案，淘宝 ¥80~¥95。

协议：57600 baud, 每秒约 1 个数据包
输出内容：
- 专注度 (0-100)
- 放松度 (0-100)
- 眨眼强度
- 原始脑电信号
- 8 频段能量谱
- 信号质量
"""

import struct
import time
import numpy as np
from typing import Optional, Tuple, Callable, List
from dataclasses import dataclass, field
from collections import deque

# 信号增强模块
from signal_enhancer import (
    SignalEnhancer, SignalQualityReport, ArtifactDetector,
    BaselineCalibrator, AdaptiveThreshold, SignalQualityMonitor
)

# 尝试导入串口（可能未安装）
try:
    import serial
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False
    serial = None

try:
    import serial.tools.list_ports
except ImportError:
    pass


@dataclass
class TGAMData:
    """TGAM 数据包"""
    attention: int = 0      # 专注度 0-100
    meditation: int = 0     # 放松度 0-100
    blink: int = 0          # 眨眼强度
    raw_eeg: int = 0        # 原始脑电信号
    signal_quality: int = 0 # 信号质量 (0=好, 200=差)
    # 8 频段能量值
    delta: int = 0          # 0.5-2.75 Hz
    theta: int = 0          # 3.5-6.75 Hz
    low_alpha: int = 0      # 7.5-9.25 Hz
    high_alpha: int = 0     # 10-11.75 Hz
    low_beta: int = 0       # 13-16.75 Hz
    high_beta: int = 0      # 18-29.75 Hz
    low_gamma: int = 0      # 31-39.75 Hz
    mid_gamma: int = 0      # 41-49.75 Hz
    timestamp: float = 0.0


class TGAMParser:
    """TGAM 串口数据解析器"""

    def __init__(self, enable_signal_enhancement: bool = True, sampling_rate: int = 1):
        self._buffer = bytearray()
        self._current = TGAMData()
        self._on_data: Optional[Callable[[TGAMData], None]] = None
        # 平滑缓冲区
        self._attention_history = deque(maxlen=10)
        self._meditation_history = deque(maxlen=10)
        # 信号增强：原始EEG累积用于质量分析
        self._raw_eeg_history = deque(maxlen=250)  # 累积 ~250 个采样点
        self._signal_enhancer: Optional[SignalEnhancer] = None
        self._last_quality_report: Optional[SignalQualityReport] = None
        self._bad_signal_count = 0
        self._consecutive_good_count = 0
        self._enable_signal_enhancement = enable_signal_enhancement

        if enable_signal_enhancement:
            # TGAM每秒一个数据包，原始EEG累积后分析
            self._signal_enhancer = SignalEnhancer(sampling_rate=250, line_freq=50.0)

    @property
    def signal_enhancer(self) -> Optional[SignalEnhancer]:
        """获取信号增强器"""
        return self._signal_enhancer

    @property
    def last_quality_report(self) -> Optional[SignalQualityReport]:
        """获取最后一次信号质量报告"""
        return self._last_quality_report

    def get_signal_quality_summary(self) -> dict:
        """获取信号质量摘要"""
        summary = {
            'good': False,
            'contact_quality': 3,
            'snr_db': 0.0,
            'raw_score': 0.0,
            'warnings': [],
            'suggestions': [],
            'bad_count': self._bad_signal_count,
        }

        if self._last_quality_report:
            summary.update({
                'good': self._last_quality_report.is_clean,
                'contact_quality': self._last_quality_report.contact_quality,
                'snr_db': self._last_quality_report.snr_db,
                'raw_score': self._last_quality_report.raw_score,
                'warnings': self._last_quality_report.warnings,
                'suggestions': self._last_quality_report.suggestions,
            })
        return summary

    def get_user_guidance(self) -> str:
        """根据当前信号质量生成用户引导"""
        if not self._last_quality_report:
            return "请等待信号采集..."

        report = self._last_quality_report
        contact_level = report.contact_quality

        guidance_map = {
            0: "✅ 信号良好",
            1: "⚠️ 信号质量一般，建议调整电极位置",
            2: "❌ 信号质量差，请重新佩戴电极",
            3: "🔴 未检测到有效信号，请检查设备连接"
        }

        guidance = guidance_map.get(contact_level, "⚠️ 信号异常")

        if report.warnings:
            guidance += "\n  问题: " + ", ".join(report.warnings)

        if report.suggestions:
            guidance += "\n  建议: " + "; ".join(report.suggestions[:3])

        return guidance

    def should_ignore_current_data(self) -> Tuple[bool, str]:
        """判断当前数据包是否应该被忽略（信号质量太差）"""
        if not self._enable_signal_enhancement or self._signal_enhancer is None:
            # 传统规则：TGAM signal_quality > 50 就忽略
            if self._current.signal_quality > 50:
                return True, f"TGAM硬件报告信号质量差 ({self._current.signal_quality})"
            return False, "OK"

        # 使用完整信号增强评估
        if len(self._raw_eeg_history) < 30:
            # 累积数据不足，暂不判断
            return False, "OK"

        # 将累积原始数据转换为numpy数组
        raw_data = np.array([x for x in self._raw_eeg_history])

        # 评估信号质量
        quality = self._signal_enhancer.evaluate(raw_data, self._current.signal_quality)
        self._last_quality_report = quality

        # 检测伪迹
        artifacts = self._signal_enhancer.detect_artifacts(raw_data)

        # 判断是否忽略
        should_ignore, reason = self._signal_enhancer.should_ignore_data(quality, artifacts)

        # 统计不良信号
        if should_ignore:
            self._bad_signal_count += 1
            self._consecutive_good_count = 0
        else:
            self._consecutive_good_count += 1

        return should_ignore, reason

    def set_callback(self, callback: Callable[[TGAMData], None]):
        """设置数据回调"""
        self._on_data = callback

    def feed(self, byte_data: bytes):
        """喂入字节数据，自动解析"""
        self._buffer.extend(byte_data)

        while len(self._buffer) >= 2:
            # 查找同步头 0xAA 0xAA
            if self._buffer[0] == 0xAA and self._buffer[1] == 0xAA:
                # 需要至少 4 字节: 2 帧头 + 1 长度 + 1 校验
                if len(self._buffer) >= 4:
                    payload_len = self._buffer[2]
                    total_len = 4 + payload_len  # 2头 + 1长 + payload + 1校验

                    if payload_len > 169:  # 最大 payload 长度
                        self._buffer.pop(0)
                        continue

                    if len(self._buffer) >= total_len:
                        payload = self._buffer[3:3 + payload_len]
                        checksum = self._buffer[3 + payload_len]

                        # 验证校验和
                        calc_sum = (0xAA + 0xAA + payload_len + sum(payload)) & 0xFF
                        calc_sum = (~calc_sum) & 0xFF

                        if calc_sum == checksum:
                            self._parse_payload(payload)
                            self._current.timestamp = time.time()
                            if self._on_data:
                                self._on_data(self._current)
                        # 移除已处理的包
                        del self._buffer[:total_len]
                    else:
                        break
                else:
                    break
            else:
                self._buffer.pop(0)

    def _parse_payload(self, payload: bytes):
        """解析 payload 数据"""
        i = 0
        while i < len(payload):
            code = payload[i]
            i += 1

            if code == 0x02:  # 信号质量
                if i < len(payload):
                    self._current.signal_quality = payload[i]
                    i += 1

            elif code == 0x04:  # 专注度
                if i < len(payload):
                    self._current.attention = payload[i]
                    self._attention_history.append(payload[i])
                    i += 1

            elif code == 0x05:  # 放松度
                if i < len(payload):
                    self._current.meditation = payload[i]
                    self._meditation_history.append(payload[i])
                    i += 1

            elif code == 0x16:  # 眨眼强度
                if i < len(payload):
                    self._current.blink = payload[i]
                    i += 1

            elif code == 0x80:  # 原始脑电信号
                if i + 1 < len(payload):
                    # 16-bit 有符号整数，高字节在前
                    high = payload[i]
                    low = payload[i + 1]
                    self._current.raw_eeg = (high << 8) | low
                    if self._current.raw_eeg > 32767:
                        self._current.raw_eeg -= 65536
                    # 累积原始EEG用于信号质量分析
                    if self._enable_signal_enhancement:
                        self._raw_eeg_history.append(self._current.raw_eeg)
                    i += 2

            elif code == 0x83:  # EEG 频段能量
                if i + 24 <= len(payload):
                    bands = struct.unpack('>8I', b'\x00' * 8 + payload[i:i+24])
                    self._current.delta = bands[0]
                    self._current.theta = bands[1]
                    self._current.low_alpha = bands[2]
                    self._current.high_alpha = bands[3]
                    self._current.low_beta = bands[4]
                    self._current.high_beta = bands[5]
                    self._current.low_gamma = bands[6]
                    self._current.mid_gamma = bands[7]
                    i += 24
                else:
                    i = len(payload)

            else:
                # 未知 code，跳过下一个字节
                if i < len(payload):
                    i += 1

    @property
    def smoothed_attention(self) -> float:
        """平滑后的专注度"""
        if not self._attention_history:
            return 0
        return sum(self._attention_history) / len(self._attention_history)

    @property
    def smoothed_meditation(self) -> float:
        """平滑后的放松度"""
        if not self._meditation_history:
            return 0
        return sum(self._meditation_history) / len(self._meditation_history)

    def get_alpha_beta_ratio(self) -> float:
        """α/β 比：专注状态判断的常用指标"""
        alpha = self._current.low_alpha + self._current.high_alpha
        beta = self._current.low_beta + self._current.high_beta
        if beta == 0:
            return 1.0
        return alpha / beta


class TGAMReader:
    """TGAM 串口读取器"""

    def __init__(self, port: str = "auto", baud_rate: int = 57600):
        self.port = port
        self.baud_rate = baud_rate
        self.serial_conn = None
        self.parser = TGAMParser()
        self._running = False

    @staticmethod
    def find_tgam_port() -> Optional[str]:
        """自动查找 TGAM 串口"""
        if not HAS_SERIAL:
            return None
        ports = serial.tools.list_ports.comports()
        for p in ports:
            # TGAM 通常用 HC-06 蓝牙模块或 CH340 CP2102 USB-TTL
            if any(x in p.description.lower() for x in
                   ['ch340', 'cp210', 'hc-06', 'bluetooth', 'serial']):
                return p.device
        # 没找到特定设备，返回第一个可用串口
        if ports:
            return ports[0].device
        return None

    def connect(self, port: Optional[str] = None) -> bool:
        """连接 TGAM 模块"""
        if not HAS_SERIAL:
            print("错误: 需要安装 pyserial: pip install pyserial")
            return False

        if port:
            self.port = port
        elif self.port == "auto":
            self.port = self.find_tgam_port()
            if not self.port:
                print("错误: 未找到 TGAM 设备，请手动指定 COM 端口")
                return False

        try:
            self.serial_conn = serial.Serial(
                port=self.port,
                baudrate=self.baud_rate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=1.0
            )
            print(f"[OK] TGAM 已连接: {self.port}")
            return True
        except Exception as e:
            print(f"错误: 无法连接 TGAM ({e})")
            return False

    def set_callback(self, callback: Callable[[TGAMData], None]):
        """设置数据回调"""
        self.parser.set_callback(callback)

    def read_loop(self):
        """主循环：持续读取串口数据"""
        if not self.serial_conn or not self.serial_conn.is_open:
            print("错误: 串口未连接")
            return

        self._running = True
        print("开始读取脑电数据... (Ctrl+C 停止)\n")
        try:
            while self._running:
                if self.serial_conn.in_waiting > 0:
                    data = self.serial_conn.read(self.serial_conn.in_waiting)
                    self.parser.feed(data)
                else:
                    time.sleep(0.01)
        except KeyboardInterrupt:
            pass
        finally:
            self.disconnect()

    def disconnect(self):
        """断开连接"""
        self._running = False
        if self.serial_conn and self.serial_conn.is_open:
            self.serial_conn.close()
            print("TGAM 已断开")


class TGAMSimulator:
    """TGAM 模拟器：用于无硬件时测试，可模拟信号衰减场景"""

    def __init__(self, simulate_signal_degradation: bool = True):
        self._attention = 50
        self._meditation = 50
        self._blink = 0
        self._direction = 1  # 1=上升, -1=下降
        self._callbacks = []
        self._simulate_degradation = simulate_signal_degradation
        # 模拟信号衰减
        self._signal_quality = 0  # 0=好, 200=差
        self._degradation_timer = 0
        self._degradation_cycle = 0
        self._simulated_raw_eeg: List[int] = []  # 模拟原始EEG

    def set_callback(self, callback: Callable[[TGAMData], None]):
        """添加数据回调"""
        self._callbacks.append(callback)

    def update(self):
        """模拟更新，生成变化的数据"""
        import random

        # 模拟信号衰减周期（每30秒来一轮信号变差）
        if self._simulate_degradation:
            self._degradation_timer += 1
            cycle = self._degradation_timer % 90  # 90秒周期

            if cycle < 60:  # 前60秒信号好
                self._signal_quality = max(0, self._signal_quality - 2)
            elif cycle < 65:  # 5秒信号快速恶化（模拟电极松动）
                self._signal_quality = min(200, self._signal_quality + 40)
            elif cycle < 75:  # 10秒信号差
                self._signal_quality = min(200, self._signal_quality + random.randint(-5, 10))
            else:  # 最后15秒恢复（模拟重新调整电极）
                self._signal_quality = max(0, self._signal_quality - 14)

        # 模拟专注度在 30-90 之间正弦波动
        self._attention += self._direction * random.uniform(1, 5)
        if self._attention >= 90:
            self._direction = -1
        elif self._attention <= 30:
            self._direction = 1

        self._meditation = 100 - self._attention + random.uniform(-10, 10)
        self._meditation = max(0, min(100, self._meditation))

        # 偶尔模拟眨眼
        self._blink = random.randint(0, 100) if random.random() < 0.1 else 0

        # 模拟原始EEG（信号差时加噪声）
        base_eeg = random.randint(-200, 200)
        if self._signal_quality > 100:
            # 信号差：噪声大幅增加
            base_eeg += random.randint(-500, 500)
        elif self._signal_quality > 50:
            base_eeg += random.randint(-200, 200)
        self._simulated_raw_eeg = [base_eeg]

        data = TGAMData(
            attention=int(self._attention),
            meditation=int(self._meditation),
            blink=self._blink,
            signal_quality=self._signal_quality,
            raw_eeg=base_eeg,
            timestamp=time.time()
        )

        for cb in self._callbacks:
            cb(data)

    @property
    def signal_quality(self) -> int:
        """当前模拟信号质量"""
        return self._signal_quality

    def get_simulated_raw_eeg(self) -> np.ndarray:
        """获取模拟的原始EEG数据（用于信号增强测试）"""
        if not self._simulated_raw_eeg:
            return np.array([0])
        return np.array(self._simulated_raw_eeg)