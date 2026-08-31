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
2026-08-28 15:09:13 7fb5c4509da4 pass build=skip board=pass acc={"overall":"pass","items":[{"tag":"svc:l 性能测量脚本化并修时区与在途上限：mode_perf 统一 64MB 负载采集三 | {"daemon_rss_kb": 5432, "dd_s": 2.569, "drain_ms_per_event": 9.923, "jsonl_delta": 3073, "load_mb": 64, "throughput_evs": 1196.3, "total_delta": 3073}
2026-08-28 16:39:25 a975c5cd7945 pass build=pass board=pass acc={"overall":"pass","items":[{"tag":"svc:l 性能观测改直读并补 FileWriter 覆盖：内核加 sysfs 只读导出落地 | {"daemon_rss_kb": 5448, "dd_s": 2.672, "drain_ms_per_event": 0.344, "jsonl_delta": 3071, "load_mb": 64, "throughput_evs": 1149.1, "total_delta": 3071}
2026-08-28 18:23:29 e604fbfb830c pass build=pass board=pass acc={"overall": "pass", "items": [{"tag": "s 恢复守恒判据（conserve v3 直读双端增量，免疫 daemon 重启）并 | {"daemon_rss_kb": 5556, "dd_s": 2.493, "drain_ms_per_event": 0.345, "jsonl_delta": 3071, "load_mb": 64, "throughput_evs": 1231.7, "total_delta": 3071}
2026-08-29 10:19:39 0c0a33e77f9c pass build=pass board=pass acc={"overall":"pass","items":[ daemon 迁入 vendor（vendor:true + rc/file_c | {"daemon_rss_kb": 5616, "dd_s": 2.635, "drain_ms_per_event": 0.348, "drain_s": 1.07, "load_mb": 64, "throughput_evs": 1165.9}
2026-08-29 11:42:38 0cc5eb08831b pass build=pass board=pass acc=svc:lechao_lcview boot daemon 直读内核并停用 HAL: liveness pass, drain 1.071s, RSS 5140kB, 吞吐 1270.8ev/s | {"drain_s": 1.071, "latency_ms_per_event": 0.348, "daemon_rss_kb": 5140, "throughput_evs": 1270.8}
2026-08-29 12:53:51 9056a45555e6 pass build=pass board=pass acc=svc:lechao_lcview boot 清理 HAL 残留并建写路径指标: liveness pass, avg_format_us=2 avg_write_us=1 | {"drain_s": 1.085, "latency_ms_per_event": 0.353, "daemon_rss_kb": 5208, "throughput_evs": 1270.3}
2026-08-29 14:48:13 d79eb012faaf pass build=pass board=pass acc={"overall":"pass","items":[ lcview 可维护性重构(删除对齐/假红修正/死代码/TLV 统一解码/拆函数
2026-08-29 15:28:55 29f480f0c4b8 pass build=pass board=pass acc={"overall":"pass","items":[ 补做数据面验收(transfer/pipeline/perf 全过,avg_fo | {"avg_format_us": 2, "avg_write_us": 1, "baseline_avg_format_us": 2, "daemon_rss_kb": 5088, "dd_s": 3.556, "drain_ms_per_event": 0.36, "jsonl_delta": 3067, "load_mb": 64, "throughput_evs": 862.6, "total_delta": 3067}
2026-08-29 15:54:42 fc88f2e251a7 pass build=pass board=pass acc={"overall":"pass","items":[ conserve 迁 transfer(自带负载守恒成立)+负向追赶期放行+li | {"conserve_landed": 201, "conserve_produced": 201, "daemon_rss_kb": 5088, "drain_ms_per_event": 0.359, "throughput_evs": 1316.5, "unit_tests_lcview": 127}
2026-08-29 16:29:45 2b97c169d670 pass build=pass board=pass acc={"overall":"pass","items":[{"tag":"svc:l 恢复 conserve 负向判红(v4 两段式采样,起点无积压负向为真异常)+w
2026-08-29 17:18:03 9c691bb86793 skip build=skip board=skip acc=- 定 known-issues 字段与模板（-s 无需上板）
2026-08-29 17:41:11 18f27638d9f6 skip build=skip board=skip acc=- 补 known-issues 校验与门禁（-s 无需上板）
2026-08-29 20:41:12 manual-2608292041 skip build=skip board=skip acc=- 清理去 HAL/迁 vendor 残留：README/recover hal 分
2026-08-30 20:34:09 manual-2608302034 pass build=pass board=pass acc=[{"tag":"svc:lechao_lcview","status":"pa publish-main-base 基线发布验证：lcview 去 HAL 迁移
2026-08-31 10:31:08 f43558ffa5c7 skip build=skip board=skip acc=- known-issues 未闭环不老化并修 precheck 假拒（-s 无需上
2026-08-31 10:50:54 3b2019456966 skip build=skip board=skip acc=- 门禁下移到 Python 并堵畸形登记（-s 无需上板）
2026-08-31 11:32:30 80634fbba714 skip build=skip board=skip acc=- promote 收紧为 pass 且 board 并记 ki_gate（-s 无
2026-08-31 11:45:28 7b5fe41de795 skip build=skip board=skip acc=- promote 打 verified tag 并断言树等价（-s 无需上板）
2026-08-31 14:30:55 30d0ce4139d2 skip build=skip board=skip acc=- 证据快照与 evidence_scope 登记（-s 无需上板）
2026-08-31 15:07:03 ecee8106f315 skip build=skip board=skip acc=- 清空验收假绿与指纹过激归一化（-s 无需上板）
2026-08-31 15:39:36 8548e61e03c0 skip build=skip board=skip acc=- 修 emit 侧 11 项测试红并扩引用扫描（-s 无需上板）
2026-08-31 16:55:03 ec62ae0ce5f6 pass build=pass board=pass acc={ lcview 重构闭环证据:四组验收全绿+单测 190 全过+perf 指标齐备 | {"daemon_rss_kb": 5040, "dd_s": 2.33, "drain_ms_per_event": 0.371, "jsonl_delta": 3075, "load_mb": 64, "throughput_evs": 1319.8, "total_delta": 3075}
2026-08-31 17:29:24 manual-2608311729 pass build=pass board=pass acc={ 基线发布验证:四组验收全绿+单测190全过+perf指标齐备 | {"daemon_rss_kb": 5040, "dd_s": 2.357, "drain_ms_per_event": 0.373, "jsonl_delta": 3081, "load_mb": 64, "throughput_evs": 1307.2, "total_delta": 3081}
2026-08-31 17:49:01 d736c6283cd0 skip build=skip board=skip acc=- evidence-scope 由人工申报改为证据推导（-s 无需上板）
