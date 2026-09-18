# 设计: YAML 基线 + SQLite 覆盖层的配置体系

> **2026-09-18 方向更新: 本文档的薄 overlay 终态已被 v2 取代** —
> 用户明确诉求是"配置全量解构入 DB, 平时不维护 YAML, YAML 仅作模板"。
> 见 [2026-09-18-sqlite-config-full-database.md](./2026-09-18-sqlite-config-full-database.md)。
> 本文档的 P0(已提交 d37ca6f)/ P1(已实现未提交)代码不废弃, 是 v2 的存储/事务/热生效/golden 基础。

日期: 2026-09-18
状态: P0 + P1 已实现 (2026-09-18, 未提交); 终态方向被 v2 全量入库取代, P2/P3 按 v2 重定义

## 1. 背景与动机

现有配置全部在 YAML 中:

- `config/upstreams.yaml` (317 行): cache 段、8 个上游连接块、gateway 调参、`upstream_tool_mapping`、内联的 67 个 fuyao 工具 spec
- `config/config.yaml` (45 行): 旧版覆盖 (console auth 等), 合并在后、优先级更高
- 5 个工具文件: `tools_tdx_quant.yaml` (1156, 生成) / `tools_mootdx2.yaml` (1235) /
  `tools_thsdk.yaml` (947) / `tools_omni.yaml` (148) / `tools_ths_local.yaml` (98)
- 合计约 3948 行 / ~291 个工具

核心动机 (用户拍板): **便于维护**, 且明确预期 **会在控制台直接编辑工具的描述/参数**,
未来希望支持 FQGate 一类上游的 OpenAPI 重新导入。

现状维护痛点:

1. 控制台写 YAML 已不可持续: `save_fuyao_cache` 整文件 `safe_dump` 会抹掉全部注释和手工对齐列;
   `save_upstream_enabled` 为此退化为正则文本手术。
2. tdx_quant 工具文件是生成器产物却入库 git, 与手改内容混在一起。
3. 顶层 mapping 与生成文件内联 mapping 双份事实, 靠加载时隐式合并。
4. 配置错误要等启动才暴露, 没有静态校验。
5. FQGate 87 工具为手工导入, 上游 OpenAPI 变更后无法追踪差异。

## 2. 关键决策

1. **YAML 是出厂基线 (builtin), 随版本走, git 可审; SQLite 只存"与出厂不同的部分" (overlay)。**
   不做全量搬家——避免升级时新默认值/新工具被本地库永久挡住。
2. **引导链与密钥不进 DB**: DB 路径、console 账号/session_secret、`${ENV}` 密钥占位解析
   全部留在 bootstrap YAML / 环境变量。DB 中若出现密钥字段, 只允许存 `${VAR}` 占位符, 不存明文。
3. **可编辑面收窄**: 控制台只允许改"对外描述层"字段; 上游调用契约字段
   (params 的 name/type/required、routing、upstream_tool_mapping、requires_env) 禁改。
4. **运行时消费的 dict 形态不变**: router / registry / tool_loader 的下游契约不动,
   只在 dict 的**来源**上增加一层 merge。
5. 分四期落地, P0 与 DB 无关、无论后续如何都先做。

## 3. 总体结构

```
出厂态 (builtin, git 跟踪, 开发者维护)
  config/upstreams.yaml + config/config.yaml + tools_*.yaml
        │  config_loader + tool_loader (现状, 只读)
        ▼
  baseline config dict + baseline specs
        │
运营态 (SQLite overlay, data/config.db, 控制台维护, 不进 git)
  config_overlay / tool_override / tool_custom
        │  core/config_merge.py (纯函数, key 级 merge)
        ▼
  effective config dict + effective specs
        │  validate_specs (现状校验, 前移到 merge 之后)
        ▼
  router / registry / FastMCP  (下游零改动)
```

逃生通道:

- 启动开关 `--config-source yaml` (或 env `UNIHIVE_CONFIG_SOURCE=yaml`) 完全绕过 overlay, 用于故障隔离。
- 控制台"导出覆盖为 YAML/JSON"与"清空全部覆盖恢复出厂"。
- `data/config.db` 损坏可删库, 从 YAML 重新起步 (丢失的只是本机定制, 且可事先导出)。

## 4. 表结构草图

```sql
-- 段级标量/对象覆盖: 上游 enabled、fuyao TTL override、未来各类调参
CREATE TABLE config_overlay (
    path       TEXT PRIMARY KEY,           -- 'upstreams.fuyao_meta.enabled'
                                           -- 'fuyao_cache.tools.<tool>'
    value_json TEXT NOT NULL,              -- JSON: true / 60 / {"ttl": 3600}
    revision   INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

-- 工具级覆盖 (按字段 merge, 不覆盖整工具)
CREATE TABLE tool_override (
    tool_name    TEXT PRIMARY KEY,
    patch_json   TEXT NOT NULL,            -- JSON Merge Patch, 见 4.1
    publish_mode TEXT NOT NULL DEFAULT 'default',  -- 'default' | 'hidden' (单工具下架)
    revision     INTEGER NOT NULL,
    updated_at   TEXT NOT NULL
);

-- 基线中不存在的工具 (OpenAPI 重新导入发现的新工具 / 手工新增)
CREATE TABLE tool_custom (
    name       TEXT PRIMARY KEY,
    origin     TEXT NOT NULL,              -- 'import:ths_local' | 'user'
    spec_json  TEXT NOT NULL,              -- 完整 spec
    revision   INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE config_meta (
    key   TEXT PRIMARY KEY,                -- 'schema_version'
    value TEXT NOT NULL
);
```

`config_audit` (section/key/diff/时间) 列为 P3 可选项, 单机单用户场景先不建。

### 4.1 tool_override 的 patch 形状

只允许出现"对外描述层"字段, 采用 JSON Merge Patch (RFC 7386) 语义,
按字段与基线合并——用户没改的字段继续继承基线, 升级时基线的新描述能自然流入:

```json
{
  "description": "用户改写后的工具描述",
  "cache_ttl_key": "fundamentals",
  "params": {
    "code": { "description": "证券代码, 如 600519.SH" },
    "period": { "enum": ["1d", "1w", "1M"] }
  }
}
```

允许出现在 patch 中的字段 (白名单, 写入时校验):

| 字段 | 说明 |
|---|---|
| `description` | 工具描述 (MCP clients 主要可见面) |
| `cache_ttl_key` | 缓存档位 |
| `params[*].description` | 参数描述 |
| `params[*].enum` | 枚举值域 (网关侧对外约束) |
| `publish_mode` (存列上, 不在 patch 内) | 单工具强制下架 |

禁改 (契约层): `name`、`routing`、`upstream_tool_mapping`、`data_source_type`、
`requires_env`、`params[*].name/type/required`、`annotations` (P2 阶段只读展示;
如确需改分类, 后续单开评审)。

`params` 按参数名 merge: 基线中已不存在的参数, 其 patch 在 lint 中报 orphan 警告并忽略,
不阻断启动。

## 5. 模块划分

新增:

- `src/unihive/storage/config_store.py` — async SQLAlchemy + aiosqlite,
  复用 `storage/cache.py` 的既有模式 (WAL、busy_timeout、orjson、operation_timeout)。
  方法: `get_overlay(path)` / `upsert_overlay(path, value, expected_revision)` /
  `all_overlays()` / `delete_overlay(path)`; tool_overlay / tool_custom 同理。
- `src/unihive/core/config_merge.py` — **纯函数**, 不碰 DB 不碰 IO, pytest 直测:
  `apply_config_overlays(baseline: dict, rows: list) -> dict`、
  `apply_tool_overrides(specs: list[dict], rows: list) -> list[dict]`。
- `python -m unihive config lint` (或 scripts/config_lint.py) — 静态校验, 见 §8。

改造:

- 加载链: `load_config` + `load_all_tools` 产出 baseline 后, 网关初始化处
  (stdio 与 HTTP 共用的 `_do_initialize`) 读 overlay 并 merge, 再走现有 validate/register。
- 控制台写路径:
  - `save_upstream_enabled` / `rewrite_upstream_enabled` (正则文本手术) → 删除, 改 store。
  - `save_fuyao_cache` (整文件 safe_dump) → 删除, 改 store。
  - 乐观锁从"两文件 size+mtime 的 sha256 截 16 位"改为行级整数 `revision`。

## 6. 热生效

复用 2026-09-18 已落地的热开关基建 (`register_tool` 单工具注册、
`mcp.local_provider.remove_tool`、`_publish/_unpublish_tools_locked` 的部分失败收集与
reconcile 自愈):

- enabled / TTL / 调参覆盖写入: 更新内存 config dict 对应路径; TTL 类即时生效,
  enabled 走现有 `set_upstream_enabled` 编排 (但持久化目标换成 store)。
- 工具描述/参数 patch 写入: 事务内 upsert → 对 merge 后的单条 spec 跑校验
  → `remove_tool(name)` + `register_tool(spec)`; 重注册失败则回滚 overlay 事务,
  线上保留旧工具, 返回明确错误。
- `publish_mode=hidden`: 等同该工具在上游链上被摘空, 走 unpublish; 恢复 default 走 publish。
- tool_custom 新增: 校验 + 注册; 删除: 反注册 + 删行。
- 引导类配置 (DB 路径本身、console auth) 改动仍提示重启, 不做热生效。

## 7. OpenAPI 重新导入 (P3)

以 FQGate (`http://127.0.0.1:17281/openapi.json`) 为首个场景:

1. 拉取上游 OpenAPI, 按现有导入映射逻辑生成候选 spec。
2. 与 effective 目录做三方 diff: **新增** (基线和 overlay 都没有) /
   **结构变化** (params 增删、required 变化) / **消失**。
3. 处理策略:
   - 新增 → 控制台预览确认后写入 `tool_custom` (`origin='import:ths_local'`), 热注册。
   - 基线工具的结构变化 → **只报告不自动改** (基线归 git 管);
     控制台提供"导出 YAML patch", 由人评审后提交进 `tools_ths_local.yaml`。
   - 消失 → 报告; 若为 tool_custom 来源则提示可删除。
4. tool_custom 是"晋升前的缓冲区": 验证稳定后可一键导出为 YAML 片段,
   评审入库即转为 builtin, 同时删行。

## 8. config lint

P0 先建 (此时只校验 YAML), P1 后增加 overlay 维度:

- 现有 `validate_specs` 全量结果。
- `upstream_tool_mapping` 双向一致: 顶层映射与生成文件内联映射指向不存在的工具/上游时报错。
- overlay 引用的 path / tool_name / param_name 在基线中存在 (orphan 警告)。
- patch 只含白名单字段; `publish_mode=hidden` 仍被 routing 引用时提示。
- tool_custom 的 name 不与基线重名; spec 自洽 (params/routing 指向真实上游)。
- 退出码非零即失败, 可挂启动前与 CI。

## 9. 分期

### P0 — 与 DB 无关的前置清理 (无论后续如何都做)

1. ~~引入 `ruamel.yaml` round-trip~~ — **决定跳过**: P1 后两条 YAML 写路径整体删除,
   过渡代码写完即弃, 不划算 (2026-09-18 拍板)。
2. `config lint` + 等价性/一致性测试。**已落盘 (2026-09-18, 未提交)**:
   - `src/unihive/core/config_lint.py` 纯函数 `lint_config` (C001/T001-T003/M001-M006/W001);
   - `python -m unihive config lint` (`cli.py` + `__main__.py`),
     默认 non-strict env、含 requires_env 门控 spec 全量校验、`--warnings-as-errors`;
   - `merge_config_overlay` 抽入 config_loader, gateway 合并 config.yaml 改调同一函数;
   - `load_all_tools(..., include_env_gated=True)` 供 lint 全量加载;
   - 实测真实配置首跑 0 error / 14 warning, **当日全部按代码实际清掉, 复跑 0 issue**:
     - 孤儿映射 `fund_market_snapshot` → 补齐 gateway spec (fuyao_fund 上游实测存在
       `get_fund_market_snapshot(thscode)`, 当日真实调用验证返回 510300.SH ETF 快照);
     - 13 个 tdx_quant codegen 类型偏差 → generator 加 `normalize_param_type`
       (裸 `List`→`List[str]`、`Optional` 按 SKILL.md 文档语义覆盖、`function`
       参数丢弃不发布、`Any`/结构数组表达为 `List[dict]`) + registry
       `resolve_python_type` 统一识别任意 `List[*]`/`Dict` (数组/对象原样透传
       tqcenter, 不再静默回落 str);
     - 复验: `config lint` **0 issue / 290 spec**, codegen `--check` 无漂移,
       pytest **648 全绿**。

### P1 — 薄 overlay 上线 (运营态: enabled + TTL)

3. ~~`config_store.py` + 建表/迁移 (`schema_version`) + `data/config.db` (gitignore)。~~
   **已落 (2026-09-18)**: `storage/config_store.py` — `config_overlay` + `config_meta`
   (全局整数 `revision`, schema_version=1); WAL / synchronous=NORMAL / busy_timeout=5000;
   `apply_changes(upserts, deletes, expected_revision)` 事务化, 同值 upsert / 删不存在行
   / 空 reset 不 bump; 点路径字符集 `[A-Za-z0-9_-]+(.[…])*` (需容纳 `THS-LOCAL-GATEWAY`)。
4. ~~`config_merge.py` 纯函数 + 单测; 加载链接入, `--config-source` 逃生开关。~~
   **已落**: `core/config_merge.py` (`overlay_tree` + `apply_config_overlays`, 深拷贝不 mutate,
   结构冲突 fail-closed); 加载链 `_do_initialize` 最前面 merge 后再跑 `validate_config`;
   env `UNIHIVE_CONFIG_SOURCE=yaml` 逃生、`UNIHIVE_CONFIG_DB` 覆盖默认路径
   (`data/config.db`, 已随 `data/` gitignore); console_api 持行镜像保证同步读零 DB IO。
5. ~~**等价性测试**: 空 overlay 时 effective dict/specs 与现状 YAML 路径 golden 全量一致
   (291 工具、mapping、TTL), 不一致不许合并。~~ **已落**:
   `tests/test_config_overlay_golden.py` — 空覆盖 = 与 YAML dict 深等;
   `load_all_tools` 全字段相等, 实测 **290 spec**; 真实空库 roundtrip;
   yaml 逃生模式 server.config_store is None; enabled 覆盖 seed 后启动翻转且兄弟字段保留。
6. ~~两个控制台写路径切 store, 删除 YAML 写法; 前端乐观锁改 revision。~~ **已落**:
   `save_upstream_enabled` / `save_fuyao_cache` 全 async 化、只存差异 (与出厂相同即删行);
   删掉 `rewrite_upstream_enabled` / `_unquote_yaml_key` 正则文本手术;
   `models/fuyao_cache.py` 定稿单层 tools (StrictInt, extra=forbid, 0=显式不缓存);
   顺带修掉 `fuyao_ttl_for` 运行时读旧 endpoints shape 导致 TTL override 从未生效的潜伏 bug;
   乐观锁改整数 revision (yaml 模式回落 16 位文件 hash, 前端 number|string 双收);
   503 `config_store_unavailable` 贯穿开关/TTL/覆盖管理三类写端点。
7. ~~控制台"覆盖列表 / 单条恢复默认 / 清空全部"。~~ **已落**:
   `GET /api/config/overlays`、`DELETE /api/config/overlays/{path}`、
   `POST /api/config/overlays/reset` (静态 reset 路由排在 {path} 前);
   enabled 行删除后按出厂值做运行时热收敛, 失败进 warnings 不阻断;
   前端配置页新增「本地覆盖」tab (`OverlayPanel.vue` + `overlays.ts` 纯函数)。
   测试: backend **734 passed / 1 skipped** (新增 ~86 例), web vitest **240 passed**,
   `npm run build` (vue-tsc) 绿, `config lint` 0 issue。

### P2 — 工具描述/参数可编辑

8. 工具目录编辑 UI (复用 MCP 浏览器组件): description / param 描述 / enum / ttl_key /
   单工具 hidden 开关; 禁改字段只读。
9. 白名单校验 + 单 spec merge 校验 + 热重注册 (含失败回滚)。
10. 编辑历史依赖行内 revision + updated_at (审计表缓建)。

### P3 — 重新导入与导出

11. FQGate OpenAPI diff 预览 → tool_custom 入库/热注册 → YAML patch 导出晋升。
12. (可选) config_audit、全量导出备份。

## 10. 风险

| 风险 | 缓解 |
|---|---|
| 三层合并引入认知负担 | merge 全部集中在无 IO 纯函数 + golden 等价性测试; `--config-source yaml` 可秒级回退 |
| 失去 git diff 审查面 | 出厂基线仍在 git; 用户定制面极小 (overlay 行), 提供导出; P3 审计表 |
| 升级合并覆盖用户手改 | patch 按字段 merge 而非整工具覆盖; 基线删除的参数/工具走 lint orphan 提示 |
| 控制台改 enum/描述产生对外契约漂移 | 可编辑白名单 + 写入即校验 + 热重注册失败回滚; 契约层字段物理上不可写 |
| SQLite 多进程/损坏 | 单进程假设同 cache.db; WAL + busy_timeout; 删库可重建, 覆盖可导出 |
| stdio 模式无控制台 | overlay 加载对两模式同源生效; 写 API 在 stdio 下 503 (现状语义) |
| 生成器 (gen_tdx_quant) 仍写 YAML | 基线本就归 git/生成器管, 与 overlay 无冲突; P3 后可评估生成器直连"导出 patch"流 |

## 11. 待确认/遗留

- ~~DB 文件位置~~: 已定 `data/config.db` (与 `data/board.db` 同类, 随 `data/` gitignore),
  可用 `UNIHIVE_CONFIG_DB` 覆盖; 引导配置不进 DB。
- ~~P0 是否上 ruamel 过渡~~: 已拍板跳过, 直接 P1 删 YAML 写路径。
- `annotations` 是否长期保持只读 — P2 再评审, 当前禁改。
- P1 遗留 (小): console 读路径目前不暴露 fuyao 工具的「基线值 vs 覆盖值」差异
  (GET 返回合并后的生效值); 覆盖来源在「本地覆盖」tab 可查, P2 做工具编辑时再合并视图。
