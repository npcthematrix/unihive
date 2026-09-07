# MooTDX2 板块（sector）工具：3 工具在线→离线切换 + 2 个自定义板块工具设计方案

## 命名标准化

**核心约定**：英文统一用 `sector`，中文统一用 `板块`。全文不再出现 `block` / `board` 这两个词（除 TDX 本地文件目录名 `vipdoc/block/` 和 `tdxpy.reader.BlockReader` 类名这种**外部依赖名**外）。

| 类别 | 名称 |
|------|------|
| 工具 | `get_sector_list` / `get_sector_stocks` / `get_stock_sectors` / `get_custom_sector_list` / `get_custom_sector_stocks` |
| 参数 | `sector_type` (str: `industry`/`concept`/`region`/`custom`)、`sector_code` |
| 返回字段 | `sector_name` / `sector_type` |
| 错误类型 | `tdx_sector_file_missing` / `tdx_custom_sector_unavailable` / `sector_not_found` |
| 内部方法 | `_get_sector_list_sync` / `_sector_stocks_sync` / `_stock_sectors_sync` / `_get_custom_sector_list_sync` / `_get_custom_sector_stocks_sync` |
| 测试文件 | `tests/test_mootdx2_sector.py`（合并现有 `test_mootdx2_block_query.py`） |

## 1. 背景

MooTDX2 封装的板块相关工具在代码层和文档层有错位：

- 已有 `get_block` / `get_stocks_in_block` / `get_blocks_for_stock` 三个工具
- `get_stocks_in_block` 和 `get_blocks_for_stock` 实际已读本地 `.dat` 文件（离线）
- `get_block` 错用 `q.block()` Quote API（在线），但描述同列在板块类
- 三个 YAML 描述全标 `【· 在线】`，与实际行为不符

另外两个工具完全缺失：

- `get_custom_sector_list`：通达信客户端本地自定义板块（自选股分组）清单
- `get_custom_sector_stocks`：自定义板块成分股

本设计把 3 个工具对齐为"真离线"（含修 `get_block` 的实现），并新增 2 个自定义板块工具。同时统一命名为 `sector` 字眼。

## 2. 范围

**改动**：
1. 修 `get_block` → 重命名为 `get_sector_list`，实现改用 `tdxpy.reader.BlockReader` 读 `vipdoc/block/*.dat`
2. `get_stocks_in_block` → 重命名为 `get_sector_stocks`（已是本地实现）
3. `get_blocks_for_stock` → 重命名为 `get_stock_sectors`（已是本地实现）
4. 新增 `get_custom_sector_list` 工具
5. 新增 `get_custom_sector_stocks` 工具
6. 新增 3 个错误类型：`tdx_sector_file_missing` / `tdx_custom_sector_unavailable` / `sector_not_found`
7. 合并测试文件 `tests/test_mootdx2_block_query.py` → `tests/test_mootdx2_sector.py`

**不改**：
- `tdxdir` 配置项（已存在，复用）
- cache 层（本设计无缓存）
- 工具 method_map 结构（YAML `routing` 已经是工具名，client 已按工具名分发）

## 3. 架构

### 数据源

| 工具组 | 数据源 | 文件位置 | 更新频率 |
|--------|--------|----------|----------|
| 官方板块（3 工具） | 通达信客户端盘后拉取 | `{tdxdir}/vipdoc/block/block_ch.dat` / `block_zs.dat` / `block_fd.dat` | 日级（盘后） |
| 自定义板块（2 工具） | 用户在 TDX 客户端手动维护 | `{tdxdir}/T0002/blocknew/blocknew.cfg` + `*.blk` | 用户操作触发 |

### Reader 选择

| 工具 | 解析器 | 模式 |
|------|--------|------|
| `get_sector_list` | `tdxpy.reader.BlockReader` | `GROUP`（一行一板块，code_list 逗号分隔） |
| `get_sector_stocks` | `tdxpy.reader.BlockReader` | `GROUP`（按 sector_name 过滤行，拆 code_list） |
| `get_stock_sectors` | `tdxpy.reader.BlockReader` | `FLAT`（按 code 过滤行） |
| `get_custom_sector_list` | `mootdx.tools.customize.Customize.search(group=True)` | 内置 `CustomerBlockReader` |
| `get_custom_sector_stocks` | `Customize.search(name=...)` | 内置 `CustomerBlockReader` |

## 4. 组件改动清单

### 4.1 `src/mootdx2_client.py`

**重命名 + 修改**：

- `_get_block_sync` (line 699) → `_get_sector_list_sync`：换实现
  - 旧：`q.block(block_type=...)` 拉在线
  - 新：`tdxpy.reader.BlockReader().get_df(sector_path, result_type=1)` 读本地，按 `sector_type` 选文件
- `get_block` (line 707) → `get_sector_list`：参数类型 `int` 改 `str`（`industry`/`concept`/`region`）
- `_stocks_in_block_sync` (line 725) → `_sector_stocks_sync`：参数 `block_type` 由 `int` 改 `str`，内部映射
- `get_stocks_in_block` (line 775) → `get_sector_stocks`：参数同步
- `_blocks_for_stock_sync` (line 795) → `_stock_sectors_sync`：内部变量 `block_files` 改 `sector_files`
- `get_blocks_for_stock` (line 838) → `get_stock_sectors`：方法名重命名，**参数不变**（无 `sector_type` 参数，内部固定遍历 3 种类型）

**新增**：

- `_get_custom_sector_list_sync()` → `get_custom_sector_list()`
- `_get_custom_sector_stocks_sync(sector_code, market='auto')` → `get_custom_sector_stocks()`
- 2 个新工具在 method_map 注册

### 4.2 `config/tools_mootdx2.yaml`

**修改**（3 个工具重命名 + 标记离线）：

- `get_block` → `get_sector_list`：标记 `【· 离线】`、补数据来源、补错误类型
- `get_stocks_in_block` → `get_sector_stocks`
- `get_blocks_for_stock` → `get_stock_sectors`

**新增**（2 个工具）：

- `get_custom_sector_list`
- `get_custom_sector_stocks`

### 4.3 `src/mootdx2_errors.py`

**新增**：

- `TDX_SECTOR_FILE_MISSING = "tdx_sector_file_missing"`
- `TDX_CUSTOM_SECTOR_UNAVAILABLE = "tdx_custom_sector_unavailable"`
- `SECTOR_NOT_FOUND = "sector_not_found"`

每个错误类型带 `message` 模板（中文）。

### 4.4 `tests/test_mootdx2_sector.py`（新建，覆盖原 block_query）

mock 工具：
- mock `BlockReader` 返回固定 DataFrame
- mock `Customize.search` 返回固定列表
- mock `Path.exists` 控制文件存在/不存在
- mock `tdxdir` 路径探测

覆盖：
- `get_sector_list` 3 种 sector_type 路径
- `get_sector_stocks` 命中 / 未命中
- `get_stock_sectors` 多类型聚合 / 空结果
- `get_custom_sector_list` 启用 / 探测失败
- `get_custom_sector_stocks` 命中 / 板块不存在
- 5 个错误类型各自触发路径

## 5. 数据流

### `get_sector_list` 调用链

```
MCP caller
  → get_sector_list(sector_type="industry")
  → run_in_executor(_get_sector_list_sync, "industry")
  → sector_path = tdxdir/vipdoc/block/block_ch.dat
  → exists() check
       ├─ False → ToolResult(success=False, error=tdx_not_installed 或 tdx_sector_file_missing)
       └─ True
            → BlockReader().get_df(path, result_type=1)  # GROUP
            → df.iterrows() 构造 [{"sector_name", "sector_type", "stock_count"}]
            → ToolResult(success=True, data=[...])
```

### `get_sector_stocks` 调用链

```
MCP caller
  → get_sector_stocks(sector_code="银行板块", sector_type="industry")
  → run_in_executor(_sector_stocks_sync, "银行板块", "industry")
  → sector_path = tdxdir/vipdoc/block/block_ch.dat
  → exists() check (失败同上)
  → BlockReader().get_df(path, result_type=1)  # GROUP 拿到 code_list
  → for row in df:
       if row.sector_name == "银行板块":
          parse code_list → [{"code", "market"}]
  → 命中返回 / 未命中返回 [] (不报错)
```

### `get_stock_sectors` 调用链

```
MCP caller
  → get_stock_sectors(stock_code="600000")
  → run_in_executor(_stock_sectors_sync, "600000", "auto")
  → normalize: strip sh/sz/bj prefix → "600000"
  → for sector_type in [industry, concept, region]:
       sector_path = tdxdir/vipdoc/block/block_*.dat
       BlockReader().get_df(path, result_type=0)  # FLAT
       df[df.code == "600000"]
       → append {"sector_name", "sector_type"}
  → return aggregated list
```

### `get_custom_sector_list` 调用链

```
MCP caller
  → get_custom_sector_list()
  → run_in_executor(_get_custom_sector_list_sync)
  → vipdoc = tdxdir/T0002/blocknew/
  → exists() check
       ├─ False → ToolResult(success=False, error=tdx_custom_sector_unavailable)
       └─ True
            → Customize(tdxdir=tdxdir).search(group=True)
            → CustomerBlockReader().get_df(vipdoc, TYPE_GROUP)
            → [{"sector_name", "stock_count", "sector_type": "custom"}]
```

### `get_custom_sector_stocks` 调用链

```
MCP caller
  → get_custom_sector_stocks(sector_code="我的持仓")
  → run_in_executor(_get_custom_sector_stocks_sync, "我的持仓", "auto")
  → vipdoc = tdxdir/T0002/blocknew/  (同上探测)
  → Customize.search(name="我的持仓")
       ├─ returns None → ToolResult(success=False, error=sector_not_found)
       └─ returns [code1, code2, ...]
            → normalize each: 前缀推断 sh/sz/bj
            → [{"code", "market"}]
```

## 6. 错误处理

| 错误类型 | 触发条件 | HTTP-ish 状态 |
|----------|----------|---------------|
| `tdx_not_installed` | `tdxdir` 为空或目录不存在 | 503 |
| `tdx_sector_file_missing` | `vipdoc/block/<type>.dat` 不存在（任一官方文件） | 503 |
| `tdx_custom_sector_unavailable` | `T0002/blocknew/` 不存在或 `blocknew.cfg` 解析失败 | 503 |
| `sector_not_found` | 板块名在文件中未找到 | 404 |
| `stock_not_found` | 股票代码长度不是 6 位纯数字 | 400 |
| 已有 `internal_error` | 其它未预期异常 | 500 |

**关键不变量**：
- "股票存在但当前无板块归属" = **空列表 + success=True**，不是错误
- "板块存在但当前无成分股" = **空列表 + success=True**，不是错误
- 区分"TDX 客户端未配置"（503，运维问题）和"调用方传的参数找不到"（404，业务问题）

## 7. 编码体系一致性

### 板块标识符

| 工具 | 返回字段 | 入参字段 | 数据类型 |
|------|----------|----------|----------|
| `get_sector_list` | `sector_name` | — | str（中文） |
| `get_sector_stocks` | — | `sector_code` | str（中文） |
| `get_stock_sectors` | `sector_name` | — | str（中文） |
| `get_custom_sector_list` | `sector_name` | — | str（中文） |
| `get_custom_sector_stocks` | — | `sector_code` | str（中文） |

**官方/自定义板块名不会冲突走不同工具**，但调用方应明确知道：官方板块名是 "XX板块" / "XX概念" 等通用分类，用户自定义板块名是个人标签。

### 股票代码

| 工具 | 输出格式 | 输入接受 |
|------|----------|----------|
| 内部存储 | 6 位纯数字（如 `600000`） | `600000` / `sh600000` / `SH600000` |
| 对外暴露 | `[{"code": "600000", "market": "sh"}]` | 接受 sh/sz/bj 前缀并归一化 |

**与项目其他工具一致**（`get_kline` / `get_quote` / `get_quote_batch` 同样输出 `{code, market}` 结构）。

## 8. 缓存策略

**不缓存**。每次调用都读盘 + 解析。

**理由**：
- `.dat` 解析很快（BLOCK 模式下，单个文件 < 50ms）
- 文件由 TDX 客户端盘后更新，本工具链路上无中间陈旧层
- 调用方拿到的总是当前 `.dat` 状态
- 避免引入新缓存键和失效逻辑

## 9. 测试策略

### 9.1 单元测试 (`tests/test_mootdx2_sector.py`)

mock 重点：
- `Path.exists` — 控制 4 种文件存在性组合
- `tdxpy.reader.BlockReader` — 返回固定 DataFrame
- `mootdx.tools.customize.Customize` — 返回固定 list

### 9.2 覆盖矩阵

| 工具 | 测试场景 | 期望结果 |
|------|----------|----------|
| `get_sector_list` | tdxdir 空 | `tdx_not_installed` |
| `get_sector_list` | block_ch.dat 不存在 | `tdx_sector_file_missing` |
| `get_sector_list` | sector_type=industry 命中 | success, 行业板块列表 |
| `get_sector_list` | sector_type=concept 命中 | success, 概念板块列表 |
| `get_sector_stocks` | 命中"银行板块" | success, 成分股列表 |
| `get_sector_stocks` | 板块名不在文件 | success, 空列表 |
| `get_stock_sectors` | 600000 命中 3 个板块 | success, 3 元素 |
| `get_stock_sectors` | 不存在的代码 | success, 空列表 |
| `get_stock_sectors` | 代码格式错（`< 6` 位） | `stock_not_found` |
| `get_custom_sector_list` | T0002/blocknew 不存在 | `tdx_custom_sector_unavailable` |
| `get_custom_sector_list` | blocknew.cfg 存在 | success, 自定义板块列表 |
| `get_custom_sector_stocks` | 命中"我的持仓" | success, 成分股列表 |
| `get_custom_sector_stocks` | 板块名不存在 | `sector_not_found` |

### 9.3 不做的测试

- 真实 TDX 文件解析（依赖本机 TDX 安装，无 CI 价值）
- 缓存层（本设计不引入）
- 网络 IO（已是离线工具）

## 10. 已知限制

将追加到 `docs/mootdx2-known-limitations.md`：

```markdown
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
```

## 11. 实施步骤概要

按 `superpowers:writing-plans` 流程产出实施计划，预期任务序列：

1. 在 `src/mootdx2_errors.py` 新增 3 个错误类型
2. 改 `_get_sector_list_sync` 实现 + 调整 `get_sector_list` 签名
3. 改 `_sector_stocks_sync` / `get_sector_stocks` 参数类型 int→str
4. 改 `_stock_sectors_sync` / `get_stock_sectors` 内部变量名
5. 加 `_get_custom_sector_list_sync` + `get_custom_sector_list`
6. 加 `_get_custom_sector_stocks_sync` + `get_custom_sector_stocks`
7. 在 method_map 注册 2 个新工具 + 移除 3 个旧工具
8. 改 YAML：3 个旧工具 entry 重命名 + 标【· 离线】
9. 加 YAML：2 个新工具 entry
10. 删除 `tests/test_mootdx2_block_query.py`，新建 `tests/test_mootdx2_sector.py`（13 个用例）
11. 更新 `docs/mootdx2-known-limitations.md`
12. 跑测试，提交

## 12. 交付物

- 代码：3 个重命名 + 2 个新增 client 方法
- 配置：3 个重命名 + 2 个新增 YAML entry
- 测试：1 个新测试文件（13 个用例），删除 1 个旧测试文件
- 错误类型：3 个新常量
- 文档：`docs/superpowers/specs/2026-09-07-moatdx2-sector-tools-design.md`（本文件）
- 实施计划：`docs/superpowers/plans/2026-09-07-moatdx2-sector-tools-plan.md`
- 已知限制：`docs/mootdx2-known-limitations.md` 追加
