# THSDK 自选分组测试 — 2026-09-09

## TL;DR ✅ 已完成

已添加 `ths_get_all_watchlist` 工具,合并主自选(23只)与自定义分组(8个)为统一视图,主自选以 group_id=0 呈现。

## 上下文

沿用 THSDK 上游(详见 [[project-thsdk-upstream]])已有 26 spec、requires_env 双门控、4 个 confirm=true 写工具。本次目标:验证 `config/tools_ths.yaml` 10 个自选写工具实际可用,并核对分组接口返回数据完整性。

## 环境

- thsdk 包已装,版本由 `requirements.txt` 锁定
- `.env` 三个变量已设:`THS_USERNAME` / `THS_PASSWORD` / `ALLOW_WATCHLIST_WRITE=true`
- Python 直接调 `ThsdkClient.call_tool` 即可,不需启动 gateway
- .env 自动加载**不生效**(需 `_load_env.ps1` 或 bash `export`),需手动 `os.environ` 注入

## 已验证(本次会话)

| 工具 | 状态 | 备注 |
|---|---|---|
| `ths_auth_status` | ✅ | auth_mode=credentials, write_enabled=true |
| `ths_account_permissions` | ✅ | 港股 L2 开,其他关(正式账号典型) |
| `ths_get_account_watchlist` | ✅ | 23 只证券,version=17,`source_securities` 末尾有 23 个数字 |
| `ths_get_account_watchlist_groups` | ✅(API 完整) | 顶层 4 字段全列出,group_order == groups dict keys |
| `ths_create_account_watchlist_group` | ✅ | ID=43 创建,version 7→8 |
| `ths_add_account_watchlist_group_securities` | ✅ | 短代码 600519/000001 自动补全,version 8→9 |
| `ths_rename_account_watchlist_group` | ✅ | version 9→10 |
| `ths_replace_account_watchlist_group_securities` (confirm=true) | ✅ | version 10→11 |
| `ths_remove_account_watchlist_group_securities` | ✅ | version 11→12 |
| `ths_delete_account_watchlist_group` (confirm=true) | ✅ | ID 43 消失,version 12→13 |

**残留校验**: 测试前后 groups ID 集 = {35..42},**完全一致**,无脏数据。

## 未确认(下次要做)

**核心疑问**: 用户手机/PC 终端可看到更多"自选分组"(共享同步),但 thsdk `get_account_watchlist_groups` 只返回 8 个,其中 6 个是空 `板块3..板块8` 槽位。

**两种可能**:
1. thsdk "groups" = 自定义板块(同花顺 APP 端"板块管理"那种;数据由 APP 创建,8 个里只有 35/36 是用户实际命名的)
2. 真正的"自选分组"(持仓 / AI / 消费等)编码在 `ths_get_account_watchlist` 的 `source_securities` 尾部数字里(33/17/36/20/16/32/169/177),或每只证券有未展示的 `group_id` 字段

**Dump 结果已确认**:

```
主自选 (get_account_watchlist): 23 只证券
分组 (get_account_watchlist_groups): 8 个 (ID 35-42), 仅 35/36 有内容

source_securities 数字分布:
  33: 10 只 (最常见 - 可能是"同花顺自选"源)
  17: 4 只
  36: 3 只
  20: 2 只
  16, 32, 169, 177: 各 1 只
```

**关键发现**:
1. **两套独立数据**: 主自选(23只) 与 分组(35-42) 是**完全独立**的集合,只有1只重叠(300033 同花顺)
2. **source_securities 数字** = 证券的"来源/分类"ID,非 group_id
   - 33 可能是"同花顺自选"或"默认分类"
   - 17/36/20 等可能是其他来源(券商/雪球等第三方?)
3. **thsdk 返回完整**: API 确实只给 8 个 group,不存在隐藏数据
4. **用户看到更多的原因**: 手机/PC 端把"主自选(23只)"显示为独立标签页,而非 group 35-42 之一

**结论**:
- **不需要**加新工具或参数
- source_securities 是"来源标记"而非"分组ID"
- 如果用户需要把主自选也算作一个"分组",在网关层做个映射即可(主自选 → group_id=0 或 special name)
- thsdk 已返回全部可用数据,没有隐藏的分组

## 已知小问题(非阻塞)

1. `thsdk` 返回分组 `name` 是 GBK 编码(`"AIӦ��"` 显示为乱码),客户端 `json_safe` 没解码 — 服务端给原样,要看真实名需在网关层 `name.encode('latin1').decode('utf-8')`
2. `account.session` 在 CWD,游客/凭证会话共享文件 — 切换 auth_mode 需删文件才能真正登出

## 文件状态

**未改动任何项目文件**。所有测试均通过 `python -c "..."` 直接调 `ThsdkClient`,不动源码。

如需把测试脚本固化为 `tests/test_thsdk_groups_smoke.py`(凭证门控 skip),开 issue 再做。