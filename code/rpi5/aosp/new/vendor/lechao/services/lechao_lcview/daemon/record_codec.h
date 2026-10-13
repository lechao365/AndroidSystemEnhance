// ============================================================
// record_codec.h — 日志记录 TLV 字段统一解码器
// 所属模块：LcView 事件日志系统 — Daemon 层
// 设计目的：将"逐字段推进指针、解析 TLV 字段"这一协议遍历逻辑
//   从 SchemaParser::validate 与 FileWriter::formatJsonLine 中抽出，
//   统一为单一解码器，消除两处手写 switch 的重复与漂移风险。
//   调用方各自消费解码结果：
//     - SchemaParser::validate 检查类型/长度合法性
//     - FileWriter::formatJsonLine 读取字段值并格式化输出
// ============================================================

#ifndef LCVIEW_RECORD_CODEC_H
#define LCVIEW_RECORD_CODEC_H

#include <cstdint>
#include <cstddef>
#include <cstring>
#include <string>
#include "../include/lcview_events.h"

namespace vendor {
namespace lechao {
namespace lcview {

// ============================================================
// 显式小端访问助手（CXX-001）
// 线材契约：lcview 线上格式（record 头 + TLV 字段）多字节字段一律小端
// （见 lcview_events.h 字节序契约与大端编译守卫）。用户态读取端统一经
// 此处显式转换，禁止裸 memcpy / 结构体强转假设主机序。ARM64 LE 上转换
// 结果与旧实现字节完全一致（行为不变，仅消除隐式假设）。
// ============================================================
inline uint16_t readLe16(const uint8_t* p)
{
    return static_cast<uint16_t>(p[0]) |
           static_cast<uint16_t>(static_cast<uint16_t>(p[1]) << 8);
}

inline uint32_t readLe32(const uint8_t* p)
{
    return static_cast<uint32_t>(p[0]) |
           (static_cast<uint32_t>(p[1]) << 8) |
           (static_cast<uint32_t>(p[2]) << 16) |
           (static_cast<uint32_t>(p[3]) << 24);
}

inline uint64_t readLe64(const uint8_t* p)
{
    return static_cast<uint64_t>(readLe32(p)) |
           (static_cast<uint64_t>(readLe32(p + 4)) << 32);
}

// FLOAT 线材为 IEEE754 单精度小端：先小端取位模式，再按宿主 float 重解释
// （memcpy 在此仅做位模式类型双关，不承担字节序转换）
inline float readLeFloat(const uint8_t* p)
{
    uint32_t bits = readLe32(p);
    float f;
    std::memcpy(&f, &bits, sizeof(f));
    return f;
}

// record 头多字节字段的显式小端读取（偏移见 lcview_events.h packed 定义）
inline uint16_t recordMagic(const struct lcview_record_hdr* h)
{
    return readLe16(reinterpret_cast<const uint8_t*>(h) +
                    offsetof(struct lcview_record_hdr, magic));
}
inline uint16_t recordEventId(const struct lcview_record_hdr* h)
{
    return readLe16(reinterpret_cast<const uint8_t*>(h) +
                    offsetof(struct lcview_record_hdr, event_id));
}
inline uint64_t recordTimestampNs(const struct lcview_record_hdr* h)
{
    return readLe64(reinterpret_cast<const uint8_t*>(h) +
                    offsetof(struct lcview_record_hdr, timestamp_ns));
}
inline uint32_t recordSeqNo(const struct lcview_record_hdr* h)
{
    return readLe32(reinterpret_cast<const uint8_t*>(h) +
                    offsetof(struct lcview_record_hdr, seq_no));
}
inline uint64_t recordMonoNs(const struct lcview_record_hdr* h)
{
    return readLe64(reinterpret_cast<const uint8_t*>(h) +
                    offsetof(struct lcview_record_hdr, mono_ns));
}

// 字段解码结果
enum class FieldDecodeResult {
    kOk,        // 解码成功，值区可用
    kTruncated, // 数据不足（越界）
    kUnknown,   // 未知字段类型
};

// 解码后的字段描述：类型 + 值区指针/长度
struct DecodedField {
    uint8_t type = 0;        // 原始 wire type（LCVIEW_TYPE_*）
    const uint8_t* value = nullptr; // 值区起始（不含 type 字节）
    size_t valueLen = 0;     // 值区长度（定长字段 = 4/8，变长 = 实际长度）
};

// 从 *ptr 处解码一个 TLV 字段：
//   - 输入 ptr/end 界定当前记录数据区
//   - kOk：推进 *ptr 越过该字段，值区可用
//   - kUnknown：未知类型，推进 1 字节 type（调用方输出 null 后继续
//     遍历，formatJsonLine 的"跳过未知字段继续"语义依赖此推进）
//   - kTruncated：数据不足，不保证推进（out->type 已填充；
//     变长字段已推进过 2B 长度前缀时 *ptr 指向长度后，见 .cpp 注释）
FieldDecodeResult decodeRecordField(const uint8_t** ptr, const uint8_t* end,
                                    DecodedField* out);

}  // namespace lcview
}  // namespace lechao
}  // namespace vendor

#endif /* LCVIEW_RECORD_CODEC_H */
