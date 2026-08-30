// ============================================================
// record_codec.cpp — 日志记录 TLV 字段统一解码器实现
// 所属模块：LcView 事件日志系统 — Daemon 层
// 设计目的：实现 decodeRecordField——TLV 字段解码的唯一入口。
//   协议格式（与内核写入端 lcview_builder 一致）：
//     1 字节 type 标识 + 值区：
//       INT32/FLOAT: 4 字节定长
//       INT64:       8 字节定长
//       STRING/BINARY: 2 字节小端长度前缀 + 变长数据
//   边界防护：所有长度读取均先校验剩余字节，杜绝越界读（CXX-002）。
// ============================================================

#include "record_codec.h"
#include "../include/lcview_events.h"
#include <cstring>

namespace vendor {
namespace lechao {
namespace lcview {

FieldDecodeResult decodeRecordField(const uint8_t** ptr, const uint8_t* end,
                                    DecodedField* out)
{
    const uint8_t* p = *ptr;
    if (p >= end)
        return FieldDecodeResult::kTruncated;

    uint8_t type = *p++;
    size_t valueLen;
    switch (type) {
    case LCVIEW_TYPE_INT32:
    case LCVIEW_TYPE_FLOAT:
        valueLen = 4;
        break;
    case LCVIEW_TYPE_INT64:
        valueLen = 8;
        break;
    case LCVIEW_TYPE_STRING:
    case LCVIEW_TYPE_BINARY:
        valueLen = 0;  // 变长，下面读 2B 前缀
        break;
    default:
        // 未知类型：推进 1 字节 type（调用方输出 null 后继续遍历，
        // 与 formatJsonLine 原 default 语义一致），不读值区
        out->type = type;
        *ptr = p;
        return FieldDecodeResult::kUnknown;
    }

    if (type == LCVIEW_TYPE_STRING || type == LCVIEW_TYPE_BINARY) {
        // 2 字节小端长度前缀
        if ((size_t)(end - p) < 2) {
            out->type = type;
            out->valueLen = 0;  // 长度前缀缺失（未读到长度）
            return FieldDecodeResult::kTruncated;
        }
        uint16_t flen;
        memcpy(&flen, p, 2);
        p += 2;
        if ((size_t)(end - p) < flen) {
            out->type = type;
            out->valueLen = flen;  // 已读到长度，但值数据不足（调用方可用
                                   // 该值区分"前缀缺失"与"数据越界"）
            *ptr = p;              // 推进到 len 前缀之后，供调用方计算 remaining
            return FieldDecodeResult::kTruncated;
        }
        valueLen = flen;
    } else {
        if ((size_t)(end - p) < valueLen)
            return FieldDecodeResult::kTruncated;
    }

    out->type = type;
    out->value = p;
    out->valueLen = valueLen;
    *ptr = p + valueLen;
    return FieldDecodeResult::kOk;
}

}  // namespace lcview
}  // namespace lechao
}  // namespace vendor
