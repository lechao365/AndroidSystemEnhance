/* ============================================================
 * IoStats.aidl — Vendor 分区 IO 统计数据结构（parcelable）
 * 所属模块: vendor.lechao.lciod
 * 设计目的: 定义 HAL 层传递设备统计数据的 parcelable 格式，
 *           与内核驱动 struct vendor_lechao_usbd_stats 字段一一对应。
 *           由 HAL 进程在 getStats() 中填充后通过 Binder 传递。
 * VINTF: 标记 @VintfStability 以保证 OTA 兼容性
 * ============================================================ */
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

package vendor.lechao.lciod;
@VintfStability
parcelable IoStats {
  int vid;
  int pid;
  String vendor;
  String product;
  long readBytes;
  long readNs;
  long readCmds;
  long writeBytes;
  long writeNs;
  long writeCmds;
  long errorCount;
  long resetCount;
  long stallCount;
  long corruptCount;
  long timeoutCount;
  long probeCount;
  long disconnectCount;
  long degradeCount;
  long lastTransportLatencyNs;
  long currentRate;
  long lastEventTsNs;
  int lastEventType;
  boolean enabled;
  int flags;
}
