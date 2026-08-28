# LcView ring 纯逻辑 host 单测
# 编译运行：make test；退出码 0 全过。
# 不引内核头/KUnit——内核 API 已在 lcview_ring_logic.c 剥离（等价 shim 语义），
# 内核与单测共用同一份源码，杜绝复制漂移。
CC ?= gcc
CFLAGS ?= -O2 -Wall -Wextra -Werror

test: lcview_ring_host_test
	./lcview_ring_host_test

lcview_ring_host_test: lcview_ring_host_test.c ../lcview_ring_logic.c ../lcview_ring_logic.h
	$(CC) $(CFLAGS) -I.. -o $@ lcview_ring_host_test.c ../lcview_ring_logic.c

clean:
	rm -f lcview_ring_host_test

.PHONY: test clean
