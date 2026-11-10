# ![UniHive](static/unihive-wordmark-light.svg)

> 统一的 MCP (Model Context Protocol) 网关 — 聚合多源金融数据,提供单一 `/mcp` 端点和 Web 管理控制台。

## 数据源

| 源 | 类型 | 工具数 | 接入方式 |
|---|---|---|---|
| **mootdx2** | 通达信 | 38 | 本地 TCP, 行情 / K线 / 板块 |
| **tdx_quant** | 通达信量化终端 | 54 | 进程内 `tqcenter.py`, 行情 / 板块 / 交易日 / 财务 / 公式 / 交易 / 预警 |
| **fuyao** | 同花顺 aicubes.cn | 67 | HTTP MCP, 4 端点 (a-share / a-share-index / meta / fund) |
| **omni** | OMNIDATA | 5 | 本地 SQLite, 行业/概念/地域/风格板块 |

合计 **218 个工具**,按路由优先级链分发; 同一 gateway 工具可在多个上游配置降级路径 (`config/upstreams.yaml` 的 `routing` 段)。

## 快速开始

```bash
pip install -e .
# 编辑 config/upstreams.yaml 与 .env
```

```powershell
# HTTP 模式 (默认 :18080, 一个进程同时托管 /mcp + 控制台)
.\scripts\start_gateway.ps1

# 或 stdio 模式
.\scripts\start_gateway.ps1 -Transport stdio
```

- 控制台: <http://127.0.0.1:18080>
- MCP 端点: <http://127.0.0.1:18080/mcp>

## 关键约定

- **配置事实源是 `data/config.db`** (2026-09-18 P2 起): YAML 解构为实体行 (upstream/tool/config_section + JSON 列) 全量入库,空库首次启动自动从 YAML 出厂模板 seed; 日常配置维护在控制台,不改 YAML。`UNIHIVE_CONFIG_SOURCE=yaml` 为逃生模式。
- **请求/响应 only**, 无 SSE 流; `/mcp` 保持开放,**不**做鉴权。
- **鉴权范围**: `/api/*` + `console.html` 用 cookie session。
- **代码标准化**:`600519.SH` / `000001.SZ` (fuyao 格式) 与 `sh600519` (TDX 格式) 互转。
- **缓存**: SQLite (`logs/cache.db`),按 `cache_ttl_key` 区分 TTL 档 (historical / fundamentals / valuation / ... 见 `config/upstreams.yaml` 的 `cache.ttl`); 标 `realtime_quote` 的是**故意不缓存** (实时行情要最新); TTL 是**日期/时段感知**的 (历史 immutable 与交易时段双轨)。
- **板块数据**: OMNI 离线使用,先 `python -m src.unihive.sync.board_sync` 同步 `data/board.db`,再调 `omni_*` 工具。

## 技术栈

Python 3.10+ · FastMCP 4 · MCP 2.1 · SQLAlchemy 2 + aiosqlite · httpx · PyYAML · Pydantic v2 · Uvicorn · Vue 3 + Element Plus (控制台)。

## 文档

- [已知限制 — mootdx2](docs/mootdx2-known-limitations.md)
- [已知限制 — tdx-quant](docs/tdx-quant-known-limitations.md)
- [架构总览](docs/ARCHITECTURE.md)
- [变更专题 handoff](docs/handoffs/)

## 许可

私有项目,未开源。
