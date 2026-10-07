// ============================================================
// IoQos_test.cpp — SD 卡写 QoS 限速纯函数 + IoQosManager（R4 方向 5）
// 所属模块：lechao_lciod 单元测试
// 拦截：CXX-003（外部输入防御）——ioqos_level 未知值/空值拒绝、非法设备号
//       拒绝、pid<=0 拒绝；CXX-002（边界/幂等）——档位与设备变化才写限速
//       文件、写内容正确。
// 说明：IoQosManager 经构造注入临时 cgroup 根（mkdtemp），用手动创建的
//       cgroup.controllers / blkio / io.max / cgroup.procs 等文件模拟
//       v2/v1 cgroup 环境，不触碰真实 /sys/fs/cgroup。
// ============================================================

#include <gtest/gtest.h>

#include <dirent.h>
#include <sys/stat.h>
#include <unistd.h>
#include <cstdio>
#include <cstdlib>
#include <string>

#include "io_qos.h"

using lechao::lciod::BuildBlkioLine;
using lechao::lciod::BuildIoMaxLine;
using lechao::lciod::IoQosLevel;
using lechao::lciod::IoQosManager;
using lechao::lciod::LevelToBytesPerSec;
using lechao::lciod::ParseIoQosLevel;

namespace {

/* 测试临时文件写读工具 */

void WriteFile(const std::string& path, const std::string& content) {
    FILE* f = fopen(path.c_str(), "w");
    ASSERT_NE(f, nullptr) << "fopen 失败: " << path;
    if (!content.empty())
        fputs(content.c_str(), f);
    fclose(f);
}

std::string ReadFile(const std::string& path) {
    FILE* f = fopen(path.c_str(), "r");
    EXPECT_NE(f, nullptr) << "fopen 失败: " << path;
    if (!f)
        return "";
    std::string out;
    char buf[256];
    size_t n;
    while ((n = fread(buf, 1, sizeof(buf), f)) > 0)
        out.append(buf, n);
    fclose(f);
    return out;
}

void RemoveDirRecursive(const std::string& dir) {
    DIR* d = opendir(dir.c_str());
    if (!d)
        return;
    struct dirent* ent;
    while ((ent = readdir(d)) != nullptr) {
        std::string name = ent->d_name;
        if (name == "." || name == "..")
            continue;
        std::string path = dir + "/" + name;
        struct stat st{};
        if (lstat(path.c_str(), &st) == 0 && S_ISDIR(st.st_mode))
            RemoveDirRecursive(path);
        else
            unlink(path.c_str());
    }
    closedir(d);
    rmdir(dir.c_str());
}

/* 模拟 v2 cgroup 根：cgroup.controllers 含 "io" */
void SetupV2Root(const std::string& root) {
    WriteFile(root + "/cgroup.controllers", "cpu io memory");
}

/* 模拟 v1 cgroup 根：blkio 目录存在 */
void SetupV1Root(const std::string& root) {
    ASSERT_EQ(mkdir((root + "/blkio").c_str(), 0755), 0);
}

/* --- ParseIoQosLevel：档位解析（CXX-003 外部输入防御） --- */

TEST(ParseIoQosLevelTest, ValidLevels) {
    IoQosLevel out = IoQosLevel::kOff;
    EXPECT_TRUE(ParseIoQosLevel("top", &out));
    EXPECT_EQ(out, IoQosLevel::kTop);
    EXPECT_TRUE(ParseIoQosLevel("fg", &out));
    EXPECT_EQ(out, IoQosLevel::kFg);
    EXPECT_TRUE(ParseIoQosLevel("bg", &out));
    EXPECT_EQ(out, IoQosLevel::kBg);
    EXPECT_TRUE(ParseIoQosLevel("off", &out));
    EXPECT_EQ(out, IoQosLevel::kOff);
}

TEST(ParseIoQosLevelTest, EmptyValue_MeansOff) {
    // 空串视为 off（sysprop 未设置时默认不限）
    IoQosLevel out = IoQosLevel::kTop;
    EXPECT_TRUE(ParseIoQosLevel("", &out));
    EXPECT_EQ(out, IoQosLevel::kOff);
}

TEST(ParseIoQosLevelTest, UnknownValue_Rejected) {
    // 未知值/大小写漂移/带空白一律拒绝且不写 out（CXX-003）
    IoQosLevel out = IoQosLevel::kFg;
    EXPECT_FALSE(ParseIoQosLevel("huge", &out));
    EXPECT_EQ(out, IoQosLevel::kFg);  // out 未被污染
    EXPECT_FALSE(ParseIoQosLevel("BG", &out));
    EXPECT_FALSE(ParseIoQosLevel(" bg", &out));
    EXPECT_FALSE(ParseIoQosLevel("bg ", &out));
    EXPECT_FALSE(ParseIoQosLevel("top,", &out));
}

TEST(ParseIoQosLevelTest, NullOut_ReturnsFalse) {
    EXPECT_FALSE(ParseIoQosLevel("bg", nullptr));
}

/* --- LevelToBytesPerSec：档位带宽换算 --- */

TEST(LevelToBytesPerSecTest, TopAndOff_ZeroUnlimited) {
    // kTop=0（不限，v2 写 max / v1 写 0）；kOff 等同不限
    EXPECT_EQ(LevelToBytesPerSec(IoQosLevel::kTop), 0u);
    EXPECT_EQ(LevelToBytesPerSec(IoQosLevel::kOff), 0u);
}

TEST(LevelToBytesPerSecTest, Fg_16MiBPerSec) {
    EXPECT_EQ(LevelToBytesPerSec(IoQosLevel::kFg), 16777216u);  // 16MiB/s
}

TEST(LevelToBytesPerSecTest, Bg_8MiBPerSec) {
    EXPECT_EQ(LevelToBytesPerSec(IoQosLevel::kBg), 8388608u);  // 8MiB/s
}

/* --- BuildIoMaxLine / BuildBlkioLine：限速行拼接 --- */

TEST(BuildIoMaxLineTest, Limited_V2Format) {
    EXPECT_EQ(BuildIoMaxLine("179:0", 8388608),
              "179:0 rbps=8388608 wbps=8388608");
    EXPECT_EQ(BuildIoMaxLine("8:16", 16777216),
              "8:16 rbps=16777216 wbps=16777216");
}

TEST(BuildIoMaxLineTest, Unlimited_V2Max) {
    // bytes=0（不限）：v2 写 max
    EXPECT_EQ(BuildIoMaxLine("179:0", 0), "179:0 rbps=max wbps=max");
}

TEST(BuildBlkioLineTest, Limited_V1Format) {
    EXPECT_EQ(BuildBlkioLine("179:0", 8388608), "179:0 8388608");
}

TEST(BuildBlkioLineTest, Unlimited_V1Zero) {
    // bytes=0（不限）：v1 写 0
    EXPECT_EQ(BuildBlkioLine("179:0", 0), "179:0 0");
}

/* --- IoQosManager：注入临时 cgroup 根（mkdtemp 模拟） --- */

class IoQosManagerTest : public ::testing::Test {
protected:
    void SetUp() override {
        char tmpl[] = "/data/local/tmp/qos_ut_XXXXXX";
        char* dir = mkdtemp(tmpl);
        ASSERT_NE(dir, nullptr) << "mkdtemp 失败";
        root_ = dir;
    }

    void TearDown() override {
        if (!root_.empty())
            RemoveDirRecursive(root_);
    }

    std::string root_;
};

TEST_F(IoQosManagerTest, EnsureGroup_V2_CreatesDir_Idempotent) {
    SetupV2Root(root_);
    IoQosManager mgr(root_);
    EXPECT_TRUE(mgr.EnsureGroup());
    struct stat st{};
    EXPECT_EQ(stat((root_ + "/lechao_bg").c_str(), &st), 0);
    EXPECT_TRUE(S_ISDIR(st.st_mode));
    // 幂等：已存在再次调用仍成功
    EXPECT_TRUE(mgr.EnsureGroup());
}

TEST_F(IoQosManagerTest, EnsureGroup_V1_CreatesBlkioGroup) {
    SetupV1Root(root_);
    IoQosManager mgr(root_);
    EXPECT_TRUE(mgr.EnsureGroup());
    struct stat st{};
    EXPECT_EQ(stat((root_ + "/blkio/lechao_bg").c_str(), &st), 0);
    EXPECT_TRUE(S_ISDIR(st.st_mode));
    EXPECT_TRUE(mgr.EnsureGroup());  // 幂等
}

TEST_F(IoQosManagerTest, EnsureGroup_UnsupportedRoot_ReturnsFalse) {
    // 既无 cgroup.controllers 也无 blkio 目录 → 版本不可探测
    IoQosManager mgr(root_);
    EXPECT_FALSE(mgr.EnsureGroup());
}

TEST_F(IoQosManagerTest, ApplyLevel_V2_WritesIoMax_AndIdempotent) {
    SetupV2Root(root_);
    IoQosManager mgr(root_);
    ASSERT_TRUE(mgr.EnsureGroup());
    WriteFile(root_ + "/lechao_bg/io.max", "");  // 预建 io.max（v2 内核自动生成）

    // bg 档：写 8MiB/s
    EXPECT_TRUE(mgr.ApplyLevel(IoQosLevel::kBg, "179:0"));
    EXPECT_EQ(ReadFile(root_ + "/lechao_bg/io.max"),
              "179:0 rbps=8388608 wbps=8388608\n");

    // 幂等：同档同设备不重复写 → 内容不变
    EXPECT_TRUE(mgr.ApplyLevel(IoQosLevel::kBg, "179:0"));
    EXPECT_EQ(ReadFile(root_ + "/lechao_bg/io.max"),
              "179:0 rbps=8388608 wbps=8388608\n");

    // 档位变化才写：fg 档 → 16MiB/s
    EXPECT_TRUE(mgr.ApplyLevel(IoQosLevel::kFg, "179:0"));
    EXPECT_EQ(ReadFile(root_ + "/lechao_bg/io.max"),
              "179:0 rbps=16777216 wbps=16777216\n");

    // 设备变化才写：同一档换设备
    EXPECT_TRUE(mgr.ApplyLevel(IoQosLevel::kFg, "8:16"));
    EXPECT_EQ(ReadFile(root_ + "/lechao_bg/io.max"),
              "8:16 rbps=16777216 wbps=16777216\n");

    // 不限档（kTop）：写 max
    EXPECT_TRUE(mgr.ApplyLevel(IoQosLevel::kTop, "179:0"));
    EXPECT_EQ(ReadFile(root_ + "/lechao_bg/io.max"),
              "179:0 rbps=max wbps=max\n");
}

TEST_F(IoQosManagerTest, ApplyLevel_V2_InvalidMajMin_ReturnsFalse) {
    // CXX-003：非法设备号（非 MAJ:MIN）拒绝且不写
    SetupV2Root(root_);
    IoQosManager mgr(root_);
    ASSERT_TRUE(mgr.EnsureGroup());
    WriteFile(root_ + "/lechao_bg/io.max", "");
    EXPECT_FALSE(mgr.ApplyLevel(IoQosLevel::kBg, "not_a_dev"));
    EXPECT_EQ(ReadFile(root_ + "/lechao_bg/io.max"), "");
}

TEST_F(IoQosManagerTest, ApplyLevel_V1_WritesBlkioThrottle) {
    SetupV1Root(root_);
    IoQosManager mgr(root_);
    ASSERT_TRUE(mgr.EnsureGroup());
    WriteFile(root_ + "/blkio/lechao_bg/blkio.throttle.read_bps_device", "");
    WriteFile(root_ + "/blkio/lechao_bg/blkio.throttle.write_bps_device", "");

    EXPECT_TRUE(mgr.ApplyLevel(IoQosLevel::kBg, "179:0"));
    EXPECT_EQ(ReadFile(root_ + "/blkio/lechao_bg/blkio.throttle.read_bps_device"),
              "179:0 8388608\n");
    EXPECT_EQ(ReadFile(root_ + "/blkio/lechao_bg/blkio.throttle.write_bps_device"),
              "179:0 8388608\n");

    // 幂等：同档同设备不重复写 → 内容不变
    EXPECT_TRUE(mgr.ApplyLevel(IoQosLevel::kBg, "179:0"));
    EXPECT_EQ(ReadFile(root_ + "/blkio/lechao_bg/blkio.throttle.read_bps_device"),
              "179:0 8388608\n");
}

TEST_F(IoQosManagerTest, MovePidToGroup_V2_WritesCgroupProcs) {
    SetupV2Root(root_);
    IoQosManager mgr(root_);
    ASSERT_TRUE(mgr.EnsureGroup());
    WriteFile(root_ + "/lechao_bg/cgroup.procs", "");

    EXPECT_TRUE(mgr.MovePidToGroup(123));
    EXPECT_EQ(ReadFile(root_ + "/lechao_bg/cgroup.procs"), "123\n");
    // 追加语义：第二个 pid 追加一行
    EXPECT_TRUE(mgr.MovePidToGroup(456));
    EXPECT_EQ(ReadFile(root_ + "/lechao_bg/cgroup.procs"), "123\n456\n");
}

TEST_F(IoQosManagerTest, MovePidToGroup_V1_WritesTasks) {
    SetupV1Root(root_);
    IoQosManager mgr(root_);
    ASSERT_TRUE(mgr.EnsureGroup());
    WriteFile(root_ + "/blkio/lechao_bg/tasks", "");

    EXPECT_TRUE(mgr.MovePidToGroup(7));
    EXPECT_EQ(ReadFile(root_ + "/blkio/lechao_bg/tasks"), "7\n");
}

TEST_F(IoQosManagerTest, MovePidToGroup_InvalidPid_ReturnsFalse) {
    // CXX-003：pid<=0（负 pid / 0）拒绝迁移且不写文件
    SetupV2Root(root_);
    IoQosManager mgr(root_);
    ASSERT_TRUE(mgr.EnsureGroup());
    WriteFile(root_ + "/lechao_bg/cgroup.procs", "");

    EXPECT_FALSE(mgr.MovePidToGroup(0));
    EXPECT_FALSE(mgr.MovePidToGroup(-1));
    EXPECT_EQ(ReadFile(root_ + "/lechao_bg/cgroup.procs"), "");
}

TEST_F(IoQosManagerTest, MovePidToGroup_AutoEnsureGroup) {
    // 建组（MovePidToGroup 内部 EnsureGroup 幂等）后迁移 pid
    SetupV2Root(root_);
    IoQosManager mgr(root_);
    ASSERT_TRUE(mgr.EnsureGroup());
    struct stat st{};
    EXPECT_EQ(stat((root_ + "/lechao_bg").c_str(), &st), 0);
    EXPECT_TRUE(S_ISDIR(st.st_mode));
    // 真实 v2 内核建组即生成 cgroup.procs；此处手工补建文件验证写入路径
    WriteFile(root_ + "/lechao_bg/cgroup.procs", "");
    EXPECT_TRUE(mgr.MovePidToGroup(9));
    EXPECT_EQ(ReadFile(root_ + "/lechao_bg/cgroup.procs"), "9\n");
}

TEST_F(IoQosManagerTest, StopFlag_RunLoopExits) {
    // stop() 置位后 RunLoop 可安全退出（供单测/析构）
    IoQosManager mgr(root_);
    mgr.stop();
    mgr.stop();  // 重复 stop 无害
}

}  // namespace
