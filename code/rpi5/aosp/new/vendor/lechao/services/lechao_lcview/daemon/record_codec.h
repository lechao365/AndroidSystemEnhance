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
#include <string>

namespace vendor {
namespace lechao {
namespace lcview {

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
//   - 解码成功则推进 *ptr 越过该字段并返回 kOk；失败不推进
//   - 未知类型返回 kUnknown（不推进，调用方决定处理）
FieldDecodeResult decodeRecordField(const uint8_t** ptr, const uint8_t* end,
                                    DecodedField* out);

}  // namespace lcview
}  // namespace lechao
}  // namespace vendor

#endif /* LCVIEW_RECORD_CODEC_H */
