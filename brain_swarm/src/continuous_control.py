"""
连续比例控制 (已迁移)

此文件已被 intuitive_bci.py 替代。
IntuitiveBCI 是第一性原理重构的纯脑信号控制模块:
  1. 不做分类、不做平均、不做模式切换
  2. C3 → 连续速度, C4 → 骤降点击, 天然独立
  3. 自适应基线, 永远不需要重新校准
  4. 不用训练, 戴上用, 大脑 5-10 分钟自己学会

请使用:
    from intuitive_bci import IntuitiveBCI

    bci = IntuitiveBCI()
    while True:
        out = bci.step(eeg_chunk)
        print(out.speed, out.click)
"""

from intuitive_bci import IntuitiveBCI