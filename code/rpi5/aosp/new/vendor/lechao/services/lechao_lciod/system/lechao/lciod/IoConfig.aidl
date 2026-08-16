// ============================================================
// IoConfig.aidl — System 分区 IO 配置数据结构
// 所属模块: system.lechao.lciod
// 设计目的: 定义上层传递 IO 配置的 parcelable 数据格式，
//           用于 getIoConfig/setIoService 接口的请求/响应。
// 注意: 与 vendor/ 下的 IoConfig 字段结构相同但包名不同，
//       因为分属不同 AIDL 接口域。
// ============================================================
package system.lechao.lciod;
parcelable IoConfig {
    boolean enabled;  // IO 监控功能的总开关
    int flags;        // 配置标志位，保留用于扩展功能
}
