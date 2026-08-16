// ============================================================
// IoConfig.aidl — Vendor 分区 IO 配置数据结构
// 所属模块: vendor.lechao.lciod
// 设计目的: 定义 HAL 层传递设备配置的 parcelable 格式，
//           与内核驱动 struct vendor_lechao_usbd_config 一一对应。
// VINTF: 标记 @VintfStability 以保证 OTA 兼容性
// ============================================================
package vendor.lechao.lciod;
@VintfStability
parcelable IoConfig {
    boolean enabled;  // IO 监控/统计功能启用标志
    int flags;        // 配置标志位（保留给内核扩展使用）
}
