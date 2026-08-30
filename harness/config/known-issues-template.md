# known-issues 模板

已知问题登记文件模板。实际登记写入 `data/known-issues/<YYYYMMDD-HHMMSS>-<batch_id>-<slug>.md`。
头字段集必须与 `cdp_issue._FIELDS` 完全一致（单测强制：写出头字段集 = 模板 = _FIELDS）。
字段取值枚举见下方逐字段注释；除头字段外的现场、复现步骤等自由信息一律写入正文。

## 头部

```markdown
- schema_version: 1              # 模板版本，当前恒为 1
- issue_id: KI-20260829-001      # 问题唯一编号（发现批次内人工/自动指定）
- title: 一句话问题描述          # 简洁标题，缺省派生文件名 slug
- discovered_in: 01b54c14779f    # 发现时所在 commit（12 位 hex）
- origin: introduced             # 来源：introduced（本批引入）/ pre-existing（历史遗留）
- blocking: false                # 是否阻塞发布（true/false）
- blocking_reason:               # 阻塞原因（blocking=true 时必填）
- status: open                   # 状态：open / scheduled / fixed / wontfix
- task: lcview-refactor          # 大颗粒任务稳定标识（promote 门禁按此过滤；修法描述入正文）
- resolved_in:                   # 解决时所在 commit（未解决留空）
```

## body

```markdown
- 问题现场、复现步骤、失败证据（如收据正文/CDP 原文）
- 修法描述、修复记录与验证结果
```
