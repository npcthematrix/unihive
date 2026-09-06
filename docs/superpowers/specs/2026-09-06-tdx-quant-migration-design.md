# tdx_tq_local → tdx-quant 服务源迁移设计

> 日期：2026-09-06
> 范围：用 `skills/SKILL.md`（TdxQuant `tqcenter.py v1.0.12`）替换现有 `tdx_tq_local`（HTTP JSON-RPC at 127.0.0.1:17709）上游及其 58 工具清单
> 方案：A（仿 `mootdx2_client.py` 模式，进程内直调 + 启动初始化 + 定期探活）

---

## 1. 背景与目标

### 1.1 现状

- `tdx_tq_local` 上游（`config/upstreams.yaml`）：`type: http_jsonrpc`，向 `http://127.0.0.1:17709` 发 JSON-RPC，由 `src/http_jsonrpc_client.py` 处理。
- 工具清单 `config/tools_tdx_tq_local.yaml`：58 个工具，由旧 `~/.claude/skills/tdx-tq-local/SKILL.md` 经 `scripts/gen_tdx_tq_local_tools.py` 生成，注释明确 `DO NOT EDIT`。
- 工具覆盖：行情/快照/股票信息、板块/列表、交易日历、财务、公式系统、交易下单、预警、订阅。

### 1.2 目标

用 `skills/SKILL.md`（TdxQuant Python SDK，`tqcenter.py`）作为新上游 `tdx_quant`，重新生成 60+ 工具清单，包含技能新增的 `formula_process_mul_xg`、`send_bt_data`、`refresh_kline` 等接口。

### 1.3 非目标

- 不重构现有 `MooTDX2Client`/`FuyaoClient`/`HttpJsonRpcClient` 抽象基类（属方案 B，超范围）。
- 不引入新 MCP 子服务或 HTTP 壳（属方案 C，多余 marshal 开销）。
- 不改 `registry.py`/`router.py` 主流程（仅在 `_TYPE_MAP` 加 `List[str]` 映射）。

---

## 2. 架构

### 2.1 组件清单

| 文件 | 操作 | 职责 |
|---|---|---|
| `src/tdx_quant_config.py` | 新建 | `TdxQuantSettings(tdx_root, strategy_id, health_check_interval_sec)`、`TdxQuantConfig(name, market, settings)` |
| `src/tdx_quant_client.py` | 新建 | `TdxQuantClient`：singleton `tq` 实例、`start/stop/call_tool/health_check/list_tools` |
| `src/tdx_quant_errors.py` | 新建 | `TdxQuantError`、`TdxQuantErrorType` 枚举、`translate_errorid()` |
| `scripts/gen_tdx_quant_tools.py` | 新建 | 改写自 `gen_tdx_tq_local_tools.py`，解析 `skills/SKILL.md` → `config/tools_tdx_quant.yaml` |
| `config/tools_tdx_quant.yaml` | 新建（codegen 产物） | 约 60 工具的 spec |
| `config/upstreams.yaml` | 修改 | 删 `tdx_tq_local` 段，新增 `tdx_quant` 段 |
| `config/tools_tdx_tq_local.yaml` | 删除 | 由新 yaml 取代 |
| `scripts/gen_tdx_tq_local_tools.py` | 删除 | 旧 codegen 脚本，新 codegen 取代 |
| `src/gateway_server.py` | 修改 | `_build_upstream()` 工厂分支：`type: tdx_quant` → `TdxQuantClient` |
| `src/http_jsonrpc_client.py` | 删除 | tdx_quant 不再使用，无其他上游用 http_jsonrpc |
| `src/registry.py` | 修改 | `_TYPE_MAP` 新增 `"List[str]": list`；`_LIST_PARAM_NAMES` 按需扩展 |
| `docs/tdx-quant-known-limitations.md` | 新建 | 已知限制清单（参照 `mootdx2-known-limitations.md` 风格） |

### 2.2 模块边界

- `TdxQuantClient` 只依赖 `tqcenter.py`（运行时 import）+ `tdx_quant_config` + `tdx_quant_errors`
- 不依赖 `http_jsonrpc_client`（已删除）
- `gateway_server.py` 通过 `type` 字段路由到 `TdxQuantClient`，与 mootdx2/fuyao 平行
- 错误翻译层风格对齐 `mootdx2_errors.py`（`MooTDXError` + `MooTDXErrorType` 枚举 + `translate_*` 函数）

---

## 3. 数据流（工具调用全链路）

以 `get_market_data` 为例：

```
MCP Client
  ↓ POST /mcp/ (JSON-RPC: tools/call, name=get_market_data)
Gateway (FastMCP)
  ↓ registry.py 构造的 _runtime(**kwargs) 函数
  ↓ _validate_and_normalize → 参数归一（code/dates/csv lists）
  ↓ server._execute_cached(name, params, route_key, ttl_key)
  ↓ router.py 按 route_key 路由到 tdx_quant 上游
TdxQuantClient.call_tool("get_market_data", {stock_list, period, ...})
  ↓ 查 singleton tq 是否就绪（未就绪 → 返 ToolResult(success=False, error="TdxW 未运行")）
  ↓ asyncio.get_event_loop().run_in_executor(None, _sync_call)
        └─ _sync_call: getattr(tq, "get_market_data")(**params)
            └─ tqcenter.py → TPythClient.dll → TdxW.exe IPC
  ↓ 返回 dict（含 ErrorId 字段）
  ↓ _translate_result() 检查 ErrorId：
        "0"   → ToolResult(success=True, data=result)
        "6"/"7" → DISCONNECTED + schedule_reconnect + ToolResult(success=False)
        "12"  → STRATEGY_EXISTS + ToolResult(success=False)
        其他  → ToolResult(success=False, error=...)
  ↓ 回到 _execute_cached → 命中 cache 写入 → 返回 MCP 工具结果
  ↓ FastMCP 包装为 structuredContent + content text 块
  ↓ 返回 JSON-RPC response
```

### 3.1 关键设计点

1. **`run_in_executor` 隔离同步调用**：`tqcenter.py` 全是同步阻塞调用（DLL IPC），用默认 `ThreadPoolExecutor`，每个调用 1 个线程，TdxW IPC 串行不会因并发产生竞争（DLL 单连接）。

2. **singleton `tq` 状态机**：
   - `UNINITIALIZED` → `start()` 调 `tq.initialize(__file__)` → `HEALTHY`
   - `HEALTHY` → 探活失败 N 次 → `DISCONNECTED` → 后台任务尝试 `tq.close()` + `tq.initialize()` → `HEALTHY` 或 `UNAVAILABLE`
   - 任何状态下 `call_tool` 收到 `ErrorId='6'/'7'` → 标记 `DISCONNECTED`、触发 `_reconnect()`（用 `asyncio.Lock` 串行化，避免并发重连）

3. **探活任务**：`start()` 时启动 `asyncio.create_task(self._health_loop())`，每 `health_check_interval_sec`（默认 60s）调 `tq.get_user_sector()`（轻量、无副作用），失败累计 3 次触发 reconnect；连续失败超过 10 次 → `UNAVAILABLE`、停止探活、等待人工或外部触发重启。

4. **缓存策略**：复用 `gateway_server._execute_cached` + `cache_ttl_key`，TTL 映射见 §6。

5. **危险工具标注**：codegen 按 method 名规则打 `dangerous: true`，`registry.py` 现有逻辑：每次调用写 WARNING 审计日志（不拦截，沿用）。

6. **危险工具运行时拦截**：本次不引入额外拦截。用户已有 `disable_dangerous` 全局开关 + `dangerous` 字段双重控制。

---

## 4. 错误处理

### 4.1 错误码翻译表

| `ErrorId` / 异常 | `TdxQuantErrorType` | 用户面向错误消息 | 处理动作 |
|---|---|---|---|
| `"0"` / 缺失 | 无错误 | — | 正常返回 `data` |
| `"6"` | `DISCONNECTED` | "与通达信客户端的连接已断开，正在尝试重连" | 标记 `DISCONNECTED`，触发 `schedule_reconnect()` |
| `"7"` | `DISCONNECTED` | 同上 | 同上 |
| `"12"` | `STRATEGY_EXISTS` | "已有同名策略运行，请检查是否有其他 gateway 进程" | `start()` 阶段警告但不阻塞；运行中收到则降级 `UNAVAILABLE` |
| 其他非零 | `UNKNOWN` | `"通达信返回错误：ErrorId={id}, ErrMsg={msg}"` | 记 WARNING，原样返回 |
| `asyncio.TimeoutError` | `TIMEOUT` | "调用通达信超时（{timeout}s）" | 同上 |
| `ConnectionError`/`OSError` | `UPSTREAM_UNAVAILABLE` | "通达信客户端未响应，可能已退出" | 标记 `DISCONNECTED`，触发 `schedule_reconnect()` |
| `ModuleNotFoundError`（`tqcenter` 无法导入） | `INIT_FAILED` | "无法加载 tqcenter.py，请检查 tdx_root 路径" | `start()` 阶段失败，gateway 继续启动但 tdx_quant 标 `UNAVAILABLE` |
| `FileNotFoundError`（`tqcenter.py` 不存在） | `INIT_FAILED` | "tqcenter.py 不存在，请确认通达信已安装并支持 TQ 策略" | 同上 |
| 其他 `Exception` | `UNKNOWN` | `"调用失败：{e}"` | 记 ERROR，`ToolResult(success=False)` |

### 4.2 `start()` 启动流程

```python
try:
    sys.path.insert(0, tdx_root + "/PYPlugins/user")
    import tqcenter
    self._tq = tqcenter.tq()
    self._tq.initialize(self._strategy_id)  # 默认 __file__，可配 strategy_id
    self._status = HEALTHY
    self._health_task = asyncio.create_task(self._health_loop())
except (FileNotFoundError, ModuleNotFoundError) as e:
    logger.error("[tdx_quant] init failed: %s", e)
    self._status = UNAVAILABLE
    # 不抛，gateway 继续启动
except Exception as e:
    # 含 ErrorId='12' 同名策略
    logger.warning("[tdx_quant] initialize warning: %s", e)
    self._status = HEALTHY  # '12' 仅警告，仍可用
```

`strategy_id` 配置项允许显式指定唯一标识，避免多 gateway 实例冲突。默认 `__file__`。

### 4.3 `_reconnect()` 重连

用 `asyncio.Lock` 保证只有一个重连任务在跑：

```python
async def _reconnect(self):
    async with self._reconnect_lock:  # 串行化，避免并发重连
        try:
            self._tq.close()
        except Exception: pass
        try:
            self._tq.initialize(self._strategy_id)
            self._status = HEALTHY
            self._fail_count = 0
            logger.info("[tdx_quant] reconnected")
        except Exception as e:
            self._status = DISCONNECTED
            logger.warning("[tdx_quant] reconnect failed: %s", e)
```

连续失败 10 次 → `UNAVAILABLE`，停止自动重连，等待人工介入或外部 health_check 触发。

### 4.4 `health_check()` 探活循环

```python
async def _health_loop(self):
    while self._status != UNAVAILABLE:
        await asyncio.sleep(self._health_check_interval_sec)
        try:
            result = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None, lambda: self._tq.get_user_sector()),
                timeout=5.0)
            if isinstance(result, dict) and result.get("ErrorId", "0") in ("6", "7"):
                self._fail_count += 1
            else:
                self._fail_count = 0
                self._status = HEALTHY
        except (asyncio.TimeoutError, Exception):
            self._fail_count += 1
            if self._fail_count >= 3:
                await self._reconnect()
            if self._fail_count >= 10:
                self._status = UNAVAILABLE
                return
```

### 4.5 `stop()` 优雅关闭

挂到 gateway 的 `lifespan` shutdown 钩子：

```python
if self._health_task: self._health_task.cancel()
if self._tq:
    try: self._tq.close()
    except Exception: pass
self._status = UNAVAILABLE
```

### 4.6 `call_tool` 返回结构

复用现有 `ToolResult` dataclass（`success/data/error/source/duration_ms`），不引入新类型。错误消息字符串化、不含敏感信息（如完整 path），符合 mootdx2 错误翻译规范。

---

## 5. codegen 脚本与 yaml 结构

### 5.1 `scripts/gen_tdx_quant_tools.py`

**输入**：`skills/SKILL.md`（含 50+ 接口的 `### X.Y 标题 \`method_name\`` 标题 + markdown 参数表）

**解析逻辑**：

1. 正则匹配 `### \d+\.\d+ .+? \`([a-z_]+)\`` 抓 method 名
2. 抓紧随的 markdown 参数表（`| 参数 | 必填 | 类型 | 说明 |` 行 + 数据行）
3. 类型映射：`List[str]` → `List[str]`、`str` → `str`、`int` → `int`、`bool` → `bool`、`Optional[str]` → `str` + `required: false`
4. 枚举识别：参数说明里出现 `'a' / 'b' / 'c'` 或 `` `a`/`b` `` 模式 → 提取为 `enum: [a, b, c]`
5. `dangerous: true` 推断：method 名匹配 `^(order_|cancel_order|create_sector|delete_sector|rename_sector|clear_sector|send_|refresh_|download_file|exec_to_tdx|print_to_tdx|formula_)`
6. `cache_ttl_key` 推断：method 名前缀映射（见 §6）
7. 缺参数表的 method 仍写入（`params: []`），codegen 末尾打印 warning 列表

**CLI 接口**（与现有 codegen 一致）：

- 默认：写入 `config/tools_tdx_quant.yaml`
- `--dry-run`：仅打印统计（工具数、dangerous 数、缺表 method 列表）
- `--check`：与磁盘内容比对，不一致 exit 1（供 CI 用）

### 5.2 输出 yaml 结构（样例）

```yaml
# Auto-generated from skills/SKILL.md
# DO NOT EDIT — re-run scripts/gen_tdx_quant_tools.py
tools:
- name: get_market_data
  description: K线/分钟线/历史行情
  routing: get_market_data
  upstream_tool_mapping:
    tdx_quant: get_market_data
  cache_ttl_key: historical
  dangerous: false
  params:
  - {name: field_list, required: false, type: List[str], description: 需返回的字段列表；空列表返回所有字段}
  - {name: stock_list, required: true, type: List[str], description: 股票代码列表}
  - {name: period, required: true, type: str, description: K线周期, enum: [1m, 5m, 15m, 30m, 60m, 1d, 1w, 1mon, 1q, 1hy, 1y]}
  - {name: start_time, required: false, type: str, description: 开始时间，YYYYMMDD 或 YYYY-MM-DD}
  - {name: end_time, required: false, type: str, description: 结束时间；未传则默认当前时间}
  - {name: count, required: false, type: int, description: 'count>0 取截止 end_time 最近 n 条；count<=0 用区间'}
  - {name: dividend_type, required: false, type: str, description: 复权类型, enum: [none, front, back, qfq, hfq]}
  - {name: fill_data, required: false, type: bool, description: 是否向前填充缺失数据，默认 True}

- name: order_stock
  description: 下单交易
  routing: order_stock
  upstream_tool_mapping:
    tdx_quant: order_stock
  cache_ttl_key: null
  dangerous: true
  params:
  - {name: account_id, required: true, type: str, description: 资金账户句柄}
  - {name: code, required: true, type: str, description: 证券代码}
  - {name: order_type, required: true, type: str, description: 委托类型, enum: [buy, sell]}
  # ... 其余参数
```

### 5.3 `registry.py` 兼容性

- `_TYPE_MAP` 新增 `"List[str]": list`
- `_LIST_PARAM_NAMES` 按需扩展（避免 `key_word`/`account_id` 等非列表参数被误判为 CSV）

---

## 6. 缓存 TTL 映射

下表为 codegen 推断 `cache_ttl_key` 的规则；未列出的 method 默认 `null`（不缓存）。codegen 按 method 名精确匹配下表的列举，避免前缀歧义。

| `cache_ttl_key` | TTL（秒，复用 `upstreams.yaml::cache.ttl`） | 精确方法名集合 |
|---|---|---|
| `realtime_quote` | 10 | `get_market_snapshot`、`get_more_info`、`get_gp_one_data` |
| `historical` | 3600 | `get_market_data`、`get_divid_factors`、`get_pricevol` |
| `fundamentals` | 3600 | `get_financial_data`、`get_financial_data_by_date`、`get_stock_info`、`get_gb_info`、`get_gb_info_by_date`、`get_kzz_info`、`get_ipo_info`、`get_trackzs_etf_info`、`get_gpjy_value`、`get_gpjy_value_by_date`、`get_bkjy_value`、`get_bkjy_value_by_date`、`get_scjy_value`、`get_scjy_value_by_date` |
| `ticker_list` | 3600 | `get_stock_list`、`get_sector_list`、`get_user_sector`、`get_stock_list_in_sector`、`get_relation`、`get_match_stkinfo` |
| `workday` | 86400 | `get_trading_dates`、`get_trading_calendar` |
| `null`（不缓存） | — | `send_message`、`send_file`、`send_warn`、`send_bt_data`、`send_user_block`、`create_sector`、`delete_sector`、`rename_sector`、`clear_sector`、`refresh_cache`、`refresh_kline`、`download_file`、`order_stock`、`cancel_order_stock`、`stock_account`、`query_stock_orders`、`query_stock_positions`、`query_stock_asset`、`exec_to_tdx`、`print_to_tdx`、`subscribe_hq`、`unsubscribe_hq`、`get_subscribe_hq_stock_list`、`formula_set_data`、`formula_set_data_info`、`formula_get_data`、`formula_zb`、`formula_xg`、`formula_exp`、`formula_process_mul_xg`、`formula_process_mul_zb`、`formula_process_mul_exp`、`formula_get_all`、`formula_get_info`、`formula_format_data`、`tq.close`、`tq.initialize`、`tq.price_df` |

> 备注：`get_subscribe_hq_stock_list` 归 `null`，因为订阅态在订阅期间动态变化、10s 缓存也可能过期误导用户。客户端若需精确态应直接调用。

---

## 7. 测试策略

### 7.1 层级划分

| 层级 | 范围 | 工具 | 目标覆盖 |
|---|---|---|---|
| 单元 | `tdx_quant_errors.translate_errorid`、`TdxQuantClient` 状态机（mock tq） | pytest + unittest.mock | 错误翻译层 100%、client 状态转换 90%+ |
| 集成 | codegen 脚本 round-trip | pytest | codegen 100% |
| 集成 | `registry.py` 加载新 yaml、注册工具、signature 正确性 | pytest | 工具注册 100% |
| 烟测 | 真实 TdxW.exe 运行时手动 `tools/call` | curl + 人工 | 关键路径验证 |

### 7.2 单元测试用例

**`tests/test_tdx_quant_errors.py`**：

```
test_translate_errorid_0_is_success
test_translate_errorid_6_marks_disconnect
test_translate_errorid_7_marks_disconnect
test_translate_errorid_12_strategy_exists
test_translate_errorid_unknown_falls_back
test_translate_errorid_timeout
test_translate_errorid_module_not_found
test_translate_errorid_connection_error
```

**`tests/test_tdx_quant_client.py`**（mock `tqcenter.tq`）：

```
test_start_initializes_tq_singleton
test_start_failure_marks_unavailable
test_start_when_tdx_not_installed
test_call_tool_runs_in_executor
test_call_tool_returns_success_on_errorid_0
test_call_tool_returns_error_on_errorid_6
test_call_tool_when_unavailable_returns_error
test_health_loop_marks_disconnected_after_3
test_health_loop_marks_unavailable_after_10
test_stop_closes_tq_and_cancels_health_task
test_reconnect_lock_serializes
```

### 7.3 集成测试用例

**`tests/test_gen_tdx_quant_tools.py`**：

```
test_codegen_produces_expected_tool_count    # 50-70 之间
test_codegen_marks_dangerous_correctly      # order_stock 等 17+ 个 dangerous=true
test_codegen_assigns_cache_ttl_keys          # get_market_data→historical, get_market_snapshot→realtime_quote
test_codegen_roundtrip_idempotent           # 连续跑两次输出一致
test_codegen_check_mode_detects_drift       # 改 yaml 后 --check 退 1
test_codegen_handles_missing_param_table    # 缺表 method 写入 params=[]
```

**`tests/test_registry_tdx_quant.py`**：

```
test_loads_tdx_quant_yaml_without_error
test_list_str_param_mapped_to_list_type
test_dangerous_tools_registered_with_warning
test_all_tools_have_annotations
test_all_tools_have_title
```

### 7.4 烟测脚本（`scripts/smoke_tdx_quant.py`，手动跑）

前置：TdxW.exe 已运行、已登录

```
1. 启动 gateway
2. curl /mcp/ tools/call get_market_snapshot {stock_code:"600519.SH"}
   校验 structuredContent 含 Now/LastClose/Buyp/...
3. 调用 get_market_data {stock_list:["600519.SH"], period:"1d", count:5}
   校验返回 DataFrame-like dict
4. 调用 order_stock with disable_dangerous=true → 校验 dangerous 日志
5. 杀 TdxW.exe → 调用 → 校验 ErrorId 6 翻译、reconnect 触发
6. 重启 TdxW.exe → 等探活周期 → 校验自动恢复 HEALTHY
```

### 7.5 覆盖率目标

- `tdx_quant_errors.py`：100%
- `tdx_quant_client.py`：90%+（`run_in_executor` 真实调度靠烟测覆盖）
- `gen_tdx_quant_tools.py`：95%+
- 整体新增代码 ≥ 85%，符合项目 80% 红线

---

## 8. 配置变更

### 8.1 `config/upstreams.yaml`（修改）

删除：

```yaml
  tdx_tq_local:
    enabled: true
    description: "通达信-交易终端: ..."
    type: "http_jsonrpc"
    base_url: "http://127.0.0.1:17709"
    timeout_seconds: 10
    retry: {max_attempts: 2}
    capabilities: [user_block, watchlist, conditional_stock]
```

新增：

```yaml
  tdx_quant:
    enabled: true
    description: "通达信-量化终端 (TdxQuant): 进程内直调 tqcenter.py，含 60+ 工具（行情/板块/交易日/财务/公式/交易/预警）"
    type: "tdx_quant"
    market: "std"
    tdx_root: "D:/new_tdx_mock"      # 通达信安装目录（含 PYPlugins/user/tqcenter.py）
    strategy_id: "unihive_gateway"   # 策略唯一标识，避免多实例冲突
    health_check_interval_sec: 60
    call_timeout_sec: 10
    reconnect_threshold: 3           # 连续失败次数触发重连
    unavailable_threshold: 10        # 连续失败次数降级 UNAVAILABLE
    capabilities:
      - market_data
      - market_snapshot
      - stock_info
      - sector
      - watchlist
      - trading_calendar
      - financials
      - formula
      - trading
      - subscribe
```

### 8.2 `config/tools_tdx_tq_local.yaml`（删除）

由 `config/tools_tdx_quant.yaml`（codegen 产物）取代。

### 8.3 `config/upstreams.yaml::upstream_tool_mapping`

旧 TQ-Local 工具映射行全部移除（由新 yaml 的 `upstream_tool_mapping: {tdx_quant: <method>}` 取代，属 self-routing）。

---

## 9. 已知限制与边界

### 9.1 平台限制

- 仅 Windows：`tqcenter.py` 依赖 Windows DLL + TdxW.exe
- 需通达信已安装（注册表 `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\通达信*`）
- 需 `TdxW.exe` 运行中（未运行时 tdx_quant 标 `UNAVAILABLE`，gateway 其他上游不受影响）

### 9.2 单例限制

- 整进程一个 `tq` 实例，等同于"一个策略"，符合网关场景
- 多 gateway 实例需配不同 `strategy_id`，避免 `ErrorId='12'` 冲突
- 不支持跨进程共享 `tq`（DLL 句柄进程私有）

### 9.3 同步调用限制

- 所有 `tq.*` 调用走 `run_in_executor`，占用默认 `ThreadPoolExecutor` 线程
- 高并发场景可能线程池打满，必要时后续引入独立 `ThreadPoolExecutor(max_workers=N)` 限制

### 9.4 数据格式限制

- 返回结构为 `tqcenter.py` 原生 dict（部分含 `pd.DataFrame`），不二次转换
- 客户端需自行处理 DataFrame 序列化（FastMCP structuredContent 会 `json.dumps(default=repr)`）

### 9.5 未覆盖场景

- 公式批量调用 `formula_process_mul_xg` 等的复杂 `setting` 参数：codegen 抓为 `dict` 类型，调用时由用户传入完整结构
- 订阅接口 `subscribe_hq`/`unsubscribe_hq`：当前 `cache_ttl_key=null` 不缓存，但订阅态的回调推送机制本次不实现（属后续增强）

---

## 10. 验收清单

- [ ] `scripts/gen_tdx_quant_tools.py` 运行成功，产出 `config/tools_tdx_quant.yaml`（60+ 工具）
- [ ] `config/upstreams.yaml` 含 `tdx_quant` 段，无 `tdx_tq_local`
- [ ] `src/tdx_quant_client.py` 启动时 `tq.initialize()` 成功，`get_server_status` 显示 `tdx_quant` 可用
- [ ] `tools/call get_market_snapshot` 返回正确结构（含 `Now`/`Buyp`/`Sellp`）
- [ ] `tools/call get_market_data` 返回 DataFrame-like dict
- [ ] `tools/call order_stock`（dangerous=true）触发 WARNING 审计日志
- [ ] 杀 `TdxW.exe` → 后续调用返 `DISCONNECTED` 错误
- [ ] 重启 `TdxW.exe` → 探活周期后自动恢复 `HEALTHY`
- [ ] 单元测试 + 集成测试全部通过，新增代码覆盖率 ≥ 85%
- [ ] `docs/tdx-quant-known-limitations.md` 已编写
- [ ] 旧文件已删除：`config/tools_tdx_tq_local.yaml`、`scripts/gen_tdx_tq_local_tools.py`、`src/http_jsonrpc_client.py`

---

## 11. 实施顺序建议

1. 先写 `tdx_quant_errors.py` + 单元测试（最独立、可立即验证）
2. 写 `tdx_quant_config.py`（纯 dataclass）
3. 写 `tdx_quant_client.py` + mock 单元测试
4. 写 `gen_tdx_quant_tools.py`，跑出 `tools_tdx_quant.yaml`
5. 修改 `registry.py`（`_TYPE_MAP` 加 `List[str]`）
6. 修改 `gateway_server.py` 工厂分支
7. 修改 `config/upstreams.yaml`（删旧增新）
8. 删除旧文件（`tools_tdx_tq_local.yaml`/`gen_tdx_tq_local_tools.py`/`http_jsonrpc_client.py`）
9. 写 `docs/tdx-quant-known-limitations.md`
10. 烟测：启动 TdxW → 启动 gateway → 逐项跑 §7.4 脚本
11. 集成测试 + 覆盖率检查
