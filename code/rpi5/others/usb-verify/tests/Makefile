# usb-verify 纯逻辑 host 单测
# 编译运行：make test；退出码 0 全过。
# 目标：不依赖设备节点的纯函数（事件名映射/统计断言/事件断言/报告汇总）
#       直接编入 host 测试，接入 harness/lib/check_host_tests.py（R-18 P5 方向 6）。
CC ?= gcc
CFLAGS ?= -Wall -Wextra -Werror -std=c11 -D_POSIX_C_SOURCE=199309L \
	-I../include -I../src/common -I../src/device -I../src/check -I../src/cli

SRCS := \
  ../src/common/util.c \
  ../src/device/usbd_device.c \
  ../src/check/stats_check.c \
  ../src/check/event_check.c \
  ../src/cli/parse.c \
  ../src/cli/output.c

fv_pure_host_test: fv_pure_host_test.c $(SRCS)
	$(CC) $(CFLAGS) -o $@ fv_pure_host_test.c $(SRCS)

test: fv_pure_host_test
	./fv_pure_host_test

clean:
	rm -f fv_pure_host_test

.PHONY: test clean
