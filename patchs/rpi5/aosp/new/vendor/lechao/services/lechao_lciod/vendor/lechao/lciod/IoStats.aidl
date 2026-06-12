/* ============================================================
 * IoStats.aidl — Vendor 分区 IO 统计数据结构（parcelable）
 * 所属模块: vendor.lechao.lciod
 * 设计目的: 定义 HAL 层传递设备统计数据的 parcelable 格式，
 *           与内核驱动 struct vendor_lechao_usbd_stats 字段一一对应。
 *           由 HAL 进程在 getStats() 中填充后通过 Binder 传递。
 * VINTF: 标记 @VintfStability 以保证 OTA 兼容性
 * ============================================================ */
package vendor.lechao.lciod;
@VintfStability
parcelable IoStats {
    /* --- 设备标识 --- */
    int vid;            /* USB 厂商 ID（Vendor ID） */
    int pid;            /* USB 产品 ID（Product ID） */
    String vendor;      /* 厂商名称字符串，最长 32 字节 */
    String product;     /* 产品名称字符串，最长 32 字节 */

    /* --- 读方向统计 --- */
    long readBytes;     /* 累计读取字节数 */
    long readNs;        /* 累计读取耗时（纳秒） */
    long readCmds;      /* 累计读取请求次数 */

    /* --- 写方向统计 --- */
    long writeBytes;    /* 累计写入字节数 */
    long writeNs;       /* 累计写入耗时（纳秒） */
    long writeCmds;     /* 累计写入请求次数 */

    /* --- 错误/异常计数器 --- */
    long errorCount;    /* 传输错误总次数 */
    long resetCount;    /* USB 复位次数 */
    long stallCount;    /* 端点停滞（STALL）次数 */
    long corruptCount;  /* 数据损坏检测次数 */
    long timeoutCount;  /* 传输超时次数 */

    /* --- 设备生命周期计数器 --- */
    long probeCount;       /* 设备探测（接入）次数 */
    long disconnectCount;  /* 设备断开次数 */
    long degradeCount;     /* 速率降级事件次数 */

    /* --- 性能指标 --- */
    long lastTransportLatencyNs; /* 最近一次传输延迟（纳秒） */
    long currentRate;            /* 当前传输速率（字节/秒） */
    long lastEventTsNs;          /* 最近一次事件的时间戳（纳秒） */

    /* --- 状态 --- */
    int lastEventType; /* 最近一次事件类型，见 vendor_lechao_usbd_event_type */
    boolean enabled;   /* 设备监控/统计功能是否启用 */
    int flags;         /* 配置标志位（保留给内核扩展使用） */
}
