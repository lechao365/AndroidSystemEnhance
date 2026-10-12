ccflags-y := -I$(src) \
	     -I$(srctree)/drivers/usb/storage \
	     -I$(srctree)/drivers/usb/core \
	     -I$(srctree)/drivers/mmc/core \
	     -I$(srctree)/vendor/lechao/LcView \
	     -I$(srctree)/vendor/lechao

lciod_usbd-objs := lciod_usbd.o lciod_usbd-stats.o lciod_read_logic.o lciod_link.o
obj-$(CONFIG_VENDOR_LECHAO_USBD) += lciod_usbd.o

# C 批一：直方图纯函数，USBD 与 SD 共用，单独编译一次（勿并入任一 -objs 防重复符号）
obj-$(CONFIG_VENDOR_LECHAO) += lciod_histo.o

# A 批一：SD/MMC 块层健康监控（独立模块，依赖 MMC_BLOCK=y + LCVIEW）
obj-$(CONFIG_VENDOR_LECHAO_SD_MON) += lciod_sd.o
