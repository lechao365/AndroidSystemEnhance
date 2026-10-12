/* ============================================================
 * LatencyHistogram.aidl — 传输延迟/块大小二维直方图（parcelable）
 * 所属模块: vendor.lechao.lciod
 * 设计目的: 定义 HAL 层传递内核直方图快照（struct
 *           vendor_lechao_usbd_latency_histogram，ioctl ABI v5）的 parcelable。
 *           AIDL 不支持定长数组，counts 以变长扁平数组承载，长度 =
 *           dirs*sizeBins*latencyBins，行主序 [dir][size][lat]。
 * VINTF: 标记 @VintfStability 以保证 OTA 兼容性
 * ============================================================ */
package vendor.lechao.lciod;
@VintfStability
parcelable LatencyHistogram {
    long monoTsNs;          /* 内核单调时钟时间戳（纳秒） */
    int dirs;               /* 方向数（=2：0 读 / 1 写） */
    int sizeBins;           /* 块大小档数（=8） */
    int latencyBins;        /* 延迟档数（=12） */
    long[] sizeBounds;      /* 块大小档上界（字节），长度 sizeBins；饱和末档映射为 INT64_MAX */
    long[] latencyBounds;   /* 延迟档上界（纳秒），长度 latencyBins；饱和末档映射为 INT64_MAX */
    int[] counts;           /* 计数矩阵扁平（长度 dirs*sizeBins*latencyBins），行主序 */
}
