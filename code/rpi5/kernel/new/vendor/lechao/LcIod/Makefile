ccflags-y := -I$(src) \
	     -I$(srctree)/drivers/usb/storage \
	     -I$(srctree)/drivers/usb/core \
	     -I$(srctree)/drivers/mmc/core \
	     -I$(srctree)/vendor/lechao/LcView \
	     -I$(srctree)/vendor/lechao

lciod_usbd-objs := lciod_usbd.o lciod_usbd-stats.o lciod_read_logic.o lciod_link.o
obj-$(CONFIG_VENDOR_LECHAO_USBD) += lciod_usbd.o

# A 批一：SD/MMC 块层健康监控（独立模块，依赖 MMC_BLOCK=y + LCVIEW）
obj-$(CONFIG_VENDOR_LECHAO_SD_MON) += lciod_sd.o
