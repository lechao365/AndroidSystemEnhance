---
name: workspace-verify
description: code→workspace 同步、增量编译、adb 增量推送、验收执行并写 data/verify-results 收据（cross-device-apply 拉起或独立触发）。
no_commit: true
stages:
  - research: "读批次/目标、判定影响面"
  - plan: "AI 制定编译推送与验收计划"
  - code: "执行同步/编译/推送/验收/落收据"
  - review: "落盘收据并汇总"
---
# workspace-verify

> **仅限 apply 设备（本地 WSL2）运行**（需 workspace 与开发板访问）。

核心语义：把 code/ 当前状态（含未提交改动）同步到 workspace 编译，增量推送上板，
按验收标签/自由文本判定结果，落盘 data/verify-results 收据；失败落 fail 收据并附失败现场，
跨轮重试/失败分析归 loop-engineering（见 harness/skills/loop-engineering/SKILL.md）。
## Trigger（触发条件）
- cross-device-apply 拉起（模式 A：--batch-file）
- 独立触发（模式 B：--target <12hex|dev|main> + --case <用例标签>，如 revert 后恢复验证；
  --case 为一等入口（默认 lcview-liveness），验收自动追加 boot 标签——
  设备存活是恢复的最低判据）
## Preconditions（前置条件）
- 本仓 dev/main 分支存在；KERNEL_WS/AOSP_WS 可访问（paths.conf）
- 设备可达（网络 adb；不可达时走串口诊断，见 Failure）
- 高危动作（整卡刷写、boot 分区 dd）必须人工确认
## Inputs（输入）
- 模式 A：批次文件路径（验收标签从批次解析）；模式 B：目标 commit + 验收用例标签
  （--case，一等入口默认 lcview-liveness；手打验收文本为兜底）
## Human confirmation gates（人工确认门）
- 仅整卡刷写 / boot 分区 dd 需确认；其余零确认
## Outputs / artifacts（输出/产物）
- data/verify-results/<时间戳>-<batch_id>.md + trend.md（只落盘，不 commit——由 git-works-push 随批统一提交）
- 收据 header `timings` 字段：模式 A 打点（apply 侧 start 后，verify 内部
  sync/build/push/unit_test/acceptance 各段，cdp_timing.py mark；缺失仅 warn 不阻断）
- harness/log/workspace-verify/ 运行日志（gitignore）
## Failure / recovery（失败/恢复）
- code→workspace 同步失败：verify 中止，收据 result=fail（build=fail board=skip）
- 编译/验收失败：本 skill 仅执行单次验证并落收据；跨轮重试/失败分析/修复编辑
  归 loop-engineering 会话管理（harness/skills/loop-engineering/SKILL.md）。
  无 loop 会话时由调用方 AI 自行决定是否重跑（重试纪律不内嵌本 skill）
   -> 失败收据正文含失败现场：logcat/dmesg 摘录，交 loop/调用方分析
   - 失败取证（有界只读）：python3 harness/skills/workspace-verify/ws_forensics.py
     --since-epoch <验证开始 epoch 秒> [--stdout-file f] [--stderr-file f]
     （一次收齐 host stdout/stderr + logcat crash + getprop/df/ps +
     尽力 dmesg/pstore + 仅本轮新增 tombstone；只读不改设备态，单文件
     512KB/总量 4MB 双上限截断，manifest.json 如实记录截断/跳过；
     产物 harness/log/forensics/run-<ts>/ 供收据正文引用与 AI 复盘）
- adb 不可达 → 自动三级通道：ws_acceptance 验收编排 ensure 失败自动以
  rescue_enabled=True 重试一次（rescue 经 ws_serial 设 service.adb.tcp.port 5555
  + 重启 adbd + 取 wlan0 IPv4，副作用必打印；仅编排层失败路径触发）；
  独立 CLI ensure 默认关、须 --rescue 显式开；状态判定 RESCUE_STATES =
  ok / full_brick / half_brick / boot_loop / rescue_unavailable）；
  仍失败再走串口砖机三分法人工诊断（入口：ws_serial.py 或 minicom，DBG-006 关硬件流控）：
  * 串口静默（SERIAL_SILENT，无任何输出）= 断电全砖 → 转人工
  * 串口有启动日志但 adb 起不来（adbd 未起 / NO_IPV4 无网）= 半砖 → 收据 fail 附串口日志，交 emit 分析
  * 串口反复输出相同启动日志 = boot loop → 收据 fail 附循环片段，交 emit 分析
  * 转发器未起（TCP 连不上）= rescue_unavailable（救援通道不可用，非设备故障，
    须先起 serial_bridge 再重试，不得误判断电全砖）
## Related policy IDs（关联规则 ID）
- SRC-001/002（修订后）、BLD-001~005/007~009、INC-001/003~007/009
---
## 工作流（参考实现细节）
0. 耗时打点（模式 A 可选）：打点文件由 apply 侧 start（batch_id 经
   cdp_parse.batch_id_from_text 解析，文件 harness/log/cross-device/
   timings-<batch_id>.json）。**各验证阶段由脚本自动打点**（sync/build/push/
   unit_test/acceptance 各一段，脚本完成即 mark，失败不阻断口径不变）：
   - sync：sync_code_to_workspace.py --auto 闭环完成自动 mark verify_sync
   - build：编译由执行者完成时触发（cdp_timing.py mark --name verify_build；
     batch 识别走 CDP_BATCH_ID 环境变量 > log 目录唯一 timings 文件，均缺
     静默跳过返 0，不阻断）
   - push：ws_push.py 推送循环完成自动 mark verify_push（实际推送完成后
     打点；ensure 连接成功不再打点——连接就绪量不到推送）
   - unit_test：ws_upload_tests.py 执行完成自动 mark verify_unit_test
   - acceptance：ws_acceptance.py run 完成自动 mark verify_acceptance
   重试轮沿用同段名 mark，不追加轮次前缀（段名保持稳定供复盘按段统计）；
   脚本自动 mark 缺轮次上下文时由执行者触发对应段。
0b. 链式入口（推荐，减编排往返）：三步确定性环节可单脚本串联——
    python3 harness/skills/workspace-verify/ws_verify_chain.py --result-file <chain.json>
    逐段 stdout 透传、rc 逐段门禁、失败即停（JSON 标注停在何步）；成功后
    直跳步骤 4b/5。失败时按原分步工作流重跑定位，各段打点口径不变。
1. 同步：python3 harness/skills/sync-code-to-workspace/sync_code_to_workspace.py --auto
   （同步源 = code 工作树当前状态；范围 = code/rpi5/{aosp,kernel}；
   data/verify-results、others/、rpi-zero2w 不参与同步）
   同步段打点（verify_sync）：脚本闭环完成自动 mark，无需手动
2. 影响面判定：git status --porcelain + git diff --name-only → 分类
   （aosp 模块 / 内核 / boot 相关 / others 不同步）
3. 编译：按 harness/config/verify-cases.yaml modules 段执行（不再硬编码）：
   - 测试：先 make <modules.<模块>.test_targets> -j$(nproc)（lcview 即
     lechao_lcview_unit_test——hal_test 已随 HAL 退役并入 unit_test，
     AGENTS.md 强制）
   - 部署：m <modules.<模块>.targets>（lcview 实读 Android.bp 的 4 个 Soong 模块）；
   增量路径按 incremental-dev-reference：
   - aosp 模块：m <module>（BLD-004 先 source build/envsetup.sh + lunch；BLD-005 禁裸 make）
   - boot/内核：make Image dtbs（BLD-001~003 Clang+LLD/产物拷贝 rpi5-kernel/；
     INC-006 Image+dtbs+overlays 同源；INC-009 android_rpi5_defconfig；INC-007 VINTF）
   - 打包：mk_rpi5_full_image.sh -mode 2|3|4（BLD-007 sudo 打包显式传
     TARGET_PRODUCT+ANDROID_PRODUCT_OUT；BLD-008 选对 mode）
   - 全程：INC-001 禁 make clean/clobber；BLD-009 CCACHE_DIR=out/ccache
   编译段打点（verify_build）：编译完成由执行者触发
   （cdp_timing.py mark --name verify_build，失败不阻断）
4. adb 推送：python3 harness/skills/workspace-verify/ws_push.py
   [--modules <模块名...>]（默认推送 verify-cases.yaml modules 段全部
   push 映射，映射即执行的唯一事实源，取代手敲 adb push；本步中止则
   步骤 5 不执行，不能等编排层兜底）：
   - 脚本内聚：ws_adb_connect ensure 连接 → adb root && adb remount
     （INC-003 失败查 verifiedbootstate=orange；INC-005 需 userdebug）
     → 按映射逐项推送（本地源 = AOSP out target/product/<product> 下
     与 dst 同相对路径的编译产物）→ 逐项回读设备侧 SHA256/字节数/
     SELinux 上下文与本地产物比对，任一不符判红 → 命中 sepolicy
     （/selinux/）或 vintf（/vintf/）或 init rc（.rc 结尾）的项强制
     reboot 并等待 sys.boot_completed 启动完成，跳过即判红（无跳过
      开关）→ 落自描述产物 --result-file
      harness/log/cross-device/push-<batch_id>.json（run_id + 逐项源/
      目标路径 + 三项校验值 sha256/bytes/context，原子写；供步骤 6
      PASS 核验，模式 B 可省略）
   推送段打点（verify_push）：ws_push.py 推送循环完成后自动 mark
   （实际推送完成后打点，含失败轮；ensure 连接成功不再打点）
   判红（exit 1）不得回退手敲 adb push——须修产物/环境后重跑本脚本
   （boot.img 刷写只写第一分区 INC-004，且属人工确认门，不在本脚本范围）
4b. 设备侧单测执行（制度化，AGENTS.md 测试防护强制）：
    python3 harness/skills/workspace-verify/ws_upload_tests.py \
      --result-file harness/log/cross-device/unit-tests-<batch_id>.json
    （从 verify-cases.yaml modules 段读 test_targets，覆盖 lcview/lciod 全部
    unit_test 与 hal_test；nativetest push 到设备运行 gtest 并汇总——
    仅编译不执行不达标，C++ 单测长期只编译不执行是 nextSeqFor 真 bug
    未被发现的根因；任一失败须修复后重跑，全部通过才进步骤 5；
    --result-file 写自描述单测产物（run_id + 每 target 返回码/用例数/失败数），
    供步骤 6 PASS 核验，无批次时也可省略）
    单测段打点（verify_unit_test）：脚本执行完成自动 mark，无需手动
5. 验收：python3 harness/skills/workspace-verify/ws_acceptance.py run --case <用例标签> \
      --result-file harness/log/cross-device/acceptance-<batch_id>.json
    （--case 为一等入口（默认 lcview-liveness），从 verify-cases.yaml cases 段取验收文本，
    与 L19/L26 一致；手打验收文本（--acceptance）为兜底；
    语法标签自动执行；overall=ai 的自由文本项由 AI 用 logcat/dmesg 现场判定并覆盖；
    步骤 4 有 reboot 时必须加 --wait-ready——连接后轮询 sys.boot_completed 就绪，
    未就绪按设备不可达退 1，且传 reboot 时刻（MM-DD HH:MM:SS.mmm）给 --log-since——
    logcat 时间窗从该时刻起，避免命中上轮旧日志致假绿；--log-since 自动按设备
    时钟/时区换算（本地 CST 时刻映射到设备 UTC 域，PIT-5 复发防护——直接传本地
    时刻会让窗落在设备"未来"取回 0 字符判红，已复发三次，勿再人工换算）；
    模式 B 加 --ensure-boot——验收无 boot 标签时自动追加，兑现 L20 设备存活最低判据；
    --result-file 写自描述验收产物（run_id/输入摘要/设备指纹/起止单调时间/
    逐项结果/总判定），供步骤 6 PASS 核验）
    验收段打点（verify_acceptance）：脚本执行完成自动 mark，无需手动
6. 收据：python3 harness/skills/workspace-verify/ws_report.py \
   --acceptance-file harness/log/cross-device/acceptance-<batch_id>.json \
   --unit-test-file harness/log/cross-device/unit-tests-<batch_id>.json \
   --push-file harness/log/cross-device/push-<batch_id>.json \
   --summary "<一句话>" --result <pass|fail|skip> --build <pass|fail|skip> --board <pass|fail|skip> \
   --case "<本次实际 --case 标签，逗号分隔（模式 B 逐字透传；模式 A 无则省略）>" \
   --body <正文文件> --batch-file <cdp> --target $(git rev-parse --short=12 HEAD) \
   [--metrics "<性能三指标 JSON 对象>"] \
   [--timings-file harness/log/cross-device/timings-<batch_id>.json]
   （--batch-file/--target 为模式 A 参数；--body 必传：CDP 原文 + 各阶段明细 +
   失败现场摘录，自动脱敏；PASS 必传 --acceptance-file（步骤 5 自描述验收产物）
   与 --unit-test-file（步骤 4b 自描述单测产物）与 --push-file（步骤 4 自描述
   推送产物）——ws_report 按 run_id 与输入摘要与本批一致、单调时间递增、
   单测每 target 全绿、推送逐项 sha256/字节数/上下文三项校验值全绿核验，
   缺失/不一致返 2 拒写，
   避免 promote 时 baseline 证据链有洞（新鲜度由 run_id/输入摘要/单调时间表达，
   不用固定墙钟，长编译/重试不误伤）；fail 可 --acceptance 直传现场；
   --metrics 为性能三指标结构化 JSON（lcview-perf 采集输出 METRICS 行），写入
   收据 metrics 字段 + trend 行尾，跨批可 diff——性能数字不再只散在正文；
   --timings-file 为模式 A 链路耗时打点（步骤 0 各阶段 mark 的原始文件，ws_report
   内部经 compute_segments 计算段耗时写收据 timings 字段；缺失/非法仅 warn 不阻断）

   **cases 自动落盘口径（2026-09-02 定）**：
   - ws_acceptance 验收完成把本次实跑 --case 标签写 log_apply_dir()/cases-<batch_id>.json
     （batch 识别三级回落：显式 batch_id > 环境变量 CDP_BATCH_ID > log 目录唯一
     timings 文件），ws_report 未传 --case 时自动探测该文件补全（显式传参优先，
     与 timings 探测同源）——board pass 收据的 cases 字段由此自动落盘，杜绝空
     cases 卡死 prepare 的 evidence-scope 推导。
   - **禁改历史收据文件**：收据一经落盘即证据，事后回填/改写属伪造证据链。
     board+pass 空 cases 由 ws_report 源头拒写（返 2）兜底，发现缺 cases 时
     只写新收据引用旧批次（如 -s 自检批 + 说明），禁止编辑旧收据补字段
     （2026-09-02 BL-20260902-01 发布被迫回填 7833c640079a 的教训）。
## 退出码
- 0 验证完成（含 fail 收据落盘）；1 设备不可达或验收 fail；2 参数错误或验收 ai

---
## 实战坑记录（首次真实上板验证 2026-08-26，dev df0e184 之后）

> 首次端到端"编译→推送→验收"暴露的坑，均已修复或沉淀为用例资产约定，后续执行须规避。

### PIT-1 设备可达性误判：静态 fallback 用 mDNS 域名
- **现象**：`ws_adb_connect.py ensure` 报"设备不可达"，但 `adb devices` 显示设备 `device` 在线。
- **根因**：静态 fallback 默认 `rp5.local:5555`，WSL2 镜像模式下 `rp5.local` DNS 解析失败 → 误报不可达。
- **规避**：静态地址不依赖 mDNS 时用环境变量覆盖：`export LC_VERIFY_ADB_HOST=<真实IP> LC_VERIFY_ADB_PORT=5555`。

### PIT-2 测试 target 引用未定义 filegroup（归档源码缺陷）
- **现象**：`make lechao_lcview_unit_test lechao_lcview_hal_test` 报 `depends on undefined module "lechao_lcview_daemon_sources"`。
- **根因**：`tests/Android.bp` 引用 `:lechao_lcview_daemon_sources` / `:lechao_lcview_hal_sources`，但 daemon/hal 的 Android.bp 从未定义 → **该测试 target 自归档起从未编译过**，接口脱节（LcView_test 期望 MockDeviceReader 注入式接口 vs 实现直接 open 设备）。
- **规避**：编译测试 target 前先核验 filegroup 定义存在；补定义时**勿把含 main() 的文件放进 filegroup**（与 gtest 主函数链接冲突），main 单独列在 cc_binary srcs。

### PIT-3 aidl_interface 缺 versions 声明
- **现象**：`module "vendor.lechao.lcview_interface": API Directory exists for version 1 ... but it is not specified in versions field`。
- **根因**：`aidl_api/<iface>/1/` 冻结版本存在（`aidl_api` 目录已归档），但 Android.bp 的 `aidl_interface` 未写 `versions: ["1"]`。
- **规避**：归档 aidl_api 冻结目录时必须同步在 aidl_interface 声明 versions。

### PIT-4 验收 file: 判据对 0750 system-only 目录必然假红
- **现象**：`file:/data/vendor/lechao_lcview` 恒 fail（detail 空），服务实际 running 正常。
- **根因**：目录 0750 system:system，`adb shell`（shell 用户 uid 2000）无权限列目录，`ls` 非零退出；stderr 未被 adb_exec 捕获（detail 为空）。
- **规避**：验收判据用 shell 用户可判定的目标；data 目录（system-only）改用服务状态 + 心跳日志判据。已从 lcview-liveness 移除 file:。

### PIT-5 设备时钟漂移（RTC 无电池）
- **现象**：logcat `-t "MM-DD HH:MM:SS.mmm"` 时间窗错位（取回空/错日志）；日志文件名日期错乱（写进旧日期文件）。
- **根因**：设备时钟落后真实时间 2 个月（无 RTC 电池 + 无 NTP），`--log-since` 以本地时刻为窗起点落在"未来"，logcat 返回空。
- **规避**：验收前核对 `adb shell date` 与本地时间；漂移时 root 修正：`adb shell date MMDDhhmmCCYY.ss`（如 `0826221826.00`）。日志文件名/时间窗以设备时钟为准。

### PIT-6 启动型 log 判据在非 reboot 场景必然假红
- **现象**：`log:"LcView HAL: registered"` / `log:"event schemas"`（启动即打一次）在非 reboot 验收中未命中。
- **根因**：logcat 默认 `-t 5000` 行窗口，启动日志（几分钟前）已被持续心跳/系统日志滚出缓冲区。
- **规避**：log 判据**必须选持续性日志**（心跳等周期性输出），不能用"启动即打一次"的注册/加载日志；后者仅在 reboot 场景 + `--log-since` 可靠。

### PIT-7 log: 标签不支持含空格关键字（已修复）
- **现象**：`log:"LcView HAL: registered"` 被 `_TAG_RE`（`log:\S+`）拆成 `log:LcView` / `log:event` 宽松匹配，有假绿风险。
- **修复**：ws_acceptance `_TAG_RE` 与 `split_tag` 已支持 `log:"..."`（引号包裹含空格，剥引号后精确子串匹配）。
- **用法**：用例资产（verify-cases.yaml）写 `log:"含空格关键字"`（建议 YAML 单引号外层，避免双引号转义）。

### PIT-8 sepolicy logd 权限"疑似缺口"实为误报
- **现象**：检视报告 daemon 域缺 `logd:unix_dgram_socket write`，但实测 logcat 中 daemon 日志正常出现，`avc: denied` 总数为 0。
- **结论**：system 域继承日志写权限；.te 中该 allow 为冗余无害。检视/修复时以实测 avc denied 为准，勿凭静态推断断言缺口。
---

## 业务验证用例（L1/L2，verify-cases.yaml 资产层）

> 板端验证 lcview 业务"工作 OK"的用例资产已内聚到 `harness/config/verify-cases.yaml`
> （ws_acceptance.py --case 引用，L1/L2 命令经 hostcmd/cmd 标签书写，不再散落
> cases/ 独立 yaml，9 处硬编码绝对路径消亡）：
> - `lcview-pipeline` — **L1 被动数据管道**（只读）：files / valid_json / schema_match / fresh。
> - `lcview-pipeline-warn` — L1 两条 warn 项（no_invalid / ts），非阻断判据单独成用例。
> - `lcview-trigger` — **L2 触发型全链路**（authorized 切换 Flash Drive 1-2，按 requires 顺序）：
>   baseline → authorize_off → disconnect 增量 → authorize_on → probe 增量 + vid/pid 匹配。
> - `lcview-perf` — **性能采集（脚本化统一负载）**：dd 读 64MB（与性能基线负载
>   一致）→ 事件吞吐 / 平均落盘延迟 / daemon RSS 三指标，METRICS JSON 行供
>   ws_report --metrics 结构化存档（跨批可 diff；只报数不设门禁）。直读采样：
>   内核 total_records 经 sysfs 只读节点（/sys/class/lcview/vendor_lechao_lcview/
>   lcview_stats，设备节点单打开限制故 ioctl 直读返 EBUSY）+ JSONL 行数经 wc -l
>   按 100ms 采样，不依赖心跳（心跳每 30 loop 约 28s，远粗于 dd 负载窗，
>   曾致 drain 恒为一个心跳周期、延迟随负载机械失真）。
> - `lcview-recover-daemon` — **重启恢复用例**：kill daemon 进程 → init
>   自动拉起 + 心跳恢复（kill 后新心跳行）；额外断言 dd 触发新事件落盘
>   （delta）+ 轮转 seq 递增（重启后 FileWriter 从目录续接 seq，写更高
>   _p{seq}）。kill 有副作用（服务重启自愈），单独用例
>   （lcview-recover-hal 已随 HAL 退役删除，recover.sh 仅支持 daemon 分支）。
> - `lcview_check.py/.sh` — host 侧校验器：adb pull JSONL + schema，host 解析（设备侧无 python3）；
>   delta 模式以基线 ts 过滤增量，轮转/追加均可靠；perf 模式直读采样采集三指标。

### 关键经验（2026-08-26 实测）
- **事件驱动模型**：lcview 事件是 USB 生命周期/传输/异常类，非周期心跳——USB 设备常驻
  无活动时不产生新事件（今日文件 0 字节 ≠ 故障）。判定业务 OK 须**主动触发**（L2）而非只看文件。
- **执行前置**：`adb root`（读 /data/vendor/lechao_lcview 0750 system-only 必需）；设备时钟须校准
  （PIT-5），否则 ts/fresh 判据错乱。
- **验证闭环**：L2 触发后重跑 L1 fresh/ts 全过（最新写入秒级、ts 偏差 <30s），
  且 usb_probe 的 vid/pid/vendor/product 与设备描述精确匹配——全链路（内核 hook → ring → HAL →
  daemon 解析 → JSONL）正确。

### 收据老化（设计语义，2026-08-27 明确，配额 2026-09-01 扩至 50/200）

`cdp_receipt.py` 的 `_DETAIL_KEEP=50` + `prune_details`：收据详情保留 50 份
（trend.md 不计配额，`_TREND_KEEP=200` 行），超限删最旧——**老化属设计，
被淘汰收据不得当事故恢复**（20 份窗口太短，单日多批即挤掉上一基线前证据，
2026-09-01 扩至 50 份 + trend 200 行）。
曾因误判为事故两次恢复被淘汰收据（074627/111251），并错误断言"只增不删"
（与老化设计互斥必永久红，已撤）。新增收据仍只增，淘汰由配额驱动。

### 环境准备：归档时钟回拨前历史污染（一次性，2026-08-27 实作）

设备时钟校准（PIT-5，date -u 回拨）前写入的 JSONL 记录 ts 超前当前时钟，
`lcview-pipeline-warn` 的 ts 全历史卫生检查会持续判红（曾 501 条）。校准后
一次性把含未来 ts 的文件归档到 `uploaded/`（保留卫生检查防新污染）：

```bash
adb shell 'sh -s' <<'EOS'
cd /data/vendor/lechao_lcview/logs || exit 1
NOW=$(date +%s)
for f in *.jsonl; do
  [ -f "$f" ] || continue
  MAX=$(grep -o 'ts":[0-9]*' "$f" | cut -d: -f2 | sort -n | tail -1)
  [ -z "$MAX" ] && continue
  SEC="${MAX%?????????}"   # 去末尾 9 位纳秒得秒级（toybox 32 位算术会溢出大数）
  [ "$SEC" -gt "$((NOW+600))" ] && mv "$f" uploaded/
done
EOS
```

注意：toybox sh 算术为 32 位，超大 ts 直接除法会溢出（曾得 -1/2 致判断失效），
须用 `${MAX%?????????}` 字符串截断；grep 匹配 JSON 的 `"ts":` 时模式写
`'ts":[0-9]*'`（ts 与冒号之间隔有引号）。归档后 warn 卫生检查判过（无未来
记录），新污染仍会被检出判红。

### LCVIEW_USB 变量取值（上板跑 lcview-trigger 前，步骤 3 采集真实值）

`lcview-trigger` 的 5 个变量默认值按 Flash Drive 在端口 1-2、vid/pid 04e8:6300 预设，
上板须按设备实际采集后以环境变量导出（hostcmd 在 host 侧展开）：

| 变量 | 默认 | 采集方法 |
|------|------|---------|
| `LCVIEW_USB_DEV` | `1-2` | `adb shell 'for d in /sys/bus/usb/devices/[0-9]*-*; do [ "$(cat $d/product 2>/dev/null)" ] && echo ${d##*/} $(cat $d/product); done'` 遍历找 U 盘端口（product 非空即设备） |
| `LCVIEW_USB_VID` | `1256` | `adb shell "cat /sys/bus/usb/devices/<DEV>/idVendor"`（十六进制转十进制：04e8 → 1256） |
| `LCVIEW_USB_PID` | `25344` | `adb shell "cat /sys/bus/usb/devices/<DEV>/idProduct"`（6300 → 25344） |
| `LCVIEW_USB_EVENT_DISCONNECT` | `9` | schema 固定事件 id（一般不改，仅事件 id 变更时覆盖） |
| `LCVIEW_USB_EVENT_PROBE` | `8` | 同上 |

> 表格内 `[0-9]*-*` 含竖线字符，Markdown 表格中须以 `\|` 转义（否则渲染错列）；
> 采集用遍历读 `idVendor`/`idProduct`/`product`（ls + grep 按 1-2 猜端口认不出 U 盘，
> 端口号是 bus 顺序号非固定值）。

导出示例：`export LCVIEW_USB_DEV=1-2 LCVIEW_USB_VID=1256 LCVIEW_USB_PID=25344`。


