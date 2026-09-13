# THSDK 上游（同花顺 thsdk 社区包）

UniHive 的第 5 套数据源，封装社区包 [`thsdk`](https://github.com/panghu11033/thsdk)（panghu11033/thsdk，PyPI: `thsdk>=2.0`），通过网关统一的 `/mcp` 端点暴露 **26 个工具**：

- **16 个只读工具**：常驻注册
- **10 个自选股写工具**：仅当进程启动前环境变量 `ALLOW_WATCHLIST_WRITE=true` 时才注册

thsdk 是**同步**的模块级单例，网关在线程池中执行调用，统一做 60ms 最小间隔节流（规避服务端限频）与 30s 超时；会话失效时自动重新登录并重试一次。

## 安装与依赖

`thsdk` 已写入 `pyproject.toml` 主依赖：

```bash
pip install -e .
# 或单独安装
pip install "thsdk>=2.0,<3"
```

未安装时网关不会崩溃：thsdk 上游标记为 UNAVAILABLE，其工具返回明确的 `init_failed` 错误，其他上游不受影响。

## 环境变量

| 变量 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `THS_USERNAME` | 否 | 空 | 同花顺账号；与 `THS_PASSWORD` **必须同时设置或同时留空**，只设一只会导致启动登录失败 |
| `THS_PASSWORD` | 否 | 空 | 同花顺密码 |
| `ALLOW_WATCHLIST_WRITE` | 否 | `false` | 自选写工具总开关，仅 `true/1/yes/on`（不区分大小写）为真 |

要点：

- 两个凭证都留空时，启动走 `thsdk.auth()` 游客/临时会话；只读接口通常可用，写接口会返回明确的权限错误。
- 凭证只从进程环境变量读取，**不写进 YAML、不进日志、不进工具返回体**。
- `ALLOW_WATCHLIST_WRITE` 是**加载期门控**：关闭时写工具既不出现在 `tools/list`，也不可被调用；**没有运行时动态开关后门**，改值必须重启进程。
- thsdk 登录后会在进程工作目录写出 `account.session` 会话文件，已在 `.gitignore` 中排除；`stop()` 刻意不调用 `logout()`，以免删除可复用的会话文件。

配置示例（`.env`）：

```bash
# 游客会话（只读）
ALLOW_WATCHLIST_WRITE=false

# 正式账号 + 显式开启写能力
THS_USERNAME=13800000000
THS_PASSWORD=********
ALLOW_WATCHLIST_WRITE=true
```

上游条目在 `config/upstreams.yaml`：

```yaml
upstreams:
  thsdk:
    enabled: true
    type: "thsdk"
    call_timeout_sec: 30
    throttle_ms: 60
```

## THSCODE 约定

thsdk 使用带市场前缀的**完整 THSCODE**：`USHA600519`（沪）、`USZA300033`（深）。
只有短代码或名称时，必须先经以下工具解析：

- `thsdk_search_symbols` — 代码 / 拼音缩写 / 中文名搜索
- `thsdk_complete_ths_code` — 短代码数组批量补全

写工具接收短代码时会内部自动调 `complete_ths_code` 补全；无法补全则返回 `invalid_param` 错误，不会静默执行。

## 只读工具（16）

| 工具 | 用途 | 缓存档 |
|---|---|---|
| `thsdk_auth_status` | 登录状态 / 登录方式 / 写开关（不暴露凭证） | 不缓存 |
| `thsdk_account_permissions` | 当前账号数据权限 | 不缓存 |
| `thsdk_get_account_watchlist` | 账号自选快照（含 version 乐观锁） | 不缓存 |
| `thsdk_get_account_watchlist_groups` | 全部分组及组内证券 | 不缓存 |
| `thsdk_search_symbols` | 证券搜索 | 不缓存 |
| `thsdk_complete_ths_code` | 短代码批量补全 | 不缓存 |
| `thsdk_get_price` | K 线（参数版：frequency/fq/count 或日期范围） | historical |
| `thsdk_klines` | K 线（传统参数名：interval/adjust/count） | historical |
| `thsdk_intraday_data` | 分时走势 | realtime_quote |
| `thsdk_depth` | 五档买卖盘（多标的） | realtime_quote |
| `thsdk_tick_level1` | L1 逐笔成交 | realtime_quote |
| `thsdk_corporate_action` | 分红送配 / 除权除息 | historical |
| `thsdk_wencai_nlp` | 问财自然语言选股 | special_data |
| `thsdk_news` | 7×24 快讯分页 | special_data |
| `thsdk_block_constituents` | 板块成分证券 | ticker_list |
| `thsdk_market_securities` | 市场证券分页 | ticker_list |

返回值约定：`DataFrame` 转为 `records` 形式的 `list[dict]`，时间字段转 ISO 字符串，空结果与异常统一走网关标准信封（`success/data/error/error_detail`）。

## 危险写操作（10，默认关闭）

所有写工具都带 MCP 注解 `read_only_hint=false, destructive_hint=true`，描述以固定前缀开头：

> [DANGEROUS WRITE] 此操作会修改同花顺账号自选数据，不可撤销或难以撤销。调用前必须确认用户意图。

每次调用另写一条 WARNING 级审计日志（仅工具名 + 成败，不含凭证/会话内容）。

| 工具 | 说明 | 需 `confirm=true` |
|---|---|---|
| `thsdk_add_account_watchlist_securities` | 追加自选（可置顶） | 否 |
| `thsdk_remove_account_watchlist_securities` | 移除自选 | 否 |
| `thsdk_replace_account_watchlist_securities` | 按 version 整体替换自选 | **是** |
| `thsdk_clear_account_watchlist` | 清空全部自选 | **是** |
| `thsdk_create_account_watchlist_group` | 新建分组（可带初始证券） | 否 |
| `thsdk_delete_account_watchlist_group` | 删除分组 | **是** |
| `thsdk_rename_account_watchlist_group` | 重命名分组 | 否 |
| `thsdk_add_account_watchlist_group_securities` | 分组追加证券 | 否 |
| `thsdk_remove_account_watchlist_group_securities` | 分组移除证券 | 否 |
| `thsdk_replace_account_watchlist_group_securities` | 整体替换分组内证券 | **是** |

`confirm` 为必填布尔参数，默认拒绝；只有显式传 `true` 才执行。即便配置错误导致写工具被注册，客户端运行时还有第二道门控：`ALLOW_WATCHLIST_WRITE` 未开启时一律返回"自选写操作未启用"错误。

## MCP 客户端配置

先启动网关（HTTP 模式，默认 `:18080`，一个进程同时托管 `/mcp` 与控制台）：

```powershell
.\scripts\start_gateway.ps1
# 或
python -m src.gateway_server --transport http --port 18080
```

Claude Desktop（`claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "unihive": {
      "url": "http://127.0.0.1:18080/mcp"
    }
  }
}
```

Cursor：Settings → MCP → Add new server，类型选 **Streamable HTTP / URL**，填 `http://127.0.0.1:18080/mcp`。

> `/mcp` 不做鉴权（请求/响应模式，无 SSE）；管理控制台 `/api/*` 与 `console.html` 另有 cookie 登录保护。

## 调用示例

只读：查 K 线

```json
{
  "security": "USHA600519",
  "frequency": "daily",
  "count": 20,
  "fq": "pre"
}
```

只读：短代码补全后再使用

```json
// thsdk_complete_ths_code
{ "codes": ["600519", "300033"] }
// → 从返回中取 USHA600519 / USZA300033
```

危险写：清空自选（必须二次确认）

```json
// 第 1 次（或缺省 confirm）→ 返回错误："该操作不可撤销，必须显式传 confirm=true 才会执行"
{ "confirm": false }

// 用户明确确认后
{ "confirm": true }
```

整体替换自选（version 乐观锁 + confirm 双保险）：

```json
// 先 thsdk_get_account_watchlist 取当前 version，再：
{ "securities": ["USHA600519", "USZA300033"], "version": 12, "confirm": true }
```

## 故障排查

| 现象 | error_type | 处理 |
|---|---|---|
| `thsdk 包加载失败` | init_failed | `pip install "thsdk>=2.0,<3"` |
| `同花顺登录失败` | auth_failed | 检查账号密码；游客会话失败可重试或配置凭证 |
| `THSDK 未登录或登录已失效` | not_authenticated | 网关会自动重登重试一次；持续失败检查网络/账号 |
| `参数错误：短代码无法补全…` | invalid_param | 先用 search/complete 确认代码 |
| `自选写操作未启用…` | api_error | 重启前设 `ALLOW_WATCHLIST_WRITE=true` |
| `同花顺接口错误：…限频…` | rate_limited | 网关已节流；仍出现则调大 `throttle_ms` |
