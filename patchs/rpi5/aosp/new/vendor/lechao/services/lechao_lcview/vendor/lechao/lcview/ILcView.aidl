// ============================================================
// ILcView.aidl — LcView HAL 的 AIDL 接口定义
// 所属模块：LcView 事件日志系统
// 设计目的：定义 HAL（内核驱动读取端）与 Daemon（文件写入端）
//   之间的进程间通信接口。该接口使用 NDK 后端（非 Java），
//   以减少跨进程调用的序列化开销。
//
// @VintfStability 表示此接口遵循 VINTF 稳定性规则，
//   接口签名一旦发布不可随意更改，保证 vendor/system 分离编译兼容。
//
// HAL 侧：内核驱动 → epoll 读取 → LcView 类缓存 → getBatch() 返回
// Daemon 侧：定期 getBatch() → SchemaParser 校验 → FileWriter 写 JSONL
// ============================================================

package vendor.lechao.lcview;

@VintfStability
interface ILcView {
    /**
     * 拉取批量日志数据。
     * 内部实现使用 epoll 等待内核驱动可读事件。
     * 有数据时立即返回累积的字节数组；无数据时阻塞最多约 1 秒
     * （由 HAL 侧 kEpollTimeoutMs=1000ms 控制）后返回空数组。
     * 返回的字节数组格式：4 字节 total_len + 二进制记录流（多条拼接）。
     * 每条记录含 lcview_record_hdr + 变长字段。
     * 这种方式避免了每次只读一条记录的高频 IPC 开销。
     */
    byte[] getBatch();

    /**
     * 查询自上次启动以来丢失的日志记录条数。
     * 当内核 ring buffer 写满而 HAL 读取不及时导致数据覆盖时，
     * 溢出计数递增。此值可用于评估日志缓冲区大小是否合理。
     */
    int getOverrunCount();
}
