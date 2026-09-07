# MooTDX2 板块工具：3 工具在线→离线切换 + 2 个自定义板块工具设计方案

## 1. 背景

MooTDX2 封装的板块相关工具在代码层和文档层有错位：

- 已有 `get_block` / `get_stocks_in_block` / `get_blocks_for_stock` 三个工具
- `get_stocks_in_block` 和 `get_blocks_for_stock` 实际已读本地 `.dat` 文件（离线）
- `get_block` 错用 `q.block()` Quote API（在线），但描述同列在板块类
- 三个 YAML 描述全标 `【· 在线】`，与实际行为不符

另外两个工具完全缺失：

- `get_custom_board_list`：通达信客户端本地自定义板块（自选股分组）清单
- `get_custom_board_stocks`：自定义板块成分股

本设计把 3 个工具对齐为"真离线"（含修 `get_block` 的实现），并新增 2 个自定义板块工具。

## 2. 范围

**改动**：
1. 修 `get_block` 实现：`q.block()` → `tdxpy.reader.BlockReader` 读 `vipdoc/block/*.dat`
2. 3 个工具 YAML 描述改标 `【· 离线】` 并补充数据来源
3. 新增 `get_custom_board_list` 工具
4. 新增 `get_custom_board_stocks` 工具
5. 新增 3 个错误类型：`tdx_block_file_missing` / `tdx_custom_block_unavailable` / `stock_not_found`

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
| `get_block` | `tdxpy.reader.BlockReader` | `GROUP`（一行一板块，code_list 逗号分隔） |
| `get_stocks_in_block` | `tdxpy.reader.BlockReader` | `GROUP`（按 blockname 过滤行，拆 code_list） |
| `get_blocks_for_stock` | `tdxpy.reader.BlockReader` | `FLAT`（按 code 过滤行） |
| `get_custom_board_list` | `mootdx.tools.customize.Customize.search(group=True)` | 内置 `CustomerBlockReader` |
| `get_custom_board_stocks` | `Customize.search(name=...)` | 内置 `CustomerBlockReader` |

## 4. 组件改动清单

### 4.1 `src/mootdx2_client.py`

**修改**：

- `_get_block_sync` (line 699)：换实现
  - 旧：`q.block(block_type=...)` 拉在线
  - 新：`tdxpy.reader.BlockReader().get_df(block_path, result_type=1)` 读本地，按 `block_type` 选文件
- `get_block` (line 707)：参数类型改 `str`（`'industry' / 'concept' / 'region'`），与 `get_stocks_in_block` 的 `block_type` 命名对齐
- `get_stocks_in_block` (line 775)：参数 `block_type` 由 `int` 改 `str`（`'industry' / 'concept' / 'region'`，与新 `get_block` 保持一致），内部映射到 int 给 `_stocks_in_block_sync`
- `get_blocks_for_stock` (line 838)：**参数不变**（无 `block_type` 参数，内部固定遍历 3 种类型）

**新增**：

- `_get_custom_block_list_sync()` → `get_custom_board_list()`
- `_get_custom_block_stocks_sync(block_code, market='auto')` → `get_custom_board_stocks()`
- 两个错误类型常量在 `src/mootdx2_errors.py` 注册

### 4.2 `config/tools_mootdx2.yaml`

**修改**（3 个工具）：

- `get_block` / `get_stocks_in_block` / `get_blocks_for_stock`：标记 `【· 离线】`、补数据来源、补错误类型

**新增**（2 个工具）：

- `get_custom_board_list`
- `get_custom_board_stocks`

### 4.3 `src/mootdx2_errors.py`

**新增**：

- `TDX_BLOCK_FILE_MISSING = "tdx_block_file_missing"`
- `TDX_CUSTOM_BLOCK_UNAVAILABLE = "tdx_custom_block_unavailable"`
- `STOCK_NOT_FOUND = "stock_not_found"`

每个错误类型带 `message` 模板（中文）。

### 4.4 `tests/test_mootdx2_board.py`（新建）

mock 工具：
- mock `BlockReader` 返回固定 DataFrame
- mock `Customize.search` 返回固定列表
- mock `Path.exists` 控制文件存在/不存在
- mock `tdxdir` 路径探测

覆盖：
- `get_block` 3 种 block_type 路径
- `get_stocks_in_block` 命中 / 未命中
- `get_blocks_for_stock` 多类型聚合 / 空结果
- `get_custom_board_list` 启用 / 探测失败
- `get_custom_board_stocks` 命中 / 板块不存在
- 5 个错误类型各自触发路径

## 5. 数据流

### `get_block` 调用链

```
MCP caller
  → get_block(block_type="industry")
  → run_in_executor(_get_block_sync, "industry")
  → block_path = tdxdir/vipdoc/block/block_ch.dat
  → exists() check
       ├─ False → ToolResult(success=False, error=tdx_not_installed 或 tdx_block_file_missing)
       └─ True
            → BlockReader().get_df(path, result_type=1)  # GROUP
            → df.iterrows() 构造 [{"block_name", "block_type", "stock_count"}]
            → ToolResult(success=True, data=[...])
```

### `get_stocks_in_block` 调用链

```
MCP caller
  → get_stocks_in_block(block_code="银行板块", block_type="industry")
  → run_in_executor(_stocks_in_block_sync, "银行板块", "industry")
  → block_path = tdxdir/vipdoc/block/block_ch.dat
  → exists() check (失败同上)
  → BlockReader().get_df(path, result_type=1)  # GROUP 拿到 code_list
  → for row in df:
       if row.blockname == "银行板块":
          parse code_list → [{"code", "market"}]
  → 命中返回 / 未命中返回 [] (不报错)
```

### `get_blocks_for_stock` 调用链

```
MCP caller
  → get_blocks_for_stock(stock_code="600000")
  → run_in_executor(_blocks_for_stock_sync, "600000", "auto")
  → normalize: strip sh/sz/bj prefix → "600000"
  → for block_type in [industry, concept, region]:
       block_path = tdxdir/vipdoc/block/block_*.dat
       BlockReader().get_df(path, result_type=0)  # FLAT
       df[df.code == "600000"]
       → append {"block_name", "block_type"}
  → return aggregated list
```

### `get_custom_board_list` 调用链

```
MCP caller
  → get_custom_board_list()
  → run_in_executor(_get_custom_block_list_sync)
  → vipdoc = tdxdir/T0002/blocknew/
  → exists() check
       ├─ False → ToolResult(success=False, error=tdx_custom_block_unavailable)
       └─ True
            → Customize(tdxdir=tdxdir).search(group=True)
            → CustomerBlockReader().get_df(vipdoc, TYPE_GROUP)
            → [{"block_name", "stock_count", "block_type": "custom"}]
```

### `get_custom_board_stocks` 调用链

```
MCP caller
  → get_custom_board_stocks(block_code="我的持仓")
  → run_in_executor(_get_custom_block_stocks_sync, "我的持仓", "auto")
  → vipdoc = tdxdir/T0002/blocknew/  (同上探测)
  → Customize.search(name="我的持仓")
       ├─ returns None → ToolResult(success=False, error=block_not_found)
       └─ returns [code1, code2, ...]
            → normalize each: 前缀推断 sh/sz/bj
            → [{"code", "market"}]
```

## 6. 错误处理

| 错误类型 | 触发条件 | HTTP-ish 状态 |
|----------|----------|---------------|
| `tdx_not_installed` | `tdxdir` 为空或目录不存在 | 503 |
| `tdx_block_file_missing` | `vipdoc/block/<type>.dat` 不存在（任一官方文件） | 503 |
| `tdx_custom_block_unavailable` | `T0002/blocknew/` 不存在或 `blocknew.cfg` 解析失败 | 503 |
| `block_not_found` | 板块名在文件中未找到 | 404 |
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
| `get_block` | `block_name` | — | str（中文） |
| `get_stocks_in_block` | — | `block_code` | str（中文） |
| `get_blocks_for_stock` | `block_name` | — | str（中文） |
| `get_custom_board_list` | `block_name` | — | str（中文） |
| `get_custom_board_stocks` | — | `block_code` | str（中文） |

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

### 9.1 单元测试 (`tests/test_mootdx2_board.py`)

mock 重点：
- `Path.exists` — 控制 4 种文件存在性组合
- `tdxpy.reader.BlockReader` — 返回固定 DataFrame
- `mootdx.tools.customize.Customize` — 返回固定 list

### 9.2 覆盖矩阵

| 工具 | 测试场景 | 期望结果 |
|------|----------|----------|
| `get_block` | tdxdir 空 | `tdx_not_installed` |
| `get_block` | block_ch.dat 不存在 | `tdx_block_file_missing` |
| `get_block` | block_type=industry 命中 | success, 行业板块列表 |
| `get_block` | block_type=concept 命中 | success, 概念板块列表 |
| `get_stocks_in_block` | 命中"银行板块" | success, 成分股列表 |
| `get_stocks_in_block` | 板块名不在文件 | success, 空列表 |
| `get_blocks_for_stock` | 600000 命中 3 个板块 | success, 3 元素 |
| `get_blocks_for_stock` | 不存在的代码 | success, 空列表 |
| `get_blocks_for_stock` | 代码格式错（`< 6` 位） | `stock_not_found` |
| `get_custom_board_list` | T0002/blocknew 不存在 | `tdx_custom_block_unavailable` |
| `get_custom_board_list` | blocknew.cfg 存在 | success, 自定义板块列表 |
| `get_custom_board_stocks` | 命中"我的持仓" | success, 成分股列表 |
| `get_custom_board_stocks` | 板块名不存在 | `block_not_found` |

### 9.3 不做的测试

- 真实 TDX 文件解析（依赖本机 TDX 安装，无 CI 价值）
- 缓存层（本设计不引入）
- 网络 IO（已是离线工具）

## 10. 已知限制

将追加到 `docs/mootdx2-known-limitations.md`：

```markdown
## 板块工具

### 分类体系
- 官方板块仅 3 种：行业（block_ch.dat）、概念（block_zs.dat）、地区（block_fd.dat）
- **不支持**：申万行业、中信行业、风格板块等其它分类体系

### 板块代码
- TDX 本地文件只存储中文板块名（`blockname`），不存储数字板块代码
- 调用方必须用中文板块名做 `get_stocks_in_block` / `get_custom_board_stocks` 的入参
- 不能跨机器共享板块名（不同 TDX 客户端板块名一致但 ID 不一定一致）

### 自定义板块可用性
- 完全依赖本机 TDX 客户端 + `T0002/blocknew/` 目录
- 自定义板块名是用户个人标签，跨机器无意义
- `blocknew.cfg` 格式由 TDX 客户端定义，本工具不验证内容合法性
```

## 11. 实施步骤概要

按 `superpowers:writing-plans` 流程产出实施计划，预期任务序列：

1. 在 `src/mootdx2_errors.py` 新增 3 个错误类型
2. 改 `_get_block_sync` 实现 + 调整 `get_block` 签名
3. 改 `get_stocks_in_block` / `get_blocks_for_stock` 参数类型 int→str
4. 加 `_get_custom_block_list_sync` + `get_custom_board_list`
5. 加 `_get_custom_block_stocks_sync` + `get_custom_board_stocks`
6. 在 method_map 注册 2 个新工具
7. 改 YAML 3 个工具的 description
8. 加 YAML 2 个新工具的 entry
9. 写 `tests/test_mootdx2_board.py`（13 个用例）
10. 更新 `docs/mootdx2-known-limitations.md`
11. 跑测试，提交

## 12. 交付物

- 代码：3 个修改 + 2 个新增 client 方法
- 配置：3 个修改 + 2 个新增 YAML entry
- 测试：1 个新测试文件（13 个用例）
- 错误类型：3 个新常量
- 文档：`docs/superpowers/specs/2026-09-07-...-design.md`（本文件）
- 实施计划：`docs/superpowers/plans/2026-09-07-...-plan.md`
- 已知限制：`docs/mootdx2-known-limitations.md` 追加
