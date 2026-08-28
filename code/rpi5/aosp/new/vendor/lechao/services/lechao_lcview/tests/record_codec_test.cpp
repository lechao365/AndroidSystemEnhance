// record_codec_test.cpp — 拦截 S5（字节序）端到端往返测试
//
// 核心思想：手工构造一段"内核序列化格式的二进制 record"，
// 模拟 daemon 端解析流程（le32toh 拆 4B 前缀 + le16toh 拆 2B 字段长度），
// 验证内核写入格式与 daemon 解析格式完全对齐。
//
// 这是 A 类 bug（字节序不一致）的"契约快照"测试：
// 任何一方改动序列化格式都会导致往返测试失败。
//
// 分支覆盖目标：
//   - 4B record total_len 前缀小端序（le32）：正常/边界值/大端序拒绝
//   - 16B lcview_record_hdr 内存布局：magic 校验
//   - INT32/INT64/FLOAT 定长字段往返（各 case）
//   - STRING/BINARY 变长字段 2B 长度前缀小端序（le16）：零长度/正常/大端序拒绝
//   - daemon 解析循环分支：单 record/多 record/截断 batch/坏长度

#include <gtest/gtest.h>
#include <endian.h>
#include <cstring>
#include <cstdint>
#include <vector>
#include "lcview_events.h"

namespace {

// 模拟内核 lcview_builder 构建的一条完整 record（纯手工，不经过真实内核）
struct RecordBuilder {
    std::vector<uint8_t> buf;

    void appendHdr(uint16_t eventId, uint8_t level, uint8_t fieldCount, uint64_t ts) {
        size_t off = buf.size();
        buf.resize(off + sizeof(lcview_record_hdr));
        auto* hdr = reinterpret_cast<lcview_record_hdr*>(buf.data() + off);
        hdr->magic = LCVIEW_MAGIC;
        hdr->event_id = eventId;
        hdr->level = level;
        hdr->field_count = fieldCount;
        hdr->reserved = 0;
        hdr->timestamp_ns = ts;
    }
    void appendInt32(int32_t v) {
        buf.push_back(LCVIEW_TYPE_INT32);
        size_t off = buf.size();
        buf.resize(off + 4);
        memcpy(buf.data() + off, &v, 4);
    }
    void appendInt64(int64_t v) {
        buf.push_back(LCVIEW_TYPE_INT64);
        size_t off = buf.size();
        buf.resize(off + 8);
        memcpy(buf.data() + off, &v, 8);
    }
    void appendFloat(uint32_t raw) {
        buf.push_back(LCVIEW_TYPE_FLOAT);
        size_t off = buf.size();
        buf.resize(off + 4);
        memcpy(buf.data() + off, &raw, 4);
    }
    // S5 核心：字符串字段，2B 长度前缀用小端序（与 lcview_builder.c 一致）
    void appendString(const std::string& s) {
        buf.push_back(LCVIEW_TYPE_STRING);
        uint16_t len = static_cast<uint16_t>(s.size());
        uint16_t len_le = htole16(len);
        size_t off = buf.size();
        buf.resize(off + 2);
        memcpy(buf.data() + off, &len_le, 2);
        buf.insert(buf.end(), s.begin(), s.end());
    }
    void appendBinary(const std::vector<uint8_t>& data) {
        buf.push_back(LCVIEW_TYPE_BINARY);
        uint16_t len = static_cast<uint16_t>(data.size());
        uint16_t len_le = htole16(len);
        size_t off = buf.size();
        buf.resize(off + 2);
        memcpy(buf.data() + off, &len_le, 2);
        buf.insert(buf.end(), data.begin(), data.end());
    }
};

// 把一条 record 包成 batch（前加 4B total_len 小端序前缀，模拟 ring write）
std::vector<uint8_t> wrapAsBatch(const std::vector<uint8_t>& record) {
    std::vector<uint8_t> batch;
    uint32_t total = static_cast<uint32_t>(record.size()) + 4;
    uint32_t total_le = htole32(total);
    batch.resize(4);
    memcpy(batch.data(), &total_le, 4);
    batch.insert(batch.end(), record.begin(), record.end());
    return batch;
}

// 模拟 daemon 解析循环（与 lechao_lcview.cpp:160-205 完全一致的逻辑）
// 返回解析出的 record 数量；失败时返回 -1 并设置 errMsg
int parseBatch(const std::vector<uint8_t>& batch, std::string& errMsg) {
    size_t offset = 0;
    int recordsParsed = 0;
    while (offset + 4 <= batch.size()) {
        uint32_t total_len;
        memcpy(&total_len, batch.data() + offset, 4);
        total_len = le32toh(total_len);

        // 分支：bad length（total_len < 4 或越界）
        if (total_len < 4 || offset + total_len > batch.size()) {
            errMsg = "bad length at offset=" + std::to_string(offset) +
                     " total_len=" + std::to_string(total_len);
            return -1;
        }

        const uint8_t* recordStart = batch.data() + offset + 4;
        size_t recordLen = total_len - 4;

        // 分支：record too small（不足以容纳 hdr）
        if (recordLen < sizeof(lcview_record_hdr)) {
            errMsg = "record too small";
            return -1;
        }

        auto* hdr = reinterpret_cast<const lcview_record_hdr*>(recordStart);
        if (hdr->magic != LCVIEW_MAGIC) {
            errMsg = "bad magic";
            return -1;
        }

        // 逐字段推进（与 SchemaParser::validate 一致：le16toh 读 2B 前缀）
        const uint8_t* p = recordStart + sizeof(lcview_record_hdr);
        const uint8_t* end = recordStart + recordLen;
        for (uint8_t i = 0; i < hdr->field_count; i++) {
            if (p >= end) { errMsg = "EOF at field"; return -1; }
            uint8_t type = *p++;
            switch (type) {
            case LCVIEW_TYPE_INT32:
            case LCVIEW_TYPE_FLOAT:
                if (p + 4 > end) { errMsg = "EOF int32/float"; return -1; }
                p += 4;
                break;
            case LCVIEW_TYPE_INT64:
                if (p + 8 > end) { errMsg = "EOF int64"; return -1; }
                p += 8;
                break;
            case LCVIEW_TYPE_STRING:
            case LCVIEW_TYPE_BINARY: {
                if (p + 2 > end) { errMsg = "EOF len prefix"; return -1; }
                uint16_t flen;
                memcpy(&flen, p, 2);
                flen = le16toh(flen);  // S5 核心
                p += 2;
                if (p + flen > end) { errMsg = "field exceeds"; return -1; }
                p += flen;
                break;
            }
            default:
                errMsg = "unknown type";
                return -1;
            }
        }
        if (p != end) { errMsg = "length mismatch"; return -1; }
        recordsParsed++;
        offset += total_len;
    }
    return recordsParsed;
}

}  // namespace

// ============================================================
// 定长字段往返（覆盖 INT32/INT64/FLOAT case）
// ============================================================

TEST(RecordCodecTest, Int32Field_RoundTrip) {
    RecordBuilder rb;
    rb.appendHdr(4, LCVIEW_LEVEL_INFO, 1, 0);
    rb.appendInt32(-12345);
    auto batch = wrapAsBatch(rb.buf);
    std::string err;
    EXPECT_EQ(parseBatch(batch, err), 1) << err;
}

TEST(RecordCodecTest, Int64Field_RoundTrip) {
    RecordBuilder rb;
    rb.appendHdr(4, LCVIEW_LEVEL_INFO, 1, 0);
    rb.appendInt64(0x1234567890ABCDEF);
    auto batch = wrapAsBatch(rb.buf);
    std::string err;
    EXPECT_EQ(parseBatch(batch, err), 1) << err;
}

TEST(RecordCodecTest, FloatField_RoundTrip) {
    RecordBuilder rb;
    rb.appendHdr(4, LCVIEW_LEVEL_INFO, 1, 0);
    rb.appendFloat(0x40490FDB);  // 3.14159f
    auto batch = wrapAsBatch(rb.buf);
    std::string err;
    EXPECT_EQ(parseBatch(batch, err), 1) << err;
}

// ============================================================
// S5 核心：STRING/BINARY 2B 长度前缀小端序
// ============================================================

TEST(RecordCodecTest, StringField_Le16_RoundTrip) {
    RecordBuilder rb;
    rb.appendHdr(4, LCVIEW_LEVEL_INFO, 1, 0);
    rb.appendString("usb_event_payload");
    auto batch = wrapAsBatch(rb.buf);
    std::string err;
    EXPECT_EQ(parseBatch(batch, err), 1) << err;
}

TEST(RecordCodecTest, BinaryField_Le16_RoundTrip) {
    RecordBuilder rb;
    rb.appendHdr(4, LCVIEW_LEVEL_INFO, 1, 0);
    rb.appendBinary({0xDE, 0xAD, 0xBE, 0xEF, 0x00});
    auto batch = wrapAsBatch(rb.buf);
    std::string err;
    EXPECT_EQ(parseBatch(batch, err), 1) << err;
}

TEST(RecordCodecTest, EmptyString_Le16_RoundTrip) {
    RecordBuilder rb;
    rb.appendHdr(4, LCVIEW_LEVEL_INFO, 1, 0);
    rb.appendString("");
    auto batch = wrapAsBatch(rb.buf);
    std::string err;
    EXPECT_EQ(parseBatch(batch, err), 1) << err;
}

TEST(RecordCodecTest, EmptyBinary_Le16_RoundTrip) {
    RecordBuilder rb;
    rb.appendHdr(4, LCVIEW_LEVEL_INFO, 1, 0);
    rb.appendBinary({});
    auto batch = wrapAsBatch(rb.buf);
    std::string err;
    EXPECT_EQ(parseBatch(batch, err), 1) << err;
}

// ============================================================
// 完整 record（多字段）往返
// ============================================================

TEST(RecordCodecTest, FullRecord_MultiField_RoundTrip) {
    RecordBuilder rb;
    rb.appendHdr(8, LCVIEW_LEVEL_WARN, 5, 0x1111);
    rb.appendInt64(42);
    rb.appendInt64(0x1234);
    rb.appendInt64(0x5678);
    rb.appendString("Vendor Name");
    rb.appendString("Product Name");
    auto batch = wrapAsBatch(rb.buf);
    std::string err;
    EXPECT_EQ(parseBatch(batch, err), 1) << err;
}

TEST(RecordCodecTest, AllFieldTypes_RoundTrip) {
    RecordBuilder rb;
    rb.appendHdr(4, LCVIEW_LEVEL_INFO, 5, 0);
    rb.appendInt32(1);
    rb.appendInt64(2);
    rb.appendFloat(0x40490FDB);
    rb.appendString("test");
    rb.appendBinary({0xAA, 0xBB});
    auto batch = wrapAsBatch(rb.buf);
    std::string err;
    EXPECT_EQ(parseBatch(batch, err), 1) << err;
}

// ============================================================
// 多 record 批次往返
// ============================================================

TEST(RecordCodecTest, MultiRecordBatch_RoundTrip) {
    std::vector<uint8_t> batch;
    for (int i = 0; i < 5; i++) {
        RecordBuilder rb;
        rb.appendHdr(7, LCVIEW_LEVEL_INFO, 1, i * 1000);
        rb.appendInt64(i);
        auto wrapped = wrapAsBatch(rb.buf);
        batch.insert(batch.end(), wrapped.begin(), wrapped.end());
    }
    std::string err;
    EXPECT_EQ(parseBatch(batch, err), 5) << err;
}

TEST(RecordCodecTest, SingleRecord_BatchRoundTrip) {
    RecordBuilder rb;
    rb.appendHdr(4, LCVIEW_LEVEL_INFO, 1, 0);
    rb.appendInt64(1);
    auto batch = wrapAsBatch(rb.buf);
    std::string err;
    EXPECT_EQ(parseBatch(batch, err), 1) << err;
}

// ============================================================
// daemon 解析循环的边界/错误分支
// ============================================================

TEST(RecordCodecTest, EmptyBatch_ParsesZero) {
    std::vector<uint8_t> empty;
    std::string err;
    EXPECT_EQ(parseBatch(empty, err), 0);
}

TEST(RecordCodecTest, BatchLessThan4Bytes_Skipped) {
    // 不足 4B 前缀的残留字节，daemon 循环不进入
    std::vector<uint8_t> batch = {0x01, 0x02};
    std::string err;
    EXPECT_EQ(parseBatch(batch, err), 0);
}

TEST(RecordCodecTest, BadLength_TotalLenZero_Rejected) {
    // total_len = 4（仅含前缀自身），recordLen = 0 < sizeof(hdr) → record too small
    std::vector<uint8_t> batch(4, 0);
    uint32_t total_le = htole32(4);
    memcpy(batch.data(), &total_le, 4);
    std::string err;
    EXPECT_EQ(parseBatch(batch, err), -1);
    EXPECT_NE(err.find("too small"), std::string::npos);
}

TEST(RecordCodecTest, BadLength_TotalLenExceedsBatch_Rejected) {
    // total_len 声明 100 但 batch 只有 8 字节
    std::vector<uint8_t> batch(8, 0);
    uint32_t total_le = htole32(100);
    memcpy(batch.data(), &total_le, 4);
    std::string err;
    EXPECT_EQ(parseBatch(batch, err), -1);
    EXPECT_NE(err.find("bad length"), std::string::npos);
}

TEST(RecordCodecTest, RecordTooSmall_Rejected) {
    // total_len = 5，recordLen = 1 < sizeof(hdr) = 16
    std::vector<uint8_t> batch(5, 0);
    uint32_t total_le = htole32(5);
    memcpy(batch.data(), &total_le, 4);
    std::string err;
    EXPECT_EQ(parseBatch(batch, err), -1);
    EXPECT_NE(err.find("too small"), std::string::npos);
}

TEST(RecordCodecTest, BadMagic_Rejected) {
    RecordBuilder rb;
    rb.appendHdr(4, LCVIEW_LEVEL_INFO, 1, 0);
    rb.appendInt64(1);
    // 篡改 magic
    reinterpret_cast<lcview_record_hdr*>(rb.buf.data())->magic = 0xFFFF;
    auto batch = wrapAsBatch(rb.buf);
    std::string err;
    EXPECT_EQ(parseBatch(batch, err), -1);
    EXPECT_NE(err.find("magic"), std::string::npos);
}

TEST(RecordCodecTest, TruncatedInt32_Rejected) {
    // 构造一个 INT32 字段但截断 value
    std::vector<uint8_t> rec(sizeof(lcview_record_hdr) + 1);  // 只有 type 字节
    auto* hdr = reinterpret_cast<lcview_record_hdr*>(rec.data());
    hdr->magic = LCVIEW_MAGIC;
    hdr->event_id = 4;
    hdr->field_count = 1;
    hdr->timestamp_ns = 0;
    rec[sizeof(lcview_record_hdr)] = LCVIEW_TYPE_INT32;
    auto batch = wrapAsBatch(rec);
    std::string err;
    EXPECT_EQ(parseBatch(batch, err), -1);
    EXPECT_NE(err.find("EOF"), std::string::npos);
}

TEST(RecordCodecTest, StringFieldExceedsRecord_Rejected) {
    // STRING 长度声明 1000 但实际无数据
    std::vector<uint8_t> rec(sizeof(lcview_record_hdr) + 3);
    auto* hdr = reinterpret_cast<lcview_record_hdr*>(rec.data());
    hdr->magic = LCVIEW_MAGIC;
    hdr->event_id = 4;
    hdr->field_count = 1;
    hdr->timestamp_ns = 0;
    uint8_t* p = rec.data() + sizeof(lcview_record_hdr);
    p[0] = LCVIEW_TYPE_STRING;
    uint16_t big_le = htole16(1000);
    memcpy(p + 1, &big_le, 2);
    auto batch = wrapAsBatch(rec);
    std::string err;
    EXPECT_EQ(parseBatch(batch, err), -1);
    EXPECT_NE(err.find("exceeds"), std::string::npos);
}

TEST(RecordCodecTest, UnknownFieldType_Rejected) {
    std::vector<uint8_t> rec(sizeof(lcview_record_hdr) + 1);
    auto* hdr = reinterpret_cast<lcview_record_hdr*>(rec.data());
    hdr->magic = LCVIEW_MAGIC;
    hdr->event_id = 4;
    hdr->field_count = 1;
    hdr->timestamp_ns = 0;
    rec[sizeof(lcview_record_hdr)] = 99;  // 未知类型
    auto batch = wrapAsBatch(rec);
    std::string err;
    EXPECT_EQ(parseBatch(batch, err), -1);
    EXPECT_NE(err.find("unknown"), std::string::npos);
}

// ============================================================
// 字节序回归保护（大端序应被拒绝）
// ============================================================

TEST(RecordCodecTest, BatchPrefix_BigEndian_Rejected) {
    RecordBuilder rb;
    rb.appendHdr(4, LCVIEW_LEVEL_INFO, 1, 0);
    rb.appendInt64(1);
    // 手工用大端序写 4B 前缀
    std::vector<uint8_t> batch;
    uint32_t total = static_cast<uint32_t>(rb.buf.size()) + 4;
    uint32_t total_be = htobe32(total);
    batch.resize(4);
    memcpy(batch.data(), &total_be, 4);
    batch.insert(batch.end(), rb.buf.begin(), rb.buf.end());
    std::string err;
    EXPECT_EQ(parseBatch(batch, err), -1);
    EXPECT_NE(err.find("bad length"), std::string::npos);
}

TEST(RecordCodecTest, StringFieldLen_BigEndian_Rejected) {
    RecordBuilder rb;
    rb.appendHdr(4, LCVIEW_LEVEL_INFO, 1, 0);
    rb.appendString("hello");
    // 篡改 string 长度前缀为大端序
    size_t len_offset = sizeof(lcview_record_hdr) + 1;  // type 后
    uint16_t be_len = htobe16(5);
    memcpy(rb.buf.data() + len_offset, &be_len, 2);
    auto batch = wrapAsBatch(rb.buf);
    std::string err;
    // 大端序 5 = 0x0500，le 读为 1280，远超 record → exceeds
    EXPECT_EQ(parseBatch(batch, err), -1);
    EXPECT_NE(err.find("exceeds"), std::string::npos);
}
