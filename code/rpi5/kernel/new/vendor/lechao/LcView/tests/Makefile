# LcView ring 纯逻辑 host 单测
# 编译运行：make test；退出码 0 全过。
# 不引内核头/KUnit——内核 API 已在 lcview_ring_logic.c 剥离（等价 shim 语义），
# 内核与单测共用同一份源码，杜绝复制漂移。
#
# 方向 1（调用点补链）：除 logic.c 纯逻辑判红外，还把调用点真实代码
# （../lcview_builder.c、../lcview_ring.c）一并编入 host 测试，配 host_shim/
# 内核 API 假头（-D__KERNEL__ + -I host_shim 拦截 <linux/...>），使调用点
# 改坏（漏扣 4B 前缀 / 判损坏跳过只前移前缀+头）在 host 层判红，不再照绿。
# lcview_callsite_host_test.c 直接调用真实调用点函数做边界/损坏判红。
CC ?= gcc
CFLAGS ?= -O2 -Wall -Wextra -Werror

# 调用点编译需内核头语义：-D__KERNEL__ 走 lcview_*.h 内核分支，
# -I host_shim 让 <linux/...> 命中假头；-I../.. 找 kernel_lechao_log.h
# （vendor/lechao 顶层）——绝不进入内核编译
CALLSITE_CFLAGS = -D__KERNEL__ -Ihost_shim -I.. -I../..

test: lcview_ring_host_test lcview_callsite_host_test
	./lcview_ring_host_test
	./lcview_callsite_host_test

# 纯逻辑测试（logic.c + 全部函数直调）
lcview_ring_host_test: lcview_ring_host_test.c ../lcview_ring_logic.c ../lcview_ring_logic.h
	$(CC) $(CFLAGS) -I.. -o $@ lcview_ring_host_test.c ../lcview_ring_logic.c

# 调用点测试（builder/ring 真实调用点 + shim）
lcview_callsite_host_test: lcview_callsite_host_test.c ../lcview_builder.c ../lcview_ring.c ../lcview_ring_logic.c
	$(CC) $(CFLAGS) $(CALLSITE_CFLAGS) -o $@ lcview_callsite_host_test.c \
		../lcview_builder.c ../lcview_ring.c ../lcview_ring_logic.c

clean:
	rm -f lcview_ring_host_test lcview_callsite_host_test

.PHONY: test clean
