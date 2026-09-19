# SQLite 配置实体化 P2 — 加载链切 DB 事实源 + 实体浏览 (2026-09-18)

> 接续 `2026-09-18-sqlite-config-overlay-p1.md` (薄覆盖层, 已被本方案取代)。
> 用户决策原话: 配置全部解构入数据库, 平时不维护 YAML, YAML 只作出厂模板保留。
> 本批 **未提交** (等用户明确指令再 commit); P1 的 2 个 commit (9b1aa8b / 8b82b61) 已在树里。

## 架构 (v2)

```
config/upstreams.yaml + tools_*.yaml + config.yaml (出厂模板)
        │ build_template (template_normalize, 明文密钥 fail-closed)
        ▼ 空库自动 seed (ensure_seeded, 一次性; is_seeded 判定)
data/config.db  ← 唯一事实源 (日常维护只在控制台, 不碰 YAML)
  config_template (出厂快照, P3 三方合并的 old base)
  config_section / upstream / tool (实体行 + JSON 列, origin=builtin|user)
  config_meta.revision (全局整数乐观锁, 0 合法)
        │ load_template_from_store → assemble_config (db_assembler)
        ▼ merge_bootstrap(只合 console + gateway 监听键) → resolve env → validate fail-closed
   GatewayServer.config (290 spec 全量在 config['tools'])
```

- 模式开关: `UNIHIVE_CONFIG_SOURCE=yaml` 逃生 (纯 YAML, 不建库不写库); 默认 db。
  `UNIHIVE_CONFIG_DB` 覆盖库路径 (默认 `data/config.db`)。
- 运行时 env 门控 `filter_specs_by_env` 在装配结果上执行 (与 yaml 链一致);
  enabled 隐藏链 `filter_specs_by_enabled` 不变。
- console_api 与 gateway 共享同一 live config dict, enabled/TTL 热改即时双向可见。
- `/api/config` db 模式 **不携带 tools** (290 spec 太大); 工具清单走既有
  `/api/interfaces`, ConfigView 合成给 ToolsPanel / FuyaoCachePanel。

## 本批改动

### 后端
- `core/template_normalize.py` + `core/db_assembler.py`: 实体 normalize ↔ assemble 纯函数对
  (#39, golden 双向往返, 14 tests)。
- `storage/config_store.py`: schema v2 扩表 (CREATE TABLE IF NOT EXISTS 平滑升级 v1 库);
  实体 CRUD + `seed_from_bundle` / `load_bundle` / 乐观锁; 新增 `entities_summary()`
  (只读元数据, 不读 config_json/spec_json 大列)。
- `core/config_db.py`: `build_template` / `ensure_seeded` / `seed_database` /
  `load_template_from_store`; 明文密钥正则拒绝 seed。
- `gateway_server.py`:
  - `__init__` 保留 YAML 加载作预检基线 (rate limiter / validate 同构);
  - `_do_initialize` 起手 `_load_db_config()`: seed → 装配 → bootstrap 合并 →
    resolve env → validate, 失败关 store 并抛出 (fail-closed, 不在错配置上建连);
  - `_load_all_tools()` db 模式直接读 `config['tools']` + env 门控, 不回读 tools_*.yaml;
  - `_uses_db_source()` 兜底 `__new__` 旁路 __init__ 的测试路径 (回落 env 判定);
  - gateway 调参抽 `_apply_gateway_tuning()`, 装配后重放一次。
- `utils/console_api.py`:
  - `set_config_db(store, revision)` 注入; load_config / get_interfaces /
    _build_source_map 三读路径支持实体模式;
  - save_fuyao_cache → `upsert_section(origin='user')`; save_upstream_enabled →
    `update_upstream_enabled`; RevisionConflict → VersionConflict;
  - **P1 overlay HTTP 路由全部退役** (`/api/config/overlays*`), 服务函数
    (list/delete/reset + reconcile) 作为 yaml 逃生/兼容内部路径保留, 仅测试注入可达;
  - 新增 `GET /api/config/entities` (db 摘要; yaml 模式 200 `{source:'yaml'}`)。

### 前端
- 删除 OverlayPanel.vue / overlays.ts / overlays.spec.ts / api overlay 三件套。
- 新增「配置库」tab: `DatabasePanel.vue` + `entities.ts` (originLabel/
  originTagType/shortTemplateVersion/formatUpdatedAt/isDbEntities/userModifiedCount)。
  展示 revision / 出厂模板 hash+导入时间 / 9 上游实体 (origin tag, updated_at) /
  配置段 / 290 工具按 origin 聚合; yaml 模式渲染逃生提示横幅。
- ConfigView: 并行拉 `/api/config` + `/api/config/entities` + `/api/interfaces`,
  `displayConfig` 不可变合成 (db 模式用 interfaces 补 tools)。
- FuyaoCachePanel 文案改为实体段措辞。
- build finalize 已重新生成 console.html + static/console/assets。

### 测试
- 新增 `tests/test_config_db_load_chain.py` (5: 空库 seed/装配、实体编辑后装配、
  装配校验 fail-closed、明文密钥拒绝 seed、yaml 模式完全不碰库)。
- 新增 `tests/test_config_entities_api.py` (5: summary 字段/用户写入反映/
  handler db+yaml/路由挂载与 overlay 路由不得回归)。
- `tests/conftest.py`: autouse `_isolate_config_source` — 测试进程默认钉 yaml +
  tmp 库; v2 用例自行 delenv + 指库。
- 裁剪 `test_config_overlay_api.py` 为纯服务层测试; golden 文件同步。
- 后端 **760 passed / 1 skipped** (忽略两个既有 perf 环境用例);
  web **237 passed (13 files)**, `npm run build` vue-tsc 绿。

## 实测 (API 层, 未开浏览器)
- 临时库启动: `Seeded config.db ... 9 upstreams, 290 tools, template=375d13cf7ab4`,
  `Registered 290 tools from config`, MCP HTTP initialize + tools/list = **292**
  (290 实体 spec + get_server_status/get_health)。
- 同库二次启动无 Seeded 日志, revision=0 不重 seed。
- `/api/config/entities` 未登录 401 (console auth 边界不变); overlay 路由同 401
  (单测已证路由表内不存在)。

## 后续
- **P3**: 控制台实体字段编辑 (lint + 危险动作二次确认 + 热重注册回滚) 与
  「出厂更新中心」字段级三方合并 (config_template 为 old base; 启动只提示
  模板 hash 变化, 绝不自动升级)。
- **P4**: FQGate OpenAPI 重导入 (origin='user')。
- 真实 `data/config.db` 当前是 schema v2 空实体 (v1 库自动升级), 下次正式启动
  会自动 seed 出厂模板; 不提交 data/。

## 2026-09-18 续: P3 全部落盘 (#43–#54, 仍未提交)

P3 (实体热编辑 + 出厂更新中心) 已完成,与 P2 同树未提交。提交时同样排除
`src/unihive/api/mootdx2_client.py` (用户工作区改动)。

### 后端
- `storage/config_store.py`: 实体白名单写接口 (tool/upstream/section upsert,
  origin='user', 与出厂相同自动回落 builtin 删行), 全局整数 revision 乐观锁,
  `apply_upgrade_bundle` 单事务清表重灌 + 快照前推 (始终 revision+1)。
- `core/entity_edit.py`: 白名单 patch 归一化 + 契约层字段识别 (TOOL/UPSTREAM)。
- `core/config_lint.py`: 单实体/候选配置 lint 门, `LintRejected` → HTTP 422。
- `core/config_upgrade.py`: 字段级三方合并引擎 (old 快照 / cur 实体 / new 出厂),
  FieldConflict 台账 (含已裁决项), EntityChange 台账, `.summary()`。
  **门控语义**: "未裁决" = key 不在用户 resolutions 表里;显式 keep_cur 也算已裁决
  (`unresolved_conflicts(result, resolutions)`)。
- `core/config_db.py`: `plan_config_upgrade` / `assemble_runtime_candidate` /
  `validate_resolutions` / `write_upgrade_backup` (升级前 JSON 备份, 固定
  `<db 同级>/backups/config-upgrade-*.json`) / `upgrade_database` (CLI 路径)。
- `gateway_server.py`: 热编辑 (单工具/上游, provider 失败回滚) + 出厂更新
  preview/apply: `_upstream_toggle_lock` 串行化; live config/specs/hidden/router
  引用替换; 上游 attach/reconnect/detach 差集; 工具 add/remove/republish 差集
  (republish=发布交集且 canonical spec 变化, 先摘后注, 不重复计数);
  备份 → 切运行态 → 单事务落库, provider/落库失败 `_rollback_config_upgrade_locked`。
- `utils/console_api.py`: 4 新路由 — `GET /api/config/template-status` (只给
  hash 变化信号, build 失败 200+build_error), `POST /api/config/upgrade/dry-run`,
  `POST /api/config/upgrade/apply` (confirm:true 强确认), `GET
  /api/config/upgrade/backup/latest` (固定备份目录, 无路径参数); 另有单实体
  GET detail + PUT tool/upstream。异常映射: 400/409/422/503。
- `cli.py`: `python -m unihive config upgrade-db` (默认 dry-run; `--apply`
  落库不触运行态; `--resolutions` 裁决文件; `--expected-revision`)。
- 新测试: test_config_store_v2 / test_entity_edit / test_config_lint 增补、
  test_tool_edit_hot (15)、test_config_entity_api、
  **test_config_upgrade_service (12) / test_config_upgrade_hot (15) /
  test_config_upgrade_api (31) / test_cli_config_upgrade (5)**。
  全套 **946 passed, 2 skipped**。

### 前端
- `api/config.ts`: 实体详情/编辑 3 端点 + 出厂更新 4 类型/函数
  (FactoryTemplateStatus / UpgradePreview / UpgradeApplyResult /
  ResolutionMap; backup URL 常量直接挂同源 cookie 链接)。
- 新纯函数 (vitest 覆盖, .vue 不测的既定边界):
  `views/config/toolEdit.ts` / `upstreamEdit.ts` / `entityErrors.ts` /
  **`updateCenter.ts`** (conflictKey/pendingConflicts/seed 语义/decideAll/
  canApplyUpgrade/action·kind 标签/impactGroups/isDbStatus)。
- 新组件: ToolEditDialog / UpstreamEditDialog / **UpdateCenterPanel +
  UpgradeLedgerTable** (第 8 个 subtab「出厂更新」): 模板 hash 信号 →
  检查出厂更新 (dry-run) → 摘要 chips + 冲突逐条 radio 裁决 (old/cur/new
  三向值, 全部保留/全部采用出厂批量) + lint + 运行态影响面 + 三段台账 →
  强确认 → 热 apply → 结果 (revision/备份路径/下载链接/建连警告), 409 自动复查。
- ConfigView 8 tab; DatabasePanel 横幅文案更新; build finalize 已重新生成
  console.html + static/console/assets (旧 hash 资产删除)。
- web **304 passed (17 files, updateCenter 26)**, `npm run build` vue-tsc 绿。

## 后续 (更新)
- **P3 已完成**, 未提交; 验证止步于 pytest + vitest + npm run build + API 层,
  未开浏览器 (既定约束)。
- **P4**: FQGate OpenAPI diff → origin='user' 工具行 + 热注册 + YAML 片段导出。
