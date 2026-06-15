# LcView 上传链路设计规格

## 概述

本文档描述 LcView 日志打点系统的"二期上传"能力——从设备端 JSONL 落盘到云端入库、前端可视化的完整链路。

**当前已完成的链路：**
```
内核 Builder 打点 → 环形缓冲区 → char dev → HAL epoll 批量读 → Daemon schema校验 → JSONL 落盘
```

**本文补齐的链路：**
```
Daemon 上传线程 → HTTP POST → 云侧 Go 服务 → MySQL 入库 → Grafana 看板
```

## 关键设计决策

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 上传器架构 | daemon 内嵌上传线程 | 减少进程数，共享文件状态 |
| 传输协议 | HTTP/HTTPS (libcurl) | 简单可靠，设备端 AOSP 可用 libcurl |
| 云侧技术栈 | Go + Gin | 原生高并发 (goroutine)，单二进制部署，Docker 镜像小 |
| 数据存储 | MySQL 8.0 | 开源成熟，Grafana 原生数据源，SQL 灵活查询 |
| 前端看板 | Grafana + MySQL 数据源 | 开箱即用可视化+告警，避免重复造轮子 |
| 设备标识 | 硬件序列号 | 全局唯一，无需额外生成 |
| 认证机制 | 设备 Token (预置) | 简单可靠，每台设备唯一 token |
| 文件切割 | 4MB (原 50MB) | 匹配上传粒度，减少单次传输大小 |
| 上传触发 | 4MB 满文件 OR 5min 超时 | 平衡延迟与吞吐 |

## event_id 空间规划

每个业务域预留 1000 个 event_id，通过 `event_id / 1000` 自动确定业务域：

| ID 范围 | 业务域 | domain 值 | 状态 |
|---------|--------|-----------|------|
| 1-999 | USB | usb | 已使用 1-13 |
| 1000-1999 | UFS | ufs | 预留 |
| 2000-2999 | WiFi | wifi | 预留 |
| 3000-3999 | Bluetooth | bluetooth | 预留 |
| 4000-4999 | CPU/内存/温度 | cpu | 预留 |
| 5000-5999 | 存储/文件系统 | storage | 预留 |
| 6000+ | 自定义业务 | custom | 按需扩展 |

## 端侧上传线程设计

### 整体架构变化

当前 daemon 进程改造为双线程：

```
主线程: getBatch() → schema校验 → FileWriter落盘（4MB切割）
上传线程: 扫描 logs/ → 检测可上传文件 → HTTP POST → gzip → uploaded/
```

### 文件切割策略调整

FileWriter 滚动参数调整：

| 参数 | 原值 | 新值 | 说明 |
|------|------|------|------|
| maxFileSizeMb | 50 | 4 | 匹配上传粒度 |
| rotation_interval_h | 24 | 24 | 保持不变 |
| maxTotalSizeMb | 500 | 500 | 保持不变 |

### 可上传文件识别

通过 FileWriter 接口暴露当前活跃文件名：

```cpp
// FileWriter 新增接口
std::string FileWriter::getCurrentLogFilename() const;
```

上传线程扫描 `logs/` 目录时排除当前活跃文件，其余文件均为可上传文件。

### 上传线程核心逻辑

```
上传线程 (uploaderThread):
    while (running):
        1. 扫描 logs/ 目录
           - 排除当前活跃文件 (FileWriter::getCurrentLogFilename())
           - 排除 invalid_records.log

         2. 对每个候选文件检查:
           - file.size >= 4MB → 立即加入上传队列
           - file 首条数据写入时间距今 > 5min → 加入上传队列
             （首条数据写入时间 = 文件 ctime，由 FileWriter 创建新文件时记录）

        3. schema 同步（仅在首次上传或 schema 变更时执行）:
           POST /api/v1/schemas/sync
           Body: lcview_events.json 内容
           触发条件:
             - daemon 首次连接云侧（本地无 .schema_synced 标记文件）
             - lcview_events.json 文件 MD5 与上次同步不同

        5. 对上传队列中每个文件:
           POST /api/v1/events/upload
           Headers: X-Device-SN, X-Device-Token
           Body: multipart/form-data (file + event_id + file_name)

           if 成功 (HTTP 200):
               gzip 压缩文件
               原子 rename 到 uploaded/
           if 失败:
               记录失败次数
               指数退避重试 (1s, 2s, 4s, 8s, 16s, 32s, 最大 5min)

        6. sleep 30s (扫描间隔)
```

### 健壮性设计

| 异常场景 | 处理策略 |
|---------|---------|
| 网络不可用 | 上传失败后指数退避重试，文件保留在 `logs/` 不丢失 |
| 云端服务不可用 | 同上，直到恢复后自动重传 |
| 部分上传失败 | 整文件重传（4MB 不大，不需要分片） |
| `uploaded/` 磁盘满 | 删除最旧 `.gz` 文件腾空间 |
| `logs/` 磁盘满 | 总占用超 500MB 删除最旧已上传文件，再删最旧未上传文件 |
| daemon 重启 | 扫描 `logs/` 中所有非活跃文件，重新上传 |
| 并发安全 | 上传线程只读 `logs/` 目录列表，文件移动使用原子 rename |

### HTTP 上传请求格式

**请求：**
```
POST /api/v1/events/upload HTTP/1.1
Content-Type: multipart/form-data
X-Device-SN: rpi5-0001
X-Device-Token: abc123def456...

--boundary
Content-Disposition: form-data; name="file"; filename="4_usb_transport_start_20260608_p0.jsonl"
Content-Type: application/octet-stream

<JSONL 文件内容>
--boundary
Content-Disposition: form-data; name="event_id"

4
--boundary
Content-Disposition: form-data; name="file_name"

4_usb_transport_start_20260608_p0.jsonl
--boundary--
```

**成功响应：**
```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "received_bytes": 4194304,
    "event_id": 4,
    "rows": 12345
  }
}
```

**失败响应：**
```json
{
  "code": 1001,
  "message": "invalid device token",
  "data": null
}
```

### 设备配置文件

路径：`/data/vendor/lechao_lcview/device.conf`

```
token=abc123def456...
server_url=http://192.168.1.100:8080
```

daemon 启动时读取此文件，上传线程使用其中的 token 和 server_url。

### SELinux 权限扩展

daemon 域新增权限：

```te
# 网络访问（HTTP 上传）
allow lechao_lcview self:tcp_socket { create connect write read };
allow lechao_lcview self:udp_socket { create connect write read };

# DNS 解析（域名场景）
allow lechao_lcview self:udp_socket { recv_msg send_msg };

# device.conf 读取
allow lechao_lcview lechao_lcview_data_file:file { read open getattr };
```

## 云侧接收服务设计

### 技术栈

| 组件 | 技术 | 版本 |
|------|------|------|
| HTTP 框架 | Go + Gin | Go 1.22+, Gin v1.9+ |
| 数据库 | MySQL | 8.0 |
| 前端 | Grafana | 10.x+ |
| 部署 | Docker Compose | v2+ |

### 代码目录结构

```
10-系统特性定制/patchs/cloud/lechao_lcview_server/
├── cmd/
│   └── server/
│       └── main.go                    # 入口
├── internal/
│   ├── config/
│   │   └── config.go                  # 配置加载（环境变量 + YAML）
│   ├── handler/
│   │   ├── upload.go                  # 上传接口处理
│   │   ├── device.go                  # 设备注册/查询接口
│   │   └── schema.go                  # Schema 同步接口
│   ├── model/
│   │   ├── event.go                   # 事件数据模型
│   │   ├── device.go                  # 设备模型
│   │   └── schema.go                  # Schema 模型
│   ├── service/
│   │   ├── ingest.go                  # 数据入库核心逻辑
│   │   ├── auth.go                    # Token 认证
│   │   └── table_manager.go          # 动态建表管理
│   ├── repository/
│   │   ├── event_repo.go              # 事件数据 CRUD
│   │   ├── device_repo.go             # 设备 CRUD
│   │   └── schema_repo.go             # Schema CRUD
│   └── middleware/
│       ├── auth.go                    # Token 校验中间件
│       ├── logger.go                  # 请求日志中间件
│       └── ratelimit.go              # 令牌桶限流中间件
├── migrations/
│   ├── 001_create_devices.sql
│   ├── 002_create_event_schemas.sql
│   └── 003_create_indexes.sql
├── provisioning/
│   ├── datasources/
│   │   └── mysql.yaml                 # Grafana MySQL 数据源配置
│   └── dashboards/
│       ├── lcview-overview.json       # 总览 Dashboard
│       └── lcview-usb-detail.json     # USB 详情 Dashboard
├── configs/
│   └── config.yaml                    # 默认配置
├── docker-compose.yml
├── Dockerfile
├── go.mod
└── go.sum
```

### MySQL 表设计

#### 设备表

```sql
CREATE TABLE devices (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    device_sn VARCHAR(64) NOT NULL UNIQUE COMMENT '硬件序列号',
    token VARCHAR(128) NOT NULL UNIQUE COMMENT '设备认证token',
    name VARCHAR(128) DEFAULT '' COMMENT '设备别名',
    status ENUM('online', 'offline') DEFAULT 'offline',
    last_heartbeat DATETIME COMMENT '最后上报时间',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_device_sn (device_sn),
    INDEX idx_last_heartbeat (last_heartbeat)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

#### 事件 Schema 注册表

```sql
CREATE TABLE event_schemas (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    event_id INT UNSIGNED NOT NULL UNIQUE COMMENT '端侧事件ID',
    event_name VARCHAR(64) NOT NULL COMMENT '事件名称',
    domain VARCHAR(32) NOT NULL COMMENT '业务域: usb/ufs/wifi/bluetooth/cpu',
    description VARCHAR(256) DEFAULT '',
    fields JSON NOT NULL COMMENT 'schema fields 定义',
    table_name VARCHAR(128) NOT NULL COMMENT '对应的事件数据表名',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_event_id (event_id),
    INDEX idx_domain (domain)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

`domain` 由 `event_id / 1000` 自动计算，映射关系：
- 0 → usb, 1 → ufs, 2 → wifi, 3 → bluetooth, 4 → cpu, 5 → storage, 6+ → custom

#### 事件数据表（按 event_id 动态创建）

命名规则：`event_{domain}_{event_name}`

```sql
-- 示例: event_id=4, usb_transport_start
CREATE TABLE event_usb_transport_start (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    device_sn VARCHAR(64) NOT NULL COMMENT '设备序列号',
    ts BIGINT UNSIGNED NOT NULL COMMENT '纳秒时间戳',
    level TINYINT UNSIGNED NOT NULL COMMENT '日志级别',
    device_index BIGINT NOT NULL COMMENT 'schema字段: 设备索引',
    data_direction BIGINT NOT NULL COMMENT 'schema字段: 数据方向',
    bytes_to_xfer BIGINT NOT NULL COMMENT 'schema字段: 传输字节数',
    received_at DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3) COMMENT '入库时间',
    INDEX idx_device_sn (device_sn),
    INDEX idx_ts (ts),
    INDEX idx_device_ts (device_sn, ts),
    INDEX idx_received_at (received_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**动态建表流程：**
1. 设备首次上报新 event_id 的数据
2. 查 `event_schemas` 表，若无记录则从请求中的 schema 信息自动注册
3. 检查 `table_name` 对应的表是否存在
4. 不存在则根据 `fields` JSON 自动生成 CREATE TABLE 语句并执行
5. 后续数据直接批量 INSERT

### API 设计

| 接口 | 方法 | 认证 | 说明 |
|------|------|------|------|
| `/api/v1/events/upload` | POST | Token | 设备上报 JSONL 文件 |
| `/api/v1/schemas/sync` | POST | Token | 设备同步 schema 定义 |
| `/api/v1/devices/register` | POST | Admin | 注册新设备（生成 token） |
| `/api/v1/devices` | GET | Admin | 查询已注册设备列表 |
| `/api/v1/devices/:sn` | GET | Admin | 查询单个设备信息 |
| `/api/v1/schemas` | GET | Admin | 查询所有事件 schema |
| `/health` | GET | 无 | 健康检查 |

**错误码定义：**

| code | 含义 |
|------|------|
| 0 | 成功 |
| 1001 | 无效设备 token |
| 1002 | 设备未注册 |
| 2001 | 无效的 JSONL 数据 |
| 2002 | 未知 event_id |
| 3001 | 服务器内部错误 |

### 上传接口处理流程

```
1. Auth 中间件: X-Device-Token → 查 devices 表 → 获取 device_sn
2. 解析 multipart form (file + event_id + file_name)
3. 查 event_schemas 表确认 event_id 已注册
4. 逐行解析 JSONL:
   - 验证每行 JSON 格式
   - 提取 ts, id, level, f 字段
   - 按 schema 字段顺序映射到表列
5. 批量 INSERT（每 1000 行一批）:
   INSERT INTO event_{domain}_{name} (device_sn, ts, level, ...) VALUES (...), (...), ...
6. 更新 devices.last_heartbeat
7. 返回成功响应（接收行数、字节数）
```

### 高并发处理

| 策略 | 实现 |
|------|------|
| 并发模型 | goroutine-per-request，Go net/http 默认模型 |
| 批量入库 | 逐行解析 + 1000 行一批 INSERT，减少 DB 交互 |
| 限流 | 令牌桶中间件，每设备每秒最多 10 次上传 |
| 请求超时 | 整体 30s，文件解析 10s |
| 优雅关闭 | signal.Notify + http.Server.Shutdown，等待进行中请求完成 |
| 连接池 | database/sql 内置连接池，MaxOpenConns=50, MaxIdleConns=10 |

### Docker Compose 配置

```yaml
version: "3.8"

services:
  lcview-server:
    build:
      context: .
      dockerfile: Dockerfile
    ports:
      - "8080:8080"
    environment:
      - CONFIG_PATH=/app/configs/config.yaml
      - DB_HOST=mysql
      - DB_PORT=3306
      - DB_NAME=lcview
      - DB_USER=lcview
      - DB_PASSWORD=lcview_dev
      - ADMIN_TOKEN=admin_dev_token
    depends_on:
      mysql:
        condition: service_healthy
    restart: unless-stopped

  mysql:
    image: mysql:8.0
    environment:
      MYSQL_ROOT_PASSWORD: root_dev
      MYSQL_DATABASE: lcview
      MYSQL_USER: lcview
      MYSQL_PASSWORD: lcview_dev
    volumes:
      - mysql_data:/var/lib/mysql
      - ./migrations:/docker-entrypoint-initdb.d
    ports:
      - "3306:3306"
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost"]
      interval: 5s
      timeout: 3s
      retries: 10
    restart: unless-stopped

  grafana:
    image: grafana/grafana:10.4.0
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
      - GF_USERS_ALLOW_SIGN_UP=false
    volumes:
      - grafana_data:/var/lib/grafana
      - ./provisioning:/etc/grafana/provisioning
    depends_on:
      mysql:
        condition: service_healthy
    restart: unless-stopped

volumes:
  mysql_data:
  grafana_data:
```

## Grafana 前端看板设计

### Dashboard 结构

```
Dashboard: LcView 总览
├── 变量: $device_sn (设备过滤), $domain (业务域), $time_range (时间粒度)
├── Tab: USB (domain=usb)
│   ├── Row: 概览统计
│   │   ├── Stat: 在线设备数
│   │   ├── Stat: 事件总数 (24h)
│   │   ├── Gauge: 传输错误率
│   │   └── Stat: 平均传输延迟
│   ├── Row: 实时趋势
│   │   ├── Time Series: Transport 吞吐量趋势
│   │   └── Time Series: 传输延迟分布
│   ├── Row: 设备分析
│   │   ├── Pie Chart: 各设备错误分布
│   │   └── Heatmap: USB 事件时间热力图
│   └── Row: 事件明细
│       └── Table: 事件日志表 (可过滤/排序)
├── Tab: UFS (domain=ufs, 未来)
├── Tab: WiFi (domain=wifi, 未来)
└── Tab: ... (按需扩展)
```

### Grafana MySQL 查询示例

```sql
-- Transport 吞吐量趋势 (Time Series Panel)
SELECT
  FROM_UNIXTIME(ts / 1000000000) AS time_sec,
  device_sn AS metric,
  bytes_to_xfer AS value
FROM event_usb_transport_start
WHERE $__timeFilter(FROM_UNIXTIME(ts / 1000000000))
  AND device_sn IN ($device_sn)
ORDER BY time_sec;

-- 错误率统计 (Stat Panel)
SELECT
  ROUND(COUNT(CASE WHEN was_error = 1 THEN 1 END) * 100.0 / NULLIF(COUNT(*), 0), 2) AS error_rate
FROM event_usb_transport_end
WHERE $__timeFilter(FROM_UNIXTIME(ts / 1000000000));

-- 设备事件聚合 (Table Panel)
SELECT
  d.device_sn,
  d.name,
  d.status,
  COUNT(*) AS event_count,
  MAX(FROM_UNIXTIME(e.ts / 1000000000)) AS last_event
FROM event_usb_transport_start e
JOIN devices d ON e.device_sn = d.device_sn
WHERE $__timeFilter(FROM_UNIXTIME(e.ts / 1000000000))
GROUP BY d.device_sn, d.name, d.status
ORDER BY event_count DESC;
```

### 告警规则

| 告警名称 | 条件 | 通知方式 |
|---------|------|---------|
| USB 传输高错误率 | 5min 内 error_rate > 10% | Grafana Alert → Webhook |
| 设备离线 | last_heartbeat > 10min | Grafana Alert → Webhook |
| 传输延迟异常 | 5min 内 avg(elapsed_ns) > 阈值 | Grafana Alert → Webhook |
| 数据入库延迟 | received_at - ts > 60s | Grafana Alert → Webhook |

### Dashboard Provisioning

Grafana 自动加载配置，无需手动配置：

```yaml
# provisioning/datasources/mysql.yaml
apiVersion: 1
datasources:
  - name: LcView MySQL
    type: mysql
    url: mysql:3306
    database: lcview
    user: grafana_reader
    secureJsonData:
      password: grafana_reader_pass
    isDefault: true
    editable: false
```

```yaml
# provisioning/dashboards/dashboards.yaml
apiVersion: 1
providers:
  - name: LcView
    orgId: 1
    folder: LcView
    type: file
    options:
      path: /etc/grafana/provisioning/dashboards/json
      foldersFromFilesStructure: false
```

Dashboard JSON 文件纳入 Git 版本管理。

## 端到端数据流

```
┌───────────────────────────────────────────────────────────────────┐
│  rpi5 设备端 (Android)                                             │
│                                                                   │
│  内核模块 → Builder API → 环形缓冲区 → char dev                    │
│                                         │                         │
│  HAL 进程 ←──epoll批量读────────────────┘                        │
│     │                                                             │
│     │ Binder AIDL getBatch()                                      │
│     ▼                                                             │
│  Daemon 进程                                                       │
│  ┌──────────────────────────────────────────────┐                 │
│  │ 主线程: schema校验 → FileWriter落盘 (4MB切割)  │                 │
│  │ 上传线程: HTTP POST → gzip → uploaded/         │                 │
│  └──────────────────────────────────────────────┘                 │
│     │                                                             │
│     │ HTTP/HTTPS (libcurl)                                        │
│     │ X-Device-SN + X-Device-Token                                │
└─────┼─────────────────────────────────────────────────────────────┘
      │
      │  4MB JSONL / 5min超时
      ▼
┌───────────────────────────────────────────────────────────────────┐
│  云侧 (WSL2 前期 → 商用云后续)                                      │
│                                                                   │
│  ┌─────────────┐   ┌──────────────┐   ┌───────────────┐          │
│  │ lcview-server│──→│   MySQL 8.0  │←──│    Grafana     │          │
│  │ (Go/Gin)     │   │              │   │                │          │
│  │ :8080        │   │ devices      │   │ MySQL数据源    │          │
│  │              │   │ event_schemas│   │ Dashboard      │          │
│  │ · 认证       │   │ event_*_*    │   │ 告警规则       │          │
│  │ · 解析JSONL  │   │              │   │ :3000          │          │
│  │ · 批量入库   │   │ :3306        │   │                │          │
│  └─────────────┘   └──────────────┘   └───────────────┘          │
│                                                                   │
│  Docker Compose 一键部署                                            │
└───────────────────────────────────────────────────────────────────┘
```

## 设备注册流程

```
首次部署:
1. 运维在云侧调用 POST /api/v1/devices/register
   Body: {"device_sn": "rpi5-0001", "name": "实验室1号机"}
   Response: {"device_sn": "rpi5-0001", "token": "abc123..."}

2. Token 写入设备配置文件: /data/vendor/lechao_lcview/device.conf
   内容:
     token=abc123...
     server_url=http://192.168.1.100:8080

3. daemon 启动时读取 device.conf → 上传线程使用 token 认证
```

## 编译与开发流程

### 端侧（daemon 改造）

```bash
cd /home/lechao/workspace/aosp
source build/envsetup.sh && lunch aosp_rpi5-bp1a-userdebug
m lechao_lcview           # 编译 daemon（含上传线程）
```

### 云侧（Go 服务）

```bash
cd /mnt/d/Code/Github/AndroidSystemLearn/10-系统特性定制/patchs/cloud/lechao_lcview_server
go build -o bin/lcview-server ./cmd/server    # 本地编译
docker-compose build                           # Docker 构建
docker-compose up -d                           # 启动全部服务
```

### 部署迁移

WSL2 → 商用云迁移步骤：
1. 同一份代码推送到云服务器
2. 修改 `configs/config.yaml`（数据库密码、端口等）
3. `docker-compose up -d`
4. 设备端 `device.conf` 的 `server_url` 改为公网地址

## 实施阶段

| 阶段 | 内容 | 依赖 | 预计产出 |
|------|------|------|---------|
| Phase 1 | 云侧基础服务：Go API + MySQL 建表 + 设备注册 + 上传接口 | 无 | 可部署的 Docker 镜像 |
| Phase 2 | 端侧上传线程：libcurl 集成 + 文件切割调整 + 扫描上传逻辑 | Phase 1 | 可上传的 daemon 二进制 |
| Phase 3 | Grafana Dashboard 配置：数据源 + Dashboard JSON + 告警 | Phase 1 | 可视化看板 |
| Phase 4 | 端到端联调：多设备上报 + 可视化验证 + 异常场景测试 | Phase 1-3 | 完整可运行系统 |

## 源码路径

| 组件 | 源码路径 | 说明 |
|------|---------|------|
| 端侧 daemon | `/home/lechao/workspace/aosp/vendor/lechao/services/lechao_lcview/daemon/` | AOSP 源码树内直接修改 |
| 端侧 daemon 归档 | `10-系统特性定制/patchs/aosp/vendor/lechao/services/lechao_lcview/daemon/` | 文档/patch 归档 |
| 云侧服务 | `10-系统特性定制/patchs/cloud/lechao_lcview_server/` | Go + Gin + Docker |
| SELinux 策略 | `10-系统特性定制/patchs/aosp/device/brcm/rpi5/sepolicy/` | 新增网络权限 |
