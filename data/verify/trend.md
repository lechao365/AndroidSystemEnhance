2026-08-24 16:45:21 19c0757993c7 skip build=skip board=skip acc=- revert 收据统一走 ws_report，补 trend 行与失败处置（-s
2026-08-24 19:59:26 3da6846ccecd skip build=skip board=skip acc=- 补齐 diff 语义校验 git apply --check，消除验收含未判定项
2026-08-25 10:36:51 c033538b591e skip build=skip board=skip acc=- 修复批次原文入库被压平致 batch_base 丢失，给 --against 接
2026-08-25 15:42:23 7df1f0f8e6e6 skip build=skip board=skip acc=- 修正 diff 语义校验方向性缺陷 + ws_report apply 降级（-
2026-08-25 16:11:03 2b41f82c8b6b skip build=skip board=skip acc=- -sv 路径就绪：注入防护/boot 兜底/verify_mode 语义/adb
2026-08-25 16:23:06 04969896c875 skip build=skip board=skip acc=- 补齐 -sv 解码容错与 boot 接线，收口 precheck 与脱敏（-s 
2026-08-25 16:44:16 585d5aa8dbf3 skip build=skip board=skip acc=- L3 代码项收口：check-only 预检/收据去重/头注释/push 文案/
2026-08-25 17:10:55 2a0e39ed61bb skip build=skip board=skip acc=- 测试跨平台守卫 + emit SKILL 预算与无引号落盘（-s 无需上板）
2026-08-25 19:32:47 e1f74de7b6ba skip build=skip board=skip acc=- 回收上批两处漂移，落盘 emit 约束第二片（-s 无需上板）
2026-08-25 20:07:51 d31dea233a8c skip build=skip board=skip acc=- emit 约束落盘第三片（四项交付物、可拷贝性、调查分流）+ 规则 ID 范围（
2026-08-25 21:09:30 765d01bd71a0 skip build=skip board=skip acc=- sync 与 AGENTS 对齐放宽后脚本，回收漂移，删文件删除规则节（-s 无
2026-08-26 08:51:28 47fec5c50dcb skip build=skip board=skip acc=- 修两点误导人工的表述，收口二进制矛盾（-s 无需上板）
2026-08-26 09:10:12 a6b770cf3954 skip build=skip board=skip acc=- 二进制检出改立即返回，baseline 文档对齐实现（-s 无需上板）
2026-08-26 09:25:56 7ab48f6aeb1c skip build=skip board=skip acc=- batch_id 抗重排，baseline 含义列与登记门禁对齐实现（-s 无需
2026-08-26 09:42:09 27ce0645416f skip build=skip board=skip acc=- batch_id 抗空格插入，退出码补全，冒烟前测遗留（-s 无需上板；EXTR
2026-08-26 10:17:08 1c27a03a43a2 skip build=skip board=skip acc=- 上板前补 adb 就绪、连接重试、超时可辨与 prop 判据（-s 无需上板）
2026-08-26 10:27:22 6fd3048d34d2 skip build=skip board=skip acc=- logcat 收窄时间窗，log 判据可辨，CLI 不裸崩（-s 无需上板）
2026-08-26 10:38:12 8edfea81314c skip build=skip board=skip acc=- 冒烟证据不留空洞，假绿条件强制拦截，缩进清尾（-s 无需上板）
2026-08-26 10:52:03 17a6a4c9696c skip build=skip board=skip acc=- data/ci 真正出库，返 2 路径有归宿，命令块可拷贝（-s 无需上板）
2026-08-26 14:30:53 1a34402f22c1 skip build=skip board=skip acc=- 建用例资产层（-s 无需上板）
2026-08-26 14:43:34 eed1cdec047a skip build=skip board=skip acc=- 独立触发闭环（-s 无需上板）
2026-08-26 14:59:54 ec10f7ba1c1b skip build=skip board=skip acc=- 测试源码归档进 code（-s 无需上板）
2026-08-26 15:09:24 d1dfcb778909 skip build=skip board=skip acc=- 冻结版 AIDL 归档 + Windows 串口转发器（-s 无需上板）
2026-08-26 16:52:26 a934b5b6da4a skip build=skip board=skip acc=- WSL 侧串口客户端落地（-s 无需上板）
2026-08-26 22:21:38 manual-2608262221 pass build=pass board=pass acc=svc:lechao_lcview_hal=pass svc:lechao_lc lcview CXX 修复批首次真实上板验证通过（7P1 全清，设备健康运行）
2026-08-26 23:14:50 manual-2608262314 pass build=skip board=pass acc=jsonl_files_exist=pass jsonl_valid_json= lcview 业务板端验证通过（L1 管道健康 4PASS + L2 触发全链路
2026-08-27 07:50:56 manual-2608270750 pass build=pass board=pass acc=svc:lechao_lcview_hal=pass svc:lechao_lc lcview 业务链路日志验证通过（build tag 命中16次+batch 
2026-08-27 09:18:48 8c814ca8e1dc skip build=skip board=skip acc=- 清测试红线,解 promote 阻塞,消时钟人工与机器绑定（-s 无需上板）
2026-08-27 09:53:51 14ea313975d6 skip build=skip board=skip acc=- 回收上批 6 处缺陷,消静默降级与时钟不可信（-s 无需上板）
2026-08-27 10:12:19 607399554675 skip build=skip board=skip acc=- 用例层内聚消仓外依赖,兼修 lcview_check 必崩（-s 无需上板）
2026-08-27 10:48:58 a771b060ffe2 skip build=skip board=skip acc=- 堵 lcview_check 假绿,补零覆盖,清仓外引用（-s 无需上板）
2026-08-27 11:08:38 7bde70c61fae skip build=skip board=skip acc=- 串口链路收尾,补零覆盖修错误分类,为 rescue 清场（-s 无需上板）
2026-08-27 11:42:41 89e54e60548f skip build=skip board=skip acc=- rescue 编排落地,串口成第三级救援通道（-s 无需上板）
2026-08-27 14:26:32 7e4c72374085 skip build=skip board=skip acc=- 回收 rescue 6 处缺陷,消错分与句柄泄漏（-s 无需上板）
2026-08-27 14:52:35 8e173bc0801d skip build=skip board=skip acc=- 接线 rescue 消死代码,消文档矛盾与端点重复推导（-s 无需上板）
2026-08-27 15:07:36 d9eedee45eda skip build=skip board=skip acc=- 修 lcview-trigger 截断 P0 与残余静默丢弃（-s 无需上板）
2026-08-27 15:58:14 5407e52257f2 pass build=pass board=pass acc=lcview-liveness=pass(5/5) lcview-pipelin 首次上板取 lcview 用例与单测真值（trigger 全链路 6/6 过，f
2026-08-27 16:50:30 ae65531af480 pass build=pass board=pass acc=svc_hal=pass svc_daemon=pass boot=pass p 修 hal_test 全灭与三红与时钟（hal 0/10→10/10、unit 
2026-08-27 17:18:58 852d5d24314c pass build=skip board=pass acc=svc_hal=pass svc_daemon=pass boot=pass p 修 trusted_tss 两处假绿与 manifest 护栏（四用例全绿）
2026-08-27 17:57:45 7bdd5c43bfab pass build=pass board=pass acc=svc_hal=pass svc_daemon=pass boot=pass l 引覆盖率度量并加固心跳与 ts 判据（覆盖84.33%、liveness 8/8
2026-08-27 19:27:27 2f8bc25f84a0 pass build=pass board=pass acc=svc_hal=pass svc_daemon=pass boot=pass l 修心跳子串假绿与 ts 全历史判据并补 DeviceReader UT（trig
2026-08-27 20:13:07 8cfe4f9cf98b pass build=pass board=pass acc=svc_hal=pass svc_daemon=pass boot=pass l 补 daemon 主循环与生产配置 UT 并清历史污染（warn/trigger
2026-08-27 20:39:29 a2b467030005 pass build=pass board=pass acc=svc_hal=pass svc_daemon=pass boot=pass l 补事件 4/5 DT 并加收据删除护栏（transfer 5/5、事件7实测无打
2026-08-27 21:22:56 2ae3f7ad0eac pass build=skip board=pass acc=svc_hal=pass svc_daemon=pass boot=pass l 撤错误护栏并做 lcview 收口审计（2 处假绿修复、事件登记归因修订）
2026-08-27 23:16:31 manual-2608272316 pass build=pass board=pass acc=[{"case":"lciod-liveness","status":"pass lciod 防护栏全新构建+lcview轻量收口：4测试target编译过、板上
2026-08-27 23:35:43 manual-2608272335 pass build=pass board=pass acc=[{"case":"lciod-liveness","status":"pass lciod 防护栏缺口补齐：enabled==1 假绿修复 + AIDL 投影抽
2026-08-27 23:39:52 manual-2608272339 pass build=pass board=pass acc=[{"case":"lcview-liveness","status":"pas 当前最终工作树闭环：lcview 5条(liveness 8/8,pipelin
2026-08-28 09:35:39 be5969c4fa93 pass build=pass board=pass acc={"overall":"pass","items":[{"tag":"svc:l 修 lciod 三处假绿与速率溢出：空expect判红+error_count/
2026-08-28 10:35:57 bb29bd094782 pass build=pass board=pass acc={ 建 lcview 守恒判据与性能基线：total_records 进心跳并守恒校
2026-08-28 11:18:25 fdc2e9820a41 pass build=pass board=pass acc={"overall":"pass","items":[{"tag":"svc:l 守恒判据自动化并补 V2 推送与 ring 单测：conserve 模式挂 lc
