# SPDX-License-Identifier: GPL-2.0
#
# LcView 内核模块编译规则
#
# 模块组成（三合一编译为 lcview.ko）：
#   lcview_main.o   — 字符设备驱动核心 (open/read/poll/ioctl) + EXPORT_SYMBOL 入口
#   lcview_ring.o   — 环形缓冲区实现（无锁写 + overflow eviction + 阻塞读）
#   lcview_builder.o — Builder 模式结构化事件构建 API
#
# 由 CONFIG_LCVIEW 控制编译，父级 Makefile (vendor/lechao/Makefile) 引入。

obj-$(CONFIG_LCVIEW) += lcview.o
lcview-objs := lcview_main.o lcview_ring.o lcview_builder.o

# 头文件搜索路径：
#   -I$(src)          ：本目录 (LcView/) 查找 lcview_*.h
#   -I$(srctree)/vendor/lechao ：上级目录查找 kernel_lechao_log.h
ccflags-y := -I$(src) -I$(srctree)/vendor/lechao
