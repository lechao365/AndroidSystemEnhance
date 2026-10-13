# 内核纯逻辑抽离 + host 双轨单测

> 证据：`code/rpi5/kernel/new/vendor/lechao/LcView/lcview_ring_logic.c`、
> `lcview_ring_logic.h`、`tests/lcview_ring_host_test.c`、
> `tests/lcview_callsite_host_test.c`、`tests/Makefile`、`tests/host_shim/`、
> `lcview_ring.c`。关联规则：`CXX-001`~`CXX-004`。

## 痛点：内核代码「改坏照绿」

内核环形缓冲区的索引算法（可写空间、跨尾部环绕 memcpy、最旧记录驱逐）过去
全是 `lcview_ring.c` 里的 `static` 函数，只随内核模块编译，**没有单元测试**。
逻辑一旦回归（如损坏记录跳过只前移「前缀+头」而撕裂后续流），只能上板后才
暴露。

直接给内核代码写单测的两难：内核 API（`spinlock`/`vmalloc`/`atomic`/`pr_*`）
在 host 不可用，而复制一份「测试版实现」又会与内核实现漂移。

## 设计一：纯逻辑抽离，内核与 host 编译同一份 `.c`

把只依赖「缓冲区大小 + 读写指针」、不依赖任何内核 API 的算法抽到
`lcview_ring_logic.c` / `.h`，用 `#ifdef __KERNEL__` 选择头文件
（`lcview_ring_logic.c:11`）：

```c
#ifdef __KERNEL__
#include <linux/string.h>
#include <linux/errno.h>
#else
#include <string.h>
#include <errno.h>
#endif
```

- **内核侧**：`lcview_ring.c` 经薄包装调用（如 `ring_evict_one`，
  `lcview_ring.c:203`），行为与原 `static` 实现一致。
- **host 侧**：测试直接编译**同一份** `lcview_ring_logic.c`（无 KUnit、无内核
  头），从源码层面杜绝复制漂移（`lcview_ring_logic.h:1`）。

长度前缀写死为 `LCVIEW_RING_LEN_PREFIX`（`lcview_ring_logic.h:27` = 4），
与 `lcview_internal.h` 的 `LCVIEW_LEN_PREFIX_SIZE` 保持同步——这是抽离时唯一
需要人工维护的一致性点，已在头文件注明。

## 设计二：双轨 host 测试

`tests/Makefile:19` 的 `test` 目标编两个可执行并依次运行：

1. **纯逻辑轨** `lcview_ring_host_test`（`tests/lcview_ring_host_test.c`）：
   直接调用 logic 层每个函数，覆盖索引环绕、跨尾部、驱逐、损坏回退的边界。
   用 `CHECK` 宏累计校验数，退出码非零即判红。
2. **调用点轨** `lcview_callsite_host_test`
   （`tests/lcview_callsite_host_test.c`）：把**真实调用点源文件**
   `code/rpi5/kernel/new/vendor/lechao/LcView/lcview_builder.c` 与
   `code/rpi5/kernel/new/vendor/lechao/LcView/lcview_ring.c` 连同 `host_shim/` 下的假内核头
   一起编入（`-D__KERNEL__ -Ihost_shim`，见 `tests/Makefile:17`），直接调用
   真实调用点函数——调用点改坏在 host 层即判红，不再「改坏照绿」。

`host_shim/linux/` 提供 `atomic.h`/`spinlock.h`/`uaccess.h` 等假头，让调用点
文件在 host 上以「等价 shim 语义」编译，绝不进入内核编译。

### callsite 轨如何构造损坏/溢出场景

调用点测试的价值在于「按真实 API 构造越界输入」：

- **溢出场景**：`test_add_str_callsite_overflow`（`lcview_callsite_host_test.c:51`）
  构造恰好 4096 边界的字符串，断言 `lcview_builder_add_str` 返回 `-ENOSPC`。
  修复前调用点只按 `data_offset + total` 对比上限、漏扣 4B 记录前缀，写满时
  记录总长 4100 会被读侧判损坏丢弃。
- **损坏/等长零推进场景**：纯逻辑轨 `test_evict` / `test_corrupt_skip`
  （`lcview_ring_host_test.c`）遍历 `old_len ∈ [1, default_record_len)` 与
  `old_len == size`，断言均回落保守默认跳过——后者消除「`(rpos + size) % size
  == rpos` 零推进死循环」。

配套的纯函数 `builder_str_field_fits`、`ring_corrupt_skip_len`
（`lcview_ring_logic.c:139`、`:146`）把「调用点的边界组合」内聚为可直测的纯
函数，让 callsite 测试能直接判红。

## 设计三：读路径 UAF 防护（先计数再取锁）

`lcview_ring_read` 采用「readers 计数先于锁」的生命周期契约
（`lcview_ring.c:377`）：

- 入口 `atomic_inc(&ring->readers)`（`lcview_ring.c:382`）**先于**
  `mutex_lock`：排队等锁的 reader 同样计入在途，`destroy` 的
  `wait_event(exit_wait, readers == 0)` 会等它拿到锁、查 `shutdown` 直返后
  才归零——杜绝「等待者越过归零判定后访问已释放内存」的 UAF。
- 出口 `atomic_dec_and_test` 归零时 `wake_up(&ring->exit_wait)`
  （`lcview_ring.c:400`）。
- `lcview_ring_destroy`（`lcview_ring.c:167`）先置 `shutdown` 并唤醒，再
  `wait_event` 等 `readers` 归零（`lcview_ring.c:177`），最后才 `vfree` 两个
  缓冲区。callsite 测试新增「destroy 后 read 返 0 / shutdown 含数据停交付 /
  正常与 EMSGSIZE 后 readers 归零」四类判红。

## 设计四：先判后算防溢出

`lcview_ring_write`（`lcview_ring.c:260`）在计算 `total` 之前**先**判
`len` 本身：

```c
if (len > ring->size - LCVIEW_LEN_PREFIX_SIZE)   /* 先判 */
    return -EMSGSIZE;
total = LCVIEW_LEN_PREFIX_SIZE + len;             /* 后算 */
```

若先算 `total = 4 + len`，`len` 接近 `UINT32_MAX` 时求和溢出回绕成小值，会
绕过 `total > ring->size` 检查进入写路径、`memcpy` 越界。先判 `len` 本身时
（`len ≤ size - 4`）`total` 必不溢出，从根上消除回绕（对应 `CXX-003`
输入防御、`CXX-004` 故障静默）。

## 可复用清单

- 内核中「纯标量/纯 memcpy 索引」逻辑 **MUST** 抽到 `*_logic.c` 并经
  `#ifdef __KERNEL__` 选择头，内核与 host **编译同一份源码**。
- 测试 **MUST** 分两轨：纯逻辑轨 + 用 `host_shim` 编真实调用点的 callsite 轨。
- 生命周期（UAF）契约 **MUST** 用「计数先于取锁 + 归零唤醒 + destroy 等待
  归零再释放」，并配 `destroy/readers` 判红用例。
- 涉及「加法后比较上限」的入口 **MUST** 先判被加数上界再求和（先判后算防
  溢出）。
