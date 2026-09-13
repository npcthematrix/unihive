# UniHive — 金融数据 MCP 聚合网关

统一的 MCP (Model Context Protocol) 网关,聚合多源金融数据,提供单一 /mcp 端点和 Web 管理控制台。

## 项目结构

```
unihive/
├── src/
│   └── unihive/                 # Python 包 (符合 PEP 721 src 布局)
│       ├── api/                 # 上游数据源 client
│       │   ├── mootdx2_client.py    # 通达信 (本地 TCP, MooTDX2 封装)
│       │   ├── tdx_quant_client.py  # 通达信量化终端 (in-process tqcenter)
│       │   ├── fuyao_client.py      # 同花顺 aicubes.cn (HTTP MCP, 4 个端点)
│       │   └── omni_client.py       # OMNIDATA (本地 SQLite)
│       ├── core/                # 路由 + 注册 + 工厂
│       │   ├── router.py            # 上游优先级链 + 降级
│       │   ├── registry.py          # 工具注册表
│       │   ├── tool_loader.py       # YAML → 工具加载
│       │   ├── mcp_factory.py       # FastMCP 实例化 + capability 过滤
│       │   ├── cache_strategy.py    # 缓存策略
│       │   ├── normalizer.py        # 股票代码标准化
│       │   └── auth_middleware.py   # console auth 中间件
│       ├── storage/
│       │   └── cache.py             # SQLite 缓存 (SQLAlchemy + aiosqlite)
│       ├── sync/
│       │   └── board_sync.py        # OMNIDATA 板块数据同步脚本
│       ├── utils/
│       │   ├── config_loader.py     # YAML 配置加载
│       │   ├── console_api.py       # 控制台 HTTP API (/api/*)
│       │   └── log_config.py        # 日志配置
│       ├── models/                  # 各上游的 dataclass + 异常
│       ├── exceptions/
│       ├── console_auth.py          # 控制台鉴权 (cookie-based, /api/* + console.html)
│       └── gateway_server.py        # 入口 (HTTP+stdio 双模)
├── config/
│   ├── upstreams.yaml           # 上游 + 路由 + 工具定义 (主配置)
│   ├── tools_mootdx2.yaml       # MooTDX2 工具详情
│   ├── tools_tdx_quant.yaml     # TdxQuant 工具详情
│   ├── tools_omni.yaml          # OMNI 工具详情
│   └── config.yaml              # 旧版配置 (兼容)
├── scripts/
│   ├── start_gateway.ps1        # 启动网关 (HTTP 默认 :18080,或 stdio)
│   ├── _load_env.ps1            # .env loader
│   ├── healthcheck.py           # 健康检查
│   ├── gen_tdx_quant_tools.py   # TdxQuant 工具清单生成
│   ├── audits/                  # 单次审计 (归档)
│   └── smoke/                   # 单次烟测 (归档)
├── static/console/              # 控制台静态资源 (CSS / JS)
├── tests/                       # pytest 套件
├── docs/                        # ARCHITECTURE + known-limitations + handoffs
├── console.html                 # 管理控制台 SPA (内嵌 static/console/*.css/js)
├── pyproject.toml
└── CLAUDE.md
```

## 数据源 (4 套,统一通过 /mcp 暴露)

| 源 | 类型 | 接入方式 | 工具数 | 备注 |
|---|---|---|---|---|
| **mootdx2** | 通达信 | 本地 TCP (mootdx2 库) | 34 | 行情 / K线 / 板块,需本地 TDX 服务器 |
| **tdx_quant** | 通达信量化终端 | 进程内 tqcenter.py | 54 | 行情 / 板块 / 交易日 / 财务 / 公式 / 交易 / 预警 |
| **fuyao** | 同花顺 (aicubes.cn) | HTTP MCP,4 端点 | 67 | a-share(28) / a-share-index(7) / meta(2) / fund(30) |
| **omni** | OMNIDATA | 本地 SQLite | 5 | 行业/概念/地域/风格板块,数据源 THS(同花顺)/TDX(通达信),需 `src.unihive.sync.board_sync` 预同步 |

合计 ~160 个工具,按路由优先级链分发:同一 gateway 工具可在多个上游配置降级路径(`config/upstreams.yaml` 的 `routing` 段)。

## 快速开始

```bash
pip install -e .
# 编辑 config/upstreams.yaml 与 .env
```

```powershell
# 启动 (HTTP 模式,默认 :18080,一个进程同时托管 /mcp + 控制台)
.\scripts\start_gateway.ps1

# 或 stdio 模式
.\scripts\start_gateway.ps1 -Transport stdio
```

控制台: http://127.0.0.1:18080  
MCP 端点: http://127.0.0.1:18080/mcp

## 关键约定

- **请求/响应 only**,无 SSE 流;`/mcp` 保持开放,**不**做鉴权
- **鉴权范围**: `/api/*` + `console.html` 用 cookie session (见 `src/console_auth.py`)
- **代码标准化**: `600519.SH` / `000001.SZ` (fuyao 格式) 与 `sh600519` (TDX 格式) 互转 (`src/core/normalizer.py`)
- **缓存**: SQLite (`logs/cache.db`),按 `cache_ttl_key` 区分 TTL 档 (realtime_quote / historical / fundamentals / ...)
- **板块数据**: OMNI 离线使用,先 `python -m src.unihive.sync.board_sync` 同步 `data/board.db`,再调 `omni_*` 工具

## 技术栈

- Python 3.10+ / FastMCP 4 / MCP 2.1
- SQLAlchemy 2 + aiosqlite (缓存)
- httpx (fuyao 上游)
- PyYAML (配置)
- Pydantic v2 (模型)
- Uvicorn (HTTP server)
- pytest + pytest-asyncio + pytest-cov (测试)

> 已知限制见 `docs/mootdx2-known-limitations.md` 与 `docs/tdx-quant-known-limitations.md`。