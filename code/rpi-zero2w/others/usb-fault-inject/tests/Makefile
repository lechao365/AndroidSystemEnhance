# usb-fault-inject 纯逻辑 host 单测
# 编译运行：make test；退出码 0 全过。
# 目标：不依赖 Raw Gadget 设备的纯逻辑（expect_table schema / fault_id 命名）
#       直接编入 host 测试，接入 harness/lib/check_host_tests.py（R-18 P5 方向 7）。
CC ?= gcc
CFLAGS ?= -O2 -Wall -Wextra -Wno-unused-parameter

expect_schema_host_test: expect_schema_host_test.c ../expect.c ../faults.c
	$(CC) $(CFLAGS) -I.. -o $@ expect_schema_host_test.c ../expect.c ../faults.c

test: expect_schema_host_test
	./expect_schema_host_test

clean:
	rm -f expect_schema_host_test

.PHONY: test clean
