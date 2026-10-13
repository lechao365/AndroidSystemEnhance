/*
 * ============================================================
 * expect_json.h — usb-fault-inject expect JSON 消费模型
 * 所属模块: rpi5-usb-verify (fault-verify)
 * 设计目的: 消费 rpi-zero2w 侧 usb-fault-inject 的
 *           expect_output_by_id() 单行 JSON，打通跨设备期望值契约
 *           （KI-20261008-001）。
 *
 * 输入 JSON 形态（fault-inject expect.c 输出，单行）:
 *   {"fault":"<name>","expect":{
 *       "kernel_event":<n>,
 *       "error_count":<n>,"reset_count":<n>,"stall_count":<n>,
 *       "corrupt_count":<n>,"timeout_count":<n>}}
 *   - kernel_event: enum fdi_kernel_event 数值，与 usb-verify 侧内核
 *     枚举 VENDOR_LECHAO_USBD_EVENT_* 严格一致（0..6）。
 *   - 计数: -1 = 不校验；>=0 = 期望 actual >= N（与 usb-verify 的
 *     *_ge 语义一致）。
 * ============================================================
 */
#ifndef FV_EXPECT_JSON_H
#define FV_EXPECT_JSON_H

#include <stddef.h>
#include <stdint.h>

#include "parse.h"

/* fault 名字段最大长度（含结尾 '\0'），超长判失败防静默截断 */
#define FV_EXPECT_JSON_FAULT_MAX 64

/*
 * struct fv_expect_json — 解析后的 expect 对象
 *
 * 未出现的字段保持“未提供”默认值：计数字段为 -1，has_* 标志为 0。
 */
struct fv_expect_json {
    char fault[FV_EXPECT_JSON_FAULT_MAX]; /* fault 名（已 NUL 结尾） */
    int  has_fault;         /* fault 字段是否出现 */
    int  has_expect;        /* expect 对象是否出现 */
    int  kernel_event;      /* 0..6，见 VENDOR_LECHAO_USBD_EVENT_* */
    int  has_kernel_event;  /* kernel_event 字段是否出现 */
    int  error_count;       /* -1 = 未提供/不校验，>=0 = 期望下限 */
    int  reset_count;
    int  stall_count;
    int  corrupt_count;
    int  timeout_count;
};

/*
 * fv_parse_expect_json_buf — 从内存缓冲区解析 expect JSON（纯函数）
 * @buf/@len: JSON 文本（无需 NUL 结尾）
 * @out: 输出参数，成功时写入解析结果
 * 返回: 0 成功；-1 格式非法 / 字段越界 / 缺字段（已打印明确错误）
 *
 * 防御（CXX-003）：逐字段范围校验；非对象 / 缺 expect 对象 /
 * kernel_event 越界 / 计数 < -1 均判失败，禁止静默忽略。
 */
int fv_parse_expect_json_buf(const char *buf, size_t len,
                             struct fv_expect_json *out);

/*
 * fv_parse_expect_json_file — 读取文件并解析 expect JSON
 * @path: JSON 文件路径
 * @out: 输出参数
 * 返回: 0 成功；-1 文件不可读 / 过大 / 解析失败（已打印明确错误）
 */
int fv_parse_expect_json_file(const char *path, struct fv_expect_json *out);

/*
 * fv_apply_expect_json — 将 expect 对象映射进 fv_command
 * @ej: 已解析的 expect 对象；@cmd: 待填充命令
 *
 * 映射规则（与 fault-inject 数值语义对齐）:
 *   - kernel_event → cmd->expect_event_type
 *   - error/reset/stall/corrupt/timeout_count → 对应 *_ge（仅 >=0 设）
 *
 * 优先级：命令行显式阈值优先，JSON 只填空缺——仅当 cmd 对应字段
 * 当前为“未设置”（*_ge==0 或 expect_event_type==NONE）时才写入。
 * 因 0 阈值本身不产生断言（actual>=0 恒真），此规则与“显式 0”等价。
 *
 * 返回: 0 成功；-1 参数非法。
 */
int fv_apply_expect_json(const struct fv_expect_json *ej,
                         struct fv_command *cmd);

#endif /* FV_EXPECT_JSON_H */
