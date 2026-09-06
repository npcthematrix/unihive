# UniHive MCP Gateway 架构文档

## MooTDX2 接口分类

MooTDX2 封装了两套数据接口：

### 在线接口（Quotes）
基于 `mootdx2.quotes.Quotes`，TCP 网络连接通达信服务器，需要网络。

| 工具名 | 说明 |
|--------|------|
| get_quote | 实时行情（五档） |
| get_kline | K线（日/周/月/分钟） |
| get_batch_quote | 批量行情 |
| get_minute_data | 今日分时 |
| get_trade | 逐笔成交 |
| get_trade_history | 历史逐笔 |
| get_index_kline | 指数K线 |
| get_index_bars | 指数K线（指定起止） |
| get_index_all | 全指K线 |
| get_code_list | 全量代码列表 |
| get_stock_codes | 股票代码列表 |
| get_etf_codes | ETF代码列表 |
| get_etf_list | ETF列表 |
| get_market_count | 市场证券数量 |
| get_workday | 交易日查询 |
| get_workday_range | 交易日范围 |
| get_income | 盘后收益 |
| get_block | 板块数据 |
| get_f10 | F10基础数据 |
| get_f10_company | F10公司概况 |
| get_xdxr | 除权除息数据 |
| get_stock_all | 全部股票列表 |

### 离线接口（Reader）
基于 `mootdx.reader.Reader`，读取本地 `vipdoc` 目录二进制文件，不需要网络。

| 工具名 | 说明 |
|--------|------|
| get_k_data | K线数据（本地日线文件） |
| get_minutes | 分钟K线（本地1分钟文件） |
| search_stock | 搜索股票（本地block板块） |
| get_stock_info | 聚合股票信息（混合：快照在线+K线/分时离线） |

**配置：** `tdxdir` 设置通达信本地路径，为空则自动探索。

---

## 0. 工具描述规范（Agent 说明书标准）

每个 MCP 工具的 `description` 必须包含以下四个部分，作为 Agent 调用时的说明书：

### 0.1 描述结构

```
【工具功能】【使用场景】...【参数说明】...【返回示例】...【典型错误】
```

### 0.2 描述模板

```yaml
name: get_quote
description: |
  【实时行情】获取单只股票的最新五档行情数据。

  【使用场景】
  - 当用户问"某只股票现在价格/涨跌幅/成交量"时调用
  - 当需要实时价格触发交易信号时调用
  - 不适合：批量查询（用 get_batch_quote）、历史数据（用 get_kline）

  【参数说明】
  - stock_code (必填): 股票代码，支持格式：
    - 6位纯数字："600000"（系统自动推断市场）
    - 带前缀："sh600000"、"sz000001"、"bj8xxxxx"
    - 取值范围：沪深京 A 股、ETF、指数

  【返回示例】
  成功:
    {"success": true, "data": {"code": "600000", "close": 10.5, "open": 10.2, "high": 10.8, "low": 10.1, "volume": 1000000}, "source": "mootdx2"}
  失败（停牌/非交易日）:
    {"success": true, "data": null, "error": {"error_type": "no_data", "message": "股票停牌或非交易日"}}
  失败（无效代码）:
    {"success": false, "error": {"error_type": "invalid_symbol", "message": "股票代码不存在"}}

  【典型错误】
  - invalid_symbol: 传入不存在的股票代码
  - no_data: 股票停牌或非交易日，无需重试
```

### 0.3 必填字段

| 字段 | 位置 | 说明 |
|------|------|------|
| 功能分类 | description 首行 | 如【实时行情】【K线】【财务数据】 |
| 使用场景 | description 正文 | 包含"适合"和"不适合"的明确指引 |
| 参数说明 | description 正文 | 每个参数必须说明：是否必填、格式要求、取值范围 |
| 返回示例 | description 正文 | 成功/失败各一例，含完整 JSON |
| 典型错误 | description 正文 | 列出常见 error_type 及处理方式 |

### 0.4 描述示例：好 vs 差

**❌ 差描述（给 Agent 看等于没写）：**
```yaml
description: "获取股票行情"
```

**✅ 好描述（Agent 能独立正确调用）：**
```yaml
description: |
  【实时行情】获取单只股票的最新五档行情数据。

  【使用场景】
  - 当用户问"600000 现在多少钱"时调用
  - 当需要最新价判断是否触发止盈止损时调用
  - 不适合：批量行情（用 get_batch_quote）、历史 K 线（用 get_kline）

  【参数说明】
  - stock_code (必填): 股票代码
    - 格式：6 位纯数字或带前缀（sh/sz/bj）
    - 示例："600000"、"sh600000"、"000001"
    - 注意：不支持创业板/科创板代码的纯数字格式

  【返回示例】
  成功: {"success": true, "data": {"code": "600000", "close": 10.5, ...}}
  失败: {"success": false, "error": {"error_type": "invalid_symbol", ...}}

  【典型错误】
  - invalid_symbol: 股票代码不存在，检查是否多打了数字
  - no_data: 停牌或非交易日，无需重试，直接告知用户
```

### 0.5 参数描述规范

每个参数必须包含：
1. **是否必填**：必填/可选
2. **格式要求**：如 `YYYYMMDD`、`6位数字`、`逗号分隔列表`
3. **取值范围/枚举**：所有枚举值必须列出
4. **默认值**：可选参数必须说明默认行为

---

## 1. 连接池设计

### 1.1 连接复用
- MooTDX2 使用 `Quotes.factory()` 创建连接，支持 `bestip=True` 自动选最快服务器
- 连接池 `ConnectionPool` 按服务器地址 (`host:port`) 缓存连接
- 每次 `get_connection()` 优先复用已有健康连接，避免频繁创建销毁

### 1.2 健康检查
- 定期执行 `_health_check()`（每 30 秒），调用 `stock_count(market=0)` 验证连接
- 检查结果更新 `server.healthy` 和 `conn.healthy` 状态
- 记录延迟到 `server.latency_ms`，用于最优服务器选择

### 1.3 最优服务器选择
- `_select_best_server()` 选择延迟最低且健康的服务器
- `auto_select_fastest_server=True` 时自动选择，否则使用配置顺序

### 1.4 连接池配置
```yaml
mootdx2:
  servers:
    - host: "106.14.201.78"
      port: 7709
    - host: "112.74.214.42"
      port: 7709
  connection_pool_size: 5
  auto_select_fastest_server: true
```

---

## 2. 重试策略

### 2.1 指数退避 + 抖动
- `RetryHelper.get_delay()` 计算重试延迟：`base * 2^attempt + random_jitter`
- jitter 范围为 delay 的 ±10%，避免多请求同时重试造成雪崩

### 2.2 可重试 vs 不可重试
| error_type | 可重试 | 说明 |
|------------|--------|------|
| `connection_error` | ✅ | 网络问题、超时 |
| `timeout_error` | ✅ | 读取超时 |
| `server_unavailable` | ✅ | 所有服务器不可用 |
| `rate_limited` | ✅ | 限流触发 |
| `invalid_symbol` | ❌ | 股票代码不存在 |
| `invalid_param` | ❌ | 参数错误 |
| `no_data` | N/A | 正常业务状态，非错误 |
| `internal_error` | ❌ | 未预期异常 |

---

## 3. 缓存策略

### 3.1 缓存键命名
- 格式：`{ttl_key}:{tool_name}:{params_hash}`
- ttl_key 来源于 `config/upstreams.yaml` 中 `cache_ttl_key` 字段

### 3.2 TTL 配置
```yaml
cache:
  ttl:
    realtime_quote: 10      # 实时行情 10 秒
    index_data: 60          # 指数数据 60 秒
    fundamentals: 3600      # 财务数据 1 小时
    fund_data: 300          # 基金数据 5 分钟
    historical: 3600        # 历史K线 1 小时
    workday: 86400          # 交易日历 1 天
```

### 3.3 缓存范围
- 仅缓存 Fuyao HTTP 上游数据（`source.startswith("fuyao_")`）
- TDX/MooTDX2/TokenWave/TQ-Local 本地数据不缓存（实时性要求高）

---

## 4. 错误分类映射

### 4.1 两层错误机制（必须严格区分）

UniHive 网关存在**两个正交的错误处理层次**，混用会导致调用方无所适从：

#### 第一层：MCP 协议级错误（`isError: true`）

这是 MCP 协议本身的传输层错误，表示**请求未能到达业务逻辑层**。触发条件：

| 触发条件 | MCP isError | 业务 response |
|----------|-------------|---------------|
| 网络不通/连接被拒绝 | `true` | 不返回业务响应 |
| 连接超时（读超时） | `true` | 不返回业务响应 |
| 上游服务崩溃/进程退出 | `true` | 不返回业务响应 |
| JSON-RPC 格式错误 | `true` | 不返回业务响应 |

**调用方处理方式**：收到 `isError: true` 应立即重试（指数退避），不做业务层解析。

#### 第二层：业务级错误（`{success: false, error: {...}}`）

请求已到达业务逻辑层，但业务逻辑判定为失败。**永远不设置 `isError: true`**。

| 场景 | success | error_type | recoverable | 说明 |
|------|---------|------------|-------------|------|
| 股票代码不存在 | `false` | `invalid_symbol` | `false` | 参数校验失败 |
| 参数超范围 | `false` | `invalid_param` | `false` | 参数校验失败 |
| 无数据（停牌/非交易日） | `true` | `no_data` | `true` | **正常业务状态，非错误** |
| 数据校验失败（脏数据） | `true` | `no_data` | `true` | 脏数据按无数据处理 |
| 触发限流 | `false` | `rate_limited` | `true` | 返回 429 |
| 上游连接失败 | `false` | `connection_error` | `true` | 网络问题 |
| 读取超时 | `false` | `timeout_error` | `true` | 服务器响应慢 |
| 所有服务器不可用 | `false` | `server_unavailable` | `true` | 全部节点故障 |
| 未预期异常 | `false` | `internal_error` | `false` | 记录堆栈，返回脱敏消息 |

### 4.2 映射规则（强制）

```
┌─────────────────────────────────────────────────────────────────────┐
│                     调用方视角的决策树                                 │
├─────────────────────────────────────────────────────────────────────┤
│  收到 MCP 响应                                                       │
│      │                                                               │
│      ▼                                                               │
│  isError == true ?                                                   │
│      │                                                               │
│      ├─ YES → 传输层失败 → 指数退避重试 → 最多 N 次 → 报告最终失败     │
│      │                     ↑                                         │
│      │                     └── 可重试错误：connection_error,          │
│      │                          timeout_error, server_unavailable,   │
│      │                          rate_limited                         │
│      │                                                               │
│      └─ NO → 解析 response.body                                      │
│              │                                                       │
│              ▼                                                       │
│          success == true ?                                           │
│              │                                                       │
│              ├─ YES → 使用 data（可能是 null，当 no_data 时）          │
│              │                                                       │
│              └─ NO → 检查 error_type：                                │
│                      │                                               │
│                      ├─ invalid_symbol/invalid_param → 不重试        │
│                      ├─ no_data → 正常业务状态，使用 data=null        │
│                      └─ 其他 → 酌情重试                               │
└─────────────────────────────────────────────────────────────────────┘
```

### 4.3 错误响应格式

**业务级错误响应**（`isError: false`）：
```json
{
  "success": false,
  "error": {
    "error_type": "connection_error",
    "message": "连接失败: connection refused",
    "recoverable": true,
    "details": {
      "context": "get_quote(600000)",
      "server": "106.14.201.78:7709"
    }
  }
}
```

**正常但无数据响应**（`success: true, data: null`，不是错误）：
```json
{
  "success": true,
  "data": null,
  "error": {
    "error_type": "no_data",
    "message": "股票 600000 无行情数据（可能停牌或非交易日）",
    "recoverable": true
  },
  "cache_hit": false,
  "source": "mootdx2",
  "hops": ["mootdx2"]
}
```

### 4.4 典型错误处理示例

```python
# 调用方示例（伪代码）
result = await call_mcp_tool("get_quote", {"code": "600000"})

if result.isError:
    # 协议级错误：传输失败，必须重试
    if is_retryable_protocol_error(result):
        await exponential_backoff_retry()
    else:
        raise "最终失败"
elif not result.body.success:
    # 业务级错误
    error_type = result.body.error.error_type
    if error_type == "no_data":
        # 正常业务状态，不是错误
        data = None
    elif error_type in ("invalid_symbol", "invalid_param"):
        # 参数错误，不重试
        raise f"参数错误: {result.body.error.message}"
    else:
        # 其他业务错误，酌情重试
        await retry()
else:
    data = result.body.data
```

---

## 5. Server Capabilities 与版本号

### 5.1 Server Capabilities 声明

UniHive 网关在 MCP 初始化时必须声明 capabilities：

```json
{
  "name": "unihive",
  "version": "1.2.0",
  "capabilities": {
    "tools": {
      "description": "统一金融数据访问接口，支持 A 股/ETF/基金/指数/财务数据",
      "tool_count": 148,
      "sources": ["fuyao_ashare", "fuyao_fund", "fuyao_index", "fuyao_meta", "tokenwave_tdx", "mootdx2", "tdx_local"]
    },
    "resources": {
      "description": "管理控制台 Web UI",
      "endpoints": ["/", "/api/status", "/api/interfaces", "/api/config", "/api/mcp-tools-list"]
    }
  }
}
```

### 5.2 版本号规范（semver）

| 字段 | 格式 | 变更规则 |
|------|------|----------|
| name | 字符串 | 固定为 `unihive`，不可变更 |
| version | `major.minor.patch` | 遵循语义化版本 |
| major | 整数 | 不兼容的 API 变更（如删除工具、改变参数类型） |
| minor | 整数 | 向后兼容的功能新增（如新增工具、新增参数） |
| patch | 整数 | 向后兼容的问题修复 |

### 5.3 兼容性承诺

#### 工具版本兼容性
- 调用方应使用 `server.capabilities.tools` 中的信息做能力发现
- 不应硬编码工具列表，应动态从 `/api/mcp-tools-list` 获取
- major version 变更前至少 2 个 minor version 提前标记 `deprecated: true`

#### 响应格式兼容性
- `success` / `data` / `error` 三段式永久兼容
- `error.error_type` 枚举值永久保持含义，不重定义已有 error_type
- 新增 error_type 一定是向后兼容

#### 工具删除流程
1. 先标记 `deprecated: true`，在 description 中说明替代工具
2. 连续 2 个 minor version 保持标记
3. 下个 major version 删除

### 5.4 字段废弃流程
1. **Deprecated 阶段**：字段标记为 deprecated，返回值中仍包含该字段
2. **删除阶段**：下个 major version（如 1.x → 2.0）删除该字段

### 5.5 新增字段
- 向后兼容，直接添加
- 新增字段不会是 breaking change

### 5.6 工具新增
- 向后兼容，直接添加
- 调用方应忽略未知工具名

### 5.7 工具删除
1. 先标记 `deprecated: true`，返回 deprecated 警告
2. 下个 major version 删除

---

## 6. 可观测性

### 6.1 结构化日志
- 格式：JSON
- 字段：`timestamp`, `level`, `request_id`, `tool_name`, `source`, `duration_ms`, `success`, `error_type`

### 6.2 请求追踪 ID
- 格式：`{upstream_name}-{timestamp}-{request_seq}`
- 贯穿 gateway → mootdx2_client → mootdx2_pool 全链路

### 6.3 指标
| 指标 | 说明 |
|------|------|
| `total_requests` | 总请求数 |
| `total_errors` | 总错误数 |
| `error_counts` | 各 error_type 计数 |
| `pool_stats.server_health` | 各服务器健康状态 |
| `pool_stats.avg_latency_ms` | 平均延迟 |

### 6.4 健康检查
- `get_health`：轻量 ping，返回 `{"status": "ok"}`
- `get_server_status`：返回服务器状态、连接池状态、错误率（供网关管理台读取）

---

## 7. 优雅关闭

### 7.1 关闭流程
1. 收到 SIGTERM/SIGINT 信号
2. 设置 `_running = False`，唤醒健康检查循环
3. **等待在途请求完成**（最多 10 秒）
4. 关闭所有上游客户端连接池（超时 5 秒/个）
5. 关闭缓存（超时 2 秒）
6. 退出

### 7.2 请求计数
- `_active_requests` 计数器，在 `_execute_cached` 入口增减
- `stop()` 等待计数器归零或超时

---

## 8. 安全

### 8.1 Token 鉴权
- 通过 `Authorization: Bearer <token>` Header 验证
- 配置：`gateway.auth.token`

### 8.2 入参校验
- Pydantic schema 校验股票代码格式、市场前缀
- 拒绝无效参数，避免触发底层库未定义行为

### 8.3 敏感信息
- API Key 通过环境变量 `${VAR}` 引用，不写进配置文件
- 错误响应不泄露完整堆栈，只返回脱敏摘要

---

## 9. 部署

### 9.1 Windows 服务化
- 建议使用 nssm 注册为 Windows 服务
- 配置自动重启（异常退出后自动拉起）

### 9.2 配置优先级
```
环境变量 > config文件 > 默认值
```

### 9.3 日志
- 默认路径：`./logs/mootdx2.log`
- JSON 格式，便于 ELK/Grafana 采集
