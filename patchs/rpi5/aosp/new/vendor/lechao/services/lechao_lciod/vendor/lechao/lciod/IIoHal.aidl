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
package vendor.lechao.lciod;

import vendor.lechao.lciod.IoStats;
import vendor.lechao.lciod.IoConfig;
import vendor.lechao.lciod.IoEvent;

@VintfStability
interface IIoHal {
    /* 枚举当前所有在线的 USB 设备节点路径，如 ["/dev/vendor_lechao_usbd0"] */
    String[] listDevices();

    /* 获取指定设备的传输统计快照；deviceMinor 对应设备节点路径的数字后缀 */
    IoStats getStats(int deviceMinor);

    /* 重置指定设备的内核端统计计数器（对应 IOC_RESET_STATE） */
    void resetState(int deviceMinor);

    /* 获取指定设备的运行时配置（enabled + flags） */
    IoConfig getConfig(int deviceMinor);

    /* 设置指定设备的运行时配置；返回 true 表示内核接受，false 表示失败 */
    boolean setConfig(int deviceMinor, in IoConfig config);

    /*
     * 从内核事件环形缓冲区读取一条异步事件
     * deviceMinor — 设备节点编号
     * timeoutMs   — 阻塞等待超时（毫秒），0 表示非阻塞
     * 返回 IoEvent，valid=false 表示超时或无事件
     */
    IoEvent readEvent(int deviceMinor, int timeoutMs);
}
