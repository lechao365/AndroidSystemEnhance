// ============================================================
// lciod_probe.c — LcIod 设备统计取数工具（上板验证用）
// 所属模块：lechao_lciod — 工具
// 设计目的：枚举 /dev/vendor_lechao_usbd<minor> 节点（数字后缀，
//   排除 /dev/vendor_lechao_usbd_link 全局链路节点），逐设备执行
//   GET_STATS ioctl 并按固定 key=value 格式单行打印（全 33 字段
//   + abi_version），供 host 侧 lciod_check.py 做字段齐全性/增量
//   校验（设备侧最小操作 + host 复杂解析，防假绿原则同 lcview）。
//
// 用法：
//   lciod_probe            打印全部设备统计快照
//   lciod_probe --reset    打印前先对每设备执行 RESET_STATE
//                          （trigger 用例 baseline 归零，delta 断言
//                          简化为绝对值判定）
//   lciod_probe --link     打印全局链路节点（/dev/vendor_lechao_usbd_link）
//                          GET_LINK_STATS 快照（R2 方向 7：lciod-check
//                          --mode link 取数，供电归因链路计数）
//   lciod_probe --histo    逐设备打印 GET_LATENCY_HISTOGRAM（v5）二维直方图
//                          快照：dirs/size_bins/latency_bins 维度、mono_ts_ns
//                          时戳、档位元数据（size/latency bounds）与累计矩阵
//                          counts（C 批二 方向 3；lciod-check --mode histo 取数）
//
// 退出码：0 成功（无设备也返回 0、输出为空，由 host 侧判红）/
//         1 open 或 ioctl 失败 / 2 参数错误
// 注：本工具内 minor 解析为取数辅助（尾部数字 strtol）；
//     严格校验版本在 common/minor_utils.cpp 且有单测覆盖。
// ============================================================

#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

#include "vendor_lechao_usbd-ioctl.h"

#define DEV_PREFIX "/dev/vendor_lechao_usbd"

/* 从路径尾部提取 minor（取数辅助，严格版见 common/minor_utils.cpp） */
static int tail_minor(const char* path)
{
    const char* p = path + strlen(path);
    while (p > path && p[-1] >= '0' && p[-1] <= '9')
        p--;
    return (int)strtol(p, NULL, 10);
}

/* 对单个设备节点执行可选 reset + GET_STATS 并单行打印 */
static int probe_device(const char* path, int do_reset)
{
    int fd = open(path, O_RDONLY);
    if (fd < 0) {
        fprintf(stderr, "lciod_probe: open %s failed: %s\n", path, strerror(errno));
        return 1;
    }

    if (do_reset && ioctl(fd, VENDOR_LECHAO_USBD_IOC_RESET_STATE) < 0) {
        fprintf(stderr, "lciod_probe: RESET_STATE %s failed: %s\n", path, strerror(errno));
        close(fd);
        return 1;
    }

    struct vendor_lechao_usbd_stats st;
    memset(&st, 0, sizeof(st));
    if (ioctl(fd, VENDOR_LECHAO_USBD_IOC_GET_STATS, &st) < 0) {
        fprintf(stderr, "lciod_probe: GET_STATS %s failed: %s\n", path, strerror(errno));
        close(fd);
        return 1;
    }
    close(fd);

    /* 单行 key=value：vendor/product 引号包裹防空格破坏 host 解析 */
    printf("device minor=%d path=%s vid=0x%04x pid=0x%04x protocol=%u vendor=\"%s\" product=\"%s\" "
           "read_bytes=%llu write_bytes=%llu read_ns=%llu write_ns=%llu "
           "read_cmds=%llu write_cmds=%llu error_count=%llu reset_count=%llu "
           "probe_count=%llu disconnect_count=%llu degrade_count=%llu "
           "current_rate=%llu peak_rate=%llu last_transport_latency_ns=%llu "
           "last_event_ts_ns=%llu last_update=%lld stall_count=%llu "
           "corrupt_count=%llu timeout_count=%llu last_event_type=%u "
           "enabled=%u flags=%u event_drop_count=%llu "
           "read_error_count=%llu write_error_count=%llu abi_version=%u\n",
           tail_minor(path), path, st.vid, st.pid, st.protocol, st.vendor, st.product,
           (unsigned long long)st.read_bytes, (unsigned long long)st.write_bytes,
           (unsigned long long)st.read_ns, (unsigned long long)st.write_ns,
           (unsigned long long)st.read_cmds, (unsigned long long)st.write_cmds,
           (unsigned long long)st.error_count, (unsigned long long)st.reset_count,
           (unsigned long long)st.probe_count, (unsigned long long)st.disconnect_count,
           (unsigned long long)st.degrade_count,
           (unsigned long long)st.current_rate, (unsigned long long)st.peak_rate,
           (unsigned long long)st.last_transport_latency_ns,
           (unsigned long long)st.last_event_ts_ns, (long long)st.last_update,
           (unsigned long long)st.stall_count, (unsigned long long)st.corrupt_count,
           (unsigned long long)st.timeout_count, st.last_event_type,
           st.enabled, st.flags,
           (unsigned long long)st.event_drop_count,
           (unsigned long long)st.read_error_count,
           (unsigned long long)st.write_error_count,
           VENDOR_LECHAO_USBD_ABI_VERSION);
    fflush(stdout);
    return 0;
}

/* 全局链路节点取数（R2 方向 7）：GET_LINK_STATS ioctl 快照单行 key=value，
 * 供 host 侧 lciod_check.py --mode link 解析（字段与
 * struct vendor_lechao_usbd_link_stats 一一对应 + abi_version）。 */
static int probe_link(void)
{
    const char* path = "/dev/vendor_lechao_usbd_link";
    int fd = open(path, O_RDONLY);
    if (fd < 0) {
        fprintf(stderr, "lciod_probe: open %s failed: %s\n", path, strerror(errno));
        return 1;
    }

    struct vendor_lechao_usbd_link_stats st;
    memset(&st, 0, sizeof(st));
    if (ioctl(fd, VENDOR_LECHAO_USBD_IOC_GET_LINK_STATS, &st) < 0) {
        fprintf(stderr, "lciod_probe: GET_LINK_STATS %s failed: %s\n", path, strerror(errno));
        close(fd);
        return 1;
    }
    close(fd);

    printf("link connect_count=%llu disconnect_count=%llu enum_fail_count=%llu "
           "overcurrent_count=%llu last_event_ts_ns=%llu last_event_type=%u "
           "last_busnum=%u last_port=%u last_vid=0x%04x last_pid=0x%04x "
           "last_err=%d last_count=%u last_duration_ns=%llu abi_version=%u\n",
           (unsigned long long)st.connect_count,
           (unsigned long long)st.disconnect_count,
           (unsigned long long)st.enum_fail_count,
           (unsigned long long)st.overcurrent_count,
           (unsigned long long)st.last_event_ts_ns,
           st.last_event_type,
           st.last_busnum, st.last_port, st.last_vid, st.last_pid,
           st.last_err, st.last_count,
           (unsigned long long)st.last_duration_ns,
           VENDOR_LECHAO_USBD_ABI_VERSION);
    fflush(stdout);
    return 0;
}

/* 将 u64 数组按逗号拼接为十进制字符串（供 host 侧解析档位上界）。
 * 始终保留结尾 NUL 位，防写越界；返回已写字符数（不含 NUL）。 */
static int join_u64_csv(char* buf, size_t cap, const u64* vals, size_t n)
{
    size_t off = 0;
    for (size_t i = 0; i < n; ++i) {
        int w = snprintf(buf + off, cap - off, "%s%llu", i ? "," : "",
                         (unsigned long long)vals[i]);
        if (w < 0 || (size_t)w >= cap - off) {
            buf[cap - 1] = '\0';
            break;
        }
        off += (size_t)w;
    }
    return (int)off;
}

/* 将 u32 数组按逗号拼接为十进制字符串（counts 矩阵用）。
 * 同 join_u64_csv：保留结尾 NUL，返回已写字符数。 */
static int join_u32_csv(char* buf, size_t cap, const u32* vals, size_t n)
{
    size_t off = 0;
    for (size_t i = 0; i < n; ++i) {
        int w = snprintf(buf + off, cap - off, "%s%u", i ? "," : "", vals[i]);
        if (w < 0 || (size_t)w >= cap - off) {
            buf[cap - 1] = '\0';
            break;
        }
        off += (size_t)w;
    }
    return (int)off;
}

/* 对单个设备节点执行 GET_LATENCY_HISTOGRAM（v5 二维直方图）并单行打印。
 * 数组字段（size_bounds/latency_bounds/counts）逗号拼接后用双引号包裹，
 * 保证 host 侧正则 (\w+)=(?:"([^"]*)"|(\S+)) 正确取值（值内无空格但含逗号，
 * 引号包裹可兼容其他 key=value 混排）。counts 按行主序展开 dir*size_bins*lat。 */
static int probe_histo_device(const char* path)
{
    int fd = open(path, O_RDONLY);
    if (fd < 0) {
        fprintf(stderr, "lciod_probe: open %s failed: %s\n", path, strerror(errno));
        return 1;
    }

    struct vendor_lechao_usbd_latency_histogram h;
    memset(&h, 0, sizeof(h));
    if (ioctl(fd, VENDOR_LECHAO_USBD_IOC_GET_LATENCY_HISTOGRAM, &h) < 0) {
        fprintf(stderr, "lciod_probe: GET_LATENCY_HISTOGRAM %s failed: %s\n",
                path, strerror(errno));
        close(fd);
        return 1;
    }
    close(fd);

    /* 缓冲尺寸：size 8 档 * 21B；latency 12 档 * 21B；counts 192 格 * 21B */
    char size_buf[256];
    char lat_buf[384];
    char cnt_buf[4608];
    join_u64_csv(size_buf, sizeof(size_buf), h.size_bounds,
                 VENDOR_LECHAO_USBD_HISTO_SIZE_BINS);
    join_u64_csv(lat_buf, sizeof(lat_buf), h.latency_bounds,
                 VENDOR_LECHAO_USBD_HISTO_LAT_BINS);
    join_u32_csv(cnt_buf, sizeof(cnt_buf), &h.counts[0][0][0],
                 (size_t)VENDOR_LECHAO_USBD_HISTO_DIRS
                 * VENDOR_LECHAO_USBD_HISTO_SIZE_BINS
                 * VENDOR_LECHAO_USBD_HISTO_LAT_BINS);

    printf("histo minor=%d dirs=%u size_bins=%u latency_bins=%u mono_ts_ns=%llu "
           "size_bounds=\"%s\" latency_bounds=\"%s\" counts=\"%s\" abi_version=%u\n",
           tail_minor(path), h.dirs, h.size_bins, h.latency_bins,
           (unsigned long long)h.mono_ts_ns,
           size_buf, lat_buf, cnt_buf,
           VENDOR_LECHAO_USBD_ABI_VERSION);
    fflush(stdout);
    return 0;
}

int main(int argc, char* argv[])
{
    int do_reset = 0;
    int do_link = 0;
    int do_histo = 0;
    if (argc == 2 && strcmp(argv[1], "--reset") == 0) {
        do_reset = 1;
    } else if (argc == 2 && strcmp(argv[1], "--link") == 0) {
        do_link = 1;
    } else if (argc == 2 && strcmp(argv[1], "--histo") == 0) {
        do_histo = 1;
    } else if (argc != 1) {
        fprintf(stderr, "usage: %s [--reset|--link|--histo]\n", argv[0]);
        return 2;
    }

    if (do_link)
        return probe_link();

    glob_t gl;
    memset(&gl, 0, sizeof(gl));
    /* 仅枚举数字后缀的 per-device 节点（排除 /dev/vendor_lechao_usbd_link：
     * 该全局链路节点只支持 GET_LINK_STATS，向其发 GET_STATS/RESET_STATE
     * 会 ENOTTY 判红，2026-10-07 上板回归首现） */
    if (glob(DEV_PREFIX "[0-9]*", 0, NULL, &gl) != 0 || gl.gl_pathc == 0) {
        /* 无设备：正常退出 + 空输出（host 侧 stats/histo 模式判红） */
        globfree(&gl);
        return 0;
    }

    int ret = 0;
    for (size_t i = 0; i < gl.gl_pathc; ++i) {
        if (do_histo)
            ret |= probe_histo_device(gl.gl_pathv[i]);
        else
            ret |= probe_device(gl.gl_pathv[i], do_reset);
    }
    globfree(&gl);
    return ret;
}
