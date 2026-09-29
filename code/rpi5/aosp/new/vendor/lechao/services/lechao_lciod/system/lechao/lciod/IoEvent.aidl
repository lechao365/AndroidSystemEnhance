/* ============================================================
 * IoEvent.aidl — System 分区 IO 异步事件数据结构（parcelable）
 * 所属模块: system.lechao.lciod
 * 设计目的: 定义 system daemon 向上层暴露的事件格式，
 *           与 vendor IoEvent 字段完全相同（1:1 直传）。
 *           由 readIoEvent() 返回，valid=false 表示无事件或超时。
 *
 * eventType 可能值:
 *   0 — NONE
 *   1 — TRANSPORT_ERROR
 *   2 — STALL
 *   3 — DATA_CORRUPT
 *   4 — TIMEOUT
 *   5 — RESET
 *   6 — RATE_DEGRADED
 *
 * v3 新增字段语义（R-14，与 vendor IoEvent 1:1 直传）:
 *   wallTimeNs — 墙钟时间戳（CLOCK_REALTIME），与 timestampNs（mono）双时间戳。
 *   opcode/lba/bytes/retry — SCSI 命令上下文；RATE_DEGRADED 复用 lba 为降级
 *     基线速率、status 为降级阈值速率，判定降级幅度。
 * ============================================================ */
package system.lechao.lciod;
parcelable IoEvent {
    long timestampNs;    /* 事件发生时的内核单调时钟时间戳（纳秒） */
    int eventType;       /* 事件类型枚举值，见上方说明 */
    int eventValue;      /* 事件附加数值，语义取决于 eventType */
    byte dataDirection;  /* 数据传输方向：0=NONE, 1=READ, 2=WRITE */
    int status;          /* 事件状态码：0=成功，负值=内核错误码；RATE_DEGRADED 为降级阈值速率 */
    boolean valid;       /* 事件是否有效；false 表示超时或缓冲区为空 */
    long wallTimeNs;     /* v3：墙钟时间戳（CLOCK_REALTIME 纳秒），与 timestampNs 双时间戳 */
    int opcode;          /* v3：SCSI 操作码，无可得命令上下文时为 0 */
    long lba;            /* v3：SCSI 起始逻辑块地址；RATE_DEGRADED 复用为降级基线速率 */
    int bytes;           /* v3：本次传输有效字节数 */
    int retry;           /* v3：命令重试次数 */
}
