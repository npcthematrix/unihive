# THSDK 上游代码审查与修复 — 2026-09-10

## TL;DR ✅ 8/14 已修复

用户要求审视 thsdk 对外 MCP 封装逻辑。本次会话按 TDD 节奏落地 8 条（H1/H2/M1/M2/M5/L3/L7 + 2 个预存 bug），剩余 6 条需要设计讨论或重构面较大，留待下个 session。

## 已落地（8 条）

### H1 — YAML/CODE 双向不变式 (`tests/test_thsdk_client_offline.py`)

防 YAML 加新写工具时漏同步 `WRITE_TOOLS` 字典导致 confirm 门控失效：

- `test_yaml_write_tools_all_in_WRITE_TOOLS` — YAML → 代码方向
- `test_WRITE_TOOLS_no_dead_entries` — 代码 → YAML 方向

已用临时删 `ths_clear_account_watchlist` 条目验证能捕捉漂移（RED ✅）。

### H2 — 写工具会话失效不重试 (`src/api/thsdk_client.py:295-330`)

写工具遇 `NotAuthenticatedError` 不重试 handler，避免服务端已部分执行时重复修改自选数据：

- `_invoke_with_reauth` 新增 `tool_name` 参数
- 写工具分支：best-effort 后台重登 + 抛回原异常（不调 handler）
- 只读工具分支：保持 re-auth + retry 行为
- 测试：`test_write_tool_session_expiry_no_retry`

### M1 — `thsdk_errors.py` 注释修正

把"按类名 + 动态 isinstance 双重判定"改为"按异常类的 MRO 类名匹配"——实现里只有类名匹配，没有 isinstance。

### M2 — `is_available` 收紧 (`src/api/thsdk_client.py:172-179`)

THSDK 没有 TdxQuant 的"半恢复"语义（DEGRADED ≠ DLL 部分活着），改为 `is_available` 仅 `HEALTHY` 时为 True。直接构造三态验证：`test_is_available_false_when_degraded`。

### M5 — 凭证 `.strip()` 一致性

`password = os.getenv(PASSWORD_ENV, "").strip()`，与 `user` 的 strip 对齐。测试：`test_password_is_stripped`。

### L3 — `start()` 失败清理 `_mod`

import 成功但 `_authenticate` 失败时，显式 `self._mod = None; self._authed = False`，注释说明与 `call_tool` 的 `_authed` 守卫构成双保险。

### L7 — 死链注释修正

测试文件中 `src/exceptions/ths_errors.py` → `src/exceptions/thsdk_errors.py`。

### 预存 bug 修复

- `tests/test_thsdk_client_offline.py:22`: `tools_thsdk.yaml` → `tools_ths.yaml`（文件名实际如此）
- `test_yaml_shape` 断言：26 → 27（实际 17 只读 + 10 写，`ths_get_all_watchlist` 新增后未同步）
- `test_filter_specs_by_env_off/on`: 16/26 → 17/27

## 未完成（6 条需设计讨论或重构）

| ID | 项目 | 阻碍 | 建议下一步 |
|---|---|---|---|
| M3 | GBK 分组名解码（`thsdk.get_account_watchlist_groups` 返回 name 是 GBK 编码） | 解码位置（客户端/网关/调用方）+ 失败回退（错误字段 vs warnings） | 单开 session 决策；建议网关层做 latin1→utf8 round-trip + warnings 字段 |
| M4 | 空 `securities: []` 早失败 | 校验位置（每个写 handler 前 vs `_resolve_codes` 内） | 建议 `_resolve_codes` 内 raise ValueError，统一 5 处写工具路径 |
| L1 | `WRITE_TOOLS: dict[str, dict[str, bool]]` 简化为 `dict[str, bool]` 或 `set + set` | 连带改 5 处访问 + 测试 + docstring | 重构面较大，建议随 M3 一起做 |
| L2 | `_TRUTHY` / `env_flag` 在 `thsdk_config.py` 与 `tool_loader.py` 重复 | 跨文件统一到 `src/utils/env_utils.py` | 风险低但涉及多文件，单独评估 |
| L6 | `ths_klines` 的 `adjust=""` 默认值不直观 | 需决策：空串 vs 显式 None + thsdk 行为对齐 | 需先验证 thsdk 对 `adjust=""` 的实际语义 |
| — | 未做 | 把 H1/H2/M1/M2/M5/L3/L7 整合提交 | 用户决定提交粒度 |

## 测试状态

`python -m pytest tests/test_thsdk_client_offline.py -q` → **26 passed**

| 测试类型 | 数量 |
|---|---|
| 生命周期与鉴权 | 5 |
| 只读调用 / 数据转换 | 3 |
| 写门控 / confirm | 3 |
| 短代码补全 / 会话失效重登 | 2 |
| YAML 加载期门控 | 2 |
| registry annotations 透传 | 1 |
| H1 双向不变式 | 2 |
| H2 写工具不重试 | 1 |
| M2 is_available 三态 | 1 |
| M5 password.strip | 1 |
| 原有其他（启动/auth 失败等） | 5 |

## 文件状态

**未提交**。修改文件：

- `src/api/thsdk_client.py` — H2 (`_invoke_with_reauth` + `call_tool` 调用点)、M2 (`is_available`)、M5 (`.strip()`)、L3 (`start` 失败清理)
- `src/exceptions/thsdk_errors.py` — M1 (注释)
- `tests/test_thsdk_client_offline.py` — H1 (×2)、H2、M5、M2、新 imports、文件名与断言修正

## 上下文元信息

会话上下文使用率达 93%。下次 session 优先推进 M3 + M4（需要设计讨论）+ 整理提交。