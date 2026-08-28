# SPDX-License-Identifier: GPL-2.0
#
# LcView 内核驱动编译规则
#
# 模块组成（四合一链接为 lcview.o / lcview.ko）：
#   lcview_main.o      — 字符设备驱动核心 (open/read/poll/ioctl) + EXPORT_SYMBOL 入口
#   lcview_ring.o      — 环形缓冲区实现（无锁写 + overflow eviction + 阻塞读）
#   lcview_ring_logic.o — 环形缓冲区纯索引逻辑（与 tests/ host 单测共用，
#                         改动后须跑 host 单测：make -C tests/ test）
#   lcview_builder.o   — Builder 模式结构化事件构建 API
#
# 由 CONFIG_LCVIEW 控制编译（Kconfig 当前为 bool，内建进 Image；
# 若未来改为 tristate 则产出 lcview.ko，届时需补齐 module_exit
# 卸载链：device_destroy/class_destroy/unregister_chrdev/ring_destroy）。
# 父级 Makefile (vendor/lechao/Makefile) 引入。

obj-$(CONFIG_LCVIEW) += lcview.o
lcview-objs := lcview_main.o lcview_ring.o lcview_ring_logic.o lcview_builder.o

# 头文件搜索路径：
#   -I$(src)          ：本目录 (LcView/) 查找 lcview_*.h
#   -I$(srctree)/vendor/lechao ：上级目录查找 kernel_lechao_log.h
ccflags-y := -I$(src) -I$(srctree)/vendor/lechao
