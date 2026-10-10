# harness Python 工程约束

> **规则 ID**：`HPY-001` / `HPY-002` / `HPY-003`
> 加载时机：新增或修改 `harness/lib/`（及 `harness/skills/*/lib`、`*/scripts`）
> 下的 Python 模块、证据/收据落盘逻辑、检查器（guard）及其测试前，先加载本规则。
> 本规则把深度检视中确认的三条可复用实践收敛为硬约束，配套设计说明见
> [docs/harness-design/](../../docs/harness-design/README.md)。

## HPY-001 模块级状态单例的双导入名收敛

**背景**：同一模块被两种 import 名加载时（如 `from paths import ...` 与
`from harness.lib.paths import ...`），Python 会为两个模块名创建**独立模块
对象**，模块级缓存随之分裂——`harness/lib/paths.py` 的 `_CONF` 曾因此在两种
加载方式下各存一份，出现「改了一半」的隐患。

- **MUST**：带模块级可变状态（缓存/单例/注册表）的 harness 模块，须在模块尾部
  把自身注册到另一 import 名下（`sys.modules` 别名），使两种名字拿到**同一
  模块对象**，单一事实源语义成立。
- **MUST**：短名先加载的场景，除写 `sys.modules` 别名外，还须回填父包属性链
  （显式 `import harness.lib` 并 `setattr`），否则 `harness.lib.paths` 属性访问
  仍 `AttributeError`；父包不可定位时静默跳过，加载不得因别名块失败而中断。

正例（`harness/lib/paths.py:108`）：

```python
_OTHER_NAME = "harness.lib.paths" if __name__ == "paths" else "paths"
if __name__ in ("paths", "harness.lib.paths") and _OTHER_NAME not in _sys.modules:
    _sys.modules[_OTHER_NAME] = _sys.modules[__name__]
    # 短名先加载：补回填父包属性链（ImportError 静默跳过）
    ...
```

- **MUST NOT**：仅靠「调用方统一 import 名」的口头约定回避——约定无法阻止新
  调用方用另一种名字，分裂会静默复发。
- **MUST NOT**：把别名注册块写成会抛异常的形状（父包不可定位属正常，须
  try/except ImportError 静默跳过）。

**反例**：在 `harness/lib/` 下新增模块（假设名 `foo`）并内建 `_CACHE = {}`
缓存，却未做双名收敛；
某调用方 `from foo import ...`、另一调用方 `from harness.lib.foo import ...`，
两者各持一份 `_CACHE`，命中/失效互相不可见。

**违规后果**：模块级缓存分裂，行为随 import 路径漂移且难以复现 → 当批修。

## HPY-002 证据文件必须原子写

**背景**：收据、已知问题登记、baseline 登记 yaml、打包/单测/推送证据 JSON
都是证据链输入，下游会按「最新文件」身份采信。非原子写会在中断时留下半写态
（半写收据/issue 曾可按 latest 身份进入 promote 判定），或并发互写产出混合/
截断内容。

- **MUST**：证据/收据/登记类文件落盘一律走统一原子原语
  `harness/lib/cdp_paths.py:16` 的 `atomic_write_text` 或
  `harness/lib/verify_common.py:15` 的 `atomic_write_json`。
- **MUST**：临时名含 `os.getpid()` + `threading.get_ident()`，且写后经同目录
  `os.replace` 提交（同文件系统原子 rename，目标路径无半写态）。
- **MUST NOT**：用 `open(path, "w")` 直写目标路径；**MUST NOT** 用固定
  `<name>.tmp` 作中转名（多进程/多线程互踩）。

正例见 `harness/lib/cdp_paths.py:25`（tmp 命名）与
`harness/lib/cdp_paths.py:28`（`os.replace`）；收敛说明见
[atomic-evidence-write.md](../../docs/harness-design/atomic-evidence-write.md)。

**反例**：`path.write_text(content)` 直接写收据目标路径；或
`tmp = path + ".tmp"` 后 `tmp.rename(path)`——同进程两线程同写同名 tmp 可产出
混合内容。

**违规后果**：半写/混合证据被当作完整证据采信，验证链失真 → 当批修
（配套红灯用例见 `harness/lib/tests/test_atomic_write.py`）。

## HPY-003 守卫自身必须有红灯用例

**背景**：检查器（guard）若只有「绿灯通过」用例、没有「制造破坏 → 判红」的
用例，则接线漂移、判定失效都无感——CI 只跑不判即假绿。

- **MUST**：每个接入自检/门禁链的检查器，须配套「制造破坏场景 → 该检查器判红
  （rc 非零 / 拒写 / 拒登）」的单元测试，证明门禁真的会拦。
- **MUST**：原子写等原语亦须有「并发写 + 完整性哨兵」红灯用例
  （`harness/lib/tests/test_atomic_write.py:65`）。
- **MUST NOT**：用「断言正常输入通过」代替判红用例；**MUST NOT** 只测辅助纯
  函数而漏测真实调用点（调用点改坏照绿）——内核侧配套范式见
  `docs/harness-design/kernel-host-dual-test.md` 的 callsite 轨。

**反例**：新检查器（假设脚本名 `check_x`）只断言「合法输入 rc=0」，不构造非法输入断言
rc≠0；一旦判定分支被删/写反，测试仍全绿。

**违规后果**：检查器漂移/失效无感，判红门禁形同虚设（同 `CDP-DOD-001`）→
当批修。

## 与其他规则的关系

- `HPY-002` 与 `HPY-003` 是 `CDP-DOD-001`（检查器门禁三要件）在 Python 工程
  层面的具体化。
- `HPY-002` 的证据域纪律与 `CDP-DOD-002`（禁依赖未跟踪产物）配套：运行期产物
  缺失走降级而非断言。
