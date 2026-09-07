# UniHive - 金融数据 MCP 聚合网关

UniHive MCP Gateway 是一个统一的 MCP (Model Context Protocol) 网关服务，用于聚合多个金融数据源，提供统一的数据访问接口。

## 项目结构

```
mcp-data-gateway/
├── src/
│   ├── __init__.py           # 包初始化
│   ├── normalizer.py         # 股票代码标准化
│   ├── upstream_client.py    # 上游 MCP Client 封装 (stdio)
│   ├── rhths_client.py       # 同花顺 HTTP MCP Client
│   ├── router.py             # 路由决策器
│   ├── cache.py              # 缓存管理
│   ├── gateway_server.py     # MCP 网关主服务
│   └── console_server.py     # 管理控制台 HTTP 服务
├── config/
│   └── upstreams.yaml        # 上游配置
├── scripts/
│   ├── healthcheck.py        # 健康检查脚本
│   ├── start_gateway.ps1     # 启动网关
│   ├── start_console.ps1     # 启动控制台
│   └── start_all.ps1         # 启动全部服务
├── console.html              # 管理控制台前端
├── pyproject.toml            # Python 项目配置
└── CLAUDE.md                 # 本文件
```

## 功能特性

- **多数据源聚合**: 支持通达信 (TDX)、同花顺 (RHTHS) 等多个金融数据源
- **智能路由**: 基于配置优先级链自动路由请求，支持降级
- **缓存管理**: 内置 SQLite 缓存，减少重复请求
- **统一接口**: 通过 MCP 协议提供统一的数据访问接口
- **管理控制台**: 提供 Web UI 查看上游状态和配置

## 快速开始

### 1. 安装依赖

```bash
pip install -e .
```

### 2. 配置

编辑 `config/upstreams.yaml` 配置上游数据源。

### 3. 启动服务

**启动网关 (stdio 模式):**
```powershell
.\scripts\start_gateway.ps1
```

**启动管理控制台:**
```powershell
.\scripts\start_console.ps1
```

**同时启动两者:**
```powershell
.\scripts\start_all.ps1
```

### 4. 访问控制台

打开浏览器访问 http://127.0.0.1:18080

## 支持的数据接口

| 接口 | 描述 | 数据源 |
|------|------|--------|
| moo_realtime_quote | 股票实时行情 | MooTDX |
| moo_daily_bar | 日K线 | MooTDX |
| moo_minute_bar | 分钟K线 | MooTDX |
| moo_kline | K线 (day/week/month/minute) | MooTDX |
| moo_block_data | 板块/概念数据 | MooTDX |
| moo_trade_dates | 交易日历 | MooTDX |
| moo_etf_list | ETF列表 | MooTDX |
| moo_get_financial_data | 财务数据 | MooTDX |
| moo_get_stock_info | 股票信息 | MooTDX |

> 注: 139 个 MCP 接口全部通过管理控制台 http://127.0.0.1:18080 的 "MCP APIs" 标签页查看

## 技术栈

- **Python 3.10+**
- **MCP Protocol**: 用于与上游服务通信
- **FastMCP**: MCP 服务器框架
- **SQLAlchemy + aiosqlite**: 缓存数据库
- **httpx**: HTTP 客户端
- **PyYAML**: 配置文件解析
