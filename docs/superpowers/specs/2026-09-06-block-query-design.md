# 板块查询接口设计 (Block Query MCP Tools)

## 背景

用户需要三个板块查询接口：
1. **板块列表** — 已有 `get_block(block_type)` 可返回
2. **根据板块查个股** — 给定板块代码，返回其所有成分股
3. **根据个股查板块** — 给定股票代码，返回其所属所有板块

现有 `get_block(block_type)` 返回板块列表（含 code、name、stock_count），但**没有**接口能查：
- 某板块包含哪些个股
- 某个股属于哪些板块

## 数据源分析

### mootdx2 StdQuotes

| 方法 | 说明 | 返回内容 |
|------|------|----------|
| `Quotes.block(block_type)` | TDX 网络请求，获取板块列表 | `[{code, name, stock_count}, ...]` |
| `Quotes.block()` (StdReader) | 本地 block.dat 文件解析 | 板块元数据 |

**关键限制：** mootdx2 的 `Quotes` 类（基于 TdxHq_API）**不暴露** `SECTOR` 协议命令，无法直接用网络请求查个股→板块或板块→个股。

TDX Hq API 底层支持 `SECTOR` 命令，但 tdxpy 的 `TdxHq_API` 类未直接暴露，需要通过原始连接调用。

### 可行路径

**路径 A：TDX 协议 SECTOR 命令（在线，需实现原始 pkg 发送）**
- TDX 协议 `SECTOR` 命令可查板块成分股（板块ID→股票列表）和反向映射（股票代码→所属板块）
- 通过 `client.send_raw_pkg()` 实现，绕过 tdxpy 的封装
- 准确、在线、实时

**路径 B：本地 block XML 文件（离线，稳健）**
- TDX 本地数据目录包含 `vipdoc/block/block_*.xml` 文件
- `block_ch.xml`（行业）、`block_zs.xml`（概念）、`block_fd.xml`（地区）
- XML 中含 `<stock>` 节点含股票代码，可解析出完整的股票→板块关系
- 离线可用，依赖本地 TDX 安装

**路径 C：扩展现有 `get_block` + 新接口用 TDX SECTOR（混合）**
- 板块列表：复用现有 `get_block`
- 查板块成分股 / 查个股板块：直接调用 TDX SECTOR 协议
- 优点：可获取实时数据；缺点：需要实现原始协议

**决策：路径 B（本地 XML）+ 路径 C 的 SECTOR 作为备选**

路径 B 更稳健：离线可用，不依赖网络，TDX 安装后即可用。路径 C 作为在线备选。

## 接口设计

### 工具一：get_stocks_in_block

查询指定板块的所有成分股。

**YAML 配置键名：** `get_stocks_in_block`

**MCP 工具名：** `get_stocks_in_block`

**参数：**

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `block_code` | str | 是 | — | 板块代码，如 `880301`（行业）、`884126`（概念） |
| `block_type` | int | 否 | 0 | 板块类型：0=行业板块，1=概念板块，2=地区板块 |

**返回示例：**
```json
{
  "success": true,
  "data": [
    {"code": "600000", "name": "浦发银行", "market": "sh"},
    {"code": "600016", "name": "民生银行", "market": "sh"}
  ],
  "source": "mootdx2"
}
```

**错误：**
- 板块代码不存在 → `{success: false, error: {code: "BLOCK_NOT_FOUND", message: "板块不存在"}}`
- 本地 TDX 目录未配置 → `{success: false, error: {code: "TDX_NOT_INSTALLED", message: "请配置 TDX 安装目录"}}`

**实现：** 解析本地 `vipdoc/block/block_*.xml` 文件，或通过 TDX SECTOR 命令在线获取。

---

### 工具二：get_blocks_for_stock

查询指定股票所属的所有板块。

**YAML 配置键名：** `get_blocks_for_stock`

**MCP 工具名：** `get_blocks_for_stock`

**参数：**

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `stock_code` | str | 是 | — | 股票代码，如 `600000` |
| `market` | str | 否 | auto | 市场：sh/sz/bj，自动推断 |

**返回示例：**
```json
{
  "success": true,
  "data": [
    {"block_code": "880301", "block_name": "银行板块", "block_type": "industry"},
    {"block_code": "884054", "block_name": "沪股通", "block_type": "concept"}
  ],
  "source": "mootdx2"
}
```

**错误：**
- 股票代码不存在 → `{success: false, error: {code: "STOCK_NOT_FOUND", message: "股票不存在"}}`
- 未找到任何板块 → `{success: true, data: [], ...}`（无所属板块是正常数据，不是错误）

**实现：** 扫描本地 `vipdoc/block/block_*.xml` 所有板块，找包含该股票的条目。或通过 TDX SECTOR 命令反向查。

---

### 工具三：get_block_list（已有，补充说明）

现有 `get_block(block_type)` 重命名/明确为 `get_block_list`。

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `block_type` | str | 否 | "block" | 板块类型：block=行业，concept=概念，region=地区 |

返回结构不变。

---

## 实现计划

### Phase 1：本地 XML 解析（离线方案）

实现 `MooTDX2Client` 新方法：

```
MooTDX2Client._get_block_xml_path(block_type: int) -> Path
MooTDX2Client._parse_block_xml(block_type: int) -> list[dict]  # 缓存
MooTDX2Client._stocks_in_block_xml(block_code, block_type) -> list
MooTDX2Client._blocks_for_stock_xml(stock_code, market) -> list
```

文件路径：`vipdoc/block/block_ch.xml`（行业）、`block_zs.xml`（概念）、`block_fd.xml`（地区）

XML 结构（推测）：
```xml
<data>
  <item>
    <blockcode>880301</blockcode>
    <blockname>银行板块</blockname>
    <stock>
      <code>600000</code>
      <name>浦发银行</name>
    </stock>
  </item>
</data>
```

解析策略：使用 Python `xml.etree.ElementTree`，缓存 5 分钟（文件修改时间判断失效）。

### Phase 2：TDX SECTOR 协议（在线方案）

如果 Phase 1 解析失败或用户有网络需求，通过 TDX SECTOR 命令在线获取。

SECTOR 命令格式（二进制）：
```
包头(32B) + SECTOR(1B) + 板块类型(1B) + 保留(4B) + 起始位置(4B) + 数量(4B)
```

### Phase 3：注册到 call_tool

```python
async def call_tool(self, name: str, arguments: dict) -> ToolResult:
    method_map = {
        # ... existing ...
        "get_stocks_in_block": self.get_stocks_in_block,
        "get_blocks_for_stock": self.get_blocks_for_stock,
    }
```

### Phase 4：YAML 配置

在 `config/tools_mootdx2.yaml` 添加两个工具定义，描述参考本设计 Agent instruction manual 标准。

## 文件变更

| 文件 | 变更 |
|------|------|
| `src/mootdx2_client.py` | 添加 `_get_stocks_in_block`, `_get_blocks_for_stock` 方法，注入 `call_tool` method_map |
| `src/mootdx2_pool.py` | 如需要，添加连接池方法 |
| `config/tools_mootdx2.yaml` | 添加两个工具定义 |

## 测试策略

1. **离线测试：** 解析本地 XML 文件，验证 stock_count 与实际 stock 数量一致
2. **在线测试：** 对比 XML 解析结果与 SECTOR 命令返回结果
3. **边界测试：** 板块代码不存在、股票无板块、股票同时属于多个板块

## 缓存策略

- 板块 XML 文件：解析后缓存，TTL 5 分钟，按文件 mtime 判断是否过期
- SECTOR 查询结果：复用现有缓存策略（cache_ttl_key: null，实时查询）
