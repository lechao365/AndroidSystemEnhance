/*
 * ============================================================
 * minor_utils.cpp — LcIod 设备 minor 编号公共解析实现
 *
 * 严格数字校验，避免 atoi 对空串/非数字/溢出的隐患。
 * ============================================================
 */
#include "minor_utils.h"

#include <cctype>
#include <cerrno>
#include <cstdlib>

namespace lechao {
namespace lciod {

bool ParseMinorFromPath(const std::string& path, int32_t* minor) {
    const size_t prefix_len = std::char_traits<char>::length(kUsbdDevPrefix);

    /* 1) 前缀匹配 */
    if (path.compare(0, prefix_len, kUsbdDevPrefix) != 0)
        return false;

    /* 2) 后缀必须非空且全部为数字 */
    const std::string suffix = path.substr(prefix_len);
    if (suffix.empty())
        return false;
    for (char c : suffix) {
        if (!std::isdigit(static_cast<unsigned char>(c)))
            return false;
    }

    /* 3) strtol 解析并做范围校验（atoi 无法区分 0 与错误） */
    errno = 0;
    char* end = nullptr;
    long val = std::strtol(suffix.c_str(), &end, 10);
    if (errno != 0 || end == suffix.c_str() || *end != '\0')
        return false;
    if (val < 0 || val > 65535)
        return false;

    *minor = static_cast<int32_t>(val);
    return true;
}

std::string BuildDevicePath(int32_t minor) {
    return std::string(kUsbdDevPrefix) + std::to_string(minor);
}

}  // namespace lciod
}  // namespace lechao
