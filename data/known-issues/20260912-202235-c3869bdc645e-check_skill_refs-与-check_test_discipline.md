- schema_version: 1
- issue_id: KI-20260912-003
- title: check_skill_refs 与 check_test_discipline 去掉 --exclude-standard 各自零检出
- discovered_in: 61927c927825
- origin: pre-existing
- severity: P1
- blocking: False
- blocking_reason: 
- status: open
- task: checker-hardening
- resolved_in: 
- archived_in: 
- kind: 

## body

- 方向3: check_skill_refs 与 check_test_discipline 去掉 --exclude-standard 各自零检出。
- 定位: harness/lib/check_skill_refs.py:160-162（git ls-files --cached --others --exclude-standard）；harness/lib/check_test_discipline.py:123-124（git ls-files --others --exclude-standard）。
- 证据: 两检查器把未跟踪文件并入扫描面均依赖 --exclude-standard 排除 .gitignore 忽略产物。实测（临时仓）: `git ls-files --others`（无 --exclude-standard）会列出被 .gitignore 忽略的 sub/generated.log，带 --exclude-standard 后仅列 new_file.py。含义: 被 .gitignore 命中的未跟踪源码/测试文件对两个检查器各自零检出（fail-open 盲区）——新增但未 add 且被 .gitignore 规则误命中的测试文件（含 xfail/skip/sleep 违禁行）或文档/SKILL 文件（含悬空引用）完全逃过 discipline_rc/refs_rc；且无测试断言这两个 ls-files 调用带 --exclude-standard（参数被误删即静默 fail-open）。
- 影响: 未跟踪文件面依赖单个 git 参数，参数回归或被 .gitignore 规则覆盖时检查器静默漏检，守卫假绿。
- 修法方向: 测试补 --exclude-standard 存在性断言；或将"被忽略未跟踪文件"纳入扫描面（对 ignore 规则更保守，宁可误报）。
