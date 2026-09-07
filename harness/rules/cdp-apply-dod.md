# CDP apply 完成判据（Definition of Done）

> **规则 ID**：`CDP-DOD-001` / `CDP-DOD-002` / `CDP-DOD-003`
> 加载时机：执行 cross-device-apply 批次、接入新检查器到自检/门禁、书写
> harness 测试或文档引用时，先加载本规则。

## CDP-DOD-001 检查器门禁三要件

任何检查器（check_skill_refs / check_config / check_ioctl_headers /
gen_manifest --check-only 等）接入自检/门禁链时，须同时满足：

1. **答出调用方**：检查器接入点必须能回答"谁调用我"——在 selfcheck 输出
   行、收据或接线代码中明确该检查器的调用方（如 `manifest_rc` 由
   selfcheck 内调 gen_manifest --check-only 产出）。无调用方的检查器
   （接线前处于 dead code）不得声称已生效（如 check_ioctl_headers 曾有
   实现无调用方，漂移静默无感）。
2. **破坏即判红用例**：必须配套"制造破坏场景 → 该检查器判红（rc 非零 /
   拒写/拒登）"的单元测试，证明门禁真的会拦。只有绿灯用例没有红灯用例
   的判红门禁视同未实现（CI 只跑不判即假绿）。
3. **结果接入判红链**：检查器的 rc 必须进入 ws_report 全 `*_rc` 判红 /
   REQUIRED_RC_KEYS 必查集合，缺一即拒写收据，不静默通过。

## CDP-DOD-002 测试与文档引用禁依赖未跟踪产物

测试与文档引用（含检查器对引用完整性的判定）**不得依赖 gitignore 的
未跟踪产物**（harness/log/ 运行日志、打点文件、跨设备批次等）。判据必须
在**干净克隆**（无任何运行期产物）下通过：

- 文档/SKILL 引用 `harness/log/` 下路径属"描述运行期落盘位置"，非仓库资产，
  引用完整性检查须豁免该前缀（运行期产物域），不得因干净克隆判红。
- 测试断言默认 glob 命中真实产物时，须自建临时产物再断言（如 log_prune
  锚点自证），不得断言真实仓 gitignore 产物存在。
- 收据/证据链引用 gitignore 域文件（如 ws_package 打包证据）时走
  "探测缺失降级"而非"断言存在"，缺证据按 UNKNOWN 处理不伪造。

## CDP-DOD-003 收据逐方向自报调用方与验证

-s/-sv 收据正文（body）对批次的**每个方向**须逐条自报：

1. **调用方**：该方向改动的工具/脚本由谁调用（selfcheck / ws_report /
   git-works-push / gen_manifest 等），改动后如何被实际执行路径覆盖。
2. **验证**：该方向的验证方式与证据（单测用例名 / 自检 rc / 干净克隆判据），
   不得笼统说"已处理"。自报缺失即视为未完成（emit 复盘按此核对）。

## 违规后果

- 违反 CDP-DOD-001：检查器漂移/失效无感，判红门禁形同虚设 → 当批修。
- 违反 CDP-DOD-002：CI/干净克隆恒红或假绿，自检不可复现 → 当批修。
- 违反 CDP-DOD-003：收据无法逐方向归因，emit 复盘无信号 → 补自报重出。
