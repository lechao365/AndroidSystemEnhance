# C/C++ 编码硬规则（lcview 及内核/用户态协议栈）

> **规则 ID**：`CXX-001` / `CXX-002` / `CXX-003` / `CXX-004`
> - `CXX-001`：跨进程二进制协议的多字节字段必须显式做字节序转换（`cpu_to_le32/le16` 内核侧、`le32toh/le16toh` 用户态），禁止裸 `memcpy` 假设平台小端。
> - `CXX-002`：错误路径必须还原状态（不推进指针/释放已占用资源）；整数运算前必须校验上限防溢出；进程/对象重启后状态必须从持久层恢复；禁止错误吞噬。
> - `CXX-003`：JSON/二进制配置字段必须前置 `isMember + isXxx` 校验；禁止 try/catch 兜底（Android 默认 `-fno-exceptions`，编译失败）；数组/哈希访问前必须检查边界与存在性。
> - `CXX-004`：长生命周期线程致命错误必须执行 4 步退出（置 alive=false → notify 等待者 → ERROR 日志 → 进程 exit）；外部接口在后台线程死亡时必须返回错误或明显标记，禁止伪装正常返回空数据。

## 1. 适用范围与加载时机

- **适用对象**：`~/workspace/` 与 `patchs/` 下所有 C/C++ 源码，重点覆盖：
  - 内核驱动（`drivers/staging/android/lcview/`、`lcview_ring_write`、`lcview_builder_*`、`copy_from_user/copy_to_user` 路径）。
  - 用户态 HAL/Daemon（`lechao_lcview.cpp`、`SchemaParser`、`FileWriter`、`readerLoop/workLoop` 等后台线程）。
  - 跨进程二进制协议格式定义与解析（record 头、schema 字段、JSON line）。
- **加载时机**：编写或修改上述任何 C/C++ 代码前必须先加载本规则（AGENTS.md 已声明）。与 `SRC-001` 配合：先改 `~/workspace/` 源码，验证通过后再归档。

## 2. 规则总览

| 规则 ID | 类别 | 一句话约束 |
|---------|------|-----------|
| `CXX-001` | A 类·字节序/序列化 | 跨进程二进制协议字段必须显式 `cpu_to_le*/le*toh`，禁止裸 `memcpy` |
| `CXX-002` | B 类·资源生命周期与边界 | 错误路径还原状态、运算前防溢出、重启后从持久层恢复 |
| `CXX-003` | C 类·外部输入防御 | JSON/二进制字段前置 `isMember+isXxx` 校验，禁 try/catch 兜底 |
| `CXX-004` | D 类·故障可见性 | 长生命周期线程致命错误必须 4 步退出，禁止静默 return |

---

## 3. CXX-001 字节序 / 序列化一致性（A 类）

### 3.1 问题描述

跨进程二进制协议（内核 ↔ 用户态、HAL ↔ Daemon）一旦在不同字节序假设下 `memcpy` 多字节字段，会导致解析端拿到错误的长度/偏移，进而越界读或写入脏数据。ARM64 当前虽为小端，但显式转换是协议层"自描述"的唯一保证——一旦目标平台或编译器选项变化，隐式假设会瞬间崩塌。

### 3.2 MUST / MUST NOT 清单

1. **MUST**：跨进程二进制协议（内核 ↔ 用户态、HAL ↔ Daemon）的多字节字段必须显式用 `cpu_to_le32/le16`（内核侧）和 `le32toh/le16toh`（用户态）序列化，禁止裸 `memcpy`。
2. **MUST**：注释中的字节序声明必须与代码实现一致（review 时必须逐字段核对，注释说"小端"但代码用主机序即为缺陷）。
3. **MUST NOT**：禁止假设"目标平台总是小端"而省略显式字节序转换——即使 ARM64 当前是小端，也要写成显式（防御编译器/平台迁移）。
4. **MUST NOT**：禁止在协议结构体中使用 `uint32_t` 等主机序类型作为线材格式字段；线材层必须用 `__le32/__le16`（内核）或等价的显式小端封装。

### 3.3 正反例

**正例**（内核侧 `lcview_ring_write` 写入 record 头）：

```c
__le32 total_le = cpu_to_le32(total);
__le32 len_le   = cpu_to_le32(len);
memcpy(buf + OFFSET_TOTAL, &total_le, 4);
memcpy(buf + OFFSET_LEN,   &len_le,   4);
/* 线材格式：小端，total/len 各 4 字节 */
```

**反例 1**（裸 memcpy 假设小端）：

```c
memcpy(buf + OFFSET_TOTAL, &total, 4);   /* 隐式假设主机序 == 小端 */
```

**反例 2**（注释与代码不一致）：

```c
/* record 头为小端 */
memcpy(buf + OFFSET_TOTAL, &total_be, 4);  /* total_be 来自 htonl，注释说小端但写大端 */
```

### 3.4 触发场景

- `lcview_ring_write`、`lcview_ring_read`、`lcview_builder_add_str` 等 record 组包/拆包。
- `SchemaParser::validate`、`SchemaParser::parseJson` 中二进制字段对齐。
- `FileWriter::formatJsonLine` 拼装线材数据。
- `lechao_lcview.cpp` 的 record 解析循环（读取内核侧写入的二进制流）。
- 任何 `copy_from_user/copy_to_user` 携带的结构体多字节字段。

---

## 4. CXX-002 资源生命周期与边界（B 类）

### 4.1 问题描述

错误路径未还原状态会导致读指针错位、资源泄漏、状态机污染；整数运算未先校验上限会溢出回绕，使后续 `total > size` 检查失效；进程/对象重启后状态从 0 起算会覆盖已有数据。这三类问题的共同点是"看起来能跑，但错误场景下悄无声息地破坏数据"。

### 4.2 MUST / MUST NOT 清单

1. **MUST**：错误路径必须还原状态——`copy_to_user` 失败时不推进读指针（保留原偏移供重试或诊断）；`open` 成功获取的资源必须在对应失败路径释放（goto cleanup 或 RAII）。
2. **MUST**：整数运算前必须校验上限防溢出。如 `uint32_t total = PREFIX + len` 前必须先 `if (len > MAX_PAYLOAD - PREFIX) return -EMSGSIZE;`，禁止先加再判。
3. **MUST**：进程/对象重启后状态必须从持久层恢复。如 `FileWriter::openFile` 打开已存在的输出文件后必须 `fstat` 取 `st_size` 恢复 `currentSize`，禁止 `currentSize = 0`。
4. **MUST NOT**：禁止"错误吞噬"——失败时不返回错误码而静默继续（如 `read` 返回 -1 仍把脏 buffer 当成功数据处理）。
5. **MUST NOT**：禁止"注释承诺但未实现"——注释写"重启后从文件恢复大小"但代码 `currentSize = 0;` 即为缺陷。

### 4.3 正反例

**正例 1**（FileWriter 重启恢复）：

```cpp
int FileWriter::openFile(const std::string& path) {
    mFd = open(path.c_str(), O_RDWR | O_CREAT, 0644);
    if (mFd < 0) return -errno;
    struct stat st{};
    if (fstat(mFd, &st) == 0) {
        mCurrentSize = static_cast<size_t>(st.st_size);  /* 从持久层恢复 */
    } else {
        close(mFd); mFd = -1;                            /* 失败路径释放 */
        return -errno;
    }
    return 0;
}
```

**正例 2**（ring_write 先校验上限防溢出）：

```c
if (len > MAX_PAYLOAD - PREFIX)   /* 先校验，避免 PREFIX + len 溢出 */
    return -EMSGSIZE;
uint32_t total = PREFIX + len;    /* 此时 total 必然 < MAX_PAYLOAD */
```

**反例 1**（注释承诺但未恢复）：

```cpp
mCurrentSize = 0;   /* 重启后会从文件恢复大小 — 注释撒谎 */
```

**反例 2**（先加后判，溢出回绕使检查失效）：

```c
uint32_t total = PREFIX + len;    /* len 接近 UINT32_MAX 时 total 回绕成小值 */
if (total > size) return -ENOSPC; /* 永远为 false */
```

**反例 3**（错误吞噬）：

```c
n = read(fd, buf, sizeof(buf));
/* 未检查 n < 0，直接把 buf 当数据解析 */
```

### 4.4 触发场景

- `readerLoop`、`workLoop` 的 `read/write` 循环。
- `FileWriter::openFile`、`FileWriter::write`、`FileWriter::rotate`。
- `lcview_ring_write` / `lcview_ring_read` 的长度计算与边界推进。
- 内核侧 `copy_from_user` / `copy_to_user` 的失败路径。

---

## 5. CXX-003 外部输入防御（C 类）

### 5.1 问题描述

JSON 配置、二进制记录、跨进程消息都是不可信外部输入。直接 `asUInt()/asString()` 在字段缺失或类型不匹配时会让 jsoncpp 抛 `RuntimeError`；Android 默认 `-fno-exceptions`，没有 try/catch 兜底，抛异常即 SIGABRT。数组越界、哈希未命中 `end()` 解引用同样是 crash 源头。

### 5.2 MUST / MUST NOT 清单

1. **MUST**：JSON/二进制配置字段必须用 `isMember + isXxx`（`isUInt/isString/isArray/isObject`）前置校验，禁止假设字段存在且类型正确。
2. **MUST NOT**：禁止使用 try/catch 兜底——Android 默认 `-fno-exceptions`，try/catch 编译失败；即使编译通过也是性能与代码异味。改用前置类型校验 + 显式错误返回。
3. **MUST**：数组索引访问前必须检查 `size()`；`std::unordered_map/std::map` 查找前必须检查 `it != end()`，禁止直接 `m[key]` 假设存在。
4. **MUST**：解析失败时必须返回明确错误码并带上字段名上下文（日志或返回值），禁止静默返回默认值掩盖配置错误。

### 5.3 正反例

**正例**（SchemaParser 前置校验）：

```cpp
bool SchemaParser::parseEvent(const Json::Value& ev, Event& out) {
    if (!ev.isMember("id") || !ev["id"].isUInt()) {
        LOG(ERROR) << "schema: event.id 缺失或非 UInt";
        return false;
    }
    if (!ev.isMember("name") || !ev["name"].isString()) {
        LOG(ERROR) << "schema: event.name 缺失或非 String";
        return false;
    }
    out.id   = ev["id"].asUInt();
    out.name = ev["name"].asString();
    return true;
}
```

**反例 1**（无前置校验，jsoncpp 抛 RuntimeError → SIGABRT）：

```cpp
schema.id = ev["id"].asUInt();   /* 字段缺失时直接 crash */
```

**反例 2**（try/catch 在 -fno-exceptions 下编译失败）：

```cpp
try {
    schema.id = ev["id"].asUInt();
} catch (...) { /* Android 默认禁异常，此处根本编译不过 */ }
```

**反例 3**（数组越界 / 哈希未检查）：

```cpp
uint32_t x = arr[i];            /* 未检查 i < arr.size() */
auto& v = map[key];             /* key 不存在时插入空值，下游拿脏数据 */
```

### 5.4 触发场景

- `SchemaParser::parseJson`、`SchemaParser::validate`。
- 任何 JSON 配置解析（`/vendor/etc/lcview/schema.json` 等）。
- 二进制 record 解析（字段偏移、变长字符串长度）。
- 跨进程消息字段读取（HAL ↔ Daemon 的 IPC payload）。

---

## 6. CXX-004 故障可见性（D 类）

### 6.1 问题描述

长生命周期线程（`readerLoop`/`workLoop`/监听线程）在致命错误时仅 `return`，会让进程"活着但不工作"：init 看到 PID 还在不会重启，daemon 拿不到错误码，上层 `getBatch` 永远返回空数据，无法区分"无数据"和"HAL 已死"。结果是故障被无限期掩盖，排查时毫无现场。

### 6.2 MUST / MUST NOT 清单

1. **MUST**：长生命周期线程异常退出时必须执行 4 步，缺一不可：
   - (1) 置 `alive` 标志为 `false`（`mReaderAlive=false`），让外部接口能快速感知。
   - (2) `notify_all` 等待者（`condition_variable`），唤醒 `getBatch` 等阻塞调用。
   - (3) 记录 `ERROR` 日志含上下文（线程名、errno、最近一次操作）。
   - (4) 进程 `exit(1)` 让 init 决策重启——禁止静默 `return` 让进程"活着但不工作"。
2. **MUST**：外部接口（`getBatch` 等）在检测到后台线程死亡（`!mReaderAlive`）时必须返回错误码或明显标记，禁止伪装正常返回空数据。
3. **MUST NOT**：禁止长生命周期线程在致命错误（`read` 持续失败、fd 永久关闭、协议损坏）时仅 `return`。
4. **MUST NOT**：禁止用 `std::this_thread::sleep_for` + `return` 的"软退出"代替显式 exit——init 感知不到，重启链路断开。

### 6.3 正反例

**正例**（readerLoop 致命错误 4 步退出）：

```cpp
void LechaoLcview::readerLoop() {
    mReaderAlive = true;
    while (mRunning) {
        ssize_t n = read(mFd, buf, sizeof(buf));
        if (n < 0) {
            if (errno == EINTR) continue;
            /* 致命错误：4 步退出 */
            mReaderAlive = false;
            mBatchCv.notify_all();                 /* (2) 唤醒 getBatch */
            LOG(ERROR) << "readerLoop read failed: "
                       << "errno=" << errno
                       << " fd=" << mFd;           /* (3) 上下文 */
            std::exit(1);                          /* (4) 让 init 重启 */
        }
        /* ... 正常处理 ... */
    }
}

android::status_t LechaoLcview::getBatch(Batch* out) {
    std::unique_lock<std::mutex> lk(mMx);
    mBatchCv.wait(lk, [&] { return mReaderAlive == false || !mBatch.empty(); });
    if (!mReaderAlive) return -ENODEV;             /* 显式错误，禁伪装空数据 */
    /* ... */
}
```

**反例 1**（静默 return，进程活着但不工作）：

```cpp
void LechaoLcview::readerLoop() {
    while (mRunning) {
        ssize_t n = read(mFd, buf, sizeof(buf));
        if (n < 0) {
            LOG(ERROR) << "read failed";           /* 有日志但未 exit */
            return;                                /* 进程还活着，getBatch 永远空 */
        }
    }
}
```

**反例 2**（外部接口伪装正常）：

```cpp
android::status_t LechaoLcview::getBatch(Batch* out) {
    if (!mReaderAlive) {
        out->clear();                              /* 伪装"无数据" */
        return android::OK;                        /* 调用方无法区分 HAL 已死 */
    }
    /* ... */
}
```

### 6.4 触发场景

- `readerLoop`、`workLoop`、监听线程（socket/epoll 循环）。
- HAL/Daemon 任何后台线程的生命周期管理。
- init 启动的服务（`service lcview_daemon /system/bin/lcview_daemon`）的异常退出策略。
- 外部接口 `getBatch`、`getStats` 等在后台线程死亡时的返回值约定。

---

## 7. 检查清单（Review 时逐条核对）

> 提交 C/C++ 改动前，reviewer 与作者都必须对下列项打勾；任一不满足即不得合入。

### CXX-001 字节序

- [ ] 所有跨进程多字节字段是否经过 `cpu_to_le*/le*toh` 显式转换？
- [ ] 线材结构体字段类型是否为 `__le32/__le16`（内核）或显式小端封装（用户态）？
- [ ] 注释中的字节序声明是否与代码逐字段一致？
- [ ] 是否存在裸 `memcpy` 多字节字段的反模式？

### CXX-002 资源生命周期

- [ ] 每个错误分支是否还原了状态（不推进指针、释放已占资源）？
- [ ] 整数加法前是否先校验上限（防溢出回绕）？
- [ ] 进程/对象重启后是否从持久层（`fstat`/配置文件）恢复状态？
- [ ] 是否存在"注释承诺恢复但代码 `= 0`"的谎言？
- [ ] 是否存在错误吞噬（失败不返回错误码）？

### CXX-003 外部输入防御

- [ ] 每个 JSON 字段访问前是否 `isMember + isXxx` 双重校验？
- [ ] 是否残留任何 try/catch（Android `-fno-exceptions` 会编译失败）？
- [ ] 数组索引、哈希查找是否都做了边界/存在性检查？
- [ ] 解析失败是否返回明确错误码 + 字段名上下文？

### CXX-004 故障可见性

- [ ] 长生命周期线程的致命错误路径是否完整执行 4 步（置 false / notify / ERROR / exit）？
- [ ] 外部接口在后台线程死亡时是否返回错误码（-ENODEV 等），而非伪装空数据？
- [ ] 是否存在"软退出"（sleep + return）代替显式 exit 的反模式？
- [ ] init 服务是否配置了重启策略（`oneshot` 之外的重启间隔）？

## 8. AGENTS.md 加载触发条件

以下任一条件命中时，AI 必须在改动前加载本规则（已在 `AGENTS.md` 中声明为强制规则）：

1. **路径触发**：改动 `~/workspace/` 或 `patchs/` 下任何 `.c/.cpp/.h/.hpp` 源码。
2. **符号触发**：改动涉及 `lcview_ring_*`、`lcview_builder_*`、`SchemaParser`、`FileWriter`、`readerLoop`、`workLoop`、`getBatch`、`copy_from_user/copy_to_user`。
3. **协议触发**：改动涉及内核 ↔ 用户态、HAL ↔ Daemon 的二进制协议格式（record 头、schema 字段、JSON line）。
4. **配置触发**：改动涉及 JSON 配置解析（schema.json 等）或二进制记录解析。

> 本规则与 `SRC-001` 配合：先改 `~/workspace/` 源码，验证通过后再通过 `lc-sync-code-to-patchs` 归档，禁止直接改 `patchs/`。
