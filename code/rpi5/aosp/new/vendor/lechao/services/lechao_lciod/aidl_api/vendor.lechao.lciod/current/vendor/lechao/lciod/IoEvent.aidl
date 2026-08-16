/* ============================================================
 * IoEvent.aidl — Vendor 分区 IO 异步事件数据结构（parcelable）
 * 所属模块: vendor.lechao.lciod
 * 设计目的: 定义 HAL 层传递内核异步事件的 parcelable 格式，
 *           与内核驱动 struct vendor_lechao_usbd_event 字段一一对应。
 *           由 readEvent() 返回，valid=false 表示无事件或超时。
 * VINTF: 标记 @VintfStability 以保证 OTA 兼容性
 *
 * eventType 可能值（对应内核 enum vendor_lechao_usbd_event_type）:
 *   0 — NONE（无事件）
 *   1 — TRANSPORT_ERROR（传输错误）
 *   2 — STALL（端点停滞）
 *   3 — DATA_CORRUPT（数据损坏）
 *   4 — TIMEOUT（传输超时）
 *   5 — RESET（设备复位）
 *   6 — RATE_DEGRADED（速率降级）
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
parcelable IoEvent {
  long timestampNs;
  int eventType;
  int eventValue;
  byte dataDirection;
  int status;
  boolean valid;
}
