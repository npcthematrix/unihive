# MooTDX2 板块（sector）工具实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 3 个板块工具从「block」命名 + 在线标注 重命名为「sector」+ 真离线实现，并新增 2 个自定义板块工具。

**Architecture:**
- 5 个工具统一命名 `sector`，3 个官方板块（行业/概念/地区）走 `tdxpy.reader.BlockReader` 读本地 `vipdoc/block/*.dat`；2 个自定义板块走 `mootdx.tools.customize.Customize` 读 `T0002/blocknew/`。
- 内部 `_sync` 方法走 BlockReader / Customize，async wrapper 走 `run_in_executor` + 错误转换。
- 新增 4 个错误类型常量；`tdxdir` 缺失、文件不存在等"环境问题"返回 503 风格错误，板块名找不到返回 404。

**Tech Stack:** Python 3.10+ / tdxpy / mootdx 0.11.7 / pytest

---

## 文件结构

| 文件 | 角色 | 变更类型 |
|------|------|---------|
| `src/mootdx2_errors.py` | 错误枚举 | 修改（加 4 个枚举值） |
| `src/mootdx2_client.py` | client 实现 | 修改（3 个重命名 + 2 个新增 + 1 个内部 helper） |
| `config/tools_mootdx2.yaml` | 工具描述 | 修改（3 个重命名 + 2 个新增 entry） |
| `tests/test_mootdx2_sector.py` | 新测试 | 新建 |
| `tests/test_mootdx2_block_query.py` | 旧测试 | 删除 |
| `tests/test_console_interfaces.py` | 工具计数 | 修改（138 → 137） |
| `docs/mootdx2-known-limitations.md` | 已知限制 | 修改（追加板块工具小节） |
| `CLAUDE.md` | 文档 | 修改（"138 个" → "137 个"） |

---

## 任务依赖图

```
Task 1 (errors) ─┬─> Task 2 (rename get_block → get_sector_list)
                 ├─> Task 3 (rename get_stocks_in_block → get_sector_stocks)
                 ├─> Task 4 (rename get_blocks_for_stock → get_stock_sectors)
                 ├─> Task 5 (add get_custom_sector_list)
                 ├─> Task 6 (add get_custom_sector_stocks)
                 ├─> Task 7 (method_map: -3 +2)
                 ├─> Task 8 (YAML: rename 3)
                 ├─> Task 9 (YAML: add 2)
                 └─> Task 10 (test_mootdx2_sector.py)

Task 10 ──> Task 11 (test_console_interfaces.py: 138 → 137)
        └─> Task 12 (docs/mootdx2-known-limitations.md)
        └─> Task 13 (CLAUDE.md + final run)
```

---

## Task 1: 新增 4 个错误类型枚举值

**Files:**
- Modify: `src/mootdx2_errors.py:11-32`（在 `MooTDXErrorType` enum 中追加）

- [ ] **Step 1: 编辑 `MooTDXErrorType` enum**

在 `INTERNAL_ERROR` 后追加：

```python
# 板块数据相关
TDX_NOT_INSTALLED = "tdx_not_installed"  # tdxdir 为空或目录不存在
TDX_SECTOR_FILE_MISSING = "tdx_sector_file_missing"  # vipdoc/block/*.dat 不存在
TDX_CUSTOM_SECTOR_UNAVAILABLE = "tdx_custom_sector_unavailable"  # T0002/blocknew/ 不存在
SECTOR_NOT_FOUND = "sector_not_found"  # 板块名在文件中未找到
STOCK_NOT_FOUND = "stock_not_found"  # 股票代码格式错误
```

- [ ] **Step 2: 验证 enum 加载**

```bash
python -c "from src.mootdx2_errors import MooTDXErrorType; assert MooTDXErrorType.TDX_SECTOR_FILE_MISSING.value == 'tdx_sector_file_missing'"
```

- [ ] **Step 3: Commit**

```bash
git add src/mootdx2_errors.py
git commit -m "feat(mootdx2_errors): add 5 sector-related error types"
```

---

## Task 2: 重命名 `get_block` → `get_sector_list` 并改用 BlockReader

**Files:**
- Modify: `src/mootdx2_client.py:699-722`（`_get_block_sync` 和 `get_block`）

- [ ] **Step 1: 重写 `_get_block_sync` 为 `_get_sector_list_sync`**

替换 line 699-706（`# ========== 板块数据 ==========` 注释下的方法）为：

```python
    def _get_sector_list_sync(self, sector_type: str = "industry"):
        """同步读取板块列表（本地 vipdoc/block/*.dat）

        Args:
            sector_type: industry / concept / region

        Returns:
            list[dict] or raises TdxSectorFileMissing
        """
        tdxdir = self.config.settings.tdxdir if self.config.settings else ""
        if not tdxdir:
            raise SectorDataError(MooTDXErrorType.TDX_NOT_INSTALLED, "TDX 安装目录未配置")

        sector_file_map = {
            "industry": "block_ch.dat",
            "concept": "block_zs.dat",
            "region": "block_fd.dat",
        }
        filename = sector_file_map.get(sector_type)
        if filename is None:
            raise SectorDataError(
                MooTDXErrorType.INVALID_PARAM,
                f"不支持的 sector_type: {sector_type}（必须为 industry/concept/region）",
            )

        sector_path = Path(tdxdir) / "vipdoc" / "block" / filename
        if not sector_path.exists():
            raise SectorDataError(
                MooTDXErrorType.TDX_SECTOR_FILE_MISSING,
                f"板块文件不存在: {sector_path}",
            )

        from tdxpy.reader import BlockReader
        reader = BlockReader()
        df = reader.get_df(str(sector_path), result_type=1)
        if df is None or df.empty:
            return []

        result = []
        for _, row in df.iterrows():
            code_list = str(row.get("code_list", ""))
            stock_count = len([c for c in code_list.split(",") if c.strip()])
            result.append({
                "sector_name": str(row.get("blockname", "")).strip(),
                "sector_type": sector_type,
                "stock_count": stock_count,
            })
        return result
```

- [ ] **Step 2: 重写 `get_block` 为 `get_sector_list`**

替换 line 707-722：

```python
    async def get_sector_list(self, sector_type: str = "industry") -> ToolResult:
        """获取板块列表（行业/概念/地区）

        Args:
            sector_type: 板块类型，默认 'industry'
                - 'industry': 行业板块（block_ch.dat）
                - 'concept':  概念板块（block_zs.dat）
                - 'region':   地区板块（block_fd.dat）

        数据源：通达信客户端本地 {tdxdir}/vipdoc/block/block_*.dat
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(
                None, self._get_sector_list_sync, sector_type
            )
            return ToolResult(success=True, data=data, source="mootdx2")
        except SectorDataError as e:
            return self._sector_error_result(e)
        except Exception as e:
            logger.error(f"get_sector_list failed: {e}")
            return self._error_result(e, f"get_sector_list({sector_type})")
```

- [ ] **Step 3: 验证 client 仍可导入**

```bash
python -c "from src.mootdx2_client import MooTDX2Client; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add src/mootdx2_client.py
git commit -m "refactor(mootdx2): rename get_block to get_sector_list, use BlockReader"
```

---

## Task 3: 重命名 `get_stocks_in_block` → `get_sector_stocks`（int → str）

**Files:**
- Modify: `src/mootdx2_client.py:725-792`（`_stocks_in_block_sync` 和 `get_stocks_in_block`）

- [ ] **Step 1: 重写 `_stocks_in_block_sync` 为 `_sector_stocks_sync`**

替换 line 725-773（保留行末的 `# ========== 个股所属板块查询 ==========` 注释上方全部）：

```python
    def _sector_stocks_sync(self, sector_code: str, sector_type: str = "industry") -> list:
        """同步获取板块成分股（本地 block .dat 文件）

        Args:
            sector_code: 板块名称（中文，如 '银行板块'）
            sector_type: industry / concept / region
        """
        tdxdir = self.config.settings.tdxdir if self.config.settings else ""
        if not tdxdir:
            raise SectorDataError(MooTDXErrorType.TDX_NOT_INSTALLED, "TDX 安装目录未配置")

        sector_file_map = {
            "industry": "block_ch.dat",
            "concept": "block_zs.dat",
            "region": "block_fd.dat",
        }
        filename = sector_file_map.get(sector_type, "block_ch.dat")
        sector_path = Path(tdxdir) / "vipdoc" / "block" / filename

        if not sector_path.exists():
            raise SectorDataError(
                MooTDXErrorType.TDX_SECTOR_FILE_MISSING,
                f"板块文件不存在: {sector_path}",
            )

        from tdxpy.reader import BlockReader
        reader = BlockReader()
        df = reader.get_df(str(sector_path), result_type=1)
        if df is None or df.empty:
            return []

        for _, row in df.iterrows():
            bn = str(row.get("blockname", "")).strip()
            if bn == sector_code:
                code_list = str(row.get("code_list", ""))
                stock_codes = [c.strip() for c in code_list.split(",") if c.strip()]
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

        # 板块名未找到 → 业务 404
        raise SectorDataError(
            MooTDXErrorType.SECTOR_NOT_FOUND,
            f"板块 '{sector_code}' 不存在于 {sector_type}（{filename}）",
        )
```

- [ ] **Step 2: 重写 `get_stocks_in_block` 为 `get_sector_stocks`**

替换 line 775-792：

```python
    async def get_sector_stocks(self, sector_code: str, sector_type: str = "industry") -> ToolResult:
        """获取指定板块的成分股

        Args:
            sector_code: 板块名称（中文）
            sector_type: 板块类型，默认 'industry'
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(
                None, self._sector_stocks_sync, sector_code, sector_type
            )
            return ToolResult(success=True, data=data, source="mootdx2")
        except SectorDataError as e:
            return self._sector_error_result(e)
        except Exception as e:
            logger.error(f"get_sector_stocks failed: {e}")
            return self._error_result(e, f"get_sector_stocks({sector_code})")
```

- [ ] **Step 3: 验证导入**

```bash
python -c "from src.mootdx2_client import MooTDX2Client; c = MooTDX2Client.__dict__; assert 'get_sector_stocks' in MooTDX2Client.__dict__"
```

- [ ] **Step 4: Commit**

```bash
git add src/mootdx2_client.py
git commit -m "refactor(mootdx2): rename get_stocks_in_block to get_sector_stocks, int→str"
```

---

## Task 4: 重命名 `get_blocks_for_stock` → `get_stock_sectors`（内部变量重命名 + 错误类型）

**Files:**
- Modify: `src/mootdx2_client.py:795-852`（`_blocks_for_stock_sync` 和 `get_blocks_for_stock`）

- [ ] **Step 1: 重写 `_blocks_for_stock_sync` 为 `_stock_sectors_sync`**

替换 line 795-836：

```python
    def _stock_sectors_sync(self, stock_code: str, market: str = "auto") -> list:
        """同步获取股票所属的所有板块（本地 block .dat 文件）

        Args:
            stock_code: 股票代码（接受 sh/sz/bj 前缀）
            market: 市场（auto/auto 推断）
        """
        tdxdir = self.config.settings.tdxdir if self.config.settings else ""
        if not tdxdir:
            raise SectorDataError(MooTDXErrorType.TDX_NOT_INSTALLED, "TDX 安装目录未配置")

        code = stock_code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        if not code.isdigit() or len(code) != 6:
            raise SectorDataError(
                MooTDXErrorType.STOCK_NOT_FOUND,
                f"股票代码格式错误: '{stock_code}'（需 6 位数字）",
            )

        sector_type_names = {
            "industry": "industry",
            "concept": "concept",
            "region": "region",
        }
        sector_files = {
            "industry": "block_ch.dat",
            "concept": "block_zs.dat",
            "region": "block_fd.dat",
        }

        results = []
        for sector_type, filename in sector_files.items():
            sector_path = Path(tdxdir) / "vipdoc" / "block" / filename
            if not sector_path.exists():
                continue
            try:
                from tdxpy.reader import BlockReader
                reader = BlockReader()
                df = reader.get_df(str(sector_path), result_type=0)
                if df is None or df.empty:
                    continue
                matching = df[df["code"] == code]
                for _, row in matching.iterrows():
                    results.append({
                        "sector_name": str(row.get("blockname", "")),
                        "sector_type": sector_type,
                    })
            except Exception as e:
                logger.error(f"_stock_sectors_sync [{filename}]: {e}")
                continue

        return results
```

- [ ] **Step 2: 重写 `get_blocks_for_stock` 为 `get_stock_sectors`**

替换 line 838-852：

```python
    async def get_stock_sectors(self, stock_code: str, market: str = "auto") -> ToolResult:
        """获取指定股票所属的所有板块

        Args:
            stock_code: 股票代码（支持 sh/sz/bj 前缀）
            market: 市场，默认为 'auto'
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(
                None, self._stock_sectors_sync, stock_code, market
            )
            return ToolResult(success=True, data=data, source="mootdx2")
        except SectorDataError as e:
            return self._sector_error_result(e)
        except Exception as e:
            logger.error(f"get_stock_sectors failed: {e}")
            return self._error_result(e, f"get_stock_sectors({stock_code})")
```

- [ ] **Step 3: 验证导入**

```bash
python -c "from src.mootdx2_client import MooTDX2Client; assert 'get_stock_sectors' in MooTDX2Client.__dict__"
```

- [ ] **Step 4: Commit**

```bash
git add src/mootdx2_client.py
git commit -m "refactor(mootdx2): rename get_blocks_for_stock to get_stock_sectors"
```

---

## Task 5: 添加 `_get_custom_sector_list_sync` 和 `get_custom_sector_list`

**Files:**
- Modify: `src/mootdx2_client.py` 在 `get_stock_sectors` 后（约 line 855 后，保留原 `# ========== 指数概览 ==========` 注释前）

- [ ] **Step 1: 追加两个新方法**

在 `# ========== 指数概览 ==========` 注释前插入：

```python
    # ========== 自定义板块 ==========
    def _get_custom_sector_list_sync(self) -> list:
        """同步读取自定义板块列表（T0002/blocknew/）

        Returns:
            list[dict]: [{"sector_name", "sector_type": "custom", "stock_count"}, ...]
        """
        tdxdir = self.config.settings.tdxdir if self.config.settings else ""
        if not tdxdir:
            raise SectorDataError(MooTDXErrorType.TDX_NOT_INSTALLED, "TDX 安装目录未配置")

        vipdoc = Path(tdxdir) / "T0002" / "blocknew"
        if not vipdoc.exists():
            raise SectorDataError(
                MooTDXErrorType.TDX_CUSTOM_SECTOR_UNAVAILABLE,
                f"自定义板块目录不存在: {vipdoc}",
            )

        try:
            from mootdx.tools.customize import Customize
            customize = Customize(tdxdir=tdxdir)
            # group=True 返回 group 模式列表 [(blockname, [code, code, ...]), ...]
            grouped = customize.search(group=True)
        except Exception as e:
            logger.error(f"Customize.search failed: {e}")
            raise SectorDataError(
                MooTDXErrorType.TDX_CUSTOM_SECTOR_UNAVAILABLE,
                f"解析自定义板块失败: {e}",
            )

        result = []
        for entry in grouped or []:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                name = entry[0]
                codes = entry[1] if isinstance(entry[1], (list, tuple)) else []
            elif isinstance(entry, dict):
                name = entry.get("blockname") or entry.get("name", "")
                codes = entry.get("code_list") or entry.get("codes") or []
            else:
                continue
            result.append({
                "sector_name": str(name).strip(),
                "sector_type": "custom",
                "stock_count": len(codes) if hasattr(codes, "__len__") else 0,
            })
        return result

    async def get_custom_sector_list(self) -> ToolResult:
        """获取自定义板块列表（用户在 TDX 客户端手动维护）

        数据源：{tdxdir}/T0002/blocknew/blocknew.cfg + *.blk
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_custom_sector_list_sync)
            return ToolResult(success=True, data=data, source="mootdx2")
        except SectorDataError as e:
            return self._sector_error_result(e)
        except Exception as e:
            logger.error(f"get_custom_sector_list failed: {e}")
            return self._error_result(e, "get_custom_sector_list()")
```

- [ ] **Step 2: 验证导入**

```bash
python -c "from src.mootdx2_client import MooTDX2Client; assert 'get_custom_sector_list' in MooTDX2Client.__dict__"
```

- [ ] **Step 3: Commit**

```bash
git add src/mootdx2_client.py
git commit -m "feat(mootdx2): add get_custom_sector_list (read T0002/blocknew)"
```

---

## Task 6: 添加 `_get_custom_sector_stocks_sync` 和 `get_custom_sector_stocks`

**Files:**
- Modify: `src/mootdx2_client.py` 在 `get_custom_sector_list` 后追加

- [ ] **Step 1: 追加两个新方法**

紧接 `get_custom_sector_list` 后插入（仍在 `# ========== 指数概览 ==========` 注释前）：

```python
    def _get_custom_sector_stocks_sync(self, sector_code: str, market: str = "auto") -> list:
        """同步读取自定义板块成分股

        Args:
            sector_code: 板块名称（用户自定义）
            market: 市场（保留参数，对齐其它工具签名；内部按代码前缀自动推断）
        """
        tdxdir = self.config.settings.tdxdir if self.config.settings else ""
        if not tdxdir:
            raise SectorDataError(MooTDXErrorType.TDX_NOT_INSTALLED, "TDX 安装目录未配置")

        vipdoc = Path(tdxdir) / "T0002" / "blocknew"
        if not vipdoc.exists():
            raise SectorDataError(
                MooTDXErrorType.TDX_CUSTOM_SECTOR_UNAVAILABLE,
                f"自定义板块目录不存在: {vipdoc}",
            )

        try:
            from mootdx.tools.customize import Customize
            customize = Customize(tdxdir=tdxdir)
            codes = customize.search(name=sector_code)
        except Exception as e:
            logger.error(f"Customize.search(name=) failed: {e}")
            raise SectorDataError(
                MooTDXErrorType.TDX_CUSTOM_SECTOR_UNAVAILABLE,
                f"读取自定义板块失败: {e}",
            )

        if codes is None:
            raise SectorDataError(
                MooTDXErrorType.SECTOR_NOT_FOUND,
                f"自定义板块 '{sector_code}' 不存在",
            )

        result = []
        for code in codes:
            code_str = str(code).strip()
            if code_str.lower().startswith(("sh", "sz", "bj")):
                m = code_str[:2].lower()
                pure = code_str[2:]
            else:
                if code_str.startswith(("60", "68")):
                    m = "sh"
                elif code_str.startswith(("00", "30")):
                    m = "sz"
                elif code_str.startswith(("8", "4")):
                    m = "bj"
                else:
                    m = "sz"
                pure = code_str
            result.append({"code": pure, "market": m})
        return result

    async def get_custom_sector_stocks(
        self, sector_code: str, market: str = "auto"
    ) -> ToolResult:
        """获取自定义板块的成分股

        Args:
            sector_code: 自定义板块名（用户在 TDX 客户端命名的标签）
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(
                None, self._get_custom_sector_stocks_sync, sector_code, market
            )
            return ToolResult(success=True, data=data, source="mootdx2")
        except SectorDataError as e:
            return self._sector_error_result(e)
        except Exception as e:
            logger.error(f"get_custom_sector_stocks failed: {e}")
            return self._error_result(e, f"get_custom_sector_stocks({sector_code})")
```

- [ ] **Step 2: 验证导入**

```bash
python -c "from src.mootdx2_client import MooTDX2Client; assert 'get_custom_sector_stocks' in MooTDX2Client.__dict__"
```

- [ ] **Step 3: Commit**

```bash
git add src/mootdx2_client.py
git commit -m "feat(mootdx2): add get_custom_sector_stocks"
```

---

## Task 7: 在 client 顶部加 `SectorDataError` + `_sector_error_result` helper

**Files:**
- Modify: `src/mootdx2_client.py:1-30` 区域（import 块后、类定义前）

- [ ] **Step 1: 在 imports 后插入 exception + helper**

在 `from src.mootdx2_errors import ...` 那行之后插入：

```python


class SectorDataError(Exception):
    """板块数据相关错误的领域异常

    携带 MooTDXErrorType + 中文消息，由 async wrapper 转 ToolResult。
    """

    def __init__(self, error_type: "MooTDXErrorType", message: str):
        self.error_type = error_type
        self.message = message
        super().__init__(message)
```

- [ ] **Step 2: 在 `_error_result` 后（约 line 130）插入 `_sector_error_result`**

```python
    def _sector_error_result(self, exc: "SectorDataError") -> ToolResult:
        """将 SectorDataError 转换为 ToolResult（不打 error_metrics 计数）"""
        self._metrics["total_errors"] += 1
        error_type = exc.error_type.value
        self._metrics["error_counts"][error_type] = self._metrics["error_counts"].get(error_type, 0) + 1
        return ToolResult(
            success=False,
            error=exc.message,
            error_detail={
                "error_type": error_type,
                "message": exc.message,
                "recoverable": False,
            },
        )
```

- [ ] **Step 3: 验证两个 helper 都可用**

```bash
python -c "from src.mootdx2_client import SectorDataError, MooTDX2Client; print(SectorDataError, MooTDX2Client._sector_error_result)"
```

- [ ] **Step 4: Commit**

```bash
git add src/mootdx2_client.py
git commit -m "feat(mootdx2): add SectorDataError + _sector_error_result helper"
```

---

## Task 8: method_map：-3 +2

**Files:**
- Modify: `src/mootdx2_client.py:256-270` 区域（method_map dict）

- [ ] **Step 1: 替换 3 个旧条目 + 加 2 个新条目**

找到这三行：

```python
            "get_block": self.get_block,
            ...
            "get_stocks_in_block": self.get_stocks_in_block,
            "get_blocks_for_stock": self.get_blocks_for_stock,
```

替换为：

```python
            "get_sector_list": self.get_sector_list,
            "get_sector_stocks": self.get_sector_stocks,
            "get_stock_sectors": self.get_stock_sectors,
            "get_custom_sector_list": self.get_custom_sector_list,
            "get_custom_sector_stocks": self.get_custom_sector_stocks,
```

- [ ] **Step 2: 验证 method_map 键正确**

```bash
python -c "
from src.mootdx2_client import MooTDX2Client, MooTDX2Config
c = MooTDX2Client(MooTDX2Config(name='t', market='std'))
mm = MooTDX2Client.method_map
for name in ['get_sector_list', 'get_sector_stocks', 'get_stock_sectors', 'get_custom_sector_list', 'get_custom_sector_stocks']:
    assert name in mm, f'{name} missing'
for old in ['get_block', 'get_stocks_in_block', 'get_blocks_for_stock']:
    assert old not in mm, f'{old} should be removed'
print('method_map OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add src/mootdx2_client.py
git commit -m "refactor(mootdx2): update method_map: rename 3 block→sector, add 2 custom"
```

---

## Task 9: YAML 工具描述（3 个重命名）

**Files:**
- Modify: `config/tools_mootdx2.yaml` line 617 附近（`get_block` entry 起）、line 648 附近（`get_stocks_in_block`）、line 692 附近（`get_blocks_for_stock`）

- [ ] **Step 1: 重写 `get_block` entry 为 `get_sector_list`**

替换 line 617-644 整段（`- name: get_block` 起到下一个 `- name:` 前）：

```yaml
  - name: get_sector_list
    description: |
      【· 离线】
      【板块列表】读取板块清单。

      【使用场景】
      - 当用户问"有哪些行业板块 / 概念板块 / 地区板块"时调用
      - 板块分析和热点追踪的前置步骤

      【参数说明】
      - sector_type (可选): 板块类型，默认 'industry'
        - 'industry': 行业板块（block_ch.dat）
        - 'concept':  概念板块（block_zs.dat）
        - 'region':   地区板块（block_fd.dat）

      【数据源】通达信客户端本地文件 {tdxdir}/vipdoc/block/block_*.dat

      【返回示例】
      成功: {"success": true, "data": [{"sector_name": "银行板块", "sector_type": "industry", "stock_count": 50}], "source": "mootdx2"}

      【典型错误】
      - tdx_not_installed: TDX 目录未配置
      - tdx_sector_file_missing: 对应 block_*.dat 文件不存在
      - invalid_param: sector_type 不在 industry/concept/region 内
    routing: get_sector_list
    upstream_tool_mapping:
      mootdx2: get_sector_list
    cache_ttl_key: null
    dangerous: false
    params:
      - name: sector_type
        type: str
        required: false
        description: 板块类型，默认 'industry'（行业）
        enum:
          - industry
          - concept
          - region
```

- [ ] **Step 2: 重写 `get_stocks_in_block` entry 为 `get_sector_stocks`**

替换 line 648-683 整段：

```yaml
  - name: get_sector_stocks
    description: |
      【· 离线】
      【板块成分股】查询指定板块包含的所有成分股。

      【使用场景】
      - 用户问"银行板块有哪些股票"时调用
      - 板块内股票筛选、批量行情前置步骤

      【参数说明】
      - sector_code (必填): 板块名称（中文），如 '银行板块'、'猪肉概念'
        注意：TDX 本地文件只存板块名，不存数字板块代码
      - sector_type (可选): 板块类型，默认 'industry'
        'industry' / 'concept' / 'region'

      【数据源】通达信客户端本地 {tdxdir}/vipdoc/block/block_*.dat

      【返回示例】
      成功: {"success": true, "data": [{"code": "600000", "market": "sh"}, ...], "source": "mootdx2"}

      【典型错误】
      - tdx_not_installed / tdx_sector_file_missing: TDX 环境问题
      - sector_not_found: 板块名在文件中未找到
    routing: get_sector_stocks
    upstream_tool_mapping:
      mootdx2: get_sector_stocks
    cache_ttl_key: null
    dangerous: false
    params:
      - name: sector_code
        type: str
        required: true
        description: 板块名称（中文），如 '银行板块'
      - name: sector_type
        type: str
        required: false
        description: 板块类型，默认 industry
        enum:
          - industry
          - concept
          - region
```

- [ ] **Step 3: 重写 `get_blocks_for_stock` entry 为 `get_stock_sectors`**

替换 line 692-721 整段（保留原 `params.stock_code` 部分）：

```yaml
  - name: get_stock_sectors
    description: |
      【· 离线】
      【个股所属板块】查询指定股票所属的所有板块（行业 + 概念 + 地区聚合）。

      【使用场景】
      - 用户问"浦发银行属于哪些板块"时调用
      - 主题投资筛选、交叉板块股票查找

      【参数说明】
      - stock_code (必填): 股票代码，如 '600000'、'000001'
        支持带市场前缀（sh600000）或纯 6 位代码

      【数据源】通达信客户端本地 {tdxdir}/vipdoc/block/block_*.dat（聚合 3 种类型）

      【返回示例】
      成功: {"success": true, "data": [{"sector_name": "银行板块", "sector_type": "industry"}, ...], "source": "mootdx2"}
      空数据: {"success": true, "data": []}（该股票无板块归属是正常数据）

      【典型错误】
      - stock_not_found: 股票代码不是 6 位数字
      - tdx_not_installed: TDX 目录未配置
    routing: get_stock_sectors
    upstream_tool_mapping:
      mootdx2: get_stock_sectors
    cache_ttl_key: null
    dangerous: false
    params:
      - name: stock_code
        type: str
        required: true
        description: 股票代码，如 '600000'
      - name: market
        type: str
        required: false
        description: 市场，默认 'auto'（自动按代码前缀推断）
```

- [ ] **Step 4: 验证 YAML 语法**

```bash
python -c "import yaml; yaml.safe_load(open('config/tools_mootdx2.yaml', encoding='utf-8')); print('YAML OK')"
```

- [ ] **Step 5: Commit**

```bash
git add config/tools_mootdx2.yaml
git commit -m "docs(tools_mootdx2): rename 3 block tools to sector, mark 离线"
```

---

## Task 10: YAML 新增 2 个 entry

**Files:**
- Modify: `config/tools_mootdx2.yaml` 在 `get_stock_sectors` entry 后插入 2 个新 entry

- [ ] **Step 1: 在 `get_stock_sectors` 块结尾后插入 2 个 entry**

紧接 `get_stock_sectors` 的最后一个 `description` 行（或更后的合适位置——即原 line 720+ `block_*.dat` 描述后）插入：

```yaml

  - name: get_custom_sector_list
    description: |
      【· 离线】
      【自定义板块列表】读取用户在 TDX 客户端维护的自定义板块（自选股分组）。

      【使用场景】
      - 用户问"我建了哪些分组 / 自选板块"时调用
      - 个人持仓分组管理

      【数据源】通达信客户端本地 {tdxdir}/T0002/blocknew/blocknew.cfg + *.blk
      【依赖】需要用户在 TDX 客户端内创建过自定义板块；否则返回 tdx_custom_sector_unavailable

      【返回示例】
      成功: {"success": true, "data": [{"sector_name": "我的持仓", "sector_type": "custom", "stock_count": 5}], "source": "mootdx2"}

      【典型错误】
      - tdx_not_installed: TDX 目录未配置
      - tdx_custom_sector_unavailable: T0002/blocknew 目录不存在或 blocknew.cfg 解析失败
    routing: get_custom_sector_list
    upstream_tool_mapping:
      mootdx2: get_custom_sector_list
    cache_ttl_key: null
    dangerous: false
    params: []

  - name: get_custom_sector_stocks
    description: |
      【· 离线】
      【自定义板块成分股】读取指定自定义板块的成分股列表。

      【使用场景】
      - 用户问"我的持仓里有哪几只"或"自选股 XX 包含哪些"时调用

      【参数说明】
      - sector_code (必填): 自定义板块名称（用户在 TDX 客户端命名的标签）

      【数据源】通达信客户端本地 {tdxdir}/T0002/blocknew/

      【返回示例】
      成功: {"success": true, "data": [{"code": "600000", "market": "sh"}, ...], "source": "mootdx2"}

      【典型错误】
      - tdx_not_installed: TDX 目录未配置
      - tdx_custom_sector_unavailable: 自定义板块目录不存在
      - sector_not_found: 自定义板块名未找到
    routing: get_custom_sector_stocks
    upstream_tool_mapping:
      mootdx2: get_custom_sector_stocks
    cache_ttl_key: null
    dangerous: false
    params:
      - name: sector_code
        type: str
        required: true
        description: 自定义板块名（用户标签）
      - name: market
        type: str
        required: false
        description: 市场，默认 'auto'（按代码前缀推断）
```

- [ ] **Step 2: 验证 YAML 语法 + 5 个 entry 都在**

```bash
python -c "
import yaml
data = yaml.safe_load(open('config/tools_mootdx2.yaml', encoding='utf-8'))
names = [t['name'] for t in data.get('tools', [])]
for n in ['get_sector_list', 'get_sector_stocks', 'get_stock_sectors', 'get_custom_sector_list', 'get_custom_sector_stocks']:
    assert n in names, f'{n} missing'
print('YAML tools OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add config/tools_mootdx2.yaml
git commit -m "feat(tools_mootdx2): add 2 custom sector tool entries"
```

---

## Task 11: 删除旧测试文件，新建测试文件（13 个用例）

**Files:**
- Delete: `tests/test_mootdx2_block_query.py`
- Create: `tests/test_mootdx2_sector.py`

- [ ] **Step 1: 删除旧测试文件**

```bash
git rm tests/test_mootdx2_block_query.py
```

- [ ] **Step 2: 新建 `tests/test_mootdx2_sector.py`**

```python
"""Sector tools tests — mock BlockReader and Customize to avoid TDX file dependency."""
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def client():
    from src.mootdx2_client import MooTDX2Client, MooTDX2Config
    return MooTDX2Client(MooTDX2Config(name="test", market="std"))


# ========== get_sector_list ==========

def test_get_sector_list_tdxdir_empty(client):
    """sector_type=industry + tdxdir empty → tdx_not_installed."""
    import asyncio
    r = asyncio.run(client.get_sector_list("industry"))
    assert r.success is False
    assert r.error_detail["error_type"] == "tdx_not_installed"


def test_get_sector_list_file_missing(client):
    """sector file missing → tdx_sector_file_missing."""
    import asyncio
    with patch.object(Path, "exists", return_value=False):
        # also need tdxdir to be non-empty
        client.config.settings = MagicMock()
        client.config.settings.tdxdir = "/fake/tdx"
        r = asyncio.run(client.get_sector_list("industry"))
    assert r.success is False
    assert r.error_detail["error_type"] == "tdx_sector_file_missing"


def test_get_sector_list_invalid_param(client):
    """sector_type not in industry/concept/region → invalid_param."""
    import asyncio
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    r = asyncio.run(client.get_sector_list("bogus"))
    assert r.success is False
    assert r.error_detail["error_type"] == "invalid_param"


def test_get_sector_list_industry_ok(client):
    """industry hits mock BlockReader → success, list of sectors."""
    import asyncio
    import pandas as pd

    mock_df = pd.DataFrame({
        "blockname": ["银行板块", "钢铁板块"],
        "code_list": ["600000,600016", "600019"],
    })

    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"

    mock_reader = MagicMock()
    mock_reader.get_df.return_value = mock_df

    with patch.object(Path, "exists", return_value=True), \
         patch("tdxpy.reader.BlockReader", return_value=mock_reader):
        r = asyncio.run(client.get_sector_list("industry"))

    assert r.success is True
    assert r.data == [
        {"sector_name": "银行板块", "sector_type": "industry", "stock_count": 2},
        {"sector_name": "钢铁板块", "sector_type": "industry", "stock_count": 1},
    ]


def test_get_sector_list_concept_ok(client):
    """concept path → success, different sector_type in output."""
    import asyncio
    import pandas as pd

    mock_df = pd.DataFrame({"blockname": ["猪肉概念"], "code_list": ["002714"]})
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"

    mock_reader = MagicMock()
    mock_reader.get_df.return_value = mock_df
    with patch.object(Path, "exists", return_value=True), \
         patch("tdxpy.reader.BlockReader", return_value=mock_reader):
        r = asyncio.run(client.get_sector_list("concept"))

    assert r.success is True
    assert r.data[0]["sector_type"] == "concept"


# ========== get_sector_stocks ==========

def test_get_sector_stocks_hit(client):
    """sector name matches → return [{code, market}]."""
    import asyncio
    import pandas as pd

    mock_df = pd.DataFrame({
        "blockname": ["银行板块", "钢铁板块"],
        "code_list": ["600000,600016", "600019"],
    })
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_reader = MagicMock()
    mock_reader.get_df.return_value = mock_df
    with patch.object(Path, "exists", return_value=True), \
         patch("tdxpy.reader.BlockReader", return_value=mock_reader):
        r = asyncio.run(client.get_sector_stocks("银行板块", "industry"))

    assert r.success is True
    assert r.data == [
        {"code": "600000", "market": "sh"},
        {"code": "600016", "market": "sh"},
    ]


def test_get_sector_stocks_not_found(client):
    """sector name absent → sector_not_found error."""
    import asyncio
    import pandas as pd

    mock_df = pd.DataFrame({"blockname": ["钢铁板块"], "code_list": ["600019"]})
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_reader = MagicMock()
    mock_reader.get_df.return_value = mock_df
    with patch.object(Path, "exists", return_value=True), \
         patch("tdxpy.reader.BlockReader", return_value=mock_reader):
        r = asyncio.run(client.get_sector_stocks("不存在的板块", "industry"))

    assert r.success is False
    assert r.error_detail["error_type"] == "sector_not_found"


# ========== get_stock_sectors ==========

def test_get_stock_sectors_aggregates_three_types(client):
    """stock code matches across 3 files → returns 3 entries."""
    import asyncio
    import pandas as pd

    industry_df = pd.DataFrame({"blockname": ["银行板块"], "code": ["600000"]})
    concept_df = pd.DataFrame({"blockname": ["大盘股"], "code": ["600000"]})
    region_df = pd.DataFrame({"blockname": ["上海"], "code": ["600000"]})

    mock_reader = MagicMock()
    mock_reader.get_df.side_effect = [industry_df, concept_df, region_df]

    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    with patch.object(Path, "exists", return_value=True), \
         patch("tdxpy.reader.BlockReader", return_value=mock_reader):
        r = asyncio.run(client.get_stock_sectors("600000"))

    assert r.success is True
    assert len(r.data) == 3
    assert {x["sector_type"] for x in r.data} == {"industry", "concept", "region"}


def test_get_stock_sectors_empty_when_no_match(client):
    """stock not in any file → success with empty list (NOT error)."""
    import asyncio
    import pandas as pd

    mock_reader = MagicMock()
    mock_reader.get_df.return_value = pd.DataFrame(columns=["blockname", "code"])

    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    with patch.object(Path, "exists", return_value=True), \
         patch("tdxpy.reader.BlockReader", return_value=mock_reader):
        r = asyncio.run(client.get_stock_sectors("600000"))

    assert r.success is True
    assert r.data == []


def test_get_stock_sectors_bad_format(client):
    """non-6-digit code → stock_not_found error."""
    import asyncio
    r = asyncio.run(client.get_stock_sectors("abc"))
    assert r.success is False
    assert r.error_detail["error_type"] == "stock_not_found"


def test_get_stock_sectors_strip_prefix(client):
    """sh600000 → normalized to 600000, matches rows with code='600000'."""
    import asyncio
    import pandas as pd

    industry_df = pd.DataFrame({"blockname": ["银行板块"], "code": ["600000"]})
    mock_reader = MagicMock()
    mock_reader.get_df.side_effect = [industry_df, pd.DataFrame(), pd.DataFrame()]

    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    with patch.object(Path, "exists", return_value=True), \
         patch("tdxpy.reader.BlockReader", return_value=mock_reader):
        r = asyncio.run(client.get_stock_sectors("sh600000"))

    assert r.success is True
    assert r.data == [{"sector_name": "银行板块", "sector_type": "industry"}]


# ========== get_custom_sector_list ==========

def test_get_custom_sector_list_unavailable(client):
    """T0002/blocknew missing → tdx_custom_sector_unavailable."""
    import asyncio

    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    # TDX path exists, but T0002/blocknew does not
    def fake_exists(self):
        return "blocknew" not in str(self)
    with patch.object(Path, "exists", fake_exists):
        r = asyncio.run(client.get_custom_sector_list())
    assert r.success is False
    assert r.error_detail["error_type"] == "tdx_custom_sector_unavailable"


def test_get_custom_sector_list_ok(client):
    """Customize.search returns list → success."""
    import asyncio

    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_customize = MagicMock()
    mock_customize.search.return_value = [
        ("我的持仓", ["600000", "000001"]),
        ("观察", ["300750"]),
    ]
    with patch.object(Path, "exists", return_value=True), \
         patch("mootdx.tools.customize.Customize", return_value=mock_customize):
        r = asyncio.run(client.get_custom_sector_list())
    assert r.success is True
    assert r.data == [
        {"sector_name": "我的持仓", "sector_type": "custom", "stock_count": 2},
        {"sector_name": "观察", "sector_type": "custom", "stock_count": 1},
    ]


# ========== get_custom_sector_stocks ==========

def test_get_custom_sector_stocks_hit(client):
    """Customize.search(name=...) returns codes → success."""
    import asyncio

    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_customize = MagicMock()
    mock_customize.search.return_value = ["600000", "sh600016", "000001"]
    with patch.object(Path, "exists", return_value=True), \
         patch("mootdx.tools.customize.Customize", return_value=mock_customize):
        r = asyncio.run(client.get_custom_sector_stocks("我的持仓"))
    assert r.success is True
    assert r.data == [
        {"code": "600000", "market": "sh"},
        {"code": "600016", "market": "sh"},
        {"code": "000001", "market": "sz"},
    ]


def test_get_custom_sector_stocks_not_found(client):
    """Customize.search returns None → sector_not_found."""
    import asyncio

    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_customize = MagicMock()
    mock_customize.search.return_value = None
    with patch.object(Path, "exists", return_value=True), \
         patch("mootdx.tools.customize.Customize", return_value=mock_customize):
        r = asyncio.run(client.get_custom_sector_stocks("不存在的分组"))
    assert r.success is False
    assert r.error_detail["error_type"] == "sector_not_found"
```

- [ ] **Step 3: 跑测试**

```bash
pytest tests/test_mootdx2_sector.py -v
```

预期：13 passed

- [ ] **Step 4: Commit**

```bash
git add tests/test_mootdx2_sector.py
git commit -m "test(mootdx2): add 13 sector tool tests, remove obsolete block_query tests"
```

---

## Task 12: 更新工具计数测试 138 → 137

**Files:**
- Modify: `tests/test_console_interfaces.py:5-30`（`test_get_interfaces_returns_138_tools` 函数）

- [ ] **Step 1: 改 docstring 和断言**

把 line 5-19 的 docstring 追加历史条目：

```
    138 -> 137 (mootdx2 dropped 3 block tools: get_block / get_stocks_in_block /
    get_blocks_for_stock, renamed to get_sector_list / get_sector_stocks /
    get_stock_sectors (still 3 tools); added 2 new custom tools get_custom_sector_list
    / get_custom_sector_stocks; net -1).
```

把 line 30 的 `assert tool_count == 138` 改为 `assert tool_count == 137`。

把函数名改为 `test_get_interfaces_returns_137_tools`（同步）。

- [ ] **Step 2: 跑工具计数测试**

```bash
pytest tests/test_console_interfaces.py::test_get_interfaces_returns_137_tools -v
```

预期：PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_console_interfaces.py
git commit -m "test(console): update tool count 138→137 (sector rename +2-3)"
```

---

## Task 13: 追加 `docs/mootdx2-known-limitations.md` 板块小节

**Files:**
- Modify: `docs/mootdx2-known-limitations.md`（文件末尾追加）

- [ ] **Step 1: 追加章节**

```bash
cat >> docs/mootdx2-known-limitations.md <<'EOF'

## 板块（sector）工具

### 分类体系
- 官方板块仅 3 种：行业（block_ch.dat）、概念（block_zs.dat）、地区（block_fd.dat）
- **不支持**：申万行业、中信行业、风格板块等其它分类体系

### 板块代码
- TDX 本地文件只存储中文板块名（`sector_name`），不存储数字板块代码
- 调用方必须用中文板块名做 `get_sector_stocks` / `get_custom_sector_stocks` 的入参
- 不能跨机器共享板块名（不同 TDX 客户端板块名一致但 ID 不一定一致）

### 自定义板块可用性
- 完全依赖本机 TDX 客户端 + `T0002/blocknew/` 目录
- 自定义板块名是用户个人标签，跨机器无意义
- `blocknew.cfg` 格式由 TDX 客户端定义，本工具不验证内容合法性
EOF
```

- [ ] **Step 2: Commit**

```bash
git add docs/mootdx2-known-limitations.md
git commit -m "docs(mootdx2): append sector tools limitations"
```

---

## Task 14: 更新 CLAUDE.md 工具计数

**Files:**
- Modify: `CLAUDE.md`（找 "138 个" → "137 个"）

- [ ] **Step 1: 修改计数**

```bash
grep -n "138 个\|138 个 MCP" CLAUDE.md
```

把行末的 `138 个 MCP 接口全部通过管理控制台` 改为 `137 个 MCP 接口全部通过管理控制台`。

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update tool count 138→137 in CLAUDE.md"
```

---

## Task 15: 全量跑测试 + 最终验证

- [ ] **Step 1: 跑所有 mootdx2 相关测试**

```bash
pytest tests/test_mootdx2_sector.py tests/test_mootdx2_client.py tests/test_console_interfaces.py -v
```

预期：新 13 个全过 + 已存测试全过（除 9 个已知与本任务无关的 cache/startup 失败）。

- [ ] **Step 2: 跑网关启动 smoke test**

```bash
python -c "from src.gateway_server import main" 2>&1 | head -20
```

预期：导入无 ModuleNotFoundError。

- [ ] **Step 3: 最终 commit（如有遗漏）+ 验证 git log**

```bash
git log --oneline -15
```

预期：看到 12 个新 commit（Tasks 1-14 累计），按依赖顺序排列。

---

## Self-Review

**Spec 覆盖检查**：
- ✅ Section 4.1 改动：Task 2-6 覆盖
- ✅ Section 4.2 YAML：Task 9-10 覆盖
- ✅ Section 4.3 errors：Task 1 覆盖
- ✅ Section 4.4 测试：Task 11 覆盖
- ✅ Section 10 已知限制：Task 13 覆盖
- ✅ Section 12 交付物：CLAUDE.md 更新在 Task 14

**Placeholder scan**：无 TODO/TBD；所有代码块完整。

**Type 一致性**：
- 错误类型字符串："tdx_not_installed" / "tdx_sector_file_missing" / "tdx_custom_sector_unavailable" / "sector_not_found" / "stock_not_found" — 与 Task 1 enum 一致
- 方法签名：`get_sector_list(sector_type: str)` / `get_sector_stocks(sector_code: str, sector_type: str)` / `get_stock_sectors(stock_code: str, market: str)` / `get_custom_sector_list()` / `get_custom_sector_stocks(sector_code: str, market: str)` — Task 2-6 一致
- 内部 _sync 方法：同名 `_xxx_sector_xxx_sync` — 全部一致

**未覆盖**：cache 层（spec 明确说不缓存，不引入新缓存键）。

---

## 交付物汇总

| 类型 | 数量 |
|------|------|
| 错误类型新增 | 5 个（`TDX_NOT_INSTALLED` 顺带） |
| client 方法重命名 | 3 个（block→sector） |
| client 方法新增 | 2 个（custom sector） |
| YAML entry 重命名 | 3 个 |
| YAML entry 新增 | 2 个 |
| 测试用例 | 13 个 |
| 测试文件 | 1 删 1 建 |
| 工具计数变化 | 138 → 137 |
| Commits | ~14 个原子提交 |

**Tool count verification**:
- 起：138（已完成 indicator / trade_history_full / minute_trade_all 删除）
- -3（get_block / get_stocks_in_block / get_blocks_for_stock 删除）
- +2（get_custom_sector_list / get_custom_sector_stocks 新增）
- 终：137 ✅
