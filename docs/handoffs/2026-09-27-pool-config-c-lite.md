# Pool Config 可配置化 — 方案 C lite (handoff 2026-09-27)

> 会话因 context 紧张中断, C7+C8 留新会话。**未 commit 未 push 未 build 验证**,工作树脏。

## 任务背景

用户 2026-09-27 要求把 mootdx2 / tdx_quant 阻塞线程池 + Reader 闸门做成可配置项。
头脑风暴 4 方案 (A 仅 env / B+只读展示 / **C lite 入库** / D+运行时热重载),
用户选 **C lite**: config_db 入库 + YAML 字段 + 重启生效, 不做控制台编辑表单。
验证三选: 校验 reader_limit ≤ worker / PipelinePanel 验证 metrics 一致 / 手动 smoke test。

## 设计概要

**优先级链**: `config.db / yaml (cfg.upstreams.<name>.<field>)` >
            `env (UNIHIVE_MOOTDX2_* / UNIHIVE_TDX_QUANT_*)` >
            `code default`

**新增字段** (`config/upstreams.yaml`):
- `mootdx2.reader_limit: 4` — Reader 闸门 (asyncio.Semaphore 容量)
- `mootdx2.worker_threads: 8` — 阻塞线程池 worker 数
- `tdx_quant.worker_threads: 10` — 阻塞线程池 worker 数

**校验**: `mootdx2_reader_limit > worker_threads` 时 WARNING 不阻断启动 (Reader 在 worker 上跑,worker 不够 Reader 永远跑不满)。

**生效时机**: **仅启动期** (asyncio.Semaphore / ThreadPool 不能扩缩,运行时改需重启)。

## 已完成改动 (工作树,未 commit)

### 1. `config/upstreams.yaml`
- `mootdx2` 段加 `reader_limit: 4` + `worker_threads: 8` (line 60-66)
- `tdx_quant` 段加 `worker_threads: 10` (line 81)

### 2. `src/unihive/models/mootdx2_config.py`
- `MooTDX2Settings` 加 `reader_limit: int | None = None` 字段 (line 73-76)
- `from_dict` 处理 reader_limit (钳到 ≥1, 非法值回落 None 走 env) (line 84-92)
- `to_dict` 加 reader_limit 输出 (line 124)

### 3. `src/unihive/api/mootdx2_client.py`
- 顶部加 `import os` (line 7)
- `_READER_SEMAPHORE_LIMIT = 4` → 走 `settings.reader_limit` > env > 默认 4 (line 463-484)
- `_reader_semaphore = asyncio.Semaphore(self._READER_SEMAPHORE_LIMIT)` 保留

### 4. `src/unihive/gateway_server/__init__.py`
- `MooTDX2Settings(...)` 显式传 `reader_limit=cfg.get("reader_limit")` (line 312-313)
- `_apply_gateway_tuning()` 末尾调 `_apply_pool_config()` (line 263-264)
- 新增 `_apply_pool_config(self)` 方法 (line 266-291):
  - 调 `resolve_pool_config(self.config)` + `validate_pool_config()`
  - `_mootdx2.set_size_override(worker_threads)` + `_tdx_quant.set_size_override()`
  - 缓存 `self._pool_config` (frozen dataclass)
  - logger.info 输出三值

### 5. `src/unihive/core/blocking_pool.py`
- `_PoolBundle.__slots__` 加 `_size_override` (line 113-126)
- `__init__` 初始化 `_size_override = None`
- 新增 `set_size_override(n: int)` 方法 (line 131-148):
  - 已创建 executor 时 WARNING + 忽略 (Semaphore / Pool 不能扩缩)
  - 否则钳到 ≥1 存 `_size_override`
- `get()` 优先级: override > env > default (line 150-160)
- `snapshot()` 反映 override (前端 metrics 一致) (line 162-176)

### 6. `src/unihive/core/pool_config.py` (新文件, ~110 行)
- `PoolConfig` frozen dataclass (3 字段)
- 常量: `DEFAULT_MOOTDX2_READER_LIMIT=4` / `DEFAULT_MOOTDX2_WORKER_THREADS=8` /
        `DEFAULT_TDX_QUANT_WORKER_THREADS=10`
- 常量: `ENV_MOOTDX2_READER_LIMIT=UNIHIVE_MOOTDX2_READER_LIMIT` 等
- `_resolve_int(cfg, env_var, default, *, minimum=1)` 三级回退 + 钳到 ≥1
- `resolve_pool_config(cfg: dict | None) -> PoolConfig`
- `validate_pool_config(cfg) -> list[str]` (reader_limit > worker_threads → WARNING)

## 待做 (新会话)

### C7 — `tests/test_pool_config_priority.py` (~150 行)
覆盖:
1. cfg 优先 (worker_threads=12 from cfg, env=20, default=8) → 12
2. cfg 缺失, env 优先 → env 值
3. cfg+env 都缺失 → default
4. cfg 非法值回落 env (WARNING logged)
5. env 非法值回落 default (WARNING logged)
6. cfg 缺失字段但 upstream dict 存在 (eg. tdx_quant cfg 缺 worker_threads) → default
7. cfg=None (yaml 逃生模式但 cfg 尚未装配) → 全走 env / default
8. validate: reader_limit > worker → warning 列表非空 + 文案含 "Reader"
9. validate: reader_limit == worker → 无 warning
10. validate: reader_limit < worker → 无 warning

补充 `tests/test_blocking_pool_set_size_override.py` (~80 行):
- set_size_override 在 executor 未创建时生效 (snapshot capacity == override)
- set_size_override 在 executor 已创建时 WARNING + 忽略 (monkeypatch executor 模拟)
- 非法值 (字符串/0/-1) 钳到 ≥1

### C8 — Build 验证
```bash
cd D:/fintech_workspace/unihive/web && npm run build  # 不需要 (仅后端改动)
cd D:/fintech_workspace/unihive && .venv/Scripts/python.exe -m pytest tests/test_pool_config_priority.py tests/test_blocking_pool_set_size_override.py -v
```
应全绿。后续跑 `pytest tests/ -q` 守回归 (Round-12 末态 1750, 当前未计入本批新测试)。

## 关键设计决策记录

### 为什么 cfg > env (而非 env > cfg)
当前现状 (2026-09-18 起): config.db 是事实源, YAML 是模板, env 用于基础设施级调参。
新优先级让 cfg 优, env 兜底:
- 控制台编辑后, 字段进 config.db, 启动读 cfg (env 失效)
- env 仍作为临时逃生 (CI / docker / 临时测试)
- 与 Round-12 簇H "yaml 逃生模式" 风格对齐 (逃生降级而非覆盖)

### 为什么不做运行时热重载 (方案 D 被拒)
`asyncio.Semaphore` 创建后不能 resize (CPython 实现),
worker 池扩缩需 reinit `DaemonBlockingExecutor` + 中断 in-flight 调用 + L1 缓存击穿。
UX 收益: 用户改完立即生效。
代价: 6+ 文件改动 + reload API + 风险高。
**留未来**: 等 asyncio 库支持 `Semaphore.resize` 或自实现。

### 为什么 thsdk 不动
thsdk 是单 worker (1, daemon),硬编码 `default_size=1` (blocking_pool.py:167)。
单 worker 是设计约束 (DLL 内部 RLock 串行, worker 多无收益且增僵尸风险 — 2026-09-22 thsdk_wedge 修复)。
本批不动 thsdk。

### 校验为什么不 fail-fast
`reader_limit > worker_threads` 是 sub-optimal 但非致命:
- Reader 闸门是信号量,永远有 permit (worker 占用着就没permit空跑)
- TDX 库仍然能服务 (只是不能并跑到 worker 上限)
- WARNING 足够,阻断启动反用户体验差
- 用户主动决策改小 reader_limit 或加大 worker_threads

## 验收清单 (新会话)

完成后逐项打勾:

- [ ] pytest 新增测试全绿 (C7)
- [ ] 现有 pytest 全量无回归 (`pytest tests/ -q`)
- [ ] `mootdx2_client.py` 启动期 cfg 字段走通 (debug log: `reader_limit=4`)
- [ ] `blocking_pool` set_size_override 生效 (executor._max_workers == cfg 值)
- [ ] 重启后 `get_metrics()` `reader_gate.capacity == cfg.reader_limit`
- [ ] PipelinePanel 显示 `capacity == cfg.reader_limit` (前端 metrics 一致)
- [ ] WARNING 触发: cfg reader_limit=10 + worker_threads=4 → 启动日志有 [pool-config] warning
- [ ] env 兜底: cfg 缺字段 + env 设值 → 用 env 值
- [ ] 手动 smoke test (用户要求): 改 yaml reader_limit=6 + 重启 → PipelinePanel 显示 6

## 风险与守卫

- **`_PoolBundle.set_size_override` 已创建后 WARNING**: 防止用户误以为热改生效,提示需重启
- **`_READER_SEMAPHORE_LIMIT` 创建 Semaphore**: 非法 cfg 值回落 None → env → 默认 4,任何路径都至少 1 permit
- **validate 不 fail-fast**: WARNING 列表而非 raise, 让用户决策
- **`_apply_pool_config` 在 4 个入口重放**: `__init__` / yaml 逃生 / db 装配 / config_upgrade, 由 `_apply_gateway_tuning` 末尾兜底
- **mootdx2 client 与 blocking_pool 各自读 cfg**: 同一份 cfg 解析,但语义独立 (Reader 是 Semaphore, worker 是 ThreadPool)
- **thsdk 不受影响**: 仍硬编码 1 worker,后续若要可配单独 commit

## 待用户决策

- 是否本批一起 commit, 还是 C7+C8 完成后单 commit
- 是否手动 smoke test (重启网关看 PipelinePanel 是否反映新值)
- 是否后续加控制台编辑表单 (方案 D lite 升级版)