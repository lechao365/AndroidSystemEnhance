ccflags-y := -I$(src) \
	     -I$(srctree)/drivers/usb/storage \
	     -I$(srctree)/vendor/lechao/LcView \
	     -I$(srctree)/vendor/lechao

lciod_usbd-objs := lciod_usbd.o lciod_usbd-stats.o
obj-$(CONFIG_VENDOR_LECHAO_USBD) += lciod_usbd.o
