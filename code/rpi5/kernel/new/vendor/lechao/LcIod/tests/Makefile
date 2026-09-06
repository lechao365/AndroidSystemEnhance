# ============================================================
# lciod host 单测（无内核依赖的纯逻辑测试，gcc host 编译执行）
# 与 LcView/tests/Makefile 同构
# ============================================================

CFLAGS := -Wall -Wextra -O2

test: lciod_read_logic_host_test
	./lciod_read_logic_host_test

lciod_read_logic_host_test: lciod_read_logic_host_test.c ../lciod_read_logic.c ../lciod_read_logic.h
	$(CC) $(CFLAGS) -I.. -o $@ lciod_read_logic_host_test.c ../lciod_read_logic.c

clean:
	rm -f lciod_read_logic_host_test

.PHONY: test clean
