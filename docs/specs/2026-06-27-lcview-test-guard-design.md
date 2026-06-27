# LcView 测试防护体系 + 编码规范设计

> **日期**: 2026-06-27
> **背景**: P0 检视修复暴露 4 类模式化 bug（字节序、资源生命周期、输入防御、故障静默），现有项目零 C/C++ 单元测试、零编码规范，全靠人工 review。
> **目标**: 建立 gmock 单元测试 + C++ 编码规范双保险，拦截同类 bug 复发。

## 一、Bug 模式归类

| 类别 | P0 实例 | 根因模式 | 拦截手段 |
|---|---|---|---|
| **A. 字节序/序列化** | S5（4B/2B 前缀主机序 vs 注释漂移） | 跨进程二进制协议未固定字节序 | 字节序往返测试 + 硬规则 |
| **B. 资源生命周期** | S1(UAF)、S2(copy_to_user 丢数据)、S3(溢出)、S8(currentSize 不恢复) | 错误路径/边界未覆盖 | 边界条件单测 + 硬规则 |
| **C. 输入防御不足** | S9（JSON 字段缺失 crash） | 外部输入未防御 | 畸形配置单测 + 硬规则 |
| **D. 故障静默** | S7（readerLoop 死亡不感知） | 异常不传播 | 故障注入测试(gmock) + 硬规则 |

## 二、测试覆盖矩阵

| 测试文件 | 拦截 | 类型 | 运行位置 |
|---|---|---|---|
| `SchemaParser_test.cpp` | S9, S5(daemon) | gtest 断言 | Host |
| `FileWriter_test.cpp` | S8, S5(daemon) | gtest + 临时目录 | Host |
| `record_codec_test.cpp` | S5 端到端往返 | 手工构造 fixture | Host |
| `LcView_test.cpp` | S7 | gmock(DdeviceReader) | Host |

## 三、关键技术决策

- **gmock**：用于 mock LcView 的 DeviceReader 接口，测试 readerLoop 故障路径
- **host_supported: true**：秒级反馈，不依赖刷机
- **手工构造 fixture**：`kernel_record_sample.bin` 作为内核/daemon 的契约快照
- **LcView 可测性改造**：抽出 `DeviceReader` 虚接口，构造函数注入

## 四、新增文件清单

```
engineering/harness/rules/cxx-coding-rules.md
~/workspace/aosp/vendor/lechao/services/lechao_lcview/tests/
├── Android.bp
├── SchemaParser_test.cpp
├── FileWriter_test.cpp
├── record_codec_test.cpp
├── LcView_test.cpp
└── fixtures/ (内联在测试文件中)
```

## 五、编码规范 4 条硬规则

- **CXX-001 字节序**：跨进程二进制多字节字段必须 `cpu_to_leXX`/`leXXtoh`
- **CXX-002 资源生命周期**：错误路径必须还原状态；整数运算前校验上限；重启后状态必须从持久层恢复
- **CXX-003 输入防御**：JSON 字段必须 `isMember+isXxx` 前置校验；禁止 try/catch
- **CXX-004 故障可见性**：长生命周期线程异常退出必须设标志+notify+ERROR+exit

## 六、CI 集成

`mk_rpi5_full_image.sh` 编译后插入 `atest lechao_lcview_unit_test -- --host`，失败退出码 4。
