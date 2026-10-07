ccflags-y := -I$(src) \
	     -I$(srctree)/drivers/usb/storage \
	     -I$(srctree)/drivers/usb/core \
	     -I$(srctree)/vendor/lechao/LcView \
	     -I$(srctree)/vendor/lechao

lciod_usbd-objs := lciod_usbd.o lciod_usbd-stats.o lciod_read_logic.o lciod_link.o
obj-$(CONFIG_VENDOR_LECHAO_USBD) += lciod_usbd.o
