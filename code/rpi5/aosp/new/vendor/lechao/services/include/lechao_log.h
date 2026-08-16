#pragma once

#include <sys/system_properties.h>
#include <string.h>

namespace lechao {

inline bool debugVerbose() {
    // NOTE: cached 为 static 局部变量，首次调用后缓存结果不再刷新。
    // 修改 persist.vendor.lechao.loglevel 后需重启进程才能生效。
    static int cached = -1;
    if (cached < 0) {
        char val[92] = {0};
        __system_property_get("persist.vendor.lechao.loglevel", val);
        cached = (val[0] == '1') ? 1 : 0;
    }
    return cached == 1;
}

} // namespace lechao

// HAL layer (android-base/logging)
#define LC_LOGE(...) LOG(ERROR) << __VA_ARGS__
#define LC_LOGW(...) LOG(WARNING) << __VA_ARGS__
#define LC_LOGI(...) LOG(INFO) << __VA_ARGS__
#define LC_LOGD(...) do { \
    if (::lechao::debugVerbose()) LOG(INFO) << "[D] " << __VA_ARGS__; \
} while(0)

// Daemon layer (liblog)
#define LC_ALOGE(...) ALOGE(__VA_ARGS__)
#define LC_ALOGW(...) ALOGW(__VA_ARGS__)
#define LC_ALOGI(...) ALOGI(__VA_ARGS__)
#define LC_ALOGD(...) do { \
    if (::lechao::debugVerbose()) ALOGI("[D] " __VA_ARGS__); \
} while(0)
