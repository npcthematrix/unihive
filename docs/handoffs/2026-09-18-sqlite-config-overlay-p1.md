# Handoff: SQLite 配置薄覆盖层 P1(已实现, 未提交)

日期: 2026-09-18
专题设计: [docs/design/2026-09-18-sqlite-config-overlay.md](../design/2026-09-18-sqlite-config-overlay.md)
前置: P0 已提交 (`d37ca6f`, config lint + 等价性基建); 本批为 **P1 全部代码**, 工作区未提交。

## 一句话

控制台两条 YAML 写路径(上游 enabled 热开关、FUYAO 工具级 TTL)全部改为写
`data/config.db` 薄覆盖层; YAML 永远是 git 跟踪的出厂基线, DB 只存差异点路径,
整数全局 revision 乐观锁, 写入即热生效; 配置页新增「本地覆盖」tab 可查/单条恢复/清空。

## 变更清单

### 新增 (后端)

| 文件 | 内容 |
|---|---|
| `src/unihive/storage/config_store.py` | `ConfigStore`: `config_overlay(path PK, value_json, revision, updated_at)` + `config_meta`(全局整数 revision, schema_version=1)。SQLAlchemy2 async + aiosqlite, WAL / synchronous=NORMAL / busy_timeout=5000 (per-connect PRAGMA)。`apply_changes(upserts, deletes, expected_revision)` 事务化; 同值 upsert / 删不存在行 / 空 reset 不 bump; `RevisionConflict` 整笔回滚。点路径字符集 `[A-Za-z0-9_-]+(.[…])*` (需容纳 `THS-LOCAL-GATEWAY`)。LIKE 前缀查询 `ESCAPE '\'` 转义 `\_%\`。 |
| `src/unihive/core/config_merge.py` | 纯函数 `overlay_tree(rows)` + `apply_config_overlays(baseline, rows)`: 深拷贝基线, dict+dict 递归, 标量/list 覆盖, 结构冲突 fail-closed ValueError。不碰 IO。 |
| `tests/test_config_store.py` | 存储层: bump/no-op/冲突回滚/路径校验/前缀转义/reset/并发语义。 |
| `tests/test_config_merge.py` | merge 纯函数: 深拷贝不 mutate、标量替换、list 整体替换、结构冲突、多点路径成树。 |
| `tests/test_config_overlay_golden.py` | **等价性关卡**: 空 overlay 时 effective config dict 与 YAML 路径深等; `load_all_tools(include_env_gated=True)` 全字段相等 (实测 290 spec); 真实空库 roundtrip; `UNIHIVE_CONFIG_SOURCE=yaml` 逃生时 store is None; seed enabled 覆盖后启动翻转且兄弟字段保留。 |
| `tests/test_config_overlay_api.py` | 21 例: list/delete/reset/handler/路由顺序(静态 /reset 必须在 {path} 前; Starlette 给 GET 自动加 HEAD)/reconcile(删 enabled 行按出厂值热收敛、无 server 警告、运行时失败不阻断)。 |

### 修改 (后端)

- `src/unihive/gateway_server.py` (LF): `__init__` 持 `self.config_store`; `_do_initialize`
  最前面 `_apply_db_config_overlays` (YAML 基线 → DB rows → `validate_config` fail-closed
  → 下游 dict 形态不变); env `UNIHIVE_CONFIG_SOURCE=yaml` 逃生、`UNIHIVE_CONFIG_DB`
  覆盖默认 `data/config.db`; `stop()` 关 store (getattr 兜底 `__new__` 测试对象);
  fuyao PUT handler: `ConfigStoreUnavailable`→503 / `VersionConflict`→409 / ValueError→400。
- `src/unihive/utils/console_api.py` (LF):
  - 全局 `_config_store` + **行内存镜像** `_overlay_rows`/`_overlay_revision`
    (`set_config_store` 启动注入, 每次写后 refresh) — 控制台同步读路径零 DB IO。
  - `save_upstream_enabled` / `save_fuyao_cache` 全 async 化、**只存差异**:
    值与出厂相同 → 删行而非写行; 返回新整数 revision。
  - **删除** `rewrite_upstream_enabled` / `_unquote_yaml_key` 正则文本手术;
    控制台不再写任何 YAML。
  - 版本契约: DB 模式 = 整数 revision (**0 合法**); yaml 逃生模式回落旧 16 位文件 hash。
  - 新增 `list_config_overlays` / `delete_config_overlay` / `reset_config_overlays`
    + `_after_overlay_write` (refresh 镜像 + fuyao tools 热同步 + 清接口/MCP 工具缓存)
    + `_reconcile_enabled_overlays` (enabled 行删除/清空后按出厂值回调运行时热开关,
      异常只进 warnings, 重启自然收敛)。
  - 新路由: `GET /api/config/overlays`、`DELETE /api/config/overlays/{path}`、
    `POST /api/config/overlays/reset`。鉴权随 `/api/*` cookie 中间件自动继承。
- `src/unihive/models/fuyao_cache.py`: 定稿单层 `tools: dict[str, StrictInt]`
  (pydantic lax 会把 "60"/True/1.5 静默转 int, 必须 StrictInt); validator 拒空键/bool/负数;
  `extra="forbid"`; `is_empty()` / `override_count()` 方法; 0 = 显式不缓存。
- `src/unihive/core/cache_strategy.py`: **顺带修潜伏 bug** — `fuyao_ttl_for` 旧读
  endpoints shape, 导致 2026-09-15 落的 12 条 TTL override 运行时从未生效;
  改读 `fuyao_cache.tools`, 保存时热同步 `_GW_CFG`。
- `tests/test_upstream_enabled_toggle.py` / `tests/test_fuyao_cache_config.py`: 重写为
  store 支撑版 (tmp YAML + ConfigStore 注入/teardown, async fixture 显式 initialize/close)。
- `tests/test_startup_fixes.py`: 适配 load_config 恒带 `version` 键。
- `src/unihive/models/__init__.py`: 导出新模型。

### 新增/修改 (前端, web/)

- 新增 `src/views/config/OverlayPanel.vue`: 「本地覆盖」tab — banner 说明基线+差异语义;
  yaml 模式 (503) 只读告警; 表格列 类型 tag(上游开关/FUYAO TTL/其他, 标注是否热生效) /
  路径 / 本地值(布尔→强制启用/停用, TTL→人类时长, 0→不缓存) / 行 revision / 更新时间 /
  单条恢复; 「全部恢复出厂」; ElMessageBox 确认 + 冲突 (version_conflict / revision 不一致)
  识别; 行级/全量 busy 防连点。
- 新增 `src/views/config/overlays.ts` (纯函数, 13 测试): `overlayKind` 路径分类
  (含 near-miss 防护 `upstreams.a.enabled_extra`→other)、值/时间人类化。
- 新增 `src/views/config/revisions.ts` (7 测试): `isUsableRevision`
  (**0 是合法 revision, 不能用 truthy `||`**) / `revisionOrSkip` (无效→'' 通知后端跳过乐观检查)。
- `src/api/config.ts`: `ConfigRevision = number | string` 全链路; 新增
  `getConfigOverlays` / `deleteConfigOverlay` / `resetConfigOverlays`;
  `FuyaoCacheConfig` 收敛为单层 `{tools, version}`。
- `ConfigView.vue`: 第 7 个 tab + overlay 独立拉取 (503→只读态, 其他错误保留旧数据+toast);
  开关/TTL/覆盖变更后联动刷新。
- `UpstreamsPanel.vue` / `FuyaoCachePanel.vue`: revision 用 `?? ''`/helper;
  503 `config_store_unavailable` 明确提示 yaml 模式; TTL 保存文案改为热生效+差异存储。
- 构建产物: `console.html` + `static/console/assets/*` 新 hash 已 regenerate
  (旧 hash 文件显示 D, 新文件 untracked, 提交时一起 add)。

## 验证 (全部 2026-09-18 实跑)

- backend: `.venv/Scripts/python.exe -m pytest tests/ --ignore=tests/test_get_daily_l1_observability.py --ignore=tests/test_mootdx2_p0_perf.py`
  → **734 passed, 1 skipped** (基线 648, 新增 ~86; 两个 ignore 为既有损坏文件, 与本批无关)。
- web: `cd web && npx vitest run` → **240 passed** (基线 222);
  `npm run build` vue-tsc 绿并重新生成 console.html/assets。
- `.venv/Scripts/python.exe -m unihive config lint` → **OK: no issues found** (290 spec)。
- 未做浏览器验证 (项目约定: 止步 API 层 + build)。

## 关键语义/坑 (给续做者)

1. **revision 0 合法**: 新库初值 0。前端任何 `version || ''` 都是 bug, 必须
   `isUsableRevision` / `?? ''`。空串 = 通知后端跳过乐观锁检查。
2. **差异即删除**: 写回出厂值 = DELETE 行, revision 仍 +1 (有净变化时);
   删不存在的行/同值重写不 bump。
3. **控制台同步读零 DB IO**: 只读内存镜像, 别在 `load_config()` 里加 await。
4. **路由顺序**: `/api/config/overlays/reset` 必须注册在 `/api/config/overlays/{path}` 前。
5. **reconcile 只覆盖 enabled 类路径**; TTL/其他删行靠 `_after_overlay_write` 热同步即可。
6. 测试用临时 ConfigStore 必须 `await initialize()` + teardown `await close()`,
   否则跨用例 "Event loop is closed" 警告。
7. `console_api.py` / `gateway_server.py` 均为 **LF**。
8. scratch 脚本 `logs/_patch_overlays.py` 已删除 (logs/ 不进 git)。

## 未做 / 下一步

- **提交**: 等用户明确指令 (`git add` 需含新 static assets 与 console.html;
  切勿带入用户未提交的 `src/unihive/api/mootdx2_client.py`)。
- **P2**: `tool_override` 表 + 工具 description / param description / param enum /
  cache_ttl_key / 单工具 hidden 的控制台编辑, 白名单 + 单 spec merge 校验 +
  热重注册失败回滚 (契约层 name/routing/mapping/param name·type·required/requires_env 物理不可写)。
- **P3**: FQGate OpenAPI diff/reimport (`tool_custom`) + YAML patch 导出晋升。
- P1 小遗留: GET config 不区分 fuyao 工具 TTL 的基线值/覆盖值 (覆盖来源可在新 tab 查),
  P2 做工具编辑时合并视图。
