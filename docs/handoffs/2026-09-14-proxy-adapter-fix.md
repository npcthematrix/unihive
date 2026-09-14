# Proxy Adapter — 修复 TOOL_MAP 引用不存在方法

**Date**: 2026-09-14
**Status**: Fixed (pending real-TdxW smoke verification)
**Audit context**: 今日代理设置 10 commits (3941a7c → f49f699) 上线后审计

## 问题

`src/unihive/core/proxy_adapter.py` (commit c83090b) 的 TOOL_MAP 把 2 个
mootdx2 工具映射到了**新造的、SKILL.md/yaml 中均不存在**的 tqcenter
方法名:

| mootdx2 工具 | 原 TOOL_MAP 目标 | 实际存在? |
|---|---|---|
| get_batch_quote | get_security_snapshot | ❌ |
| get_minute_data | get_minute_time_data | ❌ |

`skills/SKILL.md` v1.0.12 (合同真相源) 与 `config/tools_tdx_quant.yaml`
(54 工具, SKILL.md codegen) 均未声明这两个方法名。`tqcenter.py` 不在本机
(随 TdxW.exe 一起安装, 期望路径 `{tdx_root}/PYPlugins/user/tqcenter.py`)。

**实际后果**: `tdx_quant_client.call_tool` 用 `getattr(self._tq, tool_name)`
做属性查找, 工具名不存在时抛 AttributeError。但 Round 10 audit 加的兜底
(`router.py:298-305` 的 `except Exception` + `return None` 降级原 chain)
救了场 — **功能上没看出 bug**, 但代理在 `get_batch_quote` 和
`get_minute_data` 上**从未真正生效**, 只是用户看不到错误日志。

## 修复

改用 SKILL.md v1.0.12 已声明的方法:

| mootdx2 工具 | 新 TOOL_MAP 目标 | 参数 |
|---|---|---|
| get_quote | get_market_snapshot | stock_code |
| get_batch_quote | **get_market_snapshot** | **stock_list=[...]** |
| get_kline / get_index_* / get_index_bars | get_market_data | stock_list, period, count |
| get_minute_data | **get_market_data** | **stock_list=[code], period='1m', count=240** |

`get_batch_quote` 复用 `get_market_snapshot` 的多代码形式 (stock_list);
`get_minute_data` 复用 `get_market_data` + period='1m'。

## Caveat (待真实 TdxW 环境验证)

`get_market_snapshot(stock_list=[...])` 的多代码形式基于 SKILL.md 单股形式
+ tqcenter 多代码风格推断。**真实 TdxW 安装的 tqcenter.py 可能不接受
stock_list 参数**, 需要:

1. 启动 gateway, console 启用代理
2. 调一次 `get_batch_quote(codes="600519.SH,000001.SZ")`
3. 看 `logs/gateway.log` 中是否成功路径走 tdx_quant
   (grep "proxy Success via tdx_quant")
4. 若 AttributeError, 说明 tqcenter 该方法仅支持单股, 需要:
   - 方案 A: 退回 `get_batch_quote` 不代理, 只代理 5 个 K 线工具
   - 方案 B: adapter 循环多次调 `get_market_snapshot(stock_code=...)`
     并合并结果

## Acceptance

- [x] TOOL_MAP 7 条全部指向 SKILL.md 已声明方法
- [x] test_proxy_adapter 25 tests 全绿 (从 22 增到 25, 加 3 条)
- [x] test_proxy_router 7 tests 全绿 (未改动, 无回归)
- [x] test_proxy_api 8 tests 全绿 (未改动, 无回归)
- [ ] **(需要真实 TdxW 环境)** 手动 smoke 验证 get_batch_quote / get_minute_data 走 tdx_quant 路径

## 关联 commits

- `f77c10e` fix(proxy): use existing SKILL.md methods for batch quote / minute data
- `01212b8` test(proxy): update adapter assertions for SKILL.md-valid target methods
- (UI 改进在另一 commit: proxy toggle auto-save + CSS 变量 + 未代理工具说明)
