# Boot 诊断报告模板

> 本模板约束 AI（opencode）在收到 EvidenceBundle 后产出的诊断报告格式。
> 报告路径：与本次 `evidence_bundle.json` 同目录，文件名固定为 `diagnosis-report.md`。

## 报告结构

每份诊断报告必须包含以下 7 节，顺序固定：

## 1. 结论
- 整体状态：PASS / FAIL
- 是否命中"zygote 未正常进入稳定 running 状态"这一类症状
- 当前是否建议进入源码试探性修复

## 2. 证据链
- suite / case 结果
- reboot transcript / serial snippet
- init / service 状态
- crash / tombstone
- kmsg 等辅助信号

## 3. 现象归类与不确定性
- 只区分"确定事实 / 相关异常现象 / 当前不确定点"
- 不强行下唯一根因结论

## 4. 调查线索（用户提供，未验证）
- 最近改动模块
- suspect 范围
- 首次出现版本 / 构建
- 其他备注

## 5. 候选修复方向（人工执行）
- 每个方向都要包含：支撑证据 / 不确定点 / 目标源码范围 / 候选 diff / 风险说明 / 验证命令

## 6. 建议新增 / 调整 case
- 只给建议，不自动修改 YAML

## 7. 循环终止建议
- 是否建议人工 review
- 是否建议进入下一轮改码 / 编译 / 重测
- 若证据不足，明确写"不建议直接改码"

## AI 行为约束

1. AI 必须按此模板产出诊断报告，不得改成自由格式
2. 报告路径必须与本次 `evidence_bundle.json` 同目录
3. 第 5 节修复方向必须具体到 `~/workspace/` 文件路径和函数 / rc stanza / sepolicy rule / service 定义位置
4. 第 5 节的候选 diff 必须标注为"候选"，不得伪装成已验证正确修复
5. 第 6 节的 YAML 建议不自动应用，只给人工 review
6. 用户线索必须标记为"用户提供，未验证"，不得当成客观事实
7. 报告必须区分"确定事实 / 相关异常现象 / 当前不确定点"，不强行下唯一根因结论
