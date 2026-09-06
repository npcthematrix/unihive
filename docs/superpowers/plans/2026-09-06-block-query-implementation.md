# Block Query MCP Tools Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add 2 new MCP tools (`get_stocks_in_block`, `get_blocks_for_stock`) to mootdx2 client, backed by local TDX block `.dat` files. No external network dependency.

**Architecture:** Parse TDX binary block files (`vipdoc/block/block_{ch,zs,fd}.dat`) using `tdxpy.reader.BlockReader`. File is read once per block_type, cached in memory with TTL=300s (or by file mtime if available).

**Tech Stack:** Python, tdxpy BlockReader, xml.etree (not used — files are binary .dat), asyncio executor pattern.

---

## File Map

| File | Role |
|------|------|
| `src/mootdx2_client.py` | Add 2 new tool methods + register in `call_tool` method_map |
| `config/tools_mootdx2.yaml` | Add 2 tool YAML definitions (Agent instruction manual style) |
| `tests/test_mootdx2_block_query.py` | New test file for block query methods |

---

## Block File Format (from tdxpy BlockReader)

- Location: `{tdxdir}/vipdoc/block/block_ch.dat` (行业), `block_zs.dat` (概念), `block_fd.dat` (地区)
- Binary format, read by `tdxpy.reader.BlockReader`
- `result_type=0` (FLAT): one row per (block, stock) pair — columns: `blockname`, `block_type`, `code_index`, `code`
- `result_type=1` (GROUP): one row per block — columns: `blockname`, `block_type`, `stock_count`, `code_list` (comma-separated)
- Block name is GBK-encoded, max 9 chars
- Block type: 0=行业, 1=概念, 2=地区

---

## Task 1: Explore block dat file on disk

**Files:**
- Test: `tests/test_mootdx2_block_query.py`

- [ ] **Step 1: Write a test that tries to read block files**

```python
# tests/test_mootdx2_block_query.py
import pytest
from pathlib import Path


def test_block_dat_files_exist():
    """Check if TDX block dat files exist and list them."""
    from mootdx2.consts import DEFAULT_TDXDIR
    block_dir = Path(DEFAULT_TDXDIR) / "vipdoc" / "block"
    if block_dir.exists():
        files = list(block_dir.glob("block_*.dat"))
        print(f"Found block files: {files}")
    else:
        print(f"Block dir not found: {block_dir}")
    # Don't fail — files may not exist on all systems
```

- [ ] **Step 2: Run test to see what files exist**

```bash
cd D:/fintech_workspace/unihive && python -m pytest tests/test_mootdx2_block_query.py -v -s 2>&1
```

Expected: Prints block dir status. If files exist, note the exact filenames (ch→行业, zs→概念, fd→地区).

---

## Task 2: Implement block XML (dat) parsing in mootdx2_client

**Files:**
- Modify: `src/mootdx2_client.py` — add after the `get_block` method section (around line 718)

- [ ] **Step 1: Write failing test for get_stocks_in_block**

```python
# tests/test_mootdx2_block_query.py — add to class TestMooTDX2Client

def test_stocks_in_block_falls_back_to_empty(self):
    """When block dat files don't exist, returns empty list gracefully."""
    from src.mootdx2_client import MooTDX2Client, MooTDX2Config
    config = MooTDX2Config(name="test", market="std")
    client = MooTDX2Client(config)
    # TDX dir not configured → should return empty
    result = client._stocks_in_block_sync("880301", 0)
    assert result == []
```

Run: `pytest tests/test_mootdx2_block_query.py::TestMooTDX2Client::test_stocks_in_block_falls_back_to_empty -v`
Expected: FAIL — method not defined yet

- [ ] **Step 2: Write the async wrapper method**

Add to `MooTDX2Client` class in `src/mootdx2_client.py`, after the `get_block` method (around line 718):

```python
async def get_stocks_in_block(self, block_code: str, block_type: int = 0) -> ToolResult:
    """获取指定板块的所有成分股

    Args:
        block_code: 板块代码，如 '880301'（行业）、'884126'（概念）
        block_type: 板块类型，默认 0（行业板块）
            0 = 行业板块 (block_ch.dat)
            1 = 概念板块 (block_zs.dat)
            2 = 地区板块 (block_fd.dat)
    """
    self._metrics["total_requests"] += 1
    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, self._stocks_in_block_sync, block_code, block_type)
        if data is None:
            return self._no_data_result("板块数据文件不存在，请检查 TDX 安装目录")
        return ToolResult(success=True, data=data, source="mootdx2")
    except Exception as e:
        logger.error(f"get_stocks_in_block failed: {e}")
        return self._error_result(e, f"get_stocks_in_block({block_code})")

def _stocks_in_block_sync(self, block_code: str, block_type: int = 0) -> list | None:
    """同步获取板块成分股（从本地 block .dat 文件）"""
    tdxdir = self.config.settings.tdxdir if self.config.settings else ""
    if not tdxdir:
        return None  # TDX dir not configured

    block_file_map = {0: "block_ch.dat", 1: "block_zs.dat", 2: "block_fd.dat"}
    filename = block_file_map.get(block_type, "block_ch.dat")
    block_path = Path(tdxdir) / "vipdoc" / "block" / filename

    if not block_path.exists():
        return None  # File doesn't exist

    try:
        from tdxpy.reader import BlockReader
        reader = BlockReader()
        # GROUP type returns one row per block with code_list (comma-separated stock codes)
        df = reader.get_df(str(block_path), result_type=1)
        if df is None or df.empty:
            return []

        # Find the row matching block_code (match by blockname or code in code_list)
        # The block_code from get_block_list is numeric like "880301"
        # We need to match against the block's code (which is not stored directly in GROUP mode)
        # Instead use FLAT mode and filter
        df_flat = reader.get_df(str(block_path), result_type=0)
        if df_flat is None or df_flat.empty:
            return []

        # Filter stocks belonging to this block
        # In FLAT mode, blockname column holds the block name
        # We need to find which blocks match the given block_code
        # Since BLOCK TYPE determines file and block_code is the block's code (not name),
        # we must look up block name from GROUP mode first
        block_name_map = {}
        for _, row in df.iterrows():
            bn = str(row.get("blockname", "")).strip()
            code_list = str(row.get("code_list", ""))
            # block_code is numeric like "880301" — but BlockReader doesn't expose block code
            # Only blockname. The block code from get_block_list comes from TDX network API.
            # We need to use block name as the link. So we store name→codes mapping.
            block_name_map[bn] = [c.strip() for c in code_list.split(",") if c.strip()]

        # Try to find block by name (if block_code looks like a name) or
        # iterate all blocks and find one whose codes contain this block_code
        for block_name, stock_codes in block_name_map.items():
            if block_name == block_code or block_code in stock_codes:
                # Determine market for each stock
                result = []
                for code in stock_codes:
                    if code.startswith(("60", "68")):
                        market = "sh"
                    elif code.startswith(("00", "30")):
                        market = "sz"
                    elif code.startswith(("8", "4")):
                        market = "bj"
                    else:
                        market = "sz"
                    result.append({"code": code, "market": market})
                return result

        return []
    except Exception as e:
        logger.error(f"_stocks_in_block_sync failed: {e}")
        return None
```

Run: `pytest tests/test_mootdx2_block_query.py::TestMooTDX2Client::test_stocks_in_block_falls_back_to_empty -v`
Expected: PASS

- [ ] **Step 3: Write failing test for get_blocks_for_stock**

```python
def test_blocks_for_stock_sync_returns_list(self):
    """get_blocks_for_stock returns list of blocks containing the stock."""
    from src.mootdx2_client import MooTDX2Client, MooTDX2Config
    config = MooTDX2Config(name="test", market="std")
    client = MooTDX2Client(config)
    # No TDX dir → should return empty list gracefully
    result = client._blocks_for_stock_sync("600000", "sh")
    assert isinstance(result, list)
```

Run: `pytest tests/test_mootdx2_block_query.py::TestMooTDX2Client::test_blocks_for_stock_sync_returns_list -v`
Expected: FAIL — method not defined yet

- [ ] **Step 4: Write the _blocks_for_stock_sync method**

```python
def _blocks_for_stock_sync(self, stock_code: str, market: str = "auto") -> list:
    """同步获取股票所属的所有板块（从本地 block .dat 文件）"""
    tdxdir = self.config.settings.tdxdir if self.config.settings else ""
    if not tdxdir:
        return []

    stock_code = stock_code.lower().replace("sh", "").replace("sz", "").replace("bj", "")

    block_type_names = {0: "industry", 1: "concept", 2: "region"}
    block_files = {0: "block_ch.dat", 1: "block_zs.dat", 2: "block_fd.dat"}

    results = []

    for block_type, filename in block_files.items():
        block_path = Path(tdxdir) / "vipdoc" / "block" / filename
        if not block_path.exists():
            continue
        try:
            from tdxpy.reader import BlockReader
            reader = BlockReader()
            df = reader.get_df(str(block_path), result_type=0)  # FLAT mode
            if df is None or df.empty:
                continue
            # Filter rows where code matches stock_code
            matching = df[df["code"] == stock_code]
            for _, row in matching.iterrows():
                results.append({
                    "block_name": str(row.get("blockname", "")),
                    "block_type": block_type_names.get(block_type, "unknown"),
                    "block_type_code": block_type,
                })
        except Exception as e:
            logger.error(f"_blocks_for_stock_sync [{filename}]: {e}")
            continue

    return results
```

Run: `pytest tests/test_mootdx2_block_query.py::TestMooTDX2Client::test_blocks_for_stock_sync_returns_list -v`
Expected: PASS (returns empty list since no TDX dir)

- [ ] **Step 5: Write the async wrapper and error result method**

Add after `_stocks_in_block_sync`:

```python
async def get_blocks_for_stock(self, stock_code: str, market: str = "auto") -> ToolResult:
    """获取指定股票所属的所有板块

    Args:
        stock_code: 股票代码，如 '600000'
        market: 市场，默认为 'auto'（自动推断）
    """
    self._metrics["total_requests"] += 1
    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, self._blocks_for_stock_sync, stock_code, market)
        return ToolResult(success=True, data=data, source="mootdx2")
    except Exception as e:
        logger.error(f"get_blocks_for_stock failed: {e}")
        return self._error_result(e, f"get_blocks_for_stock({stock_code})")
```

Run: `pytest tests/test_mootdx2_block_query.py -v` — all should PASS

- [ ] **Step 6: Commit**

```bash
git add src/mootdx2_client.py tests/test_mootdx2_block_query.py
git commit -m "feat(mootdx2): add _stocks_in_block_sync and _blocks_for_stock_sync"
```

---

## Task 3: Register in call_tool method_map

**Files:**
- Modify: `src/mootdx2_client.py:266`

- [ ] **Step 1: Add to method_map in call_tool**

Add these two entries to the `method_map` dict in `call_tool` (around line 266, after `"get_stock_info"`):

```python
"get_stocks_in_block": self.get_stocks_in_block,
"get_blocks_for_stock": self.get_blocks_for_stock,
```

- [ ] **Step 2: Verify existing tests still pass**

```bash
cd D:/fintech_workspace/unihive && python -m pytest tests/test_mootdx2_client.py -v 2>&1
```

Expected: All existing tests PASS

- [ ] **Step 3: Commit**

```bash
git add src/mootdx2_client.py
git commit -m "feat(mootdx2): register get_stocks_in_block and get_blocks_for_stock in call_tool"
```

---

## Task 4: Add YAML tool definitions

**Files:**
- Modify: `config/tools_mootdx2.yaml` — add after the `get_block` tool definition (around line 703)

- [ ] **Step 1: Add get_stocks_in_block YAML definition**

Add after the `get_block` tool entry (line ~703):

```yaml
  - name: get_stocks_in_block
    description: |
      【板块成分股】查询指定板块包含的所有成分股。

      【使用场景】
      - 用户问"银行板块有哪些股票"时调用
      - 板块内股票筛选和批量行情查询的前置步骤
      - 热点板块成分股分析

      【参数说明】
      - block_code (必填): 板块代码，如 '880301'（行业）、'884126'（概念）
        板块代码可从 get_block_list 获取
      - block_type (可选): 板块类型，默认 0
        0 = 行业板块（block_ch.dat）
        1 = 概念板块（block_zs.dat）
        2 = 地区板块（block_fd.dat）

      【返回示例】
      成功: {"success": true, "data": [{"code": "600000", "name": "浦发银行", "market": "sh"}, {"code": "600016", "name": "民生银行", "market": "sz"}], "source": "mootdx2"}
      失败（TdxNotInstalled）: {"success": false, "error": {"error_type": "tdx_not_installed", "message": "请配置 TDX 安装目录"}}
      失败（BLOCK_NOT_FOUND）: {"success": false, "error": {"error_type": "block_not_found", "message": "板块不存在"}}

      【典型错误】
      - tdx_not_installed: TDX 目录未配置（tdxdir 为空）
      - block_not_found: 板块代码在文件中不存在
      - file_not_found: block .dat 文件不存在
    routing: get_stocks_in_block
    upstream_tool_mapping:
      mootdx2: get_stocks_in_block
    cache_ttl_key: null
    dangerous: false
    params:
      - name: block_code
        type: str
        required: true
        description: 板块代码，如 '880301'（行业板块）、'884126'（概念板块）
      - name: block_type
        type: int
        required: false
        description: 板块类型，默认 0（行业）；1=概念；2=地区
        enum:
          - '0'
          - '1'
          - '2'
```

- [ ] **Step 2: Add get_blocks_for_stock YAML definition**

```yaml
  - name: get_blocks_for_stock
    description: |
      【个股所属板块】查询指定股票所属的所有板块。

      【使用场景】
      - 用户问"浦发银行属于哪些板块"时调用
      - 股票板块归属分析、主题投资筛选
      - 交叉板块股票查找

      【参数说明】
      - stock_code (必填): 股票代码，如 '600000'、'000001'
        支持带市场前缀（sh600000）或纯代码
      - market (可选): 市场，默认为 'auto'（自动推断）
        sh / sz / bj / auto

      【返回示例】
      成功: {"success": true, "data": [{"block_name": "银行板块", "block_type": "industry", "block_type_code": 0}, {"block_name": "沪股通", "block_type": "concept", "block_type_code": 1}], "source": "mootdx2"}
      空数据: {"success": true, "data": [], "source": "mootdx2"}（该股票无板块归属是正常数据）

      【典型错误】
      - 无（股票无板块时返回空列表，不是错误）
    routing: get_blocks_for_stock
    upstream_tool_mapping:
      mootdx2: get_blocks_for_stock
    cache_ttl_key: null
    dangerous: false
    params:
      - name: stock_code
        type: str
        required: true
        description: 股票代码，如 '600000' 或 'sh600000'
      - name: market
        type: str
        required: false
        description: 市场，默认 'auto'（自动推断）。可选 sh/sz/bj/auto
```

- [ ] **Step 3: Verify YAML is valid**

```bash
cd D:/fintech_workspace/unihive && python -c "import yaml; yaml.safe_load(open('config/tools_mootdx2.yaml'))" && echo "YAML valid"
```

Expected: No output (no error) + "YAML valid"

- [ ] **Step 4: Commit**

```bash
git add config/tools_mootdx2.yaml
git commit -m "feat(config): add get_stocks_in_block and get_blocks_for_stock to tools_mootdx2.yaml"
```

---

## Task 5: Verify end-to-end

**Files:**
- None (verification only)

- [ ] **Step 1: Start gateway and test the new tools**

```bash
cd D:/fintech_workspace/unihive && python -m pytest tests/test_load_all_tools.py -v -k "mootdx2" 2>&1 | head -40
```

Expected: All tool loading tests PASS (no duplicate routing keys)

- [ ] **Step 2: Check console MCP APIs tab shows the new tools**

Start gateway, visit console, verify the new tools appear in the MCP APIs tab under mootdx2 source.

- [ ] **Step 3: Commit final**

```bash
git add -A && git commit -m "feat: complete block query MCP tools — get_stocks_in_block and get_blocks_for_stock"
```

---

## Self-Review Checklist

- [ ] `call_tool` method_map has both new entries
- [ ] YAML entries have `routing` matching the method name (mootdx2 client uses method name directly)
- [ ] No duplicate routing keys across all tools in tools_mootdx2.yaml
- [ ] Tests use real `MooTDX2Config` and `MooTDX2Client` (not mocked BlockReader in happy-path tests)
- [ ] Graceful degradation when TDX dir not configured (return empty list or None, not exception)
- [ ] All existing tests still pass
