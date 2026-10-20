// ============================================================
// writeback_guard.cpp — zram writeback 拔盘韧性守护实现（事件 id=21）
// 实现见 writeback_guard.h。纯函数无 I/O；采样层仅只读 sysfs。
// 读取失败按"无供给"暴露（不静默当正常，CXX-002 不吞错）；无任何
// 块设备写或重绑动作。
// ============================================================

#define LOG_TAG "lechao_lciod"

#include "writeback_guard.h"

#include <cstdio>
#include <fstream>
#include <unistd.h>

namespace lechao {
namespace lciod {

namespace {

// 去除首尾空白（backing_dev 文件以换行结尾）。
std::string Trim(const std::string& s)
{
    const size_t b = s.find_first_not_of(" \t\r\n");
    if (b == std::string::npos)
        return "";
    const size_t e = s.find_last_not_of(" \t\r\n");
    return s.substr(b, e - b + 1);
}

}  // namespace

std::string ParseBackingTarget(const std::string& raw)
{
    std::string s = Trim(raw);
    // 内核在设备移除后把名字标记为 " (deleted)"，先剥掉再取设备名。
    const std::string deleted = " (deleted)";
    if (s.size() >= deleted.size() &&
        s.compare(s.size() - deleted.size(), deleted.size(), deleted) == 0) {
        s.resize(s.size() - deleted.size());
        s = Trim(s);
    }
    if (s.empty() || s == "none")
        return "";
    const size_t slash = s.find_last_of('/');
    const std::string name = (slash == std::string::npos) ? s : s.substr(slash + 1);
    return Trim(name);
}

bool EvaluateWritebackHealthy(const std::string& raw, bool target_present)
{
    // 设备已移除标记：即便解析出名字也不能视为健康（写回必然 EIO）。
    if (raw.find("(deleted)") != std::string::npos)
        return false;
    if (ParseBackingTarget(raw).empty())
        return false;
    return target_present;
}

bool QueryWritebackSupply(WritebackSupplySample* out)
{
    if (out == nullptr)
        return false;
    out->backing_dev.clear();
    out->target_name.clear();
    out->target_present = false;
    out->healthy = false;

    // zram0 未启用/节点缺失时读失败 → 保持空串，按"无供给"暴露。
    std::ifstream ifs(kWritebackBackingDevPath);
    if (ifs.is_open()) {
        std::string line;
        if (std::getline(ifs, line))
            out->backing_dev = Trim(line);
    }

    out->target_name = ParseBackingTarget(out->backing_dev);
    if (!out->target_name.empty()) {
        const std::string path =
            std::string(kWritebackBlockDir) + "/" + out->target_name;
        out->target_present = (access(path.c_str(), F_OK) == 0);
    }
    out->healthy = EvaluateWritebackHealthy(out->backing_dev, out->target_present);
    return true;
}

int FormatWritebackGuardJson(bool lost, const std::string& backing_dev,
                             bool target_present, char* out, size_t cap)
{
    if (out == nullptr || cap == 0)
        return -1;
    return std::snprintf(
        out, cap,
        "{\"rule\":\"writeback_guard\",\"state\":\"%s\",\"lost\":%d,"
        "\"target_present\":%d,\"backing_dev\":\"%s\"}",
        lost ? "lost" : "recovered", lost ? 1 : 0, target_present ? 1 : 0,
        backing_dev.c_str());
}

}  // namespace lciod
}  // namespace lechao
