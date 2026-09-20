# Console 启动逻辑审视 Round-13 (2026-09-19, 未提交)

**专题**: 用户要求 "审视 UNIHIVE CONSOLE 的启动逻辑"。三路并行审查
(后端服务/装配、前端 SPA 冷启动、安全/会话边界) 汇总约 20 项,
用户裁决 **全部修复**, 严格 TDD (RED→GREEN), **未明确指示前不 commit**。

**用户裁决 (本专题约束)**:
- 凭证处置 = "模板留 env 占位": 出厂模板不带任何可用凭证, 真实凭证只进
  gitignored `.env` / `UNIHIVE_CONSOLE_*`; 显式但不完整的 console 配置
  **fail-closed 拒启**; 完全不配置 = 鉴权关闭 (开放), 并修前端让关闭态可用。
- 不重提 Round-8/9 已 declined 项: cookie Secure/SameSite、登录限流/锁定、
  probe unknown 语义、start/stop 幂等、0.0.0.0 绑定、CSP/nonce、企业级 CSRF、
  密钥轮换。
- 验证止步 API 层 + vitest + npm build, **不开浏览器、不猜密码、不起网关登录**。

**结果**: 20 项 + 实施后三方复审追加项全部落盘; 后端 **1472 passed / 3 skipped**
(基线 1417 + 复审新文件 55), 前端 **418 passed (24 files)**;
`npm run build` 绿, console.html + static/console/assets 已 finalize 落盘。

---

## 簇 K — 安全根 (C1/H1/M1/M3)

| 项 | 问题 | 修复 |
|---|---|---|
| C1/H1 | `config/config.yaml` 出厂带固定 `session_secret` (`0123…cdef`×4) 与 admin/admin123 PBKDF2 哈希, 公开仓库 → 任何人可离线伪造 cookie / 默认口令登录 | console 段整段移除, 只留注释模板; `reject_known_default_credentials()` 在 env 解析层、YAML 解析层、`setup_console_auth` 三处兜底, 照抄旧默认值一律拒启 |
| M1 | `.env` 只设一两个变量 / YAML console 段缺字段时静默退化为 "console 全裸无登录" | 半配置 fail-closed, 报错列出具体缺失项 (ValueError → main exit 2); secret <32 字符同拒; YAML 明文 `password` 给可操作报错 (明文只允许经 env, 启动边界就地 PBKDF2 hash) |
| M3 | 鉴权关闭态不注册 `/api/auth/me` → 前端守卫拿 404 永久卡登录页, 而后端 `/api/*` 反而全裸, 开关两侧语义不对称 | 关闭态注册 `api_me_open` 返回 `{username:null, auth_required:false}`; 前端 store/守卫据此放行 |

文件:
- `src/unihive/console_auth.py` — `ConsoleAuthConfigError`、`reject_known_default_credentials`、`api_me_open`、setup 拒绝明文/短 secret
- `src/unihive/gateway_server/_helpers.py` — `_console_auth_config_from_env` / `_get_console_auth_config` (config.yaml → upstreams.yaml → env, 各源独立不 merge)
- `src/unihive/gateway_server/http.py` — 关闭态 `/api/auth/me` 注册
- `config/config.yaml` (模板注释化)、`.env.example` (三态语义注释)
- 测试: `tests/test_console_startup_round13.py` (新), `test_console_auth.py` / `test_console_auth_env.py` 增补

注: console 段不经 config.db 实体链, 每次启动直接读 YAML/env,
故模板改动对现有安装**重启即生效**, 不涉及 config.db 迁移。

## 簇 L — 后端 HTTP 装配硬化 (M2/B1/B2/B3)

| 项 | 问题 | 修复 |
|---|---|---|
| M2 | 4 个 console API handler 在事件循环线程跑同步阻塞调用 (上游探针 fut.result 可达 ~6s、health socket、sqlite load_config、YAML get_interfaces), 远程不可达时 loop 整体停顿 | `_blocking_json_api(sync_fn)` 装饰器下放 worker 线程, 装配处包 status/interfaces/config/health 4 路由 |
| B2 | `gateway.mcp_path` 不校验: 配 `/` 吞全部路由, 配 `/api` 影子化所有控制台 API | `validate_mcp_path()` 装配期 fail-fast: 必须 `/` 开头非根, 拒绝 `/api` `/static` `/health` `/login` `/logout` 保留根与 `//` |
| B3 | console.html 缺失时首页静默 404 | `_preflight_console_html()` 启动预检, FileNotFoundError 带 `cd web && npm run build` 可操作提示 (exit 1) |
| B1 | console.html 可被浏览器缓存, 发布后旧 HTML 引用的 hash chunk 已被 finalize 删除 → 白屏 | `_console_index_response` 给 HTML 外壳 `Cache-Control: no-cache`; `ImmutableHashedAssets` ASGI 包装给 `/static/.../assets/*` 指纹文件一年 immutable, login.css/favicon 不放大 |

文件: `src/unihive/gateway_server/http.py`、`tests/test_startup_cluster_l_round13.py` (新)。

## 簇 M — 前端冷启动 (M4/M5/M6)

旧链路最坏白屏 ~30.5s: axios 默认 15s 超时 × (1 请求 + 1 重试) + 500ms backoff,
且网关连不上时静默 masquerade 成 "未登录"; 发布后旧 chunk 404 无自愈。

- `/api/auth/me` 探测 5s 超时 + `__noRetry` (`BOOTSTRAP_PROBE_TIMEOUT_MS`,
  `web/src/api/auth.ts`); axios client 新增 `__noRetry` 支持与
  `gatewayUnavailableMessage()` 中文分类 (5xx/超时/断网三态), `errorMessage`
  接入; `/me` 401 不触发路由跳转 (探测不是业务请求)。
- auth store 新增 `probeError` + `retryProbe()`: 传输故障在登录页显示
  "冷启动探测失败, 请确认网关进程已启动" + 重试按钮; 401/4xx 仍是干净未登录态。
- `web/index.html` 内联静态 boot marker (深色渐变 + spinner, 含 reduced-motion);
  `App.vue` 在首次 `/me` 解析前挂同款 boot gate, 不再白屏。
- `web/src/utils/chunkReload.ts` (新): `vite:preloadError` (preventDefault) +
  `router.onError` 双入口识别 chunk 加载失败并 reload 自愈;
  sessionStorage 10s 冷却防 reload 循环。
- 文件: `main.ts`、`api/client.ts`、`api/auth.ts`、`stores/auth.ts`、
  `App.vue`、`LoginView.vue`、`index.html`; 测试 `utils/chunkReload.spec.ts`
  (20)、`api/auth.spec.ts` (4)、`stores/auth.spec.ts` (12, 重写)、
  `api/client.spec.ts` 增补 (55)。

## 簇 N — 边界输入校验 / 异常回显 / 死模块 (LOW)

- `POST /api/auth/login` 与 `POST /api/omni/sync` 收到非对象 JSON
  (数组/字符串/数字/null) 或坏 JSON 时, 裸 `body.get` / `req.json()` 以
  500 漏出 → `_read_json_object()` 统一 400。
- `GET /api/omni/query` 的 page/page_size 裸 `int()` →
  `_positive_int_param()` 统一 400 "{key} 必须是正整数"。
- `config_upstream_detail_api` / `config_tool_detail_api` 把内部异常文本
  (可能含 sqlite 路径/SQL) 拼进响应体 → 对外固定 `upstream_load_failed` /
  `tool_load_failed`, 细节只进 `logger.exception`。
- 删除零引用死模块 `src/unihive/core/auth_middleware.py`
  (其 PUBLIC_PATHS 会放开整个 `/api/*`, 防未来误挂载); CLAUDE.md 结构树同步。
- 测试: `tests/test_console_input_validation_round13_n.py` (22, 新)。

## 簇 O — 前端 LOW (4 项)

1. **ErrorBoundary 跨路由残留**: fallback 卡片此前只在点 "回首页" 时清,
   改点侧栏会带错跨页。状态机抽到 `web/src/composables/errorBoundary.ts`
   (`createErrorBoundaryState` + `bindRouteErrorReset`), SFC 用
   `watch(() => route.fullPath)` 自动复位; 更正错误注释 —— Vue 3 的
   `onErrorCaptured` **会**捕获后代 setup() 同步异常 (不捕获的是异步
   rejection / 事件处理器 throw)。8 测试。
2. **登录防双发**: validate() 是异步的, 连点按钮会并发登录请求;
   onSubmit 入口加 `if (loading.value) return` (与既有 retryProbe 守卫同款)。
3. **ConfigView 加载瀑布**: 旧实现先 await getConfig, 实体摘要/工具清单/proxy
   才发出。编排抽到 `web/src/views/config/pageLoader.ts`:
   四路同一 tick 并发 + `Promise.allSettled`, 三通道错误独立
   (config 内联 alert / entities+interfaces toast / proxy 独立 alert);
   proxy tab 停留时 load 等 proxy, 否则后台落地。11 测试。
4. **redirect 深链失效 + 开放重定向**: 守卫登录分支无视 `?redirect=`
   一律回 /status。决策抽到 `web/src/router/authGuard.ts`
   (`resolveAuthRoute` + `isSafeRedirect`): 已认证回跳站内目标,
   拒绝 `//host`、绝对 URL、非字符串、`/login` 自跳; LoginView 登录成功/
   探测恢复两处跳转共用同一校验。14 测试。

SFC 无 plugin-vue 不能挂载, 逻辑一律抽纯模块测; SFC 只做装配,
vue-tsc + build 兜底。

---

## 实施后三方复审 (前端 TS / 后端 Python / 安全, 2026-09-20)

20 项落盘后按用户全局规则做三视角复审, 追加发现全部修复 (严格 TDD)。
新测试文件 `tests/test_console_startup_review_round13.py` (55 用例);
前端复审在既有 4 个 spec 上先 RED 后 GREEN。

### 安全

| 项 | 问题 | 修复 |
|---|---|---|
| H1 | 簇 K 删出厂凭证后, `config/config.yaml` 出厂 host 仍是 `0.0.0.0` = 新交互风险: 鉴权默认关闭 + 全网卡绑定, 同网段任何人未认证可写 `/api/*` (改上游/触发导入)。与 Round-8 declined 的 "用户显式选 0.0.0.0" 不是同一场景 | 出厂 host 改 `127.0.0.1` (注释说明); `is_non_loopback_host()` 判 127.0.0.0/8、::1、localhost 为回环, 0.0.0.0/::/私网/公网/不可解析主机名为非回环; 用户显式绑非回环且鉴权关闭时打醒目 SECURITY WARNING (只告警不拒启 — 显式选择仍被尊重, 不重提 Round-8) |
| L1 | 坏 YAML 的 `yaml.YAMLError` 经 `str()` 回显出错源码行, 若语法错误落在 `session_secret` 行则密钥进日志/报错 | `_safe_config_error_detail()` 只报异常类型 + `problem_mark` 行号; 测试用 `DO-NOT-ECHO` 标记行断言 caplog/消息不含原文 |
| M1 | YAML 模式下 config.yaml 解析失败被当普通覆盖层失败吞掉 → console 段存在但不可解析时静默退化为开放 | 抽出 `load_config_yaml_overlay()`: 文件含顶层 `console:` 键 (列首正则, 缩进子键不算) 时解析失败抛 `ConsoleAuthConfigError` (yaml 模式 exit 2; db 模式 init 降级但 serve_http 独立再校验 fail-closed, 三个 `UNIHIVE_CONSOLE_*` 齐全是唯一逃生口); 无 console 键仍只告警 |
| M2 | console 字段非字符串 (`username: 7` 等) 被 `.strip()` 以 AttributeError 500 漏出 | `_str_field()` 统一抛 "console.{key} 必须是字符串" (中英双语匹配), `enabled` 非布尔同拒; `validate_config` lint 与运行时同语义 |
| — | 弱口令黑名单只挡 "照抄旧出厂哈希" 一个精确值, 新哈希的 admin123/123456/password 可过 | `reject_weak_password()`: env 明文路径 O(1) 集合比对 + 口令==用户名; YAML 哈希路径对 4 个弱口令逐个 `verify_password` (~800ms 启动一次性成本); 哈希落盘后明文不出现在 cfg |
| L4 | env password 被静默 strip — 首尾空格可能是口令真实组成, 造成 "录入" 与 "登录" 不一致 | username/secret 仍 strip; **password 逐字保留** (更新旧契约测试 `test_strips_whitespace` → `test_strips_whitespace_user_but_preserves_password`); 纯空白 password 按未设置处理 (fail-closed) |

### 后端

- **mcp_path 字符白名单 + 路径段** (L3 纵深): `^[A-Za-z0-9._~%/_-]+$` fullmatch,
  拒绝空格/控制字符/反斜杠/`?#`; 再拒 `.`/`..` 段 (在原 `//` 与保留根检查之前)。
  修实现过程自查 bug: 初版先 `raw.strip()` 再校验会把 `/mcp\n` 静默放行 —
  改为空白检查与校验分离, 原样字符过白名单。
- **分页 int 溢出**: sqlite 绑定 int64 上限, `page=99999999999` 类参数曾可触发
  底层错误; `_positive_int_param` 统一 1..2147483647, 越界 400。
- **固定错误码**: `config_entities_api` → `entities_load_failed`;
  `upstream_metrics_api` → `metrics_read_failed`; `upstream_enabled_api`
  500 体 → `runtime_toggle_failed` (保留 `version`, 前端重试/重启提示不丢);
  细节只进 `logger.exception`。
- **`__new__` 轻量实例兼容**: serve_http 的 config.yaml 锚定改用
  `getattr(self, "config_path", None)` 防护, 修复 3 个既有启动测试
  (m5 listen-args / shutdown chain ×2)。
- facade `__init__.py` 因 M1 内联块一度 475 行超 450 软预算, 覆盖层加载整体
  下沉 `_helpers.load_config_yaml_overlay()` 后回到 450。

### 前端 (复审 15 RED → 全绿)

- **pageLoader 代际令牌** (HIGH): config/entities/tools/proxy/load 五个独立
  generation 计数, 旧调用一律不写 ref/flags/toast, 仅最新一代清 loading;
  瞬时失败保留上次成功数据不清空; tools 响应缺数组字段显式报错而非崩在
  `.forEach`。
- **chunkReload 韧性**: sessionStorage 在 Firefox cookie-blocked / Safari
  隐私模式可抛 → 获取/读写全包 try/catch, 回退模块内存时间戳;
  冷却期抑制 reload 时**不能** preventDefault (否则 `__vitePreload` resolve
  undefined → 路由空白); `router.onError` 4.6 返回退订函数, uninstall 时调用。
- **client 401 精确匹配**: 新增 `requestPath()` (URL parse + pathname,
  异常回退 split), 仅 `/api/auth/login` 精确路径跳过 401 拦截 (旧 startsWith
  会放过 loginExtra/members 类虚构路径), `/api/auth/me?_=` 探测正确豁免。
- **authGuard 注入面**: `isSafeRedirect` 增加反斜杠 (浏览器规范 `\`→`/`) 与
  0x00–0x1F 控制字符拒绝, 与后端 mcp_path 白名单对称。
- **LoginView 防双发**: onSubmit 入口 loading 守卫 + `validate().catch(()=>false)`
  (校验 reject 时 finally 复位, 不吞按钮)。

### 复审中记录但未改 (LOW, 不阻塞)

- 其余异常反射面 (如 build_error) 仍带内部文本, 不在鉴权暴露面, 留后续专题。
- `test_console_interfaces.py` 与 `test_startup_audit_round9_batch_b.py` 存在
  **既有的**顺序相关 fixture 污染 (batch_b 先跑会留下 a_one/b_two 全局缓存):
  全量字母序下 interfaces 先跑故基线全绿, 非本专题引入; 根治需给
  get_interfaces/get_upstream_status 加显式缓存复位 fixture, 留后续。

## 已知限制 (刻意保留, 非本专题缺陷)

- **会话无服务端吊销**: 无状态签名 cookie, logout 只清当前浏览器, 改密码不使
  已签发 cookie 失效, 旧票默认存活 14 天; 唯一全量吊销 = 换
  `UNIHIVE_CONSOLE_SESSION_SECRET` 重启。已写入
  `docs/handoffs/console-auth.md` (2026-09-19 更新节)。本机单用户可接受,
  服务端会话表/口令版本号属新专题。
- Round-8/9 declined 清单 (见上) 本专题全部未动, 勿在后续专题重提。

## 验证

```
.venv/Scripts/python.exe -m pytest \
  --ignore=tests/test_get_daily_l1_observability.py \
  --ignore=tests/test_mootdx2_p0_perf.py \
  --ignore=tests/test_fuyao_cache_config.py -q
# 1472 passed, 3 skipped (簇 K-O 后 1417; 三方复审 +55)

cd web && npx vitest run      # 418 passed (24 files)
cd web && npm run build       # vue-tsc + vite build + finalize 全绿
```

(web 不写累计基线: 工作区另有本专题之前就存在的未跟踪 utils 测试;
本专题自身新增用例: 簇O +33、簇M chunkReload 20 + api/auth 4 + store 重写 12
+ client 增补, 起点为簇M 前的 329。)

Round-13 新增后端测试四文件共 118 用例:
`test_console_startup_round13.py` / `test_startup_cluster_l_round13.py` /
`test_console_input_validation_round13_n.py` /
`test_console_startup_review_round13.py` (复审 55, 含 host 回环/坏 YAML
fail-closed/非字符串字段/弱口令两路径/enabled 语义/password 逐字保留/
mcp_path 字符集/int 上限/固定错误码);
`test_console_auth_env.py` 一处旧契约更新 (password 不再 strip)。
前端新增 33 用例 (authGuard 14 / errorBoundary 8 / pageLoader 11),
簇 M 新增 47 用例 (chunkReload 20 / api-auth 4 / store 12 / client 增补等),
复审在 pageLoader/chunkReload/client/authGuard 四 spec 增补 15 用例。

## 工作区状态 (2026-09-19)

**全部改动未 commit**, 等用户明确指示。工作区同时叠有先前专题的未提交内容
(各自有 handoff, 不要混提):
- Round-12 启动审视十簇 (`docs/handoffs/2026-09-19-startup-audit-round12.md`,
  含 ps1 壳 / healthcheck / TTL=0 / 路径锚定 / 注册隔离等)
- mootdx2 L1 缓存区 (`src/unihive/api/mootdx2_client.py` 用户在改, 勿动)
- 本专题 Round-13 console 启动 (本文件)

本专题独有文件 (Round-13):
- 后端改: `console_auth.py`、`gateway_server/_helpers.py`、
  `gateway_server/http.py`、`utils/console_api.py`; 删 `core/auth_middleware.py`;
  模板: `config/config.yaml`、`.env.example`; 文档 `CLAUDE.md`、
  `docs/handoffs/console-auth.md`
- 前端改: `src/main.ts`、`src/App.vue`、`index.html`、`api/client.ts`、
  `api/auth.ts`、`stores/auth.ts`、`router/index.ts`、
  `components/ErrorBoundary.vue`、`views/LoginView.vue`、`views/ConfigView.vue`;
  新增 `utils/chunkReload.ts`、`router/authGuard.ts`、
  `composables/errorBoundary.ts`、`views/config/pageLoader.ts`;
  构建产物 `console.html` + `static/console/assets/*` (finalize 整目录替换)
