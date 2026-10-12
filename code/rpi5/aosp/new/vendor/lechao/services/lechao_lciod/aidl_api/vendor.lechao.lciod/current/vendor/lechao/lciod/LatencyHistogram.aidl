/* ============================================================
 * LatencyHistogram.aidl — 传输延迟/块大小二维直方图（parcelable）
 * 所属模块: vendor.lechao.lciod
 * 设计目的: 定义 HAL 层传递内核直方图快照（struct
 *           vendor_lechao_usbd_latency_histogram，ioctl ABI v5）的 parcelable。
 *           AIDL 不支持定长数组，counts 以变长扁平数组承载，长度 =
 *           dirs*sizeBins*latencyBins，行主序 [dir][size][lat]。
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
parcelable LatencyHistogram {
  long monoTsNs;
  int dirs;
  int sizeBins;
  int latencyBins;
  long[] sizeBounds;
  long[] latencyBounds;
  int[] counts;
}
