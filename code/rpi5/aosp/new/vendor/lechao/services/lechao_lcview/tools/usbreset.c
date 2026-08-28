// ============================================================
// usbreset.c — USBDEVFS_RESET 触发工具（lcview 事件 7 覆盖用）
// 所属模块：LcView 事件日志系统 — 工具
// 设计目的：对 /dev/bus/usb/<bus>/<devnum> 节点发起 USBDEVFS_RESET
//   ioctl，触发内核 usb_reset 事件（lcview 打点），供板端 delta 断言
//   --event 7。用法：usbreset /dev/bus/usb/001/002
// ============================================================

#include <errno.h>
#include <fcntl.h>
#include <linux/usbdevice_fs.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

int main(int argc, char* argv[])
{
    if (argc != 2) {
        fprintf(stderr, "usage: %s /dev/bus/usb/<bus>/<devnum>\n", argv[0]);
        return 2;
    }
    const char* path = argv[1];
    int fd = open(path, O_WRONLY);
    if (fd < 0) {
        fprintf(stderr, "usbreset: open %s failed: %s\n", path, strerror(errno));
        return 1;
    }
    // USBDEVFS_RESET 无参数（addr 置 0），ioctl 触发设备复位
    if (ioctl(fd, USBDEVFS_RESET, 0) < 0) {
        fprintf(stderr, "usbreset: ioctl USBDEVFS_RESET on %s failed: %s\n",
                path, strerror(errno));
        close(fd);
        return 1;
    }
    fprintf(stderr, "usbreset: reset %s ok\n", path);
    close(fd);
    return 0;
}