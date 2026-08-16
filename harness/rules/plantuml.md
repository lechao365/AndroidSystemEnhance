# PlantUML 编写约束

> **规则 ID**：`DOC-002`
> - `DOC-002`：编写任何 PlantUML 图表前，必须遵守本文件的渲染失败经验清单（禁止空图块、UML 块内禁止花括号占位符、必须显式闭合、条件块内禁止 fork、活动图颜色使用新语法）。

> 本文档记录实际遇到过的 PlantUML 渲染失败问题及其修复方案，防止重犯。

## 适用范围与加载时机

- **适用对象**：`docs/` 目录下所有 PlantUML 图表文件（`.puml`/`.plantuml`）以及 Markdown 中 ` ```plantuml ` fenced code block
- **加载时机**：新增或修改任何 PlantUML 图表前

## 强制要求（MUST）

1. **MUST** 每个 `@startuml` 必须在同一 fenced code block 内对应一个 `@enduml`，不得跨块闭合 -- 遗漏 `@enduml` 会导致渲染器把后续内容吞入同一图块，造成语法错误或整页渲染失败。

   ```plantuml
   @startuml
   participant "A" as A
   A -> A : <调用>
   @enduml
   ```

2. **MUST** `@startuml/@enduml` 内必须包含至少一个图形元素（participant、start、rectangle 等） -- 即使是模板也不能只有注释或完全为空，否则 PlantUML 报 "must contain at least one shape"。

3. **MUST** PlantUML 代码块内的占位符统一使用尖括号 `<>` -- 模板占位符 `{模块名称}`、`{调用}` 会被 PlantUML 解释为 package/object 等语法块的定界符，导致解析错误。

   ```plantuml
   @startuml
   participant "<子模块>" as M1
   Caller -> M1 : <调用>
   @enduml
   ```

4. **MUST** 活动图（Activity Diagram）节点着色使用新语法 `<<#color>>` 附着在 `;` 之后 -- 旧语法 `#color:label;` 已被 PlantUML 标记为 deprecated，渲染时会报语法警告。

   ```plantuml
   @startuml
   start
   if (条件?) then (yes)
     :return -1;<<#pink>>
   else (no)
     :return 0;<<#lightgreen>>
   endif
   stop
   @enduml
   ```

## 禁止行为（MUST NOT）

1. **MUST NOT** `@startuml/@enduml` 内只有注释或完全为空 -- 会导致渲染失败（"must contain at least one shape"）。

   ```plantuml
   @startuml
   ' 时序图
   @enduml
   ```

2. **MUST NOT** 在 `plantuml` fenced code block 内使用 `{}` / `{{}}` 占位符 -- 会被 PlantUML 解释为 package/object 等语法块的定界符，导致解析错误。正文 Markdown 中仍可使用 `{}` 或 `{{}}`。

   ```plantuml
   @startuml
   participant "{子模块}" as M1
   Caller -> M1 : {调用}
   @enduml
   ```

3. **MUST NOT** 在 `if/else` 条件块内部嵌套 `fork/fork again` -- `fork/fork again` 是并行分支语法，不能嵌套在条件块内部，会导致语法错误。应使用 `repeat/repeat while` 表达重试循环，或用 `if/else` 表达互斥分支。

   ```plantuml
   @startuml
   start
   if (cond?) then (是)
       :A;
   else (否)
       fork
           :B;
       fork again
           :C;
       end fork
   endif
   stop
   @enduml
   ```

   正确做法用 `repeat` 替代：

   ```plantuml
   @startuml
   start
   repeat
     :操作;
   repeat while (失败?)
   stop
   @enduml
   ```

4. **MUST NOT** 使用 `#color:label;` 旧语法对活动图节点着色 -- 已被 deprecated，渲染时报语法警告。颜色须移到语句末尾使用 `<<#color>>`。

   ```plantuml
   @startuml
   start
   if (条件?) then (yes)
     #pink:return -1;
   else (no)
     #lightgreen:return 0;
   endif
   stop
   @enduml
   ```

## 例外清单

| 场景 | 允许的行为 | 不允许的行为 |
|------|----------|------------|
| 普通 Markdown 正文、表格、非 plantuml 代码示例 | 使用 `{}` / `{{}}` 占位符 | -- |
| 时序图、组件图、类图等非活动图图型 | 不受颜色新语法 `<<#color>>` 约束 | -- |
| 活动图（`start`/`stop` 语法）中的 `:label;` 节点 | 必须使用 `<<#color>>` 新语法着色 | 使用 `#color:label;` 旧语法 |
| `plantuml` fenced code block 内 | 使用 `<>` 占位符 | 使用 `{}` / `{{}}` 占位符 |
