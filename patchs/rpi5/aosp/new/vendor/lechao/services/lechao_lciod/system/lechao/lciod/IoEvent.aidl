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
 * ============================================================ */
package system.lechao.lciod;
parcelable IoEvent {
    long timestampNs;    /* 事件发生时的内核单调时钟时间戳（纳秒） */
    int eventType;       /* 事件类型枚举值，见上方说明 */
    int eventValue;      /* 事件附加数值，语义取决于 eventType */
    byte dataDirection;  /* 数据传输方向：0=NONE, 1=READ, 2=WRITE */
    int status;          /* 事件状态码：0=成功，负值=内核错误码 */
    boolean valid;       /* 事件是否有效；false 表示超时或缓冲区为空 */
}
