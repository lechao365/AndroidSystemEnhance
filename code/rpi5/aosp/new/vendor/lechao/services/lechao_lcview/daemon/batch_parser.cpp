// ============================================================
// batch_parser.cpp — daemon 主循环可测函数实现
// 所属模块：LcView 事件日志系统 — Daemon 层
// 实现见 batch_parser.h；逻辑从 lechao_lcview.cpp main() 原样迁移，
// 不改变运行时行为（parseBatch 即 main 循环的批次解析段）。
// 架构演进：daemon 直读内核后取消 HAL 绑定（waitForHal/
// rebindAfterError/HalBinder/ILcView 移除）。
// ============================================================

#define LOG_TAG "lechao_lcview"

#include "batch_parser.h"
#include "../include/lcview_events.h"
#include <log/log.h>
#include <cstring>
#include <thread>

using namespace vendor::lechao::lcview;

BatchParseResult vendor::lechao::lcview::parseBatch(
    SchemaParser& schema, FileWriter& writer,
    const std::vector<uint8_t>& batch)
{
    BatchParseResult result;
    size_t offset = 0;

    while (offset + 4 <= batch.size()) {
        // 读取本条记录的总长度（含自身 4 字节）
        uint32_t total_len;
        memcpy(&total_len, batch.data() + offset, 4);

        // 长度校验：最小长度和边界检查
        if (total_len < 4 || offset + total_len > batch.size()) {
            writer.writeInvalid(batch.data() + offset,
                                batch.size() - offset, "bad length");
            ALOGE("lechao_lcview: parse: bad length at offset=%zu, total_len=%u",
                  offset, total_len);
            break;
        }

        const uint8_t* recordStart = batch.data() + offset + 4;
        size_t recordDataLen = total_len - 4;

        // 记录必须至少包含固定头的大小
        if (recordDataLen < sizeof(struct lcview_record_hdr)) {
            ALOGE("lechao_lcview: parse: record too small (%zu < %zu)",
                  recordDataLen, sizeof(struct lcview_record_hdr));
            writer.writeInvalid(recordStart, recordDataLen, "record too small");
            result.invalidCnt++;
            offset += total_len;
            continue;
        }

        // 使用 SchemaParser 校验记录的魔法数字、event_id、字段数、
        // 字段类型和总长度是否完整合法
        std::string errMsg;
        if (schema.validate(recordStart, recordDataLen, errMsg)) {
            const struct lcview_record_hdr* hdr =
                reinterpret_cast<const struct lcview_record_hdr*>(recordStart);
            const uint8_t* fields =
                recordStart + sizeof(struct lcview_record_hdr);
            const EventSchema* es = schema.find(hdr->event_id);
            if (es) {
                size_t fieldsLen = recordDataLen - sizeof(struct lcview_record_hdr);
                writer.writeRecord(*es, hdr, fields, fieldsLen);
                result.validCnt++;
            } else {
                // validate 通过但 find 失败（理论不可达）：防御分支，
                // 禁止静默丢数据（CXX-004 故障可见性）
                ALOGE("lechao_lcview: parse: schema for event %u vanished",
                      hdr->event_id);
                writer.writeInvalid(recordStart, recordDataLen, "schema vanished");
                result.invalidCnt++;
            }
        } else {
            writer.writeInvalid(recordStart, recordDataLen, errMsg);
            ALOGE("lechao_lcview: parse: validate failed: %s", errMsg.c_str());
            result.invalidCnt++;
        }

        offset += total_len;
    }

    // 批次尾部残留（<4B 读不出长度前缀）：直读路径拼包 bug 现场必须
    // 落盘 invalid，禁止静默丢弃（CXX-004 故障可见性）
    if (offset != batch.size()) {
        writer.writeInvalid(batch.data() + offset,
                            batch.size() - offset, "trailing bytes");
        ALOGE("lechao_lcview: parse: %zu trailing bytes at batch tail",
              batch.size() - offset);
        result.invalidCnt++;
    }
    return result;
}

bool vendor::lechao::lcview::loadSchemaWithRetry(
    SchemaParser& schema, const std::string& path, int maxRetries,
    std::chrono::milliseconds interval)
{
    int schemaRetry = 0;
    while (!schema.loadFromFile(path) && schemaRetry < maxRetries) {
        ALOGW("lechao_lcview: schema not ready, retrying... (%d/%d)",
              ++schemaRetry, maxRetries);
        std::this_thread::sleep_for(interval);
    }
    return schema.eventCount() > 0;
}

bool vendor::lechao::lcview::shouldFlushBatch(
    size_t buffered, bool timedOut, bool ageExpired, size_t bufferCapacity)
{
    if (buffered == 0) return false;  // 空批不 flush（避免空批次写放大）
    return buffered >= bufferCapacity || timedOut || ageExpired;
}
