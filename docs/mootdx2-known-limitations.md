# MOOTDX2 已知限制清单

本文档列出 MOOTDX2 MCP Server 实现中的已知限制和简化场景。

## 一、连接池相关

### 1.1 连接池未实际启用
- **状态**: 代码定义了 `ConnectionPool` 但 `_get_quotes()` 直接创建 `Quotes.factory()`
- **影响**: 无法复用连接、无法享受连接池的健康检查和自动重试
- **后续**: 如需使用连接池，需重构 `_get_quotes` 使用 `pool.get_connection()`
- **优先级**: 中

### 1.2 获取连接超时已添加
- **状态**: `ConnectionPool.get_connection()` 新增 `timeout` 参数，默认 10 秒
- **影响**: 连接池打满后不再无限等待
- **状态**: ✅ 已完成

## 二、限流与部署

### 2.1 多实例限流未实现
- **状态**: 限流逻辑只在进程内（令牌桶）
- **影响**: 多实例部署无法限制 TDX 服务器请求
- **文档**: 已在 console.html 和本文件中说明此限制
- **优先级**: 低（文档已补全）

### 2.2 Windows 部署测试
- **状态**: ✅ 信号处理使用 `sys.platform != "win32"` 判断
- **状态**: ✅ 路径分隔符使用 pathlib.Path
- **状态**: ✅ 所有文件读写显式指定 `encoding="utf-8"`
- **状态**: ✅ console.html 添加防火墙端口放行提醒
- **优先级**: 已完成

## 三、功能覆盖

### 3.1 北交所代码支持
- **状态**: 代码逻辑支持 8/4 开头代码（`bj` 市场）
- **测试**: 未进行专项测试
- **优先级**: 低

### 3.2 复权参数
- **状态**: 当前工具无复权参数（mootdx2 底层支持但未暴露）
- **影响**: 无法获取前复权/后复权数据
- **后续**: 如需支持需扩展工具参数
- **优先级**: 低

### 3.3 交易时间行为
- **状态**: 未覆盖非交易时间、停牌期间的具体返回行为
- **需验证**: 每个接口在收盘后、非交易日、停牌期间的真实返回
- **优先级**: 中

## 四、MCP 协议（遵循 2025-06-18 规范）

### 4.1 传输层
- **状态**: 使用 Streamable HTTP (`transport="streamable-http"`)
- **状态**: ✅ 已完成

### 4.2 无状态 Session（stateless_http）
- **状态**: ✅ `http_app(stateless_http=True)` 已启用
- **行为**: 每次 HTTP 请求自包含完整 JSON-RPC 消息，不依赖此前 `initialize` 建立 session
- **验证**: `tools/list`、`tools/call` 可直接 POST 调用，无需先 `initialize`、无需 `Mcp-Session-Id` header
- **适用性**: 本项目所有工具均为只读查询、无副作用、天然无状态，完全契合 stateless 模式
- **规范对齐**: 符合 MCP 规范演进方向（session 机制在放松，向完全无状态发展）
- **优先级**: 已完成

### 4.3 MCP-Protocol-Version header
- **状态**: ✅ FastMCP 4.0.2 内置版本协商
- **行为**: `initialize` 请求可不带该 header（客户端此时未知版本号，合规）；`initialize` 之后的所有请求必须带
- **验证**: 不支持的版本号（如 `2099-99-99`）返回 HTTP 400 Bad Request
- **当前版本**: `2025-06-18`
- **优先级**: 已完成

### 4.4 工具标注（Tool Annotations）
- **状态**: ✅ 服务端已设置 `readOnlyHint=True`、`destructiveHint=False`、`idempotentHint=True`、`openWorldHint=True`
- **实现**: `src/registry.py::register_tools_from_config` 统一注入
- **限制**: FastMCP 4.x 客户端 SDK 可能未正确读取 annotations 字段（服务端已正确设置）
- **优先级**: 已完成

### 4.5 结构化输出（outputSchema + structuredContent）
- **状态**: ✅ FastMCP 4.0.2 自动为 `dict` 返回类型生成 `outputSchema`
- **行为**: `tools/call` 响应同时包含:
  - `content: [{"type":"text", "text":"..."}]`（向后兼容，纯文本）
  - `structuredContent: {...}`（机器可解析的 JSON）
- **示例**: `get_health` 返回 `{"content":[{"text":"{\"status\":\"healthy\"}","type":"text"}],"structuredContent":{"status":"healthy"}}`
- **限制**: 当前 `outputSchema` 为通用 `{"type":"object","additionalProperties":true}`，未声明具体字段
- **后续**: 如需更精确的类型约束，可在 `registry.py` 为每个工具传 `output_schema=` 参数（需维护 148+ 个 schema）
- **优先级**: 已完成（通用 schema 满足规范要求）

### 4.6 title 字段（中文名）
- **状态**: ✅ 从工具描述中提取【】内容作为 title（如"K线数据"）
- **实现**: `src/registry.py::register_tools_from_config` 使用 `re.search(r"【([^】]+)】")`
- **优先级**: 已完成

### 4.7 JSON-RPC 批量请求
- **状态**: ✅ 不依赖 JSON-RPC 批量请求（get_batch_quote 是 mootdx2 库的批量行情查询，非 JSON-RPC 批处理）
- **优先级**: 已完成

### 4.8 资源链接（Resource Links）
- **状态**: ❌ 未实现
- **规范要求**: 大数据量场景（如 10 年日 K 线 ~2500 行、全市场快照）可返回 resource link，客户端通过 `resources/read` 按需拉取
- **当前行为**: K 线等大返回体工具直接 inline 返回全部数据
- **影响**: 单次响应体较大，但客户端实现简单（无需二次请求）
- **后续实现路径**: 注册 `resources/list` + `resources/read` handler，设计 `mcp://kline/{thscode}?start=...&end=...` URI scheme，在工具结果中返回 `resource_link` 类型 content block
- **优先级**: 低（当前 inline 返回可用；如遇客户端上下文超限问题再实施）

### 4.9 JSON Schema 高级特性（oneOf/anyOf/allOf）
- **状态**: ⚠️ 未使用
- **规范建议**: 复杂参数（如"日期范围 OR 预设区间"、"单标的 OR 批量标的"）可用 `oneOf` 表达互斥备选
- **当前行为**: 参数都是简单类型（str/int/float/bool），互斥备选用多个 optional 参数 + 文档说明表达
- **影响**: 客户端看到的 schema 较"扁平"，但调用更简单（无需构造复杂嵌套 JSON）
- **后续**: 如需更严格的表达力，可在 `tools_*.yaml` 的参数 spec 中扩展 `oneOf`/`anyOf` 字段，并在 `registry.py::_annotation_for` 中映射到 Pydantic 模型
- **优先级**: 低

### 4.10 鉴权
- **状态**: 当前无鉴权（内网部署）
- **说明**: 如需鉴权，应按 OAuth 2.1 资源服务器模型实现（FastMCP 4.x 已支持 `auth=` 参数注入）
- **优先级**: 低（内网部署无需鉴权）

## 五、日志与监控

### 5.1 日志分级与轮转
- **状态**: 已配置 INFO/DEBUG 分级
- **状态**: ✅ 已添加 RotatingFileHandler（10MB/文件，保留5个备份）
- **优先级**: 已完成

### 5.2 健康检查
- **状态**: 连接池有健康检查循环
- **状态**: ✅ 已完成

---

## 验收检查清单

- [x] Streamable HTTP 传输
- [x] run_in_executor 包裹同步调用
- [x] 工具描述包含在线/离线标注
- [x] 参数和返回结构有基本说明
- [x] 异常走错误翻译层
- [x] 连接池超时配置
- [x] 日志轮转配置（RotatingFileHandler 10MB/5备份）
- [x] 工具行为标注（readOnlyHint/idempotentHint 等）
- [x] 无 str(e) 直接序列化
- [x] 无"详见文档"指代性描述
- [x] stateless_http 无状态模式（2025-06-18 规范）
- [x] MCP-Protocol-Version header 校验（无效版本返 400）
- [x] outputSchema + structuredContent 自动生成
- [x] title 中文字段
- [ ] 多实例限流
- [ ] 北交所代码测试
- [ ] 复权参数支持
- [ ] 非交易时间行为验证
- [ ] 资源链接（resource links）—— 大数据量场景
- [ ] JSON Schema 高级特性（oneOf/anyOf/allOf）—— 复杂参数表达

---

*本文档根据 MCP 封装排查清单生成，最后更新: 2026-09-06（MCP 2025-06-18 规范合规性优化完成）*

## 板块（sector）工具

### 分类体系
- 官方板块仅 3 种：行业（block_ch.dat）、概念（block_zs.dat）、地区（block_fd.dat）
- **不支持**：申万行业、中信行业、风格板块等其它分类体系

### 板块代码
- TDX 本地文件只存储中文板块名（`sector_name`），不存储数字板块代码
- 调用方必须用中文板块名做 `get_sector_stocks_local` / `get_custom_sector_stocks` 的入参
- 不能跨机器共享板块名（不同 TDX 客户端板块名一致但 ID 不一定一致）

### 自定义板块可用性
- 完全依赖本机 TDX 客户端 + `T0002/blocknew/` 目录
- 自定义板块名是用户个人标签，跨机器无意义
- `blocknew.cfg` 格式由 TDX 客户端定义，本工具不验证内容合法性

### 与 tdx_quant 的同名工具重复
- `mootdx2.get_sector_list` 与 `tdx_quant.get_sector_list` 同名但语义不同：
  - mootdx2: 读取本地 block_*.dat，按 sector_type (industry/concept/region) 过滤
  - tdx_quant: 通过 tdx 服务端 list_type 拉取，含 cache
- 控制台 `/api/interfaces` 会去重后保留其中一个；上游路由根据工具名分发
