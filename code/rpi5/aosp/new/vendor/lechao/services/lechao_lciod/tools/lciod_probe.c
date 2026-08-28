// ============================================================
// lciod_probe.c — LcIod 设备统计取数工具（上板验证用）
// 所属模块：lechao_lciod — 工具
// 设计目的：枚举 /dev/vendor_lechao_usbd* 节点，逐设备执行
//   GET_STATS ioctl 并按固定 key=value 格式单行打印（全 26 字段
//   + abi_version），供 host 侧 lciod_check.py 做字段齐全性/增量
//   校验（设备侧最小操作 + host 复杂解析，防假绿原则同 lcview）。
//
// 用法：
//   lciod_probe            打印全部设备统计快照
//   lciod_probe --reset    打印前先对每设备执行 RESET_STATE
//                          （trigger 用例 baseline 归零，delta 断言
//                          简化为绝对值判定）
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
    printf("device minor=%d path=%s vid=0x%04x pid=0x%04x vendor=\"%s\" product=\"%s\" "
           "read_bytes=%llu write_bytes=%llu read_ns=%llu write_ns=%llu "
           "read_cmds=%llu write_cmds=%llu error_count=%llu reset_count=%llu "
           "probe_count=%llu disconnect_count=%llu degrade_count=%llu "
           "current_rate=%llu peak_rate=%llu last_transport_latency_ns=%llu "
           "last_event_ts_ns=%llu last_update=%lld stall_count=%llu "
           "corrupt_count=%llu timeout_count=%llu last_event_type=%u "
           "enabled=%u flags=%u event_drop_count=%llu abi_version=%u\n",
           tail_minor(path), path, st.vid, st.pid, st.vendor, st.product,
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
           VENDOR_LECHAO_USBD_ABI_VERSION);
    fflush(stdout);
    return 0;
}

int main(int argc, char* argv[])
{
    int do_reset = 0;
    if (argc == 2 && strcmp(argv[1], "--reset") == 0) {
        do_reset = 1;
    } else if (argc != 1) {
        fprintf(stderr, "usage: %s [--reset]\n", argv[0]);
        return 2;
    }

    glob_t gl;
    memset(&gl, 0, sizeof(gl));
    if (glob(DEV_PREFIX "*", 0, NULL, &gl) != 0 || gl.gl_pathc == 0) {
        /* 无设备：正常退出 + 空输出（host 侧 stats 模式判红） */
        globfree(&gl);
        return 0;
    }

    int ret = 0;
    for (size_t i = 0; i < gl.gl_pathc; ++i)
        ret |= probe_device(gl.gl_pathv[i], do_reset);
    globfree(&gl);
    return ret;
}
