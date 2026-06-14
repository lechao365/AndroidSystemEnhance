# AndroidSystemEnhance 项目约束

## 同步与归档规则

执行 `patchs/rpi5/sync.sh` 完成一键同步，规则详见 [rules/sync.md](rules/sync.md)。

## PlantUML 画图约束

所有 PlantUML 图表编写前，必须参考 [rules/plantuml.md](rules/plantuml.md) 中的规则，防止渲染失败。

## 权限规则

### 自动放行（无需确认）

- 当前项目目录内所有文件的增删改查
- `~/workspace/` 目录下所有文件的增删改查

### 需确认

- 上述范围之外的写入/修改操作，必须先询问，获得许可后再执行
- 上述范围之外的读取操作，可直接执行，无需确认