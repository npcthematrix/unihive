# TdxQuant 已知限制清单

本文档列出 tdx-quant (通达信量化终端) 上游接入中的已知限制和简化场景。

## 一、运行环境约束

### 1.1 仅支持 Windows
- **状态**: tqcenter.py v1.0.12 通过 `ctypes` 加载 `TdxW.exe` 提供的 DLL，DLL 仅 Windows 可用
- **影响**: Linux/macOS 部署无法启用 tdx_quant 上游
- **配置**: 在非 Windows 环境下应将 `config/upstreams.yaml` 中 `tdx_quant.enabled` 设为 `false`
- **优先级**: 高（架构性约束，无法绕开）

### 1.2 单实例 TdxW.exe 绑定
- **状态**: 进程内 singleton `tq` 通过 DLL IPC 与运行中的 TdxW.exe 通信，同一台机器只能有一个 TdxW.exe 实例
- **影响**: 多实例网关部署需分别连不同的 TdxW.exe，不能共享 strategy_id
- **优先级**: 中

### 1.3 strategy_id 命名冲突
- **状态**: `tq.initialize(strategy_id)` 在 TdxW.exe 已注册同名策略时返回 ErrorId='12' (STRATEGY_EXISTS)
- **处理**: `TdxQuantClient.start()` 捕获该异常并标记 HEALTHY（仅 warning），不阻塞网关启动
- **后续**: 建议不同网关实例使用不同的 `strategy_id`（默认 `unihive_gateway`）
- **优先级**: 中

## 二、生命周期与重连

### 2.1 初始化失败不抛异常
- **状态**: `tqcenter` 模块缺失或 `tq.initialize()` 抛异常时，`start()` 返回 `False` 并标记 UNAVAILABLE，不向上抛
- **影响**: 网关会跳过 tdx_quant 继续启动其他上游
- **优先级**: 已完成（按设计行为）

### 2.2 后台探活周期 60 秒
- **状态**: `_health_loop` 默认每 60 秒调一次 `tq.get_user_sector()` 探活
- **影响**: DISCONNECTED 状态最长需 60 秒才被发现
- **配置**: `health_check_interval_sec` 可调小，但过小会增加 DLL 调用开销
- **优先级**: 中

### 2.3 重连不串行化调用
- **状态**: `_reconnect()` 用 `asyncio.Lock` 串行化重连过程本身，但重连期间并发的 `call_tool` 不会等待重连完成
- **影响**: 重连进行中的调用会立即返回 `upstream unavailable`
- **后续**: 如需"重连期间排队等待"语义，需扩展 `_reconnect` 为可 await 的状态机
- **优先级**: 中

### 2.4 UNAVAILABLE 后无自动恢复
- **状态**: 一旦 `_fail_count >= unavailable_threshold` (默认 10) 标记 UNAVAILABLE，`_health_loop` 退出，不再尝试重连
- **影响**: 需要人工介入（重启网关或重启 TdxW.exe）
- **后续**: 如需"无限重试"，可在 UNAVAILABLE 后定时触发 `_reconnect`
- **优先级**: 低（避免无限告警的设计选择）

## 三、调用语义

### 3.1 同步 DLL 包到 asyncio
- **状态**: `call_tool` 通过 `run_in_executor` 把同步 `tq.{tool}(**args)` 包成异步
- **影响**: 默认 executor 线程池大小限制并发；DLL 调用本身可能阻塞数秒
- **配置**: `call_timeout_sec` 默认 10 秒；超时返回 `ToolResult(success=False)`
- **优先级**: 中

### 3.2 无工具列表元数据
- **状态**: `list_tools()` 返回空列表（`[]`），工具定义来自 codegen 生成的 `config/tools_tdx_quant.yaml`
- **影响**: 上游不能动态发现工具，需重新跑 codegen 才能同步新增工具
- **优先级**: 已完成（设计选择：codegen 而非反射）

### 3.3 23 个 dangerous 工具无二次确认
- **状态**: `create_sector` / `delete_sector` / `order_stock` / `cancel_order_stock` / `exec_to_tdx` / `print_to_tdx` / `formula_*` 等写操作标记 `dangerous: true`，但 gateway 当前未在 `call_tool` 前拦截
- **影响**: LLM 可直接调用这些工具修改 TdxW.exe 状态（创建/删除板块、下单、写公式数据）
- **后续**: 如需在生产启用，应在 `TdxQuantClient.call_tool` 或 `Router.route` 中加 dangerous 二次确认层
- **优先级**: 高（生产部署前必须评估）

### 3.4 0 个工具标记 cacheable
- **状态**: `config/tools_tdx_quant.yaml` 中没有任何工具声明 `cache_ttl_key`
- **影响**: 所有 tdx_quant 调用都不经过 `src/cache.py`
- **后续**: 如需缓存行情类工具（如 `get_market_data`、`get_index_bars`），可在 yaml 加 `cache_ttl_key: realtime_quote` 等字段
- **优先级**: 低

## 四、错误翻译

### 4.1 ErrorId 翻译表覆盖不全
- **状态**: `tdx_quant_errors.py` 仅翻译 '0'(success) / '6','7'(DISCONNECTED) / '12'(STRATEGY_EXISTS)；其他 ErrorId 归为 `UPSTREAM_UNAVAILABLE` 触发重连
- **影响**: 业务层错误（如"代码不存在"、"参数越界"）被笼统归为 UNAVAILABLE，可能触发不必要的重连
- **后续**: 扩展 `translate_errorid` 翻译表，区分业务错误与连接错误
- **优先级**: 中

### 4.2 探活接口固定为 `get_user_sector`
- **状态**: `_health_loop` 调 `tq.get_user_sector()` 探活
- **影响**: 若该接口本身在 TdxW.exe 中存在 bug 或权限问题，会导致误判 UNAVAILABLE
- **后续**: 可改为多个候选探活接口（如 `get_market_data` 加已知代码）
- **优先级**: 低

## 五、配置约束

### 5.1 tdx_root 路径硬编码
- **状态**: `config/upstreams.yaml` 中 `tdx_root: "D:/new_tdx_mock"` 是开发机路径
- **影响**: 部署到其他机器需修改该字段
- **优先级**: 低（运维文档应说明）

### 5.2 缺省 strategy_id 为模块路径
- **状态**: `TdxQuantSettings.strategy_id or __file__`（fallback 到 `tdx_quant_client.py` 路径）
- **影响**: 不显式配置 `strategy_id` 时会以模块路径注册，可能与 TdxW.exe 中其他策略冲突
- **后续**: 建议在 `upstreams.yaml` 始终显式声明 `strategy_id`
- **优先级**: 中

## 六、覆盖工具一览

- 总工具数: 54
- Dangerous (写操作/交易): 23
  - 板块管理: create_sector, delete_sector, rename_sector, clear_sector, refresh_cache, refresh_kline
  - 消息推送: send_message, send_file, send_warn, send_bt_data, send_user_block
  - TdxW 命令: exec_to_tdx, print_to_tdx
  - 公式写入: formula_format_data, formula_set_data, formula_set_data_info, formula_zb, formula_xg, formula_exp, formula_get_data, formula_process_mul_zb
  - 交易: order_stock, cancel_order_stock
- Cacheable: 0
