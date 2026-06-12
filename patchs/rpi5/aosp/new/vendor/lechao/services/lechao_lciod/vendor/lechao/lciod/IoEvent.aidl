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
package vendor.lechao.lciod;
@VintfStability
parcelable IoEvent {
    long timestampNs;    /* 事件发生时的内核单调时钟时间戳（纳秒） */
    int eventType;       /* 事件类型枚举值，见上方说明 */
    int eventValue;      /* 事件附加数值（如错误码、速率值等），语义取决于 eventType */
    byte dataDirection;  /* 数据传输方向：0=NONE, 1=READ, 2=WRITE */
    int status;          /* 事件状态码：0=成功，负值=内核错误码 */
    boolean valid;       /* 事件是否有效；false 表示读取超时或缓冲区为空 */
}
