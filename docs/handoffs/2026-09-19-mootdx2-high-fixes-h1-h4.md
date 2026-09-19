# mootdx2_client.py 审计 HIGH 四项 (H1–H4) — 已落盘未提交

日期: 2026-09-19
专题: mootdx2 1.4.2 真实方法面校准 (用户批准 "HIGH 四项 (推荐)", 严格 TDD)
状态: **全部 GREEN, 已提交 3408992 (2026-09-19, 含第二轮; 同会话 da5db47 补 gateway __main__)**

## 背景

对 `src/unihive/api/mootdx2_client.py` 的审计发现: 多个对外工具调用
mootdx2 1.4.2 中**根本不存在**的方法 / 读取**报文里不存在的列, 生产路径
必挂或恒空。旧单测的 mock 凭空发明方法面, 给了假信心。所有事实均对照
已安装库源码核实:

- `.venv/Lib/site-packages/mootdx2/quotes.py` — StdQuotes 真实方法面:
  `quotes, bars, kline, batch_bars, stock_count, stocks, stock_all,
  index_bars, minute, minutes, transaction, transactions, F10, F10C,
  xdxr, finance, k, ohlc, get_k_data, index, block`。无 `stock/etf/income`。
- `tdxpy/parser/std/get_security_quotes.py` — 原始行情行 OrderedDict:
  `market(int 0=SZ/1=SH/2=BJ), code, active1, price, last_close, open,
  high, low, servertime, vol, cur_vol, amount, s_vol/b_vol, bid1-5/ask1-5/
  bid_vol*/ask_vol*, reversed_bytes9, active2`。无 close/pct_chg/symbol/
  name/turnover/volume_ratio。
- `tdxpy/hq.py` — 批次中任一 symbol market=2 (BJ) → 整批返回 None。
- `tdxpy/reader/block_reader.py` + mootdx2 `block(tofile='block.dat')`
  → FLAT 行: `blockname/block_type/code_index/code`, code = 7 位
  "市场位(0=SZ/1=SH/2=BJ) + 6 位代码", 每行一个成分股。`code_list`
  只存在于 GROUP 模式 (mootdx2 不走该模式)。

## 四项修复

### H1 — 假方法工具: q.stock / q.etf / q.income

- `get_stock_codes` / `stock_top_board`: 改用真实 `q.stocks(market=1|0)`
  (SH/SZ 分页全量), 按 A 股前缀过滤 (SH 60/68, SZ 00/30);
  新增 `_list_market_securities_sync` / `_list_prefixed_codes_sync` /
  `_list_a_share_codes_sync`。
- ETF: mootdx2 无 `etf()`; 改为从 `stocks()` 按 ETF 前缀派生
  (SH 51/56/58, SZ 15; 不含 16=LOF)。`get_etf_list(exchange=bj)`
  显式 no_data —— stocks(market=2) 库内即抛 MootdxValidationException。
- `get_income`: **诚实下线**。finance() 是单股财务快照不是盘后收益序列,
  语义不符, 不用它冒充。删除 `_get_income_sync`/`get_income`/call_tool
  分发表条目 + `config/tools_mootdx2.yaml` 规格块 (留 2026-09-19 墓碑注释)。
  注意: **已存在的 data/config.db 实体内的残留行不会被 YAML 删除自动迁移**,
  运行时 call_tool 对其返回 "Unknown tool" (代码注释已说明); 如需彻底清,
  在控制台配置库面板删除该 tool 实体或重建库。

### H2 — stock_unusual 恒空 (block FLAT 列 + 假 kwarg)

- `q.block(block_type=...)` 的 kwarg 被 `**kwargs` 静默吞掉 → 改 `q.block()` 裸调。
- 消费 FLAT `code` 列: 首字符市场位映射 sh/sz/bj, 尾 6 位代码; 只收沪深
  (BJ 进 quotes 批次会毒化整批); 去重 + sorted + 500 上限。
- 行情批次带 `sh/sz` 前缀直传 (get_stock_markets 显式识别前缀, 不靠数字猜)。
- 异动宇宙改为先 `|pct_chg| >= 5` 过滤, 再做事件分类 —— 修复 "涨" 事件
  用 `pct>0` 把 +0.5% 的票当异动漏出的问题; 涨/跌/放量/缩量/炸板/天地板
  均在异动宇宙内过滤。
- 输出 `sym[2:]` 去前缀; event 标签阈值不变 (>=9.9 涨停 / <=-9.9 跌停)。

### H3 — index_overview 标签错位 + 指数代码撞车

- 旧码剥 sh/sz 前缀后 000001 被路由成深市股票 (平安银行, 与上证指数撞码);
  且按返回行**位置** zip 贴标签, 上游丢/乱序一行则后面全错。
- 改为前缀直传 `["sh000001","sz399001","sz399006","sh000688","sh000300"]`,
  返回行按 `(int(market), code)` 入 dict 回贴; 北证50 (bj889999) 单独
  best-effort 一批 (只接受 market==2 行, 失败 debug 日志, 不拖垮其余 5 个);
  缺失指数省略, 不零填; 输出按 `_INDEX_SPECS` 固定顺序; close=price,
  change_pct 由 (price-last_close)/last_close 计算。

### H4 — batch_bars 周期码错误 + count 被吞

- 删除类属性 `FREQ_MAP_BATCH` (minute 码 1/5/15/30/60 全错)。
- `_get_batch_bars_sync` 统一用模块级 `FREQ_MAP`
  (minute1=8/minute5=0/minute15=1/minute30=2/minute60=3; day=9/week=5/month=6)
  —— batch_bars 内部就是循环 `quotes.bars(frequency=...)`, 同一套 TDX category。
- 旧码 `count=limit` 落进 **kwargs 被忽略 → 改真实签名 `offset=min(limit, 800)`。

### 报文归一化辅助 (新增模块级)

`_quote_symbol` / `_quote_pct_chg` / `_quote_amplitude` /
`_annotate_quote_records` + 常量 `_MARKET_ID_TO_PREFIX`、
`_A_SHARE_PREFIXES_SH/SZ`、`_ETF_PREFIXES_BY_MARKET`、
`_BLOCK_MARKET_CHAR_TO_PREFIX`。统一补 `symbol/close(=price)/pct_chg/
amplitude`; turnover/volume_ratio 协议无此数据, top_board 诚实置 0.0
(合同键保留, 不引起路由/契约 churn)。

## 未动 (用户约束)

- L1 区: `_daily_l1`/`_top_board_l1`/`_unusual_l1` 及 get_daily/stop 中
  L1 块、stock_top_board/stock_unusual 的 L1 wrapper 一律未碰。
- 3 个禁跑测试文件未跑 (既有红): test_get_daily_l1_observability.py、
  test_mootdx2_p0_perf.py (3 个 single-flight 旧红, single-flight 已移除)、
  test_fuyao_cache_config.py。

## TDD 记录

- 新文件 `tests/test_mootdx2_high_fixes.py` (**27 用例**), FakeStdQuotes
  只实现真实方法面 (stocks/quotes/block/batch_bars); 先 RED (25 failed/
  2 passed), 实现后 GREEN。
- 清理 `tests/test_mootdx2_client.py`: 删除 3 个假 mock 的 false-green
  旧用例 (index_overview / top_board / unusual), 留指向新文件的注释;
  test_get_quote 的 mock 改为真实原始行情行。
- 工具总数 golden 随 get_income 下线 290 → **289**, 更新 4 个测试文件:
  test_config_db_golden (含函数改名 test_real_config_loads_289_tools、
  内联 53/外拆 236 注释)、test_config_overlay_golden、
  test_config_db_load_chain、test_config_entities_api。

## 验证 (2026-09-19, 未开浏览器)

- 后端: `pytest --ignore=<3 禁跑> -q` → **1160 passed, 3 skipped**
  (修复前 1153 passed / 7 golden 红)。
- web: `npx vitest run` → **324 passed**; `npm run build` ✓
  (console.html + static/console/assets 已 finalize, hash 文件名随之 churn)。
- 文档/文案: docs/ARCHITECTURE.md 删 get_income 行; CLAUDE.md
  mootdx2 34→33、合计 289 spec; web 三处陈旧计数注释 (用户可见
  "约 290" 保留, 289≈290 且本就是约数)。

## 变更文件

- src/unihive/api/mootdx2_client.py (H1–H4 实现)
- config/tools_mootdx2.yaml (get_income 下线)
- tests/test_mootdx2_high_fixes.py (新, 27 用例)
- tests/test_mootdx2_client.py (删 3 假用例 + 修 quote mock)
- tests/test_startup_audit_round9_batch_c.py (删 _probe_attempts 残留赋值)
- tests/test_config_{db_golden,db_load_chain,entities_api,overlay_golden}.py (289)
- docs/ARCHITECTURE.md, CLAUDE.md
- web/src/views/{ConfigView,InterfacesView}.vue + views/guides/AiOnboardingPanel.vue (注释)
- console.html + static/console/assets/* (build 产物)

## 死代码清理 (同专题, 用户追加要求 "去掉不必要的代码")

H1–H4 完成后对 mootdx2_client.py 做 AST 引用扫描 + 人工 grep 复核
(`_run_blocking(self._x, ...)` 间接派发会让扫描器对 sync 实现误报,
逐个 grep 排除), 删除:

- 未用 import: `uuid`、`ErrorClassifier`、`no_data_result`、`classify_symbol`
  (ErrorClassifier 由 mootdx2_pool.py 独立 import, 不受影响);
  `import datetime` 提升到模块级, 删除 2 处函数内重复导入 (L1 区 2 处不动)。
- 从未抛出的 `SectorDataError` 异常类 + `_sector_error_result` 方法 +
  6 处死 `except SectorDataError` 分支 (get_custom_sector_list/
  get_custom_sector_stocks/get_daily/get_minute/get_fzline/
  get_index_kline_local)。
- `get_metrics_async` (零引用; 指标走同步 get_metrics)。
- 模块函数 `_validate_quote_record`、死常量 MAX_LIMIT_TRADE /
  MAX_LIMIT_TRANSACTIONS / MAX_LIMIT_STOCK_LIST。
- 只写不读的 `self._quotes_cls` (init + 赋值点)。
- 残留 `self._probe_attempts` (start() 用的是局部 attempt; 同步删
  test_startup_audit_round9_batch_c.py 里 __new__ 构造的赋值行)。
- `_filter_records_by_date` 里 `return out` 后不可达的 `return None`。
- `_get_custom_sector_list_sync` 纯透传壳, 调用点直连 `_scan_blk_files_sync`。
- `_scan_blk_files_sync` 内未使用的 market_prefix_map (另一函数内的保留)。
- `_build_daily_tool_result` 两个未使用参数 adjust / reader_symbol_key
  (签名 + 调用点; 调用点在 get_daily L1 miss 分支, 仅删两行 kwargs,
  缓存逻辑一字未动)。
- 冗余局部 import: _build_daily_tool_result 的 index_registry、
  _search_stock_from_sqlite 的 pathlib (均已在模块级)。

文件 2854 → **2762 行** (-92)。删除后 1160 passed/3 skipped 不变。
FREQ_MAP_BATCH 仅剩 H4 墓碑注释, 故意保留。

## 后续

- 已提交 3408992 (2026-09-19, 两轮一并)。
- MED/LOW 台账 (mootdx2 perf audit handoff 2026-09-15 及本次审计余项) 未动。
- 实测建议: 本地 TDX 行情服务可用时, 用真实工具跑一遍 stock_unusual /
  index_overview / stock_top_board 冒烟 (本批修复事实基线来自库源码核实;
  venv 为 Python 3.13.15)。

---

# 第二轮: 3 HIGH + 4 MED + 7 LOW 合同修复 — 已落盘未提交 (2026-09-19)

专题: 用户批准 "全部 (含 7 项 LOW)", 严格 TDD (RED → GREEN),
已随 3408992 提交 (本文件第二轮章节为提交后补状态)。
新测试文件 `tests/test_mootdx2_contract_fixes.py` (**63 用例**)。
后端全量 **1224 passed, 3 skipped** (基线 1160 + 63 新 + 1 净增)。
venv Python 3.13.15 实测。未开浏览器, web 资产未触碰。

## 事实基线 (库源码/本机实测)

- tdxpy 批次含 market=2 (BJ) → 整批返 None; BJ 必须**裸 6 位码单独批**,
  且只能 best-effort, 绝不能与沪深同批。
- mootdx2 `Customize(tdxdir).search(group=True)` 返 DataFrame:
  blockname(中文)/block_type(.blk stem)/stock_count/code_list;
  直接 `for entry in df` 迭代得到的是**列名字符串** (旧扫描代码的
  isinstance(tuple) 分支是死代码, 中文名映射从未生效)。
- `StdReader(tdxdir=None)` 回退 `DEFAULT_TDXDIR = C:/new_tdx`,
  目录存在即构造成功 (本机 2026-09-19 17:23 出现空骨架 C:/new_tdx/vipdoc)。
- 排序协议键 turnover/volume_ratio 在原始行情行中**不存在**,
  top_board 一直按 H1 的诚实 0.0 输出, 无法真排序。

## HIGH (3)

- **H1 批量行情契约 (get_batch_quote)**: 新建 wrapper + `_get_batch_quote_sync`。
  codes 空/超 50 只 → invalid_param **不打 TDX**; 逐码 `_parse_quote_symbol`
  (sh/sz 前缀码 / bj 裸码, 非 6 位 → invalid_symbol); 沪深一批,
  每只 BJ 独立裸码批, BJ miss 只在 error_detail.warn 提示, 不毒化沪深结果;
  输出按 (prefix, bare) dict 回排为**请求序**; 逐行注解 symbol/close/pct_chg。
- **H2 自定义板块中文名**: `_load_custom_sector_name_map` (Customize.search
  iterrows → {stem: 中文名}, 异常/空 → {} 退化 stem); .blk 扫描
  (`_scan_blk_files_sync`) 与成分查询 (`_get_custom_sector_stocks_sync`)
  支持中文名与 stem 双入口, 中文未命中返 SECTOR_NOT_FOUND。
- **H3 tdxdir 前置闸**: `_require_blocknew_dir()` — tdxdir 空/目录不存在 →
  TDX_NOT_INSTALLED; T0002/blocknew 缺失 → TDX_CUSTOM_SECTOR_UNAVAILABLE;
  轻量内部哨兵 `_CustomSectorError(error_type)` 经两 wrapper 显式捕获 →
  `_custom_sector_error_result` (success=False, recoverable,
  **不进 error_counts / 不降级 status**); 修掉旧码 Path(None) TypeError
  误报 internal_error。

## MEDIUM (4)

- **M1 batch_bars 门控**: codes ≤20 只、limit 必须 int 且 1-500,
  代码须 6 位 (可带前缀), 违约 invalid_param/invalid_symbol 不打 TDX;
  sync 签名改为收 wrapper 解析后的 bare list; offset=min(limit,800)
  的 800 硬上限防御保留在 sync。
- **M2 workday 合同**: get_workday count 须 int 1-365;
  两工具日期均接受 YYYYMMDD / YYYY-MM-DD (`_parse_compact_date`),
  start>end → invalid_param; 参数默认当天。**诚实声明只做周末数学,
  不剔除法定节假日, 网关不内置节假日历** (未伪造日历)。
- **M3 stock_info**: 删除吞异常的宽 try (故障现按 internal_error 传播);
  market 白名单 sh/sz/bj; BJ 不走在线 quotes (best-effort 降级不致命);
  quote 帧 None/空守卫; Reader 一律 bare code。
- **M4 search_stock SQLite 路径/资源**: db 路径走 `resolve_project_path`
  锚定项目根 data/board.db (防 CWD split-brain); 连接 finally 关闭
  (BoomConn 测试验证); 代码分类统一 `_classify_search_code`
  (7 位 FLAT 市场位 / 6 位 bare / 920→bj); YAML 删除"拼音"宣传 —
  底层表只有 stock_code/stock_name 两列, 不支持字母缩写。

## LOW (7 + 1 顺带 bug)

1. get_minute_data 补 total_requests 计数 (metrics 漏统)。
2. indicator_atr: 空 K 线返 None → wrapper no_data (旧 sync 返 {} 语义错);
   limit int 1-1000 门控。
3. stock_top_board: bj 显式 no_data (stocks(market=2) 不支持);
   turnover/volume_ratio 协议无数据 → warn 并回落涨跌幅排序;
   未知排序字段 → warn; 排序映射 sort_column_map 用契约键;
   warn 经 `_with_warn` 返回**复制的** ToolResult, L1 缓存对象保持不可变,
   命中缓存的第二次调用 warn 仍在。
4. get_index_all YAML: 删除"成分股"误导表述 (q.index 只返指数自身行情),
   cache_ttl_key realtime_quote → **historical**; indicator_atr
   fundamentals → **historical**。
5. `_get_daily_sync` 改返 `(records, meta)` tuple, 删除跨并发互踩的
   实例属性 `_last_daily_meta`; 顺带修 cachetools ImportError
   (`_DAILY_L1_ENABLED=False`) 时 miss 分支 `parsed_for_key`
   UnboundLocalError (L1 缓存逻辑本身一字未动)。
6. stock_unusual 标签: 涨停/跌停判定优先; 放量/缩量带方向
   ("缩量上涨"/"放量下跌"), 其余按 pct 正负 "上涨"/"下跌"。
7. get_minutes date 参数校验 + 默认当天 (YAML 陈旧默认 20191023 清除);
   get_kline limit int 1-1000 门控 + 删冗余 tail(limit);
   get_stock_codes/get_etf_codes/get_etf_list limit 正整数门控;
   call_tool 分发表从每次新建的内联 ~40 项 dict 提为模块级
   `_TOOL_METHOD_NAMES` (get_income 故意不在表)。

## 验证期 fallout 修复 (既有测试对齐新合同/环境)

- `tests/test_mootdx2_reader_offline.py`:
  - 3 个 tdxdir_empty 测试原本隐式依赖 "本机不存在 C:/new_tdx"。
    该目录今天 17:23 在本机出现 (空 vipdoc 骨架, 非本专题创建),
    StdReader 回退构造成功 → 测试退化为 no_data 断言失败 (HEAD 同样复现,
    与本批改动无关)。改为显式指向不存在目录, 确定性覆盖 tdx_not_installed。
  - method_map 注册断言从 getsource(call_tool) 子串改为查
    `_TOOL_METHOD_NAMES` + getattr 可调用。
- `tests/test_mootdx2_high_fixes.py` (第一轮 H4 测试): sync 调用改 bare list;
  旧 "wrapper 静默截断 limit 600→500" 合同被 M1 fail-fast 取代
  (invalid_param 且不打 TDX), 另加 sync 800 硬上限防御用例。
- `tests/test_gateway_package_layout.py`: EXPECTED_MODULES 补 `__main__.py`
  (拆包后补的 `python -m` 入口, 未跟踪文件, 非本会话创建)。

## 配置库注意

YAML 6 个块 (get_index_all / get_workday / get_workday_range / get_minutes /
search_stock_local / indicator_atr) 的文案/TTL 改动**只影响空库 seed**;
已存在的 data/config.db 实体行不自动迁移, 需在控制台配置库面板手改或重建库。

## 变更文件 (本轮)

- src/unihive/api/mootdx2_client.py (与用户未提交 L1 工作同树, L1 区逻辑未碰)
- config/tools_mootdx2.yaml (6 块; 同树另有第一轮 get_income 下线改动)
- tests/test_mootdx2_contract_fixes.py (新, 63 用例)
- tests/test_mootdx2_reader_offline.py (4 处)
- tests/test_mootdx2_high_fixes.py (H4 用例对齐 M1)
- tests/test_gateway_package_layout.py (__main__.py)

未做: 工作区残留的链式 .replace("sh","")... 站点 (minute_data/trade/minute/F10)
统一替换为 _strip_market_prefix 的可选 polish, 无测试要求, 留下次。
