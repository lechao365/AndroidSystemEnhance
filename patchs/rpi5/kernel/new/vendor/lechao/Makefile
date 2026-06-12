#
# Lechao Vendor 内核模块顶层 Makefile
#
# 编译规则：
#   1. 当 CONFIG_VENDOR_LECHAO=y 时，编译 drivers/ 目录下的所有驱动
#   2. 当 CONFIG_LCVIEW=y 时，编译 LcView/ 目录下的日志子系统
#
# 这两个条件相互独立：CONFIG_LCVIEW 依赖 CONFIG_VENDOR_LECHAO 但在 Kconfig
# 层级上并不自动 select，用户需在 menuconfig 中分别配置。
# 但编译系统通过 obj-$(CONFIG_*) 条件判断，未选中的目录不会进入编译。

obj-$(CONFIG_VENDOR_LECHAO) += LcIod/
obj-$(CONFIG_LCVIEW) += LcView/
