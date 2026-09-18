# 设计 v2: 配置全量解构入 SQLite(YAML 退为出厂模板)

日期: 2026-09-18
状态: **方向已拍板, 待实施**(2026-09-18 脑暴定稿)
取代: [2026-09-18-sqlite-config-overlay.md](./2026-09-18-sqlite-config-overlay.md)
(薄 overlay 方案; P0 已提交 `d37ca6f`, P1 已实现未提交 — **代码不废弃, 作为本方案基础**)

## 1. 目标态(用户拍板)

- **`data/config.db` 是配置的唯一事实源**: 启动从 DB 装配, 日常维护只在控制台,
  不再手改 YAML。打开 SQLite 能看到全部配置实体(8 上游 / 290 工具 / routing / TTL)。
- **YAML 退为出厂模板**: 随 git 走, 仅用于 ① 全新环境 seed; ② 版本升级时出厂更新载体;
  ③ 逃生通道(`UNIHIVE_CONFIG_SOURCE=yaml` 直接读模板运行)。
- 已拍板的四个方向:
  1. 表结构: **实体行 + JSON 列混合**(不全规范化, 不纯文档)。
  2. 出厂升级: **字段级三方合并**(旧模板 / 新模板 / DB 当前值)。
  3. 契约层字段(工具名、参数 name·type·required、routing、mapping、requires_env)
     **控制台可编辑**, 但 lint 门 + 危险操作二次确认 + 热重注册失败回滚。
  4. 出厂更新**手动吸收**: 启动只检测并提示模板版本变化, 控制台「出厂更新中心」预览后一键应用。

## 2. 与 P1 overlay 方案的关系

| P1 资产 | 在 v2 的去向 |
|---|---|
| `storage/config_store.py`(事务/全局 revision/RevisionConflict/WAL) | **保留扩展**, schema_version 1→2, 增表 |
| `core/config_merge.py`(overlay 点路径 merge) | 保留给 yaml 逃生模式; db 模式改走实体装配 |
| 加载链接入点(`_do_initialize` 最前)、`UNIHIVE_CONFIG_SOURCE`/`UNIHIVE_CONFIG_DB` | **原样保留**, 语义从"叠 overlay"变为"从 DB 装配" |
| 控制台两条写路径(enabled / fuyao TTL)、热生效基建(register/remove/set_upstream_enabled) | **原样保留**, 持久化目标改为实体行 |
| golden 等价性测试 | **保留加强**: YAML→实体→dict 的往返必须与 YAML dict 逐字段相等(290 工具) |
| `config_overlay` 表 + 「本地覆盖」tab | 退役; 现有库 0 行, 无数据迁移负担; tab 演化为「出厂更新中心」 |
| P0 `config lint` | 升级为 DB 写入前/升级前的统一校验门 |

## 3. 表结构(schema_version = 2, 混合模型)

```sql
-- 已有, 保留: schema_version / revision(全局乐观锁)
CREATE TABLE config_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- 新增 meta 键: template_version(当前 DB 对齐的出厂模板内容 hash)、seeded_at

-- 出厂模板快照: seed/升级时整包写入, 3-way merge 的"旧模板"基准
CREATE TABLE config_template (
    version     TEXT PRIMARY KEY,          -- 归一化模板 dict 的 sha256
    payload_json TEXT NOT NULL,            -- 完整归一化模板(实体形态)
    imported_at TEXT NOT NULL
);

-- 非实体杂项段: cache(含 ttl)、gateway 调参、logging、fuyao_cache、
-- proxy_settings、disable_dangerous/strict_registry 等全局开关
CREATE TABLE config_section (
    section    TEXT PRIMARY KEY,           -- 'cache' | 'gateway' | 'logging' | ...
    doc_json   TEXT NOT NULL,              -- 该段完整 dict
    origin     TEXT NOT NULL DEFAULT 'builtin',  -- builtin 段目前无 user 段, 预留
    updated_at TEXT NOT NULL,
    revision   INTEGER NOT NULL
);

-- 上游实例(8 行)
CREATE TABLE upstream (
    name        TEXT PRIMARY KEY,          -- 'THS-LOCAL-GATEWAY' 等原名
    type        TEXT NOT NULL,             -- http | mootdx2 | tdx_quant | omni | thsdk | ths_local_gateway
    enabled     INTEGER NOT NULL,          -- 0/1
    config_json TEXT NOT NULL,             -- 类型异型连接块: base_url/servers/dll path/timeout...
    origin      TEXT NOT NULL DEFAULT 'builtin',  -- builtin | user(控制台新增的上游)
    updated_at  TEXT NOT NULL,
    revision    INTEGER NOT NULL
);

-- 工具(290 行, gateway 工具名为主键)
CREATE TABLE tool (
    name            TEXT PRIMARY KEY,      -- 对外 MCP 工具名
    chain_json      TEXT,                  -- routing 优先级链 [上游名...], NULL = 用 mapping 推导
    mapping_json    TEXT NOT NULL,         -- {上游名: 上游方法名}, 手工工具为 {}
    description     TEXT NOT NULL DEFAULT '',
    cache_ttl_key   TEXT,
    publish_mode    TEXT NOT NULL DEFAULT 'default',  -- default | hidden(单工具下架)
    requires_env_json TEXT NOT NULL DEFAULT '[]',
    params_json     TEXT NOT NULL DEFAULT '[]',  -- 有序参数列表, 见 3.1
    annotations_json TEXT NOT NULL DEFAULT '{}',
    source_file     TEXT,                  -- 出厂来源模板文件名(审计/导出用)
    origin          TEXT NOT NULL DEFAULT 'builtin',  -- builtin | user(OpenAPI 导入/手工新增)
    updated_at      TEXT NOT NULL,
    revision        INTEGER NOT NULL
);
```

不建 `tool_param` 子表(混合档位决策): 参数有序、常整体增删, 收进 `params_json`,
3-way 合并在 Python 内按参数名做(§4)。控制台按 JSON 结构渲染表单, 不暴露裸 JSON。

### 3.1 params_json 元素形态

```json
{
  "name": "code",
  "type": "str",                 -- 契约层, 可编辑(危险确认)
  "required": true,              -- 契约层
  "description": "证券代码...",
  "enum": ["1d", "1w"]           -- 可选; 无 enum 键即不约束
}
```

### 3.2 永不入库的内容(bootstrap 边界, 延续 v1 决策)

- DB 路径本身、console `username`/`password_hash`/`session_secret`、gateway host/port/mcp_path
  —— 留在 `config/config.yaml` + 环境变量(锁死在库外防止把自己锁在门外)。
- 密钥: DB 内只允许 `${VAR}` 占位符, 不存明文。seed 时校验, 命中明文密钥拒绝入库。
- `config.yaml` 与 `upstreams.yaml` 的同名段(cache/gateway)按现有 merge 语义先合一,
  归一化后只产生一份实体。

## 4. 出厂升级: 字段级三方合并(手动)

升级在控制台「出厂更新中心」触发, 后端先 `upgrade-db --dry-run` 出预览, 人确认后事务应用。

- **模板版本**: 归一化模板 dict 的 sha256, 存 `config_meta.template_version`;
  启动时与当前 YAML 算出的 hash 不等 → 控制台角标提示"有出厂更新", **不自动改任何东西**。
- 三方: `old` = DB 中 `config_template` 快照; `new` = 当前 YAML 归一化结果;
  `cur` = DB 实体当前值。按标量叶子路径逐格判定:

| 情况 | 判定 | 处理 |
|---|---|---|
| `old == cur`(用户没碰) | 无论 new 变没变 | 采用 `new`(自动吸收) |
| `old != cur` 且 `old == new`(只用户改了) | 保留 `cur` | 自动 |
| `old != cur` 且三方各异(两边都改了) | **冲突** | 默认保留 `cur`, 进冲突列表逐项选: 用我的 / 采用出厂 / 手填 |

实体增删:

- 新模板新增的工具/上游 → 自动新增(builtin); 若名字与 `origin='user'` 行撞名 → 冲突。
- 新模板删除的 builtin 实体: 用户未改 → 自动删(预览列出); 改过 → 冲突(保留或删除二选一)。
- `origin='user'` 的行出厂更新永不触碰。
- `params_json` 列表按参数名对齐后走同一套三方规则; 参数增删同上。

应用流程: 导出当前 DB(JSON 备份, 控制台给下载)→ 后端 lint 全量校验
(C001/T001-T003/M001-M006/W001 + 新规则: 悬空参数、未知上游、enum 非法)→
单事务写入 + 更新 `config_template` 快照 + revision +1 → 触发热重注册。
lint 不过整笔回滚, 库不动。

## 5. 加载链(v2)

```
bootstrap YAML(config.yaml 的 gateway/console 子集) + .env
        │  得到 db_path(data/config.db, UNIHIVE_CONFIG_DB 可覆盖)
        ▼
db 模式(默认):
  config.db 不存在/空 → 自动 seed(YAML 归一化 → 实体行 + template 快照)
  存在 → 实体行装配为现有 config dict(与今天 YAML merge 后同形)
        → validate_config + lint(fail-closed)
        → router / registry / FastMCP(下游零改动)
yaml 模式(逃生, UNIHIVE_CONFIG_SOURCE=yaml):
  完全走今天的 YAML 路径, 不碰 DB
```

装配器(`core/db_assembler.py`)是 merge 的逆操作, 与归一化器(`core/template_normalize.py`)
成对, 由 golden 测试锁死往返等价。stdio / HTTP 双模同源。

## 6. 控制台编辑模型

- 描述层字段(description / param description / param enum / cache_ttl_key / publish_mode):
  普通保存, 即写即热生效。
- **契约层字段**(工具改名、参数 name/type/required、chain、mapping、requires_env、
  上游连接块、上游增删): 同一保存接口, 但前端标红 + ElMessageBox 强确认
  (列明影响面: 多少 MCP 客户端可见契约变化), 后端要求 `confirm_dangerous=true`,
  写前 lint 单实体, 工具类走 remove_tool + register_tool, **重注册失败回滚该行**。
- 所有写走现有全局整数 revision 乐观锁; 控制台不再有"恢复出厂默认"按钮
  (v1 overlay tab 的职能)—— 取代它的是更新中心里"该字段出厂值 X, 一键采用"。
- 只读浏览器先行(P2), 编辑表单 P3。

## 7. CLI

- `python -m unihive config seed-db` —— YAML 归一化入库(空库; `--force` 拒绝非空)。
- `python -m unihive config upgrade-db [--dry-run]` —— 3-way 预览/应用。
- `python -m unihive config export [--format yaml|json]` —— DB 全量导出(备份/git 审计)。
- `python -m unihive config lint` —— P0 已有; v2 后同一套 lint 函数同时服务
  YAML 文件与 DB 实体。

## 8. 生成器(tdx_quant 1152 行)

`gen_tdx_quant_tools.py` 继续产 YAML 模板产物(留在仓库当出厂模板),
升级时经 3-way 合并入库 —— 本地对 tdx_quant 工具的修改由合并保护,
不再有"重生成冲掉手改"问题(重生只改模板侧, 合并器判定冲突)。

## 9. 分期

### P2 — DB 事实源切换(只读)

1. `template_normalize`(YAML dict→实体) + `db_assembler`(实体→dict) 成对实现;
   golden 往返等价: ① YAML→实体→装配 ≡ 今天的 effective dict; ② 290 工具全字段相等。
2. ConfigStore schema v2 扩表(section/upstream/tool/template), ConfigStore 扩实体 CRUD。
3. 空库自动 seed; 加载链 db 模式装配; yaml 逃生保留; export 命令。
4. 控制台配置页改读 DB 实体(只读形态), enabled/TTL 写路径落实体行;
   overlay tab 下线; lint 覆盖 DB 实体。
5. 回归: 现有 734 backend / 240 web 测试适配保持绿; seed 后真实启动 tools/list = 290。

### P3 — 编辑 + 出厂更新中心

6. 上游 / 工具 / 参数 / routing / section 的编辑表单(描述层 + 契约层危险确认)。
7. 单实体 lint + 热重注册失败回滚(复用热开关基建)。
8. 更新中心: 版本差异角标、自动吸收/冲突/增删预览、JSON 备份下载、事务应用。

### P4 — OpenAPI 重新导入

9. FQGate OpenAPI diff → `origin='user'` 工具行热注册 → 导出 YAML 片段,
   评审并入出厂模板后转为 builtin(原 v1 P3, 模型从 tool_custom 表简化为 origin 列)。

## 10. 风险

| 风险 | 缓解 |
|---|---|
| 装配/归一化不对称导致启动配置漂移 | golden 双向往返测试为硬关卡; seed 后 tools/list 与 yaml 模式逐项比对 |
| 3-way 合并误判(尤其 params 列表/JSON 段) | 全部判定先 dry-run 可视化, 手动应用; 应用前自动导出备份; 冲突默认保留用户值 |
| 契约层可编辑改挂上游调用 | lint 门 + 强确认 + 热重注册失败回滚; mapping 悬空/类型非法当场拒绝 |
| DB 损坏即不可启动 | yaml 逃生模式不依赖 DB; export 备份; 空库可从模板秒级重建 |
| console 凭证入库锁死自己 | bootstrap 段物理不入库, 模板含明文密钥拒绝 seed |
| v1 P1 已写代码返工 | 存储/事务/revision/热生效/golden 全复用; 仅替换事实源与 merge 形态 |
