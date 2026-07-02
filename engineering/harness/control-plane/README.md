# Control Plane

> LcHarness 中央控制面：Repo Registry 管理与生命周期控制

## 定位

- **是什么**：LcHarness 的集中控制面，负责 repo 注册、overlay 注入、状态管理、健康检查与 reconciliation
- **职责边界**：
  - 控制面动作：attach / inject / validate / status / detach / reconcile
  - 不承载 core 层能力（rules / config / lib / validator）
  - 不承载 pack/profile 业务语义
- **真相源**：
  - Repo Registry 保存在 `~/.local/share/lcharness/registry.yaml`（本地，不受 git 跟踪）
  - Overlay 缓存在 `~/.local/share/lcharness/overlays/<repo-hash>/`
  - 当前仓内只存放控制面脚本，不存放注册状态

## 目录说明

| 脚本 | 作用 | 调用方式 |
|------|------|---------|
| [`lc-repo-registry.sh`](./lc-repo-registry.sh) | Repo Registry 读写（add / remove / list / get / update / exists） | `bash lc-repo-registry.sh add <path> --profile <name>` |
| [`lc-attach.sh`](./lc-attach.sh) | attach + inject + validate 一键闭环入口 | `bash lc-attach.sh <repo-path> --profile <name>` |
| [`lc-status.sh`](./lc-status.sh) | 状态查询 + 健康检查 | `bash lc-status.sh [repo-id]` |
| [`lc-inject.sh`](./lc-inject.sh) | Overlay 注入（创建目录结构 + 标记文件） | `bash lc-inject.sh <repo-id>` |
| [`lc-validate.sh`](./lc-validate.sh) | Overlay 状态验证（healthy/stale/broken/detached/attached） | `bash lc-validate.sh <repo-id>` |
| [`lc-reconcile.sh`](./lc-reconcile.sh) | Stale/Broken/Attached 修复 | `bash lc-reconcile.sh <repo-id>` |
| [`lc-detach.sh`](./lc-detach.sh) | 解注入 + registry 清理 | `bash lc-detach.sh <repo-id>` |

## 状态模型

```
detached → attached → injected → healthy ↔ stale ↔ broken → detached
```

| 状态 | 判定条件 |
|------|---------|
| detached | registry 中无记录 |
| attached | registry 有记录，但 overlay 目录不存在 |
| injected | overlay 目录 + `.lcharness-overlay` 标记文件存在 |
| healthy | injected + 标记文件字段与 registry 一致 |
| stale | 标记文件字段与 registry 不一致（profile/version 变更） |
| broken | 标记文件损坏 / 关键子目录缺失 / 权限错误 |

## 使用方式

```bash
# attach 一个新 repo
bash lc-attach.sh /path/to/repo --profile <profile-name>

# 查看所有 repo 状态
bash lc-status.sh

# 查看单个 repo 状态
bash lc-status.sh <repo-id>

# 手动 reconcile
bash lc-reconcile.sh <repo-id>

# 解注入
bash lc-detach.sh <repo-id>
```

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 设计文档 | `docs/specs/2026-07-02-lcharness-framework-design.md` | LcHarness 总体设计基线 |
| 层映射 | `../config/lcharness-layer-map.yaml` | Phase 1 层次映射 |
| 架构参考 | `../reference/lcharness-architecture.md` | 当前工程到 LcHarness 映射 |