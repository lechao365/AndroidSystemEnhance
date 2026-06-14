# Makefile for usb-fault-inject
# 目标平台: Raspberry Pi Zero 2W (32-bit ARM, dwc2 gadget)
# 编译: arm-linux-gnueabihf-gcc (Raspberry Pi OS Lite 32-bit)
#
# 用法:
#   make                  # 默认 arm 交叉编译
#   make CC=gcc           # 本地编译 (host x86_64)
#   make CROSS=            # 清除交叉编译前缀
#   make clean

CROSS ?= arm-linux-gnueabihf-
CC    ?= $(CROSS)gcc

CFLAGS  = -O2 -Wall -Wextra -Wno-unused-parameter
LDFLAGS =

TARGET  = usb-fault-inject
SRCS    = main.c raw-gadget.c usb-descriptors.c scsi.c bot.c faults.c expect.c
OBJS    = $(SRCS:.c=.o)

.PHONY: all clean

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $^ $(LDFLAGS)

%.o: %.c
	$(CC) $(CFLAGS) -c $< -o $@

clean:
	rm -f $(TARGET) $(OBJS)
