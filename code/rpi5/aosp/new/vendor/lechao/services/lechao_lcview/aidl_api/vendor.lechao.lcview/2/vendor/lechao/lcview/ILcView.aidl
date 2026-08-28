// ============================================================
// ILcView.aidl — LcView HAL 的 AIDL 接口定义
// 所属模块：LcView 事件日志系统
// 设计目的：定义 HAL（内核驱动读取端）与 Daemon（文件写入端）
//   之间的进程间通信接口。该接口使用 NDK 后端（非 Java），
//   以减少跨进程调用的序列化开销。
//
// @VintfStability 表示此接口遵循 VINTF 稳定性规则，
//   接口签名一旦发布不可随意更改，保证 vendor/system 分离编译兼容。
//
// HAL 侧：内核驱动 → epoll 读取 → LcView 类缓存 → getBatch() 返回
// Daemon 侧：定期 getBatch() → SchemaParser 校验 → FileWriter 写 JSONL
// ============================================================
///////////////////////////////////////////////////////////////////////////////
// THIS FILE IS IMMUTABLE. DO NOT EDIT IN ANY CASE.                          //
///////////////////////////////////////////////////////////////////////////////

// This file is a snapshot of an AIDL file. Do not edit it manually. There are
// two cases:
// 1). this is a frozen version file - do not edit this in any case.
// 2). this is a 'current' file. If you make a backwards compatible change to
//     the interface (from the latest frozen version), the build system will
//     prompt you to update this file with `m <name>-update-api`.
//
// You must not make a backward incompatible change to any AIDL file built
// with the aidl_interface module type with versions property set. The module
// type is used to build AIDL files in a way that they can be used across
// independently updatable components of the system. If a device is shipped
// with such a backward incompatible change, it has a high risk of breaking
// later when a module using the interface is updated, e.g., Mainline modules.

package vendor.lechao.lcview;
@VintfStability
interface ILcView {
  byte[] getBatch();
  int getOverrunCount();
  long getTotalRecords();
}
