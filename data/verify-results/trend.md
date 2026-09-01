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
2026-08-31 19:16:52 641d371c3da2 skip build=skip board=skip acc=- 解畸形登记堵死门禁并自动推断 task（-s 无需上板）
2026-08-31 19:30:35 7a04f2d146f1 skip build=skip board=skip acc=- 编排器接线,让参数推断真正生效（-s 无需上板）
2026-08-31 19:47:04 f6c8c3376282 skip build=skip board=skip acc=- 给 -s 批加自检证据要求,堵零验证通道（-s 无需上板）
2026-08-31 19:47:55 f6c8c3376282 skip build=skip board=skip acc=- 给 -s 批加自检证据要求,堵零验证通道（-s 无需上板）
2026-08-31 19:48:46 f6c8c3376282 skip build=skip board=skip acc=- 给 -s 批加自检证据要求,堵零验证通道（-s 无需上板）
2026-08-31 20:04:57 eca792c3c91a skip build=skip board=skip acc=- 自检门禁改按退出码判定,补两处漏判（-s 无需上板）
2026-08-31 20:21:41 073d27101524 skip build=skip board=skip acc=- 自检生产侧改可执行脚本,修 rc 恒零（-s 无需上板）
2026-08-31 20:32:10 a1a3a1879cd8 skip build=skip board=skip acc=- 计数行只认 stdout,修兜底伪造计数复发（-s 无需上板）
2026-08-31 21:40:10 264b4faab6a6 skip build=skip board=skip acc=- 堵验收假绿并保住基线证据不被老化删（-s 无需上板）
2026-09-01 09:22:30 253457e98980 pass build=pass board=pass acc={"overall":"pass","items":[{"tag":"svc:l 修 FileWriter 裂行 P0 与 writeInvalid 假绿：jso
2026-09-01 09:39:50 4cbc6c1fcc01 pass build=pass board=pass acc={"overall":"pass","items":[{"tag":"svc:l 心跳时间驱动+解码器测真化+verify-cases 组自洽：单测 195 全过 | {"daemon_rss_kb": 5124, "dd_s": 2.365, "drain_ms_per_event": 0.378, "jsonl_delta": 3079, "load_mb": 64, "throughput_evs": 1302.1, "total_delta": 3079}
2026-09-01 10:20:34 fb7bca7bc046 pass build=pass board=pass acc={"overall":"pass","items":[{"tag":"svc:l 心跳 invalid 计数与时效断言：invalid_records 入心跳、l | {"daemon_rss_kb": 5076, "dd_s": 2.562, "drain_ms_per_event": 0.381, "jsonl_delta": 3078, "load_mb": 64, "throughput_evs": 1201.2, "total_delta": 3078}
2026-09-01 10:50:07 41b465d6e5b3 skip build=skip board=skip acc=- 新增 known-issues 准入规则骨架与有序四门（-s 无需上板）
2026-09-01 11:03:17 870931e50fcb skip build=skip board=skip acc=- 补 KIR-004 至 007 与准入场景表（-s 无需上板）
2026-09-01 11:42:42 7866aaab29d7 skip build=skip board=skip acc=- 挂载规则指引并实现 KIR-005 阈值告警（-s 无需上板）
2026-09-01 12:15:57 9278a842ef04 skip build=skip board=skip acc=- 补 severity 字段并回填存量条目（-s 无需上板）
2026-09-01 12:33:49 912659304ee2 skip build=skip board=skip acc=- 基线记带病项并按 KIR 判定 E1 遗留（-s 无需上板）
