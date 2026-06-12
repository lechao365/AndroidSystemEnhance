/* ============================================================
 * IIoService.aidl — System 分区 IO 监控服务接口
 * 所属模块: system.lechao.lciod
 * 设计目的: 定义 system daemon 暴露给上层框架/App 的 Binder 接口。
 *           作为 vendor HAL 的代理层，屏蔽底层实现细节：
 *           - 设备路径转换为 minor 编号
 *           - 投影/过滤 vendor 层字段（如省略 enabled/flags 等管理字段）
 *           - 提供计算字段（如 getAverageRate）
 *
 * 与 vendor IIoHal 的方法对应关系:
 *   listDeviceMinors  ← listDevices()（路径→minor 编号转换）
 *   getAverageRate    ← getStats()（计算派生值：总字节/总耗时）
 *   getIoStats        ← getStats()（字段投影，省略 enabled/flags）
 *   resetIoState      ← resetState()
 *   getIoConfig       ← getConfig()
 *   setIoConfig       ← setConfig()
 *   readIoEvent       ← readEvent()
 * ============================================================ */
package system.lechao.lciod;

import system.lechao.lciod.IoStats;
import system.lechao.lciod.IoConfig;
import system.lechao.lciod.IoEvent;

interface IIoService {
    /* 枚举当前所有在线设备的 minor 编号列表 */
    int[] listDeviceMinors();

    /*
     * 计算指定设备的平均传输速率
     * 计算方式: (readBytes + writeBytes) * 1e9 / (readNs + writeNs)
     * 返回值单位: 字节/秒；设备不存在或无数据时返回 0
     */
    long getAverageRate(int deviceMinor);

    /* 获取指定设备的 IO 统计快照（投影版，省略 enabled/flags 等管理字段） */
    IoStats getIoStats(int deviceMinor);

    /* 重置指定设备的内核端统计计数器 */
    void resetIoState(int deviceMinor);

    /* 获取指定设备的运行时配置 */
    IoConfig getIoConfig(int deviceMinor);

    /* 设置指定设备的运行时配置；返回 true 表示成功 */
    boolean setIoConfig(int deviceMinor, in IoConfig config);

    /*
     * 从内核事件缓冲区读取一条异步事件（带超时）
     * timeoutMs — 阻塞等待超时（毫秒），0 表示非阻塞
     */
    IoEvent readIoEvent(int deviceMinor, int timeoutMs);
}
