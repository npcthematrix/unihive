# Handoff: MOOTDX2 Affair 财务离线接口

**状态**：已完成
**前置 commit**：`ffd6c9d` (feat(mootdx2): add Reader-based offline K-line tools)
**工作树**：clean
**预期产出**：工具总数 143 → 146，offline 11 → 14

## 最终实现（按用户建议优化）

按"使用者实际会怎么问"重新组织了工具，而非直接暴露原始 Affair 接口：

| 新工具 | 描述 |
|--------|------|
| `list_financial_reports` | 列出可用的财务报告（格式化 quarter） |
| `get_financial_summary` | 获取单只股票财务摘要（自动下载+解析+缓存） |
| `sync_financial_reports` | 批量预热财务数据缓存 |

### 核心特性

1. **字段映射**：通达信原始中文字段 → 英文 snake_case（如 `每股收益` → `eps`）
2. **本地缓存**：`{quarter}_parsed.json` 按季度缓存解析后的数据
3. **自动下载**：无缓存时自动触发 `Affair.fetch()` 下载 + `Affair.parse()` 解析
4. **按 quarter 过滤**：`gpcw19960630.zip` → `1996Q2`

### 缓存策略

- 以 `{quarter}.zip` 为单位缓存下载的压缩包
- 以 `{quarter}_parsed.json` 为单位缓存解析后的 DataFrame
- `get_financial_summary` 查询时：查缓存 → 无则下载+解析 → 写缓存 → 返回

## 修改的文件

| 文件 | 改动 |
|------|------|
| `src/mootdx2_client.py` | 添加 3 个工具 + FINANCIAL_FIELD_MAP |
| `config/tools_mootdx2.yaml` | 3 个新 YAML entry |
| `tests/test_mootdx2_reader_offline.py` | 新增测试用例 |
| `tests/test_console_interfaces.py` | 总数 143 → 146 |

## 验收

- ✅ 29 tests passed
- ✅ Total tools: 146
- ✅ Offline tools: 14
