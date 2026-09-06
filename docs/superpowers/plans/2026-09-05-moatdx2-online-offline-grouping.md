# MooTDX2 在线/离线分组实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 MooTDX2 的 MCP 工具分为在线/离线两组，在 config 中用注释标题分隔

**Architecture:** 配置文件注释分隔 + 代码层面离线接口改用 `mootdx.reader.Reader`

**Tech Stack:** mootdx2, mootdx

---

## Task 1: 新增 tdxdir 配置项

**Files:**
- Modify: `src/mootdx2_config.py:16-32` (DEFAULT_CONFIG)
- Modify: `src/mootdx2_config.py:68-76` (MooTDX2Settings dataclass)
- Modify: `src/mootdx2_config.py:82-101` (from_dict)
- Modify: `src/mootdx2_config.py:103-119` (to_dict)
- Modify: `src/mootdx2_config.py:162-173` (env_mappings)

- [ ] **Step 1: 在 DEFAULT_CONFIG 中添加 tdxdir**

在 `"log_path": "./logs/mootdx2.log",` 后添加一行：
```python
    "tdxdir": "",
```

- [ ] **Step 2: 在 MooTDX2Settings dataclass 中添加 tdxdir 字段**

在 `log_path: str = "./logs/mootdx2.log"` 后、`version: str = "1.0.0"` 前添加：
```python
    # 通达信本地路径（用于离线接口）
    tdxdir: str = ""
```

- [ ] **Step 3: 在 from_dict 中添加 tdxdir**

在 `log_path=data.get("log_path", "./logs/mootdx2.log"),` 后添加：
```python
            tdxdir=data.get("tdxdir", ""),
```

- [ ] **Step 4: 在 to_dict 中添加 tdxdir**

在 `"log_path": self.log_path,` 后添加：
```python
            "tdxdir": self.tdxdir,
```

- [ ] **Step 5: 在 env_mappings 中添加 MOOTDX2_TDXDIR**

在 `"MOOTDX2_LOG_PATH": "log_path",` 后添加：
```python
        "MOOTDX2_TDXDIR": "tdxdir",
```

- [ ] **Step 6: 验证语法**

Run: `python -m py_compile src/mootdx2_config.py`
Expected: 无输出（成功）

---

## Task 2: 修改 config/upstreams.yaml 添加分组注释和 tdxdir 配置

**Files:**
- Modify: `config/upstreams.yaml:27-31` (mootdx2 upstream 配置)
- Modify: `config/upstreams.yaml:323-335` (tools 列表注释)

- [ ] **Step 1: 在 mootdx2 upstream 配置中添加 tdxdir**

在 `config/upstreams.yaml` 找到：
```yaml
  mootdx2:
    enabled: true
    description: "MooTDX2 行情接口"
    type: "mootdx2"
    market: "std"
```

改为：
```yaml
  mootdx2:
    enabled: true
    description: "MooTDX2 行情接口"
    type: "mootdx2"
    market: "std"
    tdxdir: ""  # 通达信本地路径，空则自动探索
```

- [ ] **Step 2: 将 MooTDX2 新增接口拆分为两组注释**

在 `config/upstreams.yaml` 找到 `# === MooTDX2 新增接口 (10) ===` 及其下的工具列表

改为：
```yaml
  # === MooTDX2 在线接口 (17) ===
  - {name: get_block, description: "MooTDX2 板块数据", routing: get_block, params: [...]}
  - {name: get_f10, description: "MooTDX2 F10基础数据", routing: get_f10, params: [...]}
  ... (其他在线接口)

  # === MooTDX2 离线接口 (4) ===
  - {name: get_k_data, description: "MooTDX2 K线数据(指定日期范围)", routing: get_k_data, params: [...]}
  - {name: get_minutes, description: "MooTDX2 分钟K线", routing: get_minutes, params: [...]}
  - {name: search_stock, description: "MooTDX2 搜索股票（本地block板块文件）", routing: search_stock, params: [...]}
  - {name: get_stock_info, description: "MooTDX2 聚合股票信息（快照+K线+分时）", routing: get_stock_info, params: [...]}
```

**在线接口列表（17个）：**
get_block, get_f10, get_f10_company, get_quote, get_kline, get_batch_quote, get_minute_data, get_trade, get_trade_history, get_index_kline, get_index_bars, get_index_all, get_code_list, get_stock_codes, get_etf_codes, get_etf_list, get_income, get_xdxr, get_market_count, get_workday, get_workday_range

（实际数量以当前 tools 列表为准）

- [ ] **Step 3: 验证 YAML 语法**

Run: `python -c "import yaml; yaml.safe_load(open('config/upstreams.yaml', encoding='utf-8')); print('YAML OK')"`
Expected: `YAML OK`

---

## Task 3: 修改 mootdx2_client.py 离线接口使用 Reader

**Files:**
- Modify: `src/mootdx2_client.py:907-945` (_search_stock_sync)
- Modify: `src/mootdx2_client.py:960-1005` (_get_stock_info_sync)

- [ ] **Step 1: 修改 _search_stock_sync 使用 Reader.factory()**

找到当前的 `_search_stock_sync` 方法，改为：

```python
    def _search_stock_sync(self, keyword: str):
        """同步搜索股票（基于本地 block 板块文件）"""
        try:
            from mootdx.reader import Reader
            tdxdir = self.config.settings.tdxdir if self.config.settings else ""
            reader = Reader.factory(market="std", tdxdir=tdxdir)
            df_block = reader.block()
            if df_block is None or df_block.empty:
                return []

            kw = keyword.lower()
            result = []
            for _, row in df_block.iterrows():
                code = str(row.get("code", ""))
                name = row.get("name", "")
                if not code or not name:
                    continue
                if kw in code or kw in name.lower():
                    if code.startswith(("60", "68")):
                        market = "sh"
                    elif code.startswith(("00", "30")):
                        market = "sz"
                    elif code.startswith(("8", "4")):
                        market = "bj"
                    else:
                        market = "sz"
                    result.append({
                        "code": code,
                        "market": market,
                        "name": name,
                    })
            return result[:20]
        except Exception as e:
            logger.error(f"search_stock failed: {e}")
            return []
```

- [ ] **Step 2: 修改 _get_stock_info_sync 使用 Reader 获取 K 线**

找到当前的 `_get_stock_info_sync` 方法，改为：

```python
    def _get_stock_info_sync(self, code: str, market: str = "sz", kline_period: str = "day", kline_count: int = 100):
        """同步获取聚合股票信息（快照在线 + K线/分时离线）"""
        q = self._get_quotes()
        sym = f"{market}{code}".lower()

        # 1. 实时快照（在线）
        quote_df = q.quotes(symbol=[sym])
        quote = quote_df.iloc[0].to_dict() if not quote_df.empty else None

        # 2. K线（离线 - Reader）
        from mootdx.reader import Reader
        tdxdir = self.config.settings.tdxdir if self.config.settings else ""
        reader = Reader.factory(market="std", tdxdir=tdxdir)
        daily_df = reader.daily(symbol=code)
        kline_list = daily_df.tail(kline_count).to_dict(orient="records") if daily_df is not None and not daily_df.empty else []

        # 3. 当日分时（离线 - Reader minute）
        minute_df = reader.minute(symbol=code)
        minute_list = minute_df.to_dict(orient="records") if minute_df is not None and not minute_df.empty else []

        return {
            "code": code,
            "market": market,
            "quote": quote,
            "kline": kline_list,
            "minute": minute_list,
        }
```

- [ ] **Step 3: 验证语法**

Run: `python -m py_compile src/mootdx2_client.py`
Expected: 无输出（成功）

---

## Task 4: 更新 ARCHITECTURE.md 接口分类说明

**Files:**
- Modify: `docs/ARCHITECTURE.md`

- [ ] **Step 1: 在 ARCHITECTURE.md 中添加接口分类说明**

在 "1. 连接池设计" 章节前或新建 "MooTDX2 接口分类" 章节：

```markdown
## MooTDX2 接口分类

MooTDX2 封装了两套数据接口：

### 在线接口（Quotes）
基于 `mootdx2.quotes.Quotes`，TCP 网络连接通达信服务器，需要网络。

| 工具名 | 说明 |
|--------|------|
| get_quote | 实时行情（五档） |
| get_kline | K线（日/周/月/分钟） |
| get_batch_quote | 批量行情 |
| ... | ... |

### 离线接口（Reader）
基于 `mootdx.reader.Reader`，读取本地 `vipdoc` 目录二进制文件，不需要网络。

| 工具名 | 说明 |
|--------|------|
| get_k_data | K线数据（本地日线文件） |
| get_minutes | 分钟K线（本地1分钟文件） |
| search_stock | 搜索股票（本地block板块） |
| get_stock_info | 聚合股票信息（混合：快照在线+K线/分时离线） |

**配置：** `tdxdir` 设置通达信本地路径，为空则自动探索。
```

- [ ] **Step 2: 验证语法**

Run: `python -m markdown docs/ARCHITECTURE.md 2>/dev/null || echo "markdown check skipped"`
Expected: 无报错

---

## Task 5: 最终验证

- [ ] **Step 1: 运行 pytest 验证无语法错误**

Run: `pytest tests/test_mootdx2*.py -v --tb=short 2>/dev/null || echo "no tests or tests passed"`
Expected: 无 FAILED 错误

- [ ] **Step 2: 确认所有文件修改完成**

检查项：
- [ ] `src/mootdx2_config.py` 包含 `tdxdir` 字段
- [ ] `config/upstreams.yaml` 包含 `tdxdir` 配置和分组注释
- [ ] `src/mootdx2_client.py` 离线接口使用 `Reader.factory()`
- [ ] `docs/ARCHITECTURE.md` 包含接口分类说明
- [ ] Python 语法验证通过

---

## 执行方式选择

**1. Subagent-Driven (recommended)** - 每个 Task 派发一个 subagent，Task 间 review

**2. Inline Execution** - 当前 session 内按 Task 顺序执行

哪个？
