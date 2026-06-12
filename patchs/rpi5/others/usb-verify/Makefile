CC ?= gcc
CFLAGS += -Wall -Wextra -Werror -std=c11 -D_POSIX_C_SOURCE=199309L -Iinclude -Isrc/common -Isrc/device -Isrc/check -Isrc/cli
LDFLAGS +=

SRCS := \
  src/common/util.c \
  src/device/usbd_device.c \
  src/check/stats_check.c \
  src/check/event_check.c \
  src/cli/parse.c \
  src/cli/output.c \
  src/cli/main.c

fault-verify: $(SRCS)
	$(CC) $(CFLAGS) -o $@ $(SRCS) $(LDFLAGS)

clean:
	rm -f fault-verify

.PHONY: clean
