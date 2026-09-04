# UNIHIVE MCP 合规审查 — 设计文档

**项目**: UNIHIVE MCP 金融数据聚合网关
**路径**: `D:\fintech_workspace\unihive`
**审查时间**: 2026-09-04
**审查者**: Claude Code (claude-sonnet-4-6)
**审查方法**: 静态分析 + 动态验证 + 原子修复循环

---

## 1. 目标

对 UNIHIVE MCP Gateway 做协议合规 + 工程质量审查,产出一份可作为 bug 修复任务清单的报告,并修复所有 ❌/⚠️ 项。

**审查范围** (用户确认):
- ✅ 核心 MCP 网关本体: `src/gateway_server.py`, `registry.py`, `upstream_client.py`, `router.py`, `cache.py`, `normalizer.py`, `config_loader.py`, `tool_loader.py`
- ✅ 管理控制台: `src/console_server.py`, `src/auth_middleware.py`
- ✅ HTTP JSON-RPC 上游客户端: `src/http_jsonrpc_client.py`, `src/rhths_client.py`
- ✅ 配置文件: `config/upstreams.yaml`, `config/tools_tdx_tq_local.yaml`, `pyproject.toml`, `scripts/*.ps1`, `.env.example`

**执行能力**: 用户确认可实际运行 stdio/HTTP mode 并用 MCP Inspector 连接,因此第八节(实际可用性验证)可以真实填写,而非假设通过。

**交付目标**: 报告 + 全部修复 (用户确认) — 意味着所有 ❌/⚠️ 项要在本次 session 内修完。

---

## 2. 架构: 4 阶段审查流程

```
┌─────────────────────────────────────────────────────────────────────┐
│ Phase 1: TRIAGE  (静态快速扫描, 预计 30 min)                        │
│ • 8 大类各条目快速过, 标记 P0/P1/P2/P3 优先级                       │
│ • 输出: docs/superpowers/specs/2026-09-04-unihive-mcp-review-TRIAGE.md│
└─────────────────────────────────────────────────────────────────────┘
                                ↓ CHECKPOINT (用户确认优先级)
┌─────────────────────────────────────────────────────────────────────┐
│ Phase 2: DEEP-DIVE + 动态验证  (P0/P1, 预计 2-3 h)                 │
│ • 每 P0/P1: 代码深挖 → 写复现 → 动态验证 → ISSUE 段                │
│ • 输出: docs/superpowers/specs/2026-09-04-unihive-mcp-review-ISSUES.md│
└─────────────────────────────────────────────────────────────────────┘
                                ↓ CHECKPOINT (用户确认修复方向)
┌─────────────────────────────────────────────────────────────────────┐
│ Phase 3: 原子修复循环  (按 P0→P1→P2→P3, 预计 1-2 h)                  │
│ • 每 fix: 最小 diff → 加/改 test → 重验证 → 原子 commit            │
│ • 输出: N 个 fix(scope): commits                                    │
└─────────────────────────────────────────────────────────────────────┘
                                ↓ PROGRESS CHECKPOINT (P0 修完时)
┌─────────────────────────────────────────────────────────────────────┐
│ Phase 4: 最终报告  (预计 30 min)                                     │
│ • 按模板填 8 大类表格 (含 commit hash 引用)                         │
│ • 汇总表 + 按严重程度排序的剩余 TODO (P2/P3 留作 future work)       │
│ • 输出: docs/superpowers/specs/2026-09-04-unihive-mcp-review.md      │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.1 阶段间 checkpoint 协议

| 时机 | 我做什么 | 做什么决定 |
|------|----------|----------|
| Phase 1 完成 | 提交 TRIAGE.md,展示 P0/P1 清单 | 用户确认范围: 是否所有 P0/P1 都进入 Phase 2 |
| Phase 2 完成 | 提交 ISSUES.md,列出每 P0/P1 修复方向 | 用户确认: 修复方向是否一致, 哪些可以跳过 |
| P0 修完时 | 简报 + 验证状态 | 用户确认: P1 范围是否调整 |
| Phase 4 完成 | 提交最终报告 | (结束) |

---

## 3. 各阶段详细设计

### 3.1 Phase 1: Triage

**目标**: 快速覆盖 8 大类全部条目,产出优先级矩阵。不深挖代码路径,只标记"看起来有问题"+"初步证据"。

**工具**: `Read`, `Grep`, `Glob`. 不实际启动 server。

**已知候选问题** (来自上下文初步扫描,Phase 1 会确认):

| ID | 优先级候选 | 描述 | 初步证据 |
|----|----------|------|---------|
| T-1 | P0 | `logging.StreamHandler(sys.stdout)` 在 stdio mode 下污染 JSON-RPC stream | `gateway_server.py:29` |
| T-2 | P1 | `search_stock` (手写) 与 `meta_tickers_list` (YAML) 职责重叠 | `gateway_server.py:200` + `upstreams.yaml` |
| T-3 | P1 | 危险 tool (`tdx_call`, `send_user_block` 等) 无独立确认机制 | `upstreams.yaml:310,315-317` |
| T-4 | P2 | 版本号 `0.1.0` 自首 commit 至今未更新 | `pyproject.toml:3` |
| T-5 | P2 | 依赖用 `>=` 而非 `==`,可能环境漂移 | `pyproject.toml:6-16` |

### 3.2 Phase 2: Deep-dive + 动态验证

**目标**: 对每个 P0/P1 issue 做完整诊断 (现状/复现/预期/修复方向)。

**每个 P0/P1 issue 的 4 步流程**:

1. **代码深挖**: trace 调用链 + 影响面 (调用方/被调用方)
2. **复现脚本** (可选): 写到 `tests/repro_<issue-id>.py`, Phase 3 修完删除或转为回归测试
3. **动态验证**:
   - Stdio mode: `python -m src.gateway_server --transport stdio` → 用 stdbuf/hexdump 检查 stdout 清洁度
   - HTTP mode: `python -m src.gateway_server --transport http` → curl + Bearer Token 测试 `tools/list` 和 `tools/call`
   - 错误响应: 发非法参数/缺必填/错类型 → 验证返回结构化错误而非崩溃
4. **产出 ISSUE 段**: 现状 vs 预期,含 file:line 证据和动态验证日志片段

**测试用例清单** (动态验证必跑):

| 用例 | 命令 | 期望结果 |
|------|------|----------|
| Stdio 清洁度 | `python -m src.gateway_server --transport stdio < /dev/null \| stdbuf -o0 cat \| xxd \| head -20` | stdout 上无任何非 JSON-RPC 字节 |
| Initialize | `curl -X POST http://127.0.0.1:18080/mcp -H "Authorization: Bearer $TOKEN" -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'` | 返回 `protocolVersion` 与 capabilities |
| tools/list 一致性 | 调 `tools/list` → 数 tool 数 → 与 `upstreams.yaml` + `tools_tdx_tq_local.yaml` 声明对比 | 完全一致 (含 2 个手写: search_stock, get_server_status, get_health) |
| 正常工具调用 | `tools/call get_quote args={code:"sh600000"}` | 返回结构化响应 (非崩溃) |
| 缺必填参数 | `tools/call get_quote args={}` | 返回 isError 或结构化错误, 进程不崩溃 |
| 错类型参数 | `tools/call get_quote args={code:123}` (期望 str) | schema 校验拒绝, 不进入 router |
| 鉴权拒绝 | curl 不带 Authorization | 401 with `WWW-Authenticate` |
| 危险 tool 调用 | `tools/call tdx_call args={path:"/danger"}` | 应能调用 (但描述应明示高风险) — 此项不期望被拒, 只验证描述充分 |

**输出文件结构**:
```markdown
# ISSUES

## [P0-1] stdio mode 下日志污染 stdout
**状态**: 确认 ❌ / 排除 ✅
**证据**: `gateway_server.py:29` `StreamHandler(sys.stdout)` ...
**动态验证**: `python -m src.gateway_server ... | xxd | head` 显示日志前缀污染
**现状**: stdio mode 时所有 logger.info/warn/error 输出到 stdout
**预期**: stdio mode 时只输出 JSON-RPC, 日志走 stderr 或文件
**修复方向**: 检测 transport, stdio 时移除 StreamHandler, 仅保留 FileHandler

## [P1-1] search_stock vs meta_tickers_list 职责重叠
...
```

### 3.3 Phase 3: 原子修复循环

**修复顺序**: 协议致命 → stdio 安全 → 鉴权/错误处理 → 工具质量 → 文档/版本

**每个 fix 的强制约束**:
- ✅ 最小 diff (不顺手重构)
- ✅ 一类问题一个 commit
- ✅ commit message 格式: `fix(scope): 描述 (refs TRIAGE.md <id>)`
- ✅ 涉及行为/接口变更必须加/改 test
- ✅ 修复后重跑 Phase 2 对应动态验证

**预期会触发的修复类型** (Phase 1 确认后会有清单):
- 改 logging 配置 (StreamHandler → 仅 stderr/file)
- 文档化/合并 search_stock 与 meta_tickers_list 区分
- 危险 tool 在 description 里加 ⚠️ 前缀和确认提示
- 版本号或发布流程
- 缺失的 env var 启动校验
- (Phase 1 后再补)

### 3.4 Phase 4: 最终报告

**按用户提供的模板 8 大类逐条填**,每条带:
- 状态 (✅/❌/⚠️/❓)
- 证据 (`file:line` 或动态验证日志)
- 修复 commit hash (对已修的 ❌/⚠️)

**末尾汇总表**:
```
### 通过率
- 协议握手层: X/4
- Stdio 传输层: X/4
- Tools 定义质量: X/5
- 错误处理与健壮性: X/4
- 安全性: X/4
- 日志与可观测性: X/2
- 配置与部署: X/3
- 实际可用性验证: X/3

### 严重问题 (P0, 已修)
1. ...

### 建议改进 (非阻塞, 留作 future work)
1. ...

### 未验证项
1. ...
```

---

## 4. 数据流与依赖关系

### 4.1 审查触及的文件清单

```
src/gateway_server.py        ← P0-1 候选, 主入口
src/registry.py              ← 工具注册逻辑
src/upstream_client.py       ← stdio upstream (含 start() 进程管理)
src/router.py                ← 路由/降级
src/cache.py                 ← SQLite 缓存
src/normalizer.py            ← 代码标准化
src/config_loader.py         ← YAML + env 注入
src/tool_loader.py           ← YAML 工具 spec 加载
src/console_server.py        ← HTTP UI (含 Bearer 中间件)
src/auth_middleware.py       ← Bearer 鉴权
src/http_jsonrpc_client.py   ← HTTP JSON-RPC 上游
src/rhths_client.py          ← RHTHS HTTP 上游

config/upstreams.yaml        ← 上游配置 + tools 声明
config/tools_tdx_tq_local.yaml  ← 生成的工具 spec
pyproject.toml               ← 版本 + 依赖
scripts/*.ps1                ← 启动脚本
.env.example                 ← env 样例
tests/*.py                   ← 现有测试 (不动, 只在需要时加)
```

### 4.2 Phase 3 修复的依赖关系

修复顺序必须遵守的依赖:

```
P0-1 (logging) ── 不依赖其他修复, 必须最先
   ↓
P1-* (tools/safety) ── 依赖 P0-1 完成 (验证干净日志下错误可观察)
   ↓
P2-* (config/version) ── 依赖 P0/P1 完成
```

---

## 5. 错误处理 / 边界情况

| 失败场景 | 处理 |
|----------|------|
| Phase 1 没发现任何 P0 | 简化流程,直接进 Phase 4 出空报告 |
| 动态验证时 server 启动失败 | 检查 `.env` / 网络, 不进入 Phase 3 修复, 等用户确认 |
| Phase 3 修复引入新 bug | 立即回滚该 commit, 标记为 [blocked], 不影响其他 fix |
| 用户中途取消 | 当前阶段 checkpoint 后停下, 已写文档保留, 不提交 partial commit |
| 修复 commit 数 > 15 | 评估是否需要拆分 sub-PR (本 session 内全在 master) |
| Phase 2 发现新 P0 (不在初判中) | 立即停下, 同步用户确认范围调整 |

---

## 6. 测试策略

**两类测试**:
1. **现有 pytest 套件**: `pytest -q` 在 Phase 1/3 后跑, 不能 regress
2. **动态验证**: 每个 P0/P1 issue 必须跑对应的 §3.2 测试用例 (不是抽样, 是覆盖所有 P0/P1)

**测试覆盖率**: 本次审查不把覆盖率作为通过/失败门槛 (不做 coverage gating)。但任何行为/接口变更必须带 test。Phase 4 报告里附上 `pytest --cov=src` 的输出作为 baseline 数字, 让用户决定后续是否要提升。

---

## 7. 产出物索引

| 文件 | 何时创建 | 作用 |
|------|----------|------|
| `docs/superpowers/specs/2026-09-04-unihive-mcp-review-design.md` | 现在 | 本设计文档 |
| `docs/superpowers/specs/2026-09-04-unihive-mcp-review-TRIAGE.md` | Phase 1 末 | 优先级矩阵 (用户 checkpoint) |
| `docs/superpowers/specs/2026-09-04-unihive-mcp-review-ISSUES.md` | Phase 2 末 | P0/P1 详细诊断 (用户 checkpoint) |
| `docs/superpowers/specs/2026-09-04-unihive-mcp-review.md` | Phase 4 末 | 最终报告 (按用户模板) |
| `tests/repro_*.py` (临时) | Phase 2 中 | 复现脚本, Phase 3 验证后删除或转为回归测试 |

---

## 8. 不做的事 (YAGNI)

- ❌ 不重写 FastMCP 框架部分
- ❌ 不重构 console_server.py 内部结构 (除非 P0/P1 命中)
- ❌ 不引入新的依赖
- ❌ 不为 P3 建议项开 PR (留作 future work)
- ❌ 不为覆盖率而写无意义 test
- ❌ 不改 starter 脚本命名/接口 (除非 P0/P1 命中)

---

## 9. 完成定义 (Definition of Done)

Phase 4 报告产出 + 满足:

- [ ] 8 大类所有条目有明确状态 (✅/❌/⚠️/❓), ❓ 项必须说明原因
- [ ] 所有 ❌/⚠️ 项要么已 commit 修复, 要么列入 P2/P3 future work
- [ ] 所有修复 commit 通过 `pytest -q`
- [ ] 所有修复后 P0/P1 动态验证用例重跑通过
- [ ] TRIAGE.md + ISSUES.md + 最终报告 三份文档已 commit
- [ ] 最终报告引用了所有相关 fix commit hash
