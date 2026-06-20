# Baseline 证据模板

本模板定义 patch 资产晋升为 **promoted baseline** 时必须落盘的最小证据集。状态登记见 `baseline-status.md`，状态语义见 `CONTROL-CHARTER.md` § 2.3。

---

## 字段定义

| 字段 | 含义 | 必填 | 来源/示例 |
|------|------|------|----------|
| `baseline_id` | 基线唯一标识（与 `baseline-status.md` 主键一致） | ✅ | `BL-20260620-01` |
| `source_branch` | 同步时 workspace 的分支 | ✅ | `rpi5-dev` |
| `source_commit` | 同步时 workspace 的 commit | ✅ | `a1b2c3d4e5` |
| `sync_manifest` | sync 产出的 manifest 路径 | ✅ | `log/sync_code_to_patchs/artifacts/<ts>-manifest.tsv` |
| `build_result` | 增量编译结果（`make bootimage/systemimage/vendorimage`） | ✅ | `PASS` / 附日志路径 |
| `package_result` | 打包镜像结果（`mk_rpi5_full_image.sh`） | ✅ | `PASS` / 附产物路径 |
| `board_verify` | 刷机上板功能验证结果 | ✅ | `PASS` / 附验证摘要 |
| `approved_by` | 批准人（operator） | ✅ | `lechao` |
| `approved_at` | 批准完成时间戳 | ✅ | `2026-06-20T12:34:56+08:00` |

晋升规则：

1. **candidate baseline → promoted baseline** 需全部字段填齐且非空。
2. `archive` 阶段仅需 `baseline_id` / `source_branch` / `source_commit` / `sync_manifest`。
3. `candidate baseline` 阶段需在 archive 基础上补齐 `build_result` / `package_result`。
4. 任一字段缺失 → 状态不可前进，禁止作为 revert 真相源。

---

## 模板（复制填写）

```yaml
baseline_id: BL-YYYYMMDD-NN
source_branch:
source_commit:
sync_manifest:
build_result:
package_result:
board_verify:
approved_by:
approved_at:
```
