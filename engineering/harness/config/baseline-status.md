# Baseline 状态登记表

本表登记 patch 资产在晋升链 `archive → candidate baseline → promoted baseline` 上的当前状态。状态语义见 `CONTROL-CHARTER.md` § 2.3，证据字段定义见 `baseline-evidence-template.md`。

---

## 登记表

| baseline_id | status | source_branch | source_commit | sync_manifest | build_result | package_result | board_verify | approved_by | approved_at |
|-------------|--------|---------------|---------------|---------------|--------------|----------------|--------------|-------------|-------------|
| _示例_ BL-20260620-01 | promoted | rpi5-dev | a1b2c3d4 | log/sync.../manifest.tsv | PASS | PASS | PASS | lechao | 2026-06-20T12:34:56+08:00 |

> 实际登记行追加在本表末尾，删除 `_示例_` 行。每个 `baseline_id` 唯一，与 `baseline-evidence-template.md` 实例的 `baseline_id` 主键一致。

## status 取值

| status | 含义 | 允许的下一步 |
|--------|------|-------------|
| `archive` | sync 完成归档，无验证证据 | → `candidate` |
| `candidate` | 已补 build + package 证据 | → `promoted` |
| `promoted` | 证据完整且经批准，可作 revert 真相源 | 终态 |

## 使用约束

1. revert-code-from-patchs 工作流执行前，必须先在本表查到对应 baseline 处于 `promoted` 状态。
2. 状态前进不可回退：`promoted` 不可降级为 `candidate`/`archive`。
3. 仅 `promoted` 行的 `approved_by`/`approved_at` 必填。
