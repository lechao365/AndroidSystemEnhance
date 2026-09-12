- schema_version: 1
- issue_id: KI-20260910-002
- title: content_tree ref 模式 rm --cached 缺 -f，排除路径在 ref 与工作树有差异即抛 RuntimeError
- discovered_in: 5a52d365dafd
- origin: pre-existing
- severity: P1
- blocking: False
- blocking_reason: 
- status: fixed
- task: content-tree
- resolved_in: cba533c
- archived_in: 
- kind: 

## body

- 现场: harness/lib/content_tree.py:80 排除路径 `rm --cached -r -q --ignore-unmatch` 缺 -f。ref 模式下索引内容为 read-tree 的 ref 树，当排除路径在 ref 树与工作树版本不同（如 data/verify-results/trend.md，HEAD^ 树内为旧版、工作树已更新）时，git rm --cached 报「staged content different from both the file and the HEAD」抛 RuntimeError。实测 `content_tree --tree HEAD^` exit 1 崩于 trend.md；`content_tree --tree HEAD` 正常返 f1702a1a627e（与 board 收据 verified_tree 一致）。
- 缺陷归属: KIR-001 回退验证——rm --cached 行系 content_tree.py 历史代码非本批新增；上批（5a52d36 修 KI-20260910-001 把 add -A 收进 else）使 ref 模式索引=ref 树，排除路径与工作树差异开始暴露此脆弱性，属「上批改动提高发作概率、缺陷本体早已存在」场景，非本批引入、不阻塞本批。
- 影响: ref 模式下若排除集合内路径在 ref 树与工作树有差异即整树计算失败，promote 树等价断言（dev HEAD^{tree} vs board verified_tree）在比对祖先 ref 时可能因 trend.md 等运行态文件崩，需 -f 强制删除。
- 修法方向: rm --cached 补 -f（git rm --cached -r -q -f --ignore-unmatch），ref 模式只删除索引内路径不影响工作树文件；并补 ref 模式排除路径差异的判红测试（现测试未覆盖）。
- 闭环: 已于 cba533c 修复：rm --cached 补 -f。
