# P4: FQGate OpenAPI 实时导入 (origin='user' 热注册 + YAML 片段导出)

日期: 2026-09-18
状态: **全部落盘, 测试绿, 未提交** (与 P2/P3 同树, 见 2026-09-18-sqlite-config-entity-cutover.md)
设计条款: docs/design/2026-09-18-sqlite-config-full-database.md:201-204
任务: #55–#61 (7/7 完成)

## 做了什么

MCP 浏览器页对 THS-LOCAL-GATEWAY 增加「OpenAPI 导入」: 活拉上游
`openapi.json` + 分页 `tools/list`, 与 config.db 内现状做契约差异台账
(added/changed/unchanged/removed/unmapped), 用户勾选 + 评审 YAML 片段 +
强确认后, 选中工具以 `origin='user'` upsert 入库并**即时热注册**到对外
`/mcp` (新增即发布, 变更先摘后注), 失败整笔回滚 (运行态 + DB)。
导入**永不删工具**; `removed` 仅顾问提示。用户工具评审并入出厂 YAML 模板、
经出厂更新中心吸收后转 `builtin` —— 这是既定的人审通道, P4 只负责导出片段。

## 文件清单

### 后端

| 文件 | 内容 |
|---|---|
| `src/unihive/core/openapi_import.py` (新) | 纯模块: `convert_openapi_tools` / `plan_openapi_import` / `merge_imported_tools` / `render_yaml_fragment`; dataclass `UnmappedTool` `ImportToolChange` `ImportPlan` |
| `src/unihive/storage/config_store.py` (改) | `ConfigStore.upsert_tools(entities, *, expected_revision=None)`: 单事务批量 upsert, 全批最多 bump 一次 revision; 新行 position=max+1 顺延; 已存在只刷契约列, **position 与本地门控列 (cache_ttl_key/requires_env/publish_mode) 恒保留现值** |
| `src/unihive/api/ths_local_gateway_client.py` (改) | `fetch_openapi()` (GET openapi.json) + `OpenApiFetchError`; `list_tools_raw` 复用既有分页 |
| `src/unihive/gateway_server.py` (改, ~285 行) | `_prepare_openapi_import` / `preview_openapi_import` / `apply_openapi_import` |
| `src/unihive/utils/console_api.py` (改) | `POST /api/config/import/preview`, `POST /api/config/import/apply` |

### 后端测试 (82 例, 全绿)

- `tests/test_openapi_import.py` — 14, 纯转换/差异/合并/片段
- `tests/test_openapi_import_hot.py` — 14, gateway 热编排 (成功/幂等 no-op/白名单/RevisionConflict 回滚/provider 爆炸回滚/lint 阻断不建备份)
- `tests/test_openapi_import_api.py` — 39, 路由入参校验 + 异常状态码映射
- `tests/test_ths_local_openapi.py` — 8, client 活拉
- `tests/test_config_store_upsert_tools.py` — 7, 持久层

### 前端

| 文件 | 内容 |
|---|---|
| `web/src/api/config.ts` (改) | P4 类型 (`OpenapiImportPreview`/`OpenapiImportApplyResult`/`ImportImpact` 等) + `previewOpenapiImport` / `applyOpenapiImport` |
| `web/src/views/mcp-browser/openapiImport.ts` (新) | 纯函数: 选择集/门控/标签/impact 分组/文件名/下载/错误分类 (16 vitest) |
| `web/src/views/mcp-browser/OpenapiImportDialog.vue` (新) | 台账表 + lint/impact/片段面板 + 强确认 + apply |
| `web/src/views/McpBrowserView.vue` (改) | toolbar「OpenAPI 导入」按钮, 仅 `currentUpstream.type === 'ThsLocalClient'` 显示; applied 后刷新原始台账 |
| `web/tests/views/mcp-browser/openapiImport.spec.ts` (新) | 16 例 |

## 关键不变量 (改动时必须保住)

1. **upsert-only**: apply 不删任何工具行; `plan.removed` 只在 UI 顾问提示。
2. **上游连接零影响**: impact 的 upstreams_attach/reconnect/detach 恒 `[]`,
   导入不 converge client (活拉用的是已连接 client, 用完即走)。
3. **契约以 REST 为准**: OpenAPI REST query/body params 权威; MCP
   inputSchema 不可信, 仅把 MCP-only 的 `requestTimeoutMs` 以 "，取值 ≥1"
   追加。MANUAL_MAP 34 条按真实上游名建键 (注意双 market:
   `fqgate_market_market_health`); GET-only 且无 POST 提示词的工具进 unmapped。
4. **新实体**: `origin='user'`, `source_file='openapi-import'`; 合并行保
   position + 门控列 (合并后 `_sync_preserved_to_spec` 把保留的门控列同步
   回 spec, 保证候选配置与库行一致)。
5. **apply 顺序**: lint 门 → 强确认门 → no-op 短路 (不备份/不加锁) →
   `write_upgrade_backup` 同步落盘 → `_upstream_toggle_lock` 内切运行态
   (config/tool_specs/hidden/router 四元组) → unpublish/publish → provider
   失败整笔回滚并抛 partial failure → `store.upsert_tools(expected_revision=
   当前 revision)` → 409 同样整笔回滚。
6. **确认契约字段**: `tool:<name>:contract`; 前端 apply body 恒带
   `confirm_dangerous: true`; 后端路由层先验这个布尔, 运行态内部再确认。
7. **白名单收窄重算**: 前端勾选变化 (300ms 防抖) 带 `tools` 重新 preview,
   impact/YAML 片段都以后端按白名单重算的响应为准, 前端不自己切片段;
   空选择不发请求 (后端对 tools=[] 返 400), apply 门本身关闭。
8. **异常 → HTTP**: KeyError 404 / ValueError 400 / LintRejected 422 /
   RevisionConflict 409 / OpenApiFetchError 与 "tools/list failed" 502 /
   "not connected"、"Gateway not initialized"、yaml 模式/无 store 503 /
   DangerousConfirmationRequired 400。
9. **409 前端语义**: "配置已变更, 请重新生成预览" —— 自动重拉全量 preview
   并重置默认勾选/确认框; **502**: 附加 "请确认 THS-LOCAL-GATEWAY 已启动
   (默认 http://127.0.0.1:17281)"。

## 验证 (API 层, 未开浏览器, 遵守既定约束)

- `pytest tests/ --ignore=tests/test_get_daily_l1_observability.py --ignore=tests/test_mootdx2_p0_perf.py -q`
  → **1028 passed, 2 skipped** (64.98s)
- `cd web && npx vitest run` → **320 passed / 18 files** (基线 304 + P4 16)
- `npm run build` → vue-tsc + vite 绿; `scripts/finalize-build.mjs` 已重新
  生成 console.html 与 static/console/assets (旧 hash 资产已删)
- 未做活网关手工 E2E (本机 FQGate 17281 是否在跑未假定); 热编排路径由
  test_openapi_import_hot.py 的 fake mcp/router/client 全分支覆盖。

## 未做 / 后续

- **未提交**。提交时: 不带 `src/unihive/api/mootdx2_client.py` (用户的
  未提交改动); 需包含 console.html 重生成、新 static assets、删除的旧
  assets, 以及 P2/P3/P4 全部源与测试 (同树不可分)。
- user → builtin 的翻转不在代码里自动化: 人审 YAML 片段并入出厂 YAML 后,
  走 P3 出厂更新中心三方合并吸收。
- 导入目前只 ThsLocalClient 一个上游具备能力 (按钮按 client 类型隐藏,
  后端对无 fetch_openapi 的 client 返 400)。
