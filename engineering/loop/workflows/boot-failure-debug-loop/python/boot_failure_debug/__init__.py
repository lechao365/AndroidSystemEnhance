"""boot-failure-debug-loop v1 package.

为 rp5-serial 提供启动失败诊断闭环：观察 → 分类 → 采样 → 报告。
V1 仅实现 L1（只读采样）与 L2（低风险探测），不做 L3/L4。
"""
