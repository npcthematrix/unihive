# SOURCE Map Fallback 修复设计

## 问题

`console_api.py` 中的 `_build_source_map()` 依赖 `upstream_tool_mapping` 查找工具来源，但查不到时 fallback 到 `derive_source_from_name()` —— 该函数根据工具名前缀推断来源，属于名称猜测而非真实配置。

这导致：
- 工具名不以 `mootdx2_`、`tdx_`、`fund_` 等前缀开头的工具，全部 fallback 到 `tdx_local`
- 即使 `upstream_tool_mapping` 已声明了正确来源，也被前缀猜测覆盖

## 设计原则

**SOURCE 100% 由配置决定，不做任何隐式推断。**

## 改动

### 1. 移除 `derive_source_from_name()` 函数

该函数在 `console_api.py:393-403`，前缀猜测逻辑不可靠，删除。

```python
# 删除
def derive_source_from_name(name: str) -> str:
    if name.startswith("mootdx2_"):
        return "mootdx2"
    if name.startswith("tdx_"):
        return "tdx_tq_local"
    ...
    return "tdx_local"  # 错误的 fallback
```

### 2. 替换 fallback 逻辑

`console_api.py:474-476` 改动：

```python
# 改动前
source = source_map.get(name) if name in source_map else None
if not source:
    source = derive_source_from_name(name)  # 错误

# 改动后
source = source_map.get(name, "unknown")  # 精确查询，无猜测
```

### 3. 更新注释

`console_api.py:447` 注释更新，去掉"前缀猜测仅作 fallback"相关描述。

### 4. 测试验证

- 重启网关和控制台
- 访问 TOOLS_LIST API，确认所有工具 SOURCE 不再出现 `tdx_local`
- 确认新工具（`get_index_overview`、`stock_top_board` 等）的 SOURCE 为 `mootdx2` 或 `unknown`（如果 mapping 缺失）

## 文件

- `src/console_api.py`
