/* ============================================================
 * IoStats.aidl — System 分区 IO 统计数据结构（parcelable）
 * 所属模块: system.lechao.lciod
 * 设计目的: 定义 system daemon 向上层暴露的统计数据格式。
 *           是 vendor IoStats 的投影版本：
 *
 * 字段映射关系（system ← vendor）:
 *   vid, pid, vendor, product  — 设备标识（直传）
 *   readBytes/NS/Cmds          — 读方向统计（直传）
 *   writeBytes/NS/Cmds         — 写方向统计（直传）
 *   errorCount~degradeCount    — 错误/异常/生命周期计数器（直传）
 *   lastTransportLatencyNs     — 最近传输延迟（直传）
 *   lastEventTsNs              — 最近事件时间戳（直传）
 *   lastEventType              — 最近事件类型（直传）
 *
 * 被省略的字段（vendor IoStats 有但 system 没有）:
 *   currentRate  — 当前速率（system 层通过 getAverageRate() 接口按需计算）
 *   enabled      — 监控启用标志（属于管理字段，不暴露给上层）
 *   flags        — 配置标志位（属于管理字段，不暴露给上层）
 *   peakRate     — 峰值速率（仅在 degrade check 中内部使用，不对外暴露）
 * ============================================================ */
package system.lechao.lciod;
parcelable IoStats {
    /* --- 设备标识 --- */
    int vid;            /* USB 厂商 ID */
    int pid;            /* USB 产品 ID */
    String vendor;      /* 厂商名称 */
    String product;     /* 产品名称 */

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
    long stallCount;    /* 端点停滞次数 */
    long corruptCount;  /* 数据损坏次数 */
    long timeoutCount;  /* 传输超时次数 */

    /* --- 设备生命周期计数器 --- */
    long probeCount;       /* 设备探测次数 */
    long disconnectCount;  /* 设备断开次数 */
    long degradeCount;     /* 速率降级次数 */

    /* --- 性能指标 --- */
    long lastTransportLatencyNs; /* 最近一次传输延迟（纳秒） */
    long lastEventTsNs;          /* 最近一次事件时间戳（纳秒） */
    int lastEventType;           /* 最近一次事件类型 */
}
