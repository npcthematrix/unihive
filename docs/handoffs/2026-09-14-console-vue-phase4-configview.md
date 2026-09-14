# 控制台 Vue3 迁移 — 阶段 4 ConfigView 续接 handoff

**日期**: 2026-09-14
**状态**: 阶段 1-3 已提交, 阶段 4 调研中段因上下文耗尽暂停
**下一步**: 直接从阶段 4 ConfigView 实现开始

## 总体进度

| 阶段 | 提交 | 内容 |
|---|---|---|
| 1 | `3a6e989` + `e4e7b48` | 后端 JSON 鉴权端点 + web/ 脚手架 + 登录闭环 |
| 2 | `6e20c1b` | StatusView 状态页 (755 行, code-reviewer APPROVE) |
| 3 | `8dbd725` | InterfacesView 接口页 (8 文件, code-reviewer WARNING 全修) |
| 4 | — | **本 handoff 范围**, 尚未开始 |

## 阶段 4 调研现状

已确认:
- **旧版逻辑位置**: `static/console/console.js`
  - `renderCfgUpstreams` (687-815), `renderCfgMapping` (827-884),
    `renderCfgTools` (886+), `renderCfgCache` (需查), `renderCfgProxy` (需查)
  - 加载入口: `loadConfig()` 函数, `console.js:1024` 调 `/api/config`
  - 代理 tab: `console.js:1931` GET `/api/proxy/settings`,
    `console.js:1946` PUT `/api/proxy/settings` (启用/停用 TDX_QUANT 代理)
- **后端 endpoint**: `src/unihive/utils/console_api.py`
  - `get_config` 函数未精确定位行号; 已确认通过 `load_config()` 读 upstreams.yaml
  - `mask_secret()` 在第 126 行 (递归脱敏 api_key/env)
  - 代理端点需要再查 (grep `def .*[Pp]roxy` console_api.py)
- **旧版 HTML 结构**: `console.html:202-265` 行 (cfg-subtabs + 5 个 cfg-panel)
- **代理特殊面板** (`console.html:223-264`):
  - 标题 + 描述
  - TDX_QUANT 状态指示 + 提示
  - 启用开关 (PUT /api/proxy/settings)
  - 覆盖范围表 (mootdx2 → tdx_quant 工具映射)
  - 未代理的 mootdx2 行情类工具列表

## 阶段 4 实施计划 (建议)

按规模递增顺序:

1. **api/config.ts** — 类型 + `getConfig()` + `getProxySettings()` + `setProxySettings()`
2. **views/config/UpstreamsPanel.vue** — 上游列表 (env 折叠 + api_key 脱敏 + retry JSON)
3. **views/config/MappingPanel.vue** — 工具映射搜索 + 表格
4. **views/config/ToolsPanel.vue** — 工具规格 (TTL 配置列表)
5. **views/config/CachePanel.vue** — 缓存 TTL 配置 (按 cache_ttl_key 分组)
6. **views/config/ProxyPanel.vue** — 代理 tab (独立组件, 含 PUT 切换)
7. **views/ConfigView.vue** — 5 subtab 容器 + 重载/复制 JSON 工具栏

注意文件 <800 行, 5 个 panel 必须拆出 ConfigView.vue。

## 关键业务约束

- **只读**: upstreams/mapping/tools/cache tab 全部只读
- **可写**: proxy tab 启用/停用 (PUT /api/proxy/settings), 后端有 source 校验
- **脱敏**: api_key 与 env 密钥显示 `前2 + *** + 后2` (旧 console.js:716-718)
- **CSP**: 不使用 inline onclick, 全部走 Vue 事件绑定
- **路由**: `/config` 路径, 5 subtab 切换建议用 hash query `?tab=upstreams|mapping|tools|cache|proxy` (阶段 3 的深链经验)

## 关键类型/接口 (阶段 3 已确认, 阶段 4 复用)

- `escapeRegExp` 在 `web/src/utils/text.ts`
- `copyText` 在 `web/src/utils/clipboard.ts`
- `<HighlightText>` 在 `web/src/components/HighlightText.vue`
- Pinia auth store + axios 401 拦截器已经在 `api/client.ts`

## 当前 dev 环境

- 网关: `D:\fintech_workspace\unihive\.venv\Scripts\python.exe -m src.unihive.gateway_server --transport http --port 18080` 后台任务 `blayyamvw` 仍在运行 (新代码已生效)
- Vite dev: `npm run dev` 后台任务 `bal0l4d8d` 在 :5173 (HMR 推送中)
- 浏览器: 阶段 3 已验收

## 阶段 3 关键经验 (避免重复踩坑)

- `<el-table>` `type="expand"` 用 `:key="activeGroup"` 触发重挂载清展开态, 但 visibleTools 变化也要 watch 裁剪 expandedNames 保持同步
- 倒计时/copy/timer 全部走 ref + onUnmounted 清理
- 复制空列表走 `ElMessage.error` (旧版 toast error 等价), 不是 warning
- 深链不要覆盖整个 query, 只改目标 slot
- regex 元字符完整集: `/[\\^$.*+?()[\]{}|\-]/g` (utils/text.ts)
- 提交前确保 grep 实际 diff 行数, 撤销 HMR 残留 (如 `git reset HEAD file.vue`)

## 续接第一步

```bash
# 1. 确认两个后台任务还在跑
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:5173/   # 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18080/api/auth/me  # 401

# 2. 读完整 config endpoints 与 5 个 cfg 渲染函数
sed -n '100,250p' src/unihive/utils/console_api.py
sed -n '886,1200p' static/console/console.js
sed -n '1870,1980p' static/console/console.js  # proxy tab
```

完成阶段 4 后: vue-tsc + npm run build:only + code-reviewer (5 subtab 复杂度高)
