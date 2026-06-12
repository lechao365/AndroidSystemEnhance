/* ============================================================
 * IIoHal.aidl — Vendor 分区 HAL 层 IO 监控接口
 * 所属模块: vendor.lechao.lciod
 * 设计目的: 定义 vendor HAL 进程暴露给 system daemon 的 Binder 接口。
 *           每个方法通过 deviceMinor 参数定位具体的 USB 设备节点，
 *           对应 /dev/vendor_lechao_usbd<minor>。
 * VINTF: 标记 @VintfStability 以保证跨分区 OTA 兼容性
 *
 * 方法概览:
 *   listDevices  — 枚举当前在线设备的设备节点路径列表
 *   getStats     — 获取指定设备的传输统计快照（来自内核驱动）
 *   resetState   — 重置指定设备的内核端统计计数器
 *   getConfig    — 获取指定设备的运行时配置
 *   setConfig    — 设置指定设备的运行时配置
 *   readEvent    — 从内核事件环形缓冲区读取一条异步事件（带超时）
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
interface IIoHal {
  String[] listDevices();
  vendor.lechao.lciod.IoStats getStats(int deviceMinor);
  void resetState(int deviceMinor);
  vendor.lechao.lciod.IoConfig getConfig(int deviceMinor);
  boolean setConfig(int deviceMinor, in vendor.lechao.lciod.IoConfig config);
  vendor.lechao.lciod.IoEvent readEvent(int deviceMinor, int timeoutMs);
}
