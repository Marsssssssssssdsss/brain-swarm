"""脑控电脑 - TGAM 完整演示

三种模式：
1. 实时数据监控：看 TGAM 输出的专注度、脑电波、α/β 等数据
2. 脑控电脑：用脑电专注度触发预设动作
3. 模拟模式：无硬件也能测试

硬件要求：TGAM 脑电模块（淘宝 ¥80~¥95）+ USB-TTL 模块（¥10）
连接：TGAM TX → USB-TTL RX，TGAM GND → USB-TTL GND，USB-TTL 插电脑
"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from src.tgam_reader import TGAMReader, TGAMParser, TGAMData, TGAMSimulator
from src.pc_controller import BrainPcController


def demo_monitor():
    """演示 1：实时监控 TGAM 数据"""
    print("=" * 50)
    print("  脑电数据实时监控")
    print("=" * 50)

    reader = TGAMReader(port="auto")
    controller = BrainPcController()

    def on_data(data: TGAMData):
        """显示数据"""
        bar_len = 20
        att_bar = "█" * int(data.attention * bar_len / 100)
        med_bar = "░" * int(data.meditation * bar_len / 100)

        print(f"\r  专注度: {data.attention:3d} [{att_bar:<20}]  "
              f"放松度: {data.meditation:3d} [{med_bar:<20}]  "
              f"眨眼: {data.blink:3d}  "
              f"信号: {'良好' if data.signal_quality < 50 else '差'}  ",
              end="")

    reader.set_callback(on_data)

    if reader.connect():
        reader.read_loop()
    else:
        print("未找到 TGAM 设备，切换到模拟模式...")
        demo_simulated()


def demo_pc_control():
    """演示 2：脑控电脑"""
    print("=" * 50)
    print("  脑控电脑 - 预设动作模式")
    print("=" * 50)
    print("""
  控制方式：
    专注度 > 70 → 触发当前动作
    双眨眼 (1秒内眨眼两次) → 切换动作
    专注度 < 30 → 取消

  预设动作：
    0. 讲解模式 (PPT)
    1. 编码模式 (VS Code)
    2. 浏览器 (Chrome)
    3. 终端
    4. 截屏
    5. 暂停/恢复
  """)

    reader = TGAMReader(port="auto")
    controller = BrainPcController()

    # 添加显示和触发
    def on_data(data: TGAMData):
        controller.on_tgam_data(data)

        # 显示状态
        bar = "█" * int(data.attention * 20 / 100)
        action = controller.actions[controller.current_action_idx]
        print(f"\r  专注度: {data.attention:3d} [{bar:<20}]  "
              f"当前动作: {action[0]}  "
              f"眨眼: {data.blink:3d}  "
              f"信号: {'OK' if data.signal_quality < 50 else '!'}  ",
              end="")

    reader.set_callback(on_data)

    if reader.connect():
        print("\n已连接 TGAM，开始脑控... (Ctrl+C 停止)\n")
        reader.read_loop()
    else:
        print("未找到 TGAM 设备，切换到模拟模式...")
        demo_simulated_pc_control()


def demo_simulated():
    """演示 3：模拟模式（无硬件）"""
    print("=" * 50)
    print("  模拟脑电数据监控")
    print("=" * 50)

    parser = TGAMParser()
    sim = TGAMSimulator()

    def on_data(data: TGAMData):
        bar_len = 20
        att_bar = "█" * int(data.attention * bar_len / 100)
        med_bar = "░" * int(data.meditation * bar_len / 100)
        print(f"\r  专注度: {data.attention:3d} [{att_bar:<20}]  "
              f"放松度: {data.meditation:3d} [{med_bar:<20}]  "
              f"眨眼: {data.blink:3d}  ", end="")

    parser.set_callback(on_data)
    sim.set_callback(on_data)

    print("模拟数据生成中... (Ctrl+C 停止)\n")
    try:
        while True:
            sim.update()
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n已停止")


def demo_simulated_pc_control():
    """演示 4：模拟脑控电脑"""
    print("=" * 50)
    print("  模拟脑控电脑")
    print("=" * 50)

    controller = BrainPcController()
    sim = TGAMSimulator()

    sim.set_callback(controller.on_tgam_data)

    print("\n模拟脑控中... (Ctrl+C 停止)\n")
    print("  专注度会周期性上升到 90，触发当前动作后自动切换\n")

    try:
        while True:
            sim.update()

            # 显示状态
            att = int(sim._attention)
            action = controller.actions[controller.current_action_idx]
            bar = "█" * int(att * 20 / 100)
            print(f"\r  专注度: {att:3d} [{bar:<20}]  当前动作: {action[0]}  ",
                  end="")

            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    print("=" * 50)
    print("  脑控电脑 - TGAM 脑电模块演示")
    print("=" * 50)
    print()
    print("选择模式：")
    print("  1. 实时数据监控 (需要 TGAM 硬件)")
    print("  2. 脑控电脑 (需要 TGAM 硬件)")
    print("  3. 模拟数据监控 (无需硬件)")
    print("  4. 模拟脑控电脑 (无需硬件，推荐先试这个)")
    print()

    choice = input("请输入选项 (1-4): ").strip()

    if choice == '1':
        demo_monitor()
    elif choice == '2':
        demo_pc_control()
    elif choice == '3':
        demo_simulated()
    elif choice == '4':
        demo_simulated_pc_control()
    else:
        print("无效选项")