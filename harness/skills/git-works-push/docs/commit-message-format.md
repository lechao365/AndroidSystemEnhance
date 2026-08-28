# Commit Message 格式（git-works-push）

`<中文type>(<scope>): <subject>` + body bullet（可无）

type 词表（仅此六种）：新增 / 修复 / 重构 / 文档 / 构建 / 杂项
scope：改动行数最多的顶层目录或模块（如 cross-device / workspace-verify / baseline / dev）

示例：
新增(cross-device): CDP 契约解析器与契约文档
修复(workspace-verify): adb 连接静态 fallback 端口解析
构建(baseline): BL-20260823-01 晋升 promoted