- schema_version: 1
- issue_id: KI-20260912-001
- title: check_host_tests 副本路径无锁无 pid 后缀，并发抛 FileExistsError 且产物被别路删掉仍返回 rc=0
- discovered_in: 61927c927825
- origin: pre-existing
- severity: P1
- blocking: False
- blocking_reason: 
- status: open
- task: checker-hardening
- resolved_in: 
- archived_in: 
- kind: 

## body

- 方向1: check_host_tests 副本路径无锁无 pid 后缀，并发抛 FileExistsError 且产物被别路删掉仍返回 rc=0。
- 定位: harness/lib/check_host_tests.py:41-57 _stage_module_copy；:53 dst 固定为 <repo>/harness/log/host-tests/lechao（无锁、无 pid 后缀的确定性路径）；:54-56 先 rmtree(dst) 再 copytree(src, dst)，copytree 未设 dirs_exist_ok。
- 证据: dst 为固定路径且无锁——两路并发（selfcheck 与 CI 同时跑、或两个 selfcheck 进程）同写同一副本时，一方 copytree 到另一方刚 rmtree 重建的目标目录抛 FileExistsError（dirs_exist_ok 缺省 False），进程崩溃无 host_rc 归因；另一方 make test 在副本内跑时副本被别路 rmtree 删除，make 已跑完 rc=0 但验证产物（host_test 二进制）已被别路清掉，check_host_tests 仍返回 rc=0——副本隔离语义在并发下被破坏，产物验证假绿。
- 影响: 并发/多实例环境（自检链 host 收口与 CI selfcheck job 重叠）下 check_host_tests 可崩溃或假绿，host_rc 门禁可信度下降。
- 修法方向: 副本路径加 pid（+线程 id）后缀隔离每路，配合目录锁（如 fcntl 或 mkdir 原子锁）防并发互踩；或在 _stage_module_copy 内捕获 OSError 返回 host_rc=1 归因行（见 KI-20260912-006）。
