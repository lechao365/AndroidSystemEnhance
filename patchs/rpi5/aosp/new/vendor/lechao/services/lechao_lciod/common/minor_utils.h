/*
 * ============================================================
 * minor_utils.h — LcIod 设备 minor 编号公共解析工具
 *
 * 设计目的：
 *   统一 HAL 与 Daemon 对 /dev/vendor_lechao_usbdN 路径的解析逻辑，
 *   消除两处重复的 extract_minor 实现，避免规则漂移。
 *
 * 使用方：
 *   - hal/hal_service.cpp
 *   - daemon/service.cpp
 * ============================================================
 */
#ifndef _LECHAO_LCIOD_MINOR_UTILS_H
#define _LECHAO_LCIOD_MINOR_UTILS_H

#include <string>
#include <cstdint>

namespace lechao {
namespace lciod {

/*
 * 设备节点路径前缀
 * 与内核 vendor_lechao_usbd_devnode() 生成的节点名一致
 */
inline constexpr const char* kUsbdDevPrefix = "/dev/vendor_lechao_usbd";

/*
 * ParseMinorFromPath — 从设备节点路径解析 minor 编号
 * @path:   设备节点路径，如 "/dev/vendor_lechao_usbd0"
 * @minor:  输出参数，解析成功时写入 minor 编号
 * 返回:    true 表示解析成功；false 表示路径格式无效或数字越界
 *
 * 严格校验：
 *   1) 必须以 kUsbdDevPrefix 开头
 *   2) 后缀必须全部为数字（不允许空字符串、非数字字符）
 *   3) 数字范围 [0, 65535]，避免 atoi 溢出隐患
 */
bool ParseMinorFromPath(const std::string& path, int32_t* minor);

/*
 * BuildDevicePath — 根据 minor 编号构造设备节点路径
 * @minor: 次设备号
 * 返回:   形如 "/dev/vendor_lechao_usbd0" 的路径字符串
 */
std::string BuildDevicePath(int32_t minor);

}  // namespace lciod
}  // namespace lechao

#endif  // _LECHAO_LCIOD_MINOR_UTILS_H
