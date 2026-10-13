# 原子写原语：根除「半写证据文件」

> 证据：`harness/lib/cdp_paths.py`、`harness/lib/verify_common.py`、
> `harness/lib/tests/test_atomic_write.py`。

## 问题：半写证据文件

收据、已知问题登记、baseline 登记 yaml、打包/单测/推送证据 JSON 都是
**证据链的输入**——下游（promote 判定、`check_commit_coverage`、
`git-works-push`）会按「最新文件」身份采信它们。若写盘不是原子的：

- **进程被中断**（Ctrl-C、OOM、超时 kill）会在目标路径留下「已创建但只写了
  一半」的文件。半写收据/issue 曾可按 latest 身份进入 promote 判定，即
  「未完成的证据被当作完整证据采信」。
- **并发互写**：旧实现用固定的 `<name>.tmp` 作为中转名。两进程或同进程两线程
  同写同路径时共用同一个 tmp 文件，互相覆盖对方正在写的内容，最终落盘可能是
  A 的前半 + B 的后半的**混合/截断**内容。

根因是「先建目标文件再逐步写」或「固定 tmp 名」二选一，都没有把
「产出完整内容」与「让内容在目标路径可见」分离。

## 统一口径：tmp 名带 pid + 线程 id，写后 `os.replace`

统一原语在 `harness/lib/cdp_paths.py:16`（文本）与
`harness/lib/verify_common.py:15`（JSON）两处，语义完全一致：

```python
def atomic_write_text(path, content, encoding="utf-8"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(
        f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(content, encoding=encoding)
    tmp.replace(path)          # os.replace：原子替换
```

三条关键设计：

1. **临时名唯一**：`{name}.{pid}.{thread_ident}.tmp`。同 pid 多线程并发写同一
   路径时，各线程各自持有独立 tmp，不再互踩（`cdp_paths.py:25` 注释记录了
   「旧固定 `.tmp` 名下两进程/同进程两线程同写可产出损坏文件」的真实缺陷）。
2. **先写完整 tmp，再 `os.replace`**：`os.replace` 在同一文件系统上是原子重命名，
   目标路径要么是旧内容、要么是完整新内容，**不存在半写态**。
3. **同目录 tmp**：tmp 与目标同目录，保证 rename 不跨文件系统（跨设备 rename
   会退化为 copy，失去原子性）。

> JSON 版本 `atomic_write_json`（`verify_common.py:15`）只在写 tmp 前多一步
> `json.dumps(...)` 序列化，其余（mkdir、tmp 命名、`os.replace`）与文本版
> 逐字对齐——批次四 A3/D 把此前散在 6 个验证脚本里的 `_atomic_write_json`
> 收敛为单一实现，各脚本保留原函数名作薄壳委托（签名/mock 点不变）。

## 并发互写「红灯用例」范式

`harness/lib/tests/test_atomic_write.py` 提供了可直接照搬的判红范式：

- **读写交错 + 完整性哨兵**（`test_atomic_write.py:65`）：
  两线程经 `threading.Barrier` 同时起步，各 40 轮写同路径；每轮写完立即回读，
  断言「读到的内容必等于 A 或 B 的完整载荷之一」。载荷用首尾成对哨兵
  （`_payload`：`HEAD-<tag>-...-TAIL-<tag>`）编码，任何混合/截断都能被检出。
  修复前固定 tmp 互踩会产出混合内容，该断言即判红。结束后还断言目录下无
  `*.tmp` 残留。
- **tmp 名口径断言**（`test_atomic_write.py:91`）：`mock.patch.object(Path,
  "write_text", spy)` 捕获实际写入的临时名，断言其中同时含 `os.getpid()` 与
  `threading.get_ident()`——把「tmp 名必须带 pid+线程 id」这一口径固化为测试，
  防止未来有人退回固定名。
- **JSON 版并发**（`test_atomic_write.py:113`）：两线程并发 `atomic_write_json`，
  结束后 `json.loads` 必须成功（半截 JSON 会解析失败）且为某线程完整对象。

## 导入纪律（红灯用例的配套约束）

`test_atomic_write.py:27` 刻意用 `importlib` 以独立别名加载被测模块，
**禁止裸名 `import cdp_paths`**。原因：全套件收集时 `harness/lib/tests` 按
字母序先于 cross-device 测试执行，裸 import 会把 `harness/lib` 版（无
`cdp_parse_script`）缓存进 `sys.modules["cdp_paths"]`，令 cross-device 垫片
专属断言 AttributeError（曾引入过的回归）。`TestBareNameImportHygiene`
（`test_atomic_write.py:45`）反向兜底：若裸名已被缓存，必须是兼容垫片语义。

## 可复用清单

- 任何「证据/收据/登记」类文件落盘，**MUST** 走 `atomic_write_text`
  / `atomic_write_json`，不得 `open(..., "w")` 直写或固定 `.tmp` 中转。
- 新增另一语言/脚本的等价原语时，tmp 命名 **MUST** 含 pid + 线程/协程 id，
  且写后用同目录原子 rename 提交。
- 原子写原语 **MUST** 有「并发写 + 完整性哨兵」红灯用例（见 `HPY-003`）。
