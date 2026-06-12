/*
 * lcview_main.c — LcView 字符设备驱动核心
 *
 * 本文件是 LcView 内核模块的主入口，负责：
 *   1. 注册字符设备 /dev/vendor_lechao_lcview（主设备号动态分配）
 *   2. 实现 file_operations：open / release / read / poll / unlocked_ioctl
 *   3. 提供 EXPORT_SYMBOL 入口 lcview_builder_start()，供其他内核模块
 *      写入结构化事件日志
 *   4. 模块参数 ring_size_kb 允许加载时调整环形缓冲区大小
 *
 * 模块初始化顺序：
 *   环形缓冲区初始化 → 注册字符设备 → 创建 device class → 创建设备节点
 *   任何一步失败则回滚之前所有分配，确保无资源泄漏。
 *
 * 设计约束：
 *   - 单打开限制 (device_opened atomic)：避免多个用户态 reader 争抢，
 *     简化读取指针管理（单生产者 + 单消费者）
 *   - 日志级别过滤 (min_level)：由 LCVIEW_SET_LEVEL ioctl 设置，
 *     低于此级别的事件 builder_start 直接返回 NULL，避免分配和序列化开销
 *   - spin_lock 保护：写入 API 可在中断上下文调用（如 USB 中断处理函数）
 */

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/fs.h>
#include <linux/cdev.h>
#include <linux/device.h>
#include <linux/poll.h>
#include <linux/uaccess.h>
#include "lcview_internal.h"
#include "lcview_ioctl.h"
#include "kernel_lechao_log.h"

/*
 * 日志前缀标签：所有 pr_info/pr_err 统一使用 KERNEL_LCVIEW_TAG，
 * 便于 dmesg | grep kernel_lechao_lcview 过滤
 */
#define PREFIX KERNEL_LCVIEW_TAG ": "

int lcview_debug = 0;
module_param_named(debug, lcview_debug, int, 0644);
MODULE_PARM_DESC(debug, "Enable verbose diagnostic logging (0=off, 1=on)");

#define LC_DBG(fmt, ...) do { if (lcview_debug) pr_info(PREFIX "[D] " fmt, ##__VA_ARGS__); } while (0)

/* 字符设备名称（在 /dev/ 下显示） */
#define DEVICE_NAME   "vendor_lechao_lcview"

/* device class 名称（sysfs 中使用） */
#define CLASS_NAME    "lcview"

/* 动态分配的主设备号 */
static int major_number;

/* device class / device 指针，module_exit 时用于反注册 */
static struct class *lcview_class;
static struct device *lcview_device;

/*
 * 全局环形缓冲区实例
 * 声明在 lcview_internal.h 中 extern，lcview_ring.c 未持有 extern 声明，
 * 仅有本文件定义实体，其他模块通过 lcview_ring_write/lcview_ring_read 访问
 */
struct lcview_ring lcview_ring;

/*
 * 设备打开标志（原子变量）
 * open 时 cmpxchg 0→1 防止并发打开；release 时 set 0
 * 设计理由：读指针管理需要单一消费者，否则多 reader 各持不同读位置
 * 会导致数据读取错乱
 */
static atomic_t device_opened = ATOMIC_INIT(0);

/*
 * 当前最低日志等级
 * 低于此级别的事件会被 lcview_builder_start 过滤掉，不分配也不写入
 * 默认 LCVIEW_LEVEL_DEBUG = 0（不过滤）
 */
static uint8_t min_level = LCVIEW_LEVEL_DEBUG;

/* 模块参数：环形缓冲区大小（KB），默认 256KB，最大 4096KB */
static uint32_t ring_size_kb = LCVIEW_RING_DEFAULT_KB;
module_param(ring_size_kb, uint, 0644);
MODULE_PARM_DESC(ring_size_kb, "Ring buffer size in KB (default 256, max 4096)");

/*
 * lcview_open — 打开字符设备
 *
 * 使用 atomic cmpxchg 实现单打开限制。为什么不用 mutex？
 * 因为语义不同：mutex 允许同一进程多次 open（可重入），
 * 而我们希望严格限制为同时只有一个 reader 连接。
 * cmpxchg 的 atomic 语义确保多线程并发 open 场景下
 * 只有一个成功，其余得到 -EBUSY。
 */
static int lcview_open(struct inode *inode, struct file *file)
{
    int minor = iminor(inode);
    LC_DBG("open: minor=%d\n", minor);
    if (atomic_cmpxchg(&device_opened, 0, 1) != 0) {
        pr_warn(PREFIX "open rejected: already opened (EBUSY)\n");
        return -EBUSY;
    }
    return 0;
}

/*
 * lcview_release — 关闭设备
 *
 * 重置打开标志，允许下次 open 成功。
 * 注意：reader 关闭时环形缓冲区中的未读数据保留，
 * 下次 open 后可继续读取（适合 logcat 类工具的重连场景）。
 */
static int lcview_release(struct inode *inode, struct file *file)
{
    atomic_set(&device_opened, 0);
    return 0;
}

/*
 * lcview_read — 从环形缓冲区读取事件数据
 *
 * 直接委托给 lcview_ring_read，该函数内部处理：
 *   1. 阻塞等待数据（通过 wait_event_interruptible）
 *   2. 锁内拷贝到 read_buf，锁外 copy_to_user
 *   3. 多条记录拼接返回（直到填满用户缓冲区或数据读完）
 *
 * 忽略 file->f_pos（off 参数），因为环形缓冲区不是线性文件，
 * 读位置由 ring->read_pos 维护。
 */
static ssize_t lcview_read(struct file *file, char __user *buf,
                           size_t len, loff_t *off)
{
    LC_DBG("read: count=%zu\n", len);
    return lcview_ring_read(&lcview_ring, (uint8_t __user *)buf, len);
}

/*
 * lcview_poll — poll/select/epoll 支持
 *
 * 将等待队列挂载到 lcview_ring.waitq 上，使 select/poll
 * 可以阻塞等待新事件。
 * 当环形缓冲区中有可读数据时，返回 POLLIN | POLLRDNORM。
 */
static __poll_t lcview_poll(struct file *file, poll_table *wait)
{
    __poll_t mask = 0;
    poll_wait(file, &lcview_ring.waitq, wait);
    if (lcview_ring_avail_bytes(&lcview_ring) > 0)
        mask |= POLLIN | POLLRDNORM;
    LC_DBG("poll: mask=0x%x\n", mask);
    return mask;
}

/*
 * lcview_ioctl — 设备控制命令处理
 *
 * 支持的命令：
 *   LCVIEW_GET_AVAIL_BYTES — 查询可读字节数（非阻塞决策用）
 *   LCVIEW_GET_OVERRUN     — 读取并清零溢出计数（边读边清）
 *   LCVIEW_GET_STATS       — 获取完整统计信息
 *   LCVIEW_SET_LEVEL       — 设置最低日志级别（过滤低级别事件）
 *
 * copy_from_user/copy_to_user 安全检查：
 *   如用户态地址无效，立即返回 -EFAULT，防止内核内存泄漏或崩溃。
 *   compat_ioctl 指向同一函数以支持 32/64 位兼容。
 */
static long lcview_ioctl(struct file *file, unsigned int cmd, unsigned long arg)
{
    uint32_t val;
    uint8_t level;
    struct lcview_stats stats;

    switch (cmd) {
    /*
     * 查询当前可读字节数
     * 用户态在调用 read 前可先用此命令决定缓冲区大小
     */
    case LCVIEW_GET_AVAIL_BYTES:
        val = lcview_ring_avail_bytes(&lcview_ring);
        if (copy_to_user((void __user *)arg, &val, sizeof(val))) {
            pr_err(PREFIX "GET_AVAIL_BYTES copy_to_user failed\n");
            return -EFAULT;
        }
        break;

    /*
     * 读取溢出计数并清零
     * "边读边清"语义：用户态轮询时可判断自上次查询以来是否发生过溢出
     */
    case LCVIEW_GET_OVERRUN:
        val = (uint32_t)atomic_read(&lcview_ring.overrun_cnt);
        if (copy_to_user((void __user *)arg, &val, sizeof(val))) {
            pr_err(PREFIX "GET_OVERRUN copy_to_user failed\n");
            return -EFAULT;
        }
        atomic_set(&lcview_ring.overrun_cnt, 0);
        break;

    /*
     * 获取完整环形缓冲区统计信息
     * 包括记录总数、溢出数、已用空间和总大小
     * 用于监控面板显示或调试诊断
     */
    case LCVIEW_GET_STATS:
        lcview_ring_get_stats(&lcview_ring, &stats);
        if (copy_to_user((void __user *)arg, &stats, sizeof(stats))) {
            pr_err(PREFIX "GET_STATS copy_to_user failed\n");
            return -EFAULT;
        }
        break;

    /*
     * 设置最低日志级别
     * 级别值校验：必须在 LCVIEW_LEVEL_DEBUG(0) ~ LCVIEW_LEVEL_ERROR(3) 范围内
     */
    case LCVIEW_SET_LEVEL:
        if (copy_from_user(&level, (void __user *)arg, sizeof(level))) {
            pr_err(PREFIX "SET_LEVEL invalid\n");
            return -EFAULT;
        }
        if (level > LCVIEW_LEVEL_ERROR) {
            pr_err(PREFIX "SET_LEVEL invalid\n");
            return -EINVAL;
        }
        min_level = level;
        break;

    /*
     * 不识别的命令码
     * -ENOTTY 是 Linux ioctl 处理的标准"不支持此 ioctl"错误码
     */
    default:
        pr_warn(PREFIX "unknown ioctl cmd=0x%x\n", cmd);
        return -ENOTTY;
    }

    return 0;
}

/*
 * lcview_fops — 字符设备文件操作表
 *
 * compat_ioctl 与 unlocked_ioctl 指向同一函数，因为我们的数据结构
 * (uint32_t, uint8_t, struct lcview_stats) 在 32/64 位下布局一致——
 * lcview_stats 的四个字段均为 uint32_t，不存在指针或 long 类型对齐差异。
 * 如果未来引入含指针的 struct，则需要实现 compat_ioctl 做结构体转换。
 */
static const struct file_operations lcview_fops = {
    .owner          = THIS_MODULE,
    .open           = lcview_open,
    .release        = lcview_release,
    .read           = lcview_read,
    .poll           = lcview_poll,
    .unlocked_ioctl = lcview_ioctl,
    .compat_ioctl   = lcview_ioctl,
};

/*
 * lcview_builder_start — 公开入口：构造结构化事件记录
 *
 * 这是 EXPORT_SYMBOL 的入口函数，供其他内核模块（如 USB 驱动）调用。
 *
 * 工作流程：
 *   1. 检查 level >= min_level，低于阈值则返回 NULL（跳过记录，不分配内存）
 *   2. 调用 lcview_builder_new 分配构建器（GFP_ATOMIC）
 *   3. 调用者通过返回的 lcview_builder 指针调用 add_* 系列 API 填入字段
 *   4. 最后调用 lcview_builder_commit 将记录写入环形缓冲区
 *
 * 为什么不直接让调用者调用 lcview_builder_new？
 * 因为 lcview_builder_start 封装了 min_level 过滤逻辑——如果在这里
 * 过滤掉低级别事件，可以完全避免后续的 add_* 和序列化开销。
 * 调用者得到 NULL 后应跳过整个日志记录过程。
 */
struct lcview_builder *lcview_builder_start(uint16_t event_id, uint8_t level)
{
    if (level < min_level)
        return NULL;
    return lcview_builder_new(event_id, level);
}
EXPORT_SYMBOL(lcview_builder_start);

/*
 * 导出所有 Builder API 符号，供其他内核模块动态链接使用。
 *
 * 为什么只导出了 lcview_builder_start 而这里又重复导出 add_*？
 * lcview_builder_start 是本文件定义的包装函数，而 add_int/add_str 等
 * 实际定义在 lcview_builder.c 中。每个导出的符号使其他模块可以：
 *   extern struct lcview_builder *lcview_builder_start(uint16_t, uint8_t);
 *   extern int lcview_builder_add_int(struct lcview_builder *, int64_t);
 * 而不需要包含 lcview_internal.h（当然，最好还是包含头文件以获得类型检查）。
 */
EXPORT_SYMBOL(lcview_builder_add_int);
EXPORT_SYMBOL(lcview_builder_add_int32);
EXPORT_SYMBOL(lcview_builder_add_str);
EXPORT_SYMBOL(lcview_builder_add_float);
EXPORT_SYMBOL(lcview_builder_add_binary);
EXPORT_SYMBOL(lcview_builder_commit);
EXPORT_SYMBOL(lcview_builder_cancel);

/*
 * lcview_init — 模块初始化入口 (module_init)
 *
 * 初始化顺序及其必要性：
 *   1. lcview_ring_init — 必须先成功，因为后续所有操作都依赖环形缓冲区
 *   2. register_chrdev  — 注册字符设备，分配主设备号
 *   3. class_create     — 创建设备类，udev/mdev 据此在 /dev/ 下创建设备节点
 *   4. device_create    — 真正创建 /dev/vendor_lechao_lcview 设备节点
 *
 * 错误回滚策略：每个步骤失败时，使用 goto 按逆序释放已分配资源。
 * 这种"集中式错误处理"是 Linux 内核编程的惯用模式——比在每个路径
 * 重复释放代码更安全（不容易遗漏）。
 *
 * 为什么用 register_chrdev（老式）而不是 cdev_init + cdev_add？
 * 因为模块只注册一个设备号，register_chrdev 更简洁。如果未来需要
 * 多个次设备号或多个设备，应切换到 cdev 接口。
 */
static int __init lcview_init(void)
{
    int ret;

    /* 第一步：初始化环形缓冲区 */
    ret = lcview_ring_init(&lcview_ring, ring_size_kb);
    if (ret) {
        pr_err(PREFIX "failed to init ring buffer\n");
        return ret;
    }

    /* 第二步：注册字符设备（动态主设备号） */
    major_number = register_chrdev(0, DEVICE_NAME, &lcview_fops);
    if (major_number < 0) {
        pr_err(PREFIX "failed to register chrdev\n");
        ret = major_number;
        goto err_ring;
    }

    /* 第三步：创建设备类（sysfs 接口） */
    lcview_class = class_create(CLASS_NAME);
    if (IS_ERR(lcview_class)) {
        pr_err(PREFIX "failed to create class\n");
        ret = PTR_ERR(lcview_class);
        goto err_chrdev;
    }

    /* 第四步：创建设备节点（触发 udev 创建 /dev/vendor_lechao_lcview） */
    lcview_device = device_create(lcview_class, NULL,
                                  MKDEV(major_number, 0), NULL, DEVICE_NAME);
    if (IS_ERR(lcview_device)) {
        pr_err(PREFIX "failed to create device\n");
        ret = PTR_ERR(lcview_device);
        goto err_class;
    }

    pr_info(PREFIX "initialized (ring=%uKB, major=%d)\n",
            ring_size_kb, major_number);
    return 0;

err_class:
    class_destroy(lcview_class);
err_chrdev:
    unregister_chrdev(major_number, DEVICE_NAME);
err_ring:
    lcview_ring_destroy(&lcview_ring);
    return ret;
}

module_init(lcview_init);
