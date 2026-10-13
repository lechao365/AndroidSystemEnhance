# 证据链 fail-closed 与 provenance 纪律

> 证据：`harness/lib/check_commit_coverage.py`、`harness/lib/commit_scope.py`、
> `harness/skills/workspace-verify/ws_report.py`、
> `harness/skills/publish-main-base/baseline_register.py`。
> 关联规则：`CDP-DOD-002`（引用禁依赖未跟踪产物）、`HPY-002`（原子写）。

## 原则：证据坏掉时拒绝通过，而非默认通过

证据链（收据 → 判定 → promote 登记）若采用 fail-open（坏证据静默跳过 = 等于
没这条证据 = 免检），则「手写一份垃圾收据 / 篡改判定字段」即可零证据通关。
本仓统一采用的纪律是 **fail-closed**：证据缺失/损坏/口径不符时判红或记
`UNKNOWN`，**绝不放行、绝不伪造 PASS**。下面四个点是其具体落地。

## 一、收据 result 白名单

`_receipt_scopes`（`check_commit_coverage.py:261`）把工作区收据作为「提交覆盖」
的唯一证据源，并且：

- `result == "fail"` 的收据**不作覆盖证据**（失败收据不能证明覆盖），跳过；
- `result` 不属于白名单 `("pass", "skip")` 的收据（`FAIL`/空/带尾空格等
  非标准值）**记录为 error 并判红**（`check_commit_coverage.py:318`）。

只精确匹配小写 `fail` 放行其余值的写法曾让「改判定字段/关 known-issue 零收据
通关」成为可能——白名单把口径收紧为「只有明确声称 pass/skip 的收据才算证据」。
这是批次 `e503284f97b9` / `b410b688d206` 的 fail-open 修复。

## 二、commit_scope 目录边界

`commit_scope` 记录收据落盘时刻的提交面清单，用于「发布内容与验证内容绑定」
（`commit_scope.py:1`）。自引用豁免前缀 `data/verify-results` 的匹配**必须带
目录边界**（`commit_scope.py:46`）：

```python
def _is_excluded(path, exclude):
    for p in exclude:
        p = p.rstrip("/")
        if path == p or path.startswith(p + "/"):   # 目录边界，非裸 startswith
            return True
    return False
```

裸 `startswith("data/verify-results")` 会把相邻目录
`data/verify-results-old/...` 一并豁免，使其**静默漏出提交面比对**——验证过的
内容与发布内容不再绑定。边界匹配把「前缀目录」与「同名相邻目录」区分开
（lib-10 修复）。

## 三、打包证据「同批绑定」（H3 provenance 防线）

打包证据（`ws_package` 产出的 JSON）证明「本批镜像确实打包成功」。它必须与
**载体收据同批**，否则历史证据可被张冠李戴：

- 写入侧 `ws_report._resolve_package`（`ws_report.py:342`）：证据自身
  `batch_id` 与本收据 `batch_id` 不一致即**拒绝内嵌**（显式路径）或降级不内嵌
  （自动探测）。
- 登记侧 `baseline_register._match_evidence_batch`
  （`baseline_register.py:97`）：跨批证据返 `None`，上层据此把
  `package_result` 记 **`UNKNOWN`**，不得伪造 `PASS`。
- 真实命中案例：收据 `manual-2610091135` 曾内嵌上一批
  `manual-2610091031` 的打包证据仍被当 `PASS` 采信——H3 修复后该场景判为
  `UNKNOWN` 并阻断 promote 一致性校验。

`UNKNOWN` 的处理遵循 `AGENTS.md` 基线指引：视同 `FAIL` 处理，须人工复核补齐
证据后方可引用。

## 四、原子写登记（证据落盘不半写）

所有证据落盘复用同一原子原语（见
[atomic-evidence-write.md](atomic-evidence-write.md)）。登记器
`baseline_register.save`（`baseline_register.py:67`）走
`cdp_paths.atomic_write_text`（`cdp_paths.py:16`）：并发/中断下不留半写登记
yaml，避免「半写 yaml 按 latest 身份进入 promote 判定」（语义与收据写入同源）。

## 与「未跟踪产物域」的关系（CDP-DOD-002）

上面的证据分两类落盘域，纪律不同：

- **入库可追溯域**（收据正文内嵌、`data/baselines/` 快照、登记 yaml）：作为
  仓库资产随批提交，引用完整性检查须真实存在。
- **gitignore 运行期产物域**（`harness/log/` 下的打包/单测/推送证据、收据工作区
  `data/verify-results/*.md`）：**不得**作为仓库资产引用或断言存在；缺失时走
  「探测缺失降级」（如自动探测不到打包证据就 `UNKNOWN`），不得伪造。收据通过
  「内嵌证据正文」把运行期产物固化为可追溯的仓库内容，正是弥合两域的手段
  （`ws_report.py:298`）。

## 可复用清单

- 判定字段 **MUST** 用白名单：只有明确合法的取值算证据，其余一律判红，不得
  只排除个别非法值。
- 目录/前缀豁免 **MUST** 带目录边界（`p/`），不得裸 `startswith`。
- 跨来源证据 **MUST** 校验携带者身份（batch_id/run_id），不一致记 `UNKNOWN`，
  **MUST NOT** 伪造 `PASS`。
- 证据文件 **MUST** 原子写；运行期产物域缺失 **MUST** 降级而非断言。
