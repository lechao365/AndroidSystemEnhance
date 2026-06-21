# Engineering

工程能力总目录，负责承载公共工程基础设施与 loop engineering 专属能力；不承载业务源码。

## 一级目录职责

| 目录 | 职责 |
|------|------|
| `engineering/harness/` | 公共 harness engineering 能力层：规则、模板、路径管理、日志观测、跨工程可复用脚本与 workflow |
| `engineering/loop/` | loop engineering 专属能力层：cases、connection、core、scripts、controller、workflows、contracts |
| `engineering/output/` | 本地日志与运行产物目录，不承载实现逻辑 |

## 单向依赖规则

- 允许：`engineering/loop/` 依赖 `engineering/harness/`
- 禁止：`engineering/harness/` 依赖 `engineering/loop/`

## 能力归属判定规则

### 必须放在 `engineering/loop/`
- 包含 loop-specific 语义
- 直接服务 case / suite / connection / transport / session / attempt / rerun / LE runs 生命周期
- 当前仅被 loop 使用
- 抽到 harness 会形成过早公共化

### 允许放在 `engineering/harness/`
- 不含 loop-specific 语义
- 是跨工程基础设施
- 有稳定公共接口
- 不形成 `harness -> loop` 反向依赖

## workflow 归属规则

- 通用工程 workflow -> `engineering/harness/workflows/`
- loop 专属 workflow -> `engineering/loop/workflows/`

## README 同步规则

目录边界、一级目录、核心入口发生变化时，必须同步检查：
- `engineering/README.md`
- `engineering/harness/README.md`
- `engineering/loop/README.md`
