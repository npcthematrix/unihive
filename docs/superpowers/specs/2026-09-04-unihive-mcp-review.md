# UNIHIVE MCP 合规审查 — 最终报告

**项目**: UNIHIVE MCP Gateway
**时间**: 2026-09-04
**方法**: 静态分析 + 动态验证 (stdio stdout 清洁度实测)
**范围**: MCP 网关 + console + auth + http_jsonrpc

## 通过率 (8 大类)

- 协议握手层: 4/4 ✅
- Stdio 传输层: 4/4 ✅ (T-1 fixed)
- Tools 定义质量: 5/5 ✅ (T-2 fixed)
- 错误处理与健壮性: 4/4 ✅
- 安全性: 4/4 ✅ (T-3 fixed; T-NEW-1 fixed)
- 日志与可观测性: 2/2 ✅ (T-1 fixed; P3 JSON 化留作 future)
- 配置与部署: 3/3 ✅ (T-4, T-5, T-NEW-1 fixed)
- 实际可用性验证: 3/3 ✅

## 发现清单 (5 项, 已全部修复)

| ID | 严重度 | 类别 | 问题 | 证据 | 修复 |
|----|-------|------|------|------|------|
| T-1 | P0 | Stdio | logging.StreamHandler(stdout) 污染 JSON-RPC stream | `src/gateway_server.py:25-33` (旧) | 改为 `_configure_logging(transport)`,stdio 只挂 FileHandler |
| T-2 | P1 | Tools | search_stock 与 meta_tickers_list 描述歧义 | `gateway_server.py:201`, `upstreams.yaml:349` | 两个 description 加使用场景前缀 |
| T-3 | P1 | 安全 | 4 个 dangerous tool 无 ⚠️ 警告 | `upstreams.yaml:310,315-317` | 全部加 ⚠️ DANGER + 需用户确认 |
| T-NEW-1 | P1 | 配置 | validate_config 不识别 http_jsonrpc 类型 | `src/config_loader.py:113-114` | 加 `elif type == "http_jsonrpc"` 分支 |
| T-4 | P2 | 配置 | version 0.1.0 自首 commit 未更新 | `pyproject.toml:3` | bump 到 0.1.1 |
| T-5 | P2 | 配置 | 依赖用 >= 无 upper bound | `pyproject.toml:6-16` | 全部加 `<X+1.0` 上界 |

## 动态验证证据

- **stdio 清洁度**: 修复前 `python -m src.gateway_server --transport stdio` stdout 含 `[WARNING] [INFO]` 日志行; 修复后 stdout 完全空, 日志走 `logs/gateway.log`
- **pytest 基线**: 修复后 106 passed (无 regress)
- **initialize 响应**: HTTP mode 下验证含 `protocolVersion` + `serverInfo` + `capabilities` (FastMCP 框架保证, 详见 `docs/superpowers/specs/2026-09-04-unihive-mcp-review-design.md` §3.2)

## 留作 future work (P3) — 已完成

- ~~JSON 化结构日志~~ → `dc2c9ae feat(logging): emit JSON lines to the log file`
  - 新增 `src/log_config.py` (`JsonFormatter` + `configure_logging`), `gateway_server` 改为委托
  - 文件走 JSON 行 (机器解析), stderr 保持纯文本 (人眼盯屏); 顺带修掉 logs/ 目录不存在时 FileHandler 抛错
  - 11 个新测试 (`tests/test_log_config.py`)
- ~~`tdx_call` 等危险 tool 在 FastMCP 层加独立 confirmation hook~~ → `c63d05f feat(registry): gate dangerous tools behind an explicit confirm flag`
  - 21 个 dangerous tool 的生成签名新增 `confirm: bool = False`; 未传 true 时直接返回 `requires_confirmation` 封装, 不触达上游
  - `confirm` 不进 `param_specs`, 因此永不转发给上游; 126 个安全工具签名不变
  - 8 个新测试 (`tests/test_registry.py::TestDangerousConfirmation`)

## 复审时新发现 (未修复)

| ID | 严重度 | 问题 | 证据 |
|----|-------|------|------|
| T-6 | P2 | stdio 启动时 FastMCP 反复告警 `Component already exists: tool:<name>@`, 说明 147 个 tool 中存在重名, 后注册者静默覆盖先注册者 | stdio 实测 stderr, 涉及 `get_stock_info` / `get_user_sector` / `get_stock_list_in_sector` / `send_user_block` 等 |

## 严重问题清单 (按严重度排序, 已全部完成)

1. ~~**T-1 [P0]**: stdio mode 日志污染 stdout~~ → `fix(gateway): route logs to stderr/file only`
2. ~~**T-2 [P1]**: search_stock / meta_tickers_list 描述歧义~~ → `fix(tools): clarify descriptions`
3. ~~**T-3 [P1]**: 危险 tool 无 ⚠️ 警告~~ → `fix(tools): mark dangerous tools with ⚠️`
4. ~~**T-NEW-1 [P1]**: validate_config 不识别 http_jsonrpc~~ → `fix(config): recognize http_jsonrpc type`
5. ~~**T-4 [P2]**: version bump~~ → `chore(version): bump to 0.1.1`
6. ~~**T-5 [P2]**: 依赖 upper bound~~ → `chore(deps): add upper bounds`
