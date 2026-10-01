# gateway_server god-module → 包 + mixin 机械拆分 (2026-09-19)

> 状态: **已落盘 + 全量验证通过, 未提交** (等用户明确指令)。
> 前置: 用户请求 "审视源代码目录结构, 重构 gateway_server.py (超 150K)"。
> 方案: 用户裁决 **Mixin 机械拆分 (推荐)** — 纯机械、行为保持; 真正的协作者类抽取留后续专题。

## 为什么做

拆分前 `src/unihive/gateway_server.py` **156,276 字节 / 3,327 行**, 是项目 800 行软限的
4 倍, 单文件塞 9 个不相干职责。目录审视结论: `api/ core/ models/ storage/ sync/ utils/`
分层健康, 问题集中在这一个 god module。第二大的 `mootdx2_client.py` 含用户未提交的 L1
改动、`console_api.py` 可未来再拆 —— **本次均未动**。

## 目标结构 (拆分后实测行数)

```
src/unihive/gateway_server/         3535 行总计 (旧 3327 + 208 各文件 header/import)
├── __init__.py          400  facade: re-exports + GatewayServer 组装类
│                          (__init__/_apply_gateway_tuning/_construct_upstream_client)
│                          + async_main/_build_arg_parser/main
├── _helpers.py          231  sanitizer/正则/版本常量/servers 解析/console auth 配置
├── lifecycle.py         466  LifecycleMixin: initialize/_load_db_config/_do_initialize/
│                          start/stop/_stop_one_upstream/_cache_cleanup_loop
├── http.py              268  HttpServeMixin: serve_http
├── upstreams.py         133  UpstreamControlMixin: _start_upstream_client + late-start 3 件
├── hot_toggle.py        266  HotToggleMixin: set_upstream_enabled + 摘挂/差量/reconcile 11 件
├── config_edits.py      603  ConfigEditsMixin: tool/upstream 热编辑 + 回滚 + canonical 6 件
├── config_upgrade.py    494  ConfigUpgradeMixin: 三方合并升级 + 回滚 7 件
├── ~~openapi_import.py  307~~  (2026-10-01 mixin 已删, FQGate OpenAPI 活拉导入功能下线)
├── cached_calls.py      189  CachedExecutionMixin: _execute_cached
└── mcp_tools.py         178  McpToolsMixin: 工具加载/注册/健康聚合 9 件
```

MRO (2026-10-01 后): `GatewayServer(LifecycleMixin, HttpServeMixin, UpstreamControlMixin,
HotToggleMixin, ConfigEditsMixin, ConfigUpgradeMixin,
CachedExecutionMixin, McpToolsMixin)`。各 mixin 方法名两两不相交 (有测试强制),
跨簇调用一律经 `self`, 零行为变更。

## 拆分中必须保住的机械约束 (违反即挂测试)

1. **monkeypatch binding 分叉**: 子模块内一律模块限定调用 ——
   `from ..core import registry; registry.register_tool(...)`、
   `from ..storage import cache as cache_mod; cache_mod.Cache(...)`。
   测试 patch 点随之改: 4 处 register_tool 改 patch `src.unihive.core.registry.
   register_tool` (test_tool_edit_hot / test_upstream_edit_hot / test_config_upgrade_hot /
   test_upstream_enabled_toggle; ~~test_openapi_import_hot~~ 已随 mixin 删除);
   1 处 Cache 改 patch
   `src.unihive.storage.cache.Cache` (test_startup_audit_round9_batch_b)。
   facade 保留 `register_tool` re-export —— 部分测试 setup 直调并先存真函数。
2. **facade 兼容垫片**: `socket` / `uvicorn` 以 `# noqa: F401` 留在 facade
   (审计测试直读 `gs_mod.socket`/`gs_mod.uvicorn`); `main/async_main/_build_arg_parser`
   + 5 个 helper/常量全部 re-export。console script `unihive.gateway_server:main` 不变。
3. **必须留在 facade 的方法**: `__init__` (round10 批2 getsource 断言)、
   `_construct_upstream_client` (测试 patch `gs.FuyaoClient`, 构造函数查 facade globals)、
   `main/async_main` (round11 patch `gs.async_main`)。
4. **logger 层级**: 子模块全用 `getLogger(__name__)`, 是 `src.unihive.gateway_server`
   的子 logger; caplog 挂父 logger 覆盖子孙 —— 这是必须转成**同名包**而非平级目录的原因。
5. **版本锚点**: `_helpers.GATEWAY_VERSION` 的 `Path(__file__)` 深了一级, 用
   `resolve_layout_root(Path(__file__))` 解析, 冒烟实测仍读到 0.1.1。
6. **3 处按文件路径扫源码的审计测试改写** (inspect.getsource(Method) 会自动跟到新文件):
   - round9_batch_c L12 → `inspect.getsource(GatewayServer.serve_http)`;
   - round9_batch_d M9 + round10_batch1 H1 → 新 conftest helper
     `read_gateway_package_text()` 拼接包内全部 .py 文本。

## 新增防回归测试

`tests/test_gateway_package_layout.py` (9 例):
- 拆包前 50 个方法名仍在组装类上 (防搬丢);
- 9 个 mixin 自有方法名两两不相交 (MRO 无意外覆盖);
- 代表方法的定义模块确实是对应子模块, `__init__`/构造确实留在 facade;
- facade 10 个历史符号可导入, socket/uvicorn/register_tool 垫片存在;
- 包内文件集合固定; `__init__.py` ≤450 行, 其余 ≤800 行 (防重新滚成 god module);
- 含摘挂的子模块必须写 `registry.register_tool(` 且不得裸
  `import register_tool`; lifecycle 必须写 `cache_mod.Cache(` (binding 契约)。

## 顺带修复: wheel force-include 从未生效 (先于本次重构的旧 bug)

计划要求验证 wheel 内 force-include 资源不丢, 实测发现:
`pyproject.toml` 用的是 **`[tool.hatchling.build.*]`** 表名, 而 hatchling 只读
**`[tool.hatch.build.*]`** —— 错误表名被静默忽略: 代码靠 src 布局自动探测照常入包,
但 console.html / static/console / 6 个 seed YAML **从未进过 wheel**, pip install
装出的网关控制台 404、空库首启 seed 准入 fail-closed 拒启。L13 旧测试只做 pyproject
字符串断言, 给了假保证。

修复:
- `[tool.hatch.build.targets.wheel]` + `packages = ["src/unihive"]`;
- `[tool.hatch.build.targets.wheel.force-include]` (10 条映射不变);
- 加固 L13 测试: 断言真实表名存在且 `[tool.hatchling.build` 不出现;
- 真 wheel 重建验证: `unihive/gateway_server/*.py` 11 模块齐、wheel 根
  console.html + static/console/* + config/*.yaml (6 seed) 齐;
  config.yaml / proxy_settings.yaml 确认未打包 (凭据/本机设置不泄漏);
- `pip install -e .` 重装验证 dev 工作流正常。

## 验证结果 (2026-09-19)

| 门 | 结果 |
|---|---|
| 后端全量 pytest (排除 3 个禁跑文件) | **1137 passed, 2 skipped** (1128 基线 + 9 新 layout) |
| web vitest | **324 passed** (18 files) |
| web build | ✓ built, console.html + static/console/assets 已同步 |
| 双命名空间冒烟 | `src.unihive.gateway_server` 与 installed `unihive.gateway_server` 均 OK, version 0.1.1 |
| `pip wheel` (隔离构建) + zip 内容审计 | 模块/资源/entry_points 齐, 无凭据泄漏, 无 `src/` 布局泄漏 |
| `pip install -e . --no-deps` 重装 | OK |

禁跑文件不变: `test_get_daily_l1_observability.py` / `test_mootdx2_p0_perf.py` /
`test_fuyao_cache_config.py` (用户未提交 L1 工作区, p0_perf 另有既有红)。

## 未做 / 后续专题

- **协作者类抽取** (UpstreamManager / ConfigEditService / ToolRegistry 等): 本次只做
  机械过渡形态; mixin 间仍经 `self` 强耦合, 真拆类需要重新设计依赖注入。
- `mootdx2_client.py` (2734 行, 含用户 L1 未提交改动) 与 `console_api.py` (2296 行)
  未动。
- `_sanitize_sync_message` 生产侧已无调用 (仅测试导入), 保留未删, 记为清理项。
- 旧模块在 HEAD 中仍存在 (`git status` 会显示 deleted + 新包 untracked); 提交时建议
  `git add -A src/unihive/gateway_server` 配合旧文件删除, 保留 rename 关联。

## 工作区文件清单

- 删除: `src/unihive/gateway_server.py` (HEAD 中)
- 新增: `src/unihive/gateway_server/` 11 个 .py; `tests/test_gateway_package_layout.py`
- 修改测试: conftest.py (+helper), test_startup_audit_round9_batch_c.py (L12 + 删未用 Path),
  test_startup_audit_round9_batch_d.py (M9 + L13 加固), test_startup_audit_round10_batch1.py (H1),
  test_startup_audit_round9_batch_b.py (Cache patch 点),
  test_tool_edit_hot.py / test_upstream_edit_hot.py / test_config_upgrade_hot.py /
  test_openapi_import_hot.py / test_upstream_enabled_toggle.py (register_tool patch 点) [2026-10-01: test_openapi_import_hot 已随 mixin 删除]
- 修改配置: `pyproject.toml` (hatch 表名修复)
- 修改文档: `CLAUDE.md` (项目结构条目)
- scratch (gitignored, 勿提交): `logs/_wheelcheck/`, `logs/_refactor_gate*.txt`
