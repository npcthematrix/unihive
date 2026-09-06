"""一次性清理脚本：移除非 FUYAO 工具的 cache_ttl_key，删除 announcement 死 TTL 配置。

策略：除了 FUYAO HTTP 上游（fuyao_ashare / fuyao_index / fuyao_meta / fuyao_fund），
其他上游（tdx_local / tokenwave_tdx / tdx_tq_local）的工具不参与缓存。
缓存策略由 gateway._is_fuyao_source 运行时执行；本脚本同步清理 YAML 让声明与策略一致。
"""
import re
from pathlib import Path

YAML = Path("config/upstreams.yaml")

# 非 FUYAO 工具名清单 (tdx_local / tokenwave_tdx)。TQ-Local 工具由 codegen 生成,
# YAML 中无 cache_ttl_key, 这里不需要清理。
NON_FUYAO_TOOLS = {
    # TDX 直通
    "get_quote", "get_kline", "get_minute_data", "get_code_list", "get_batch_quote",
    "get_kline_history", "get_kline_all", "get_kline_all_tdx", "get_kline_all_ths",
    "get_index_kline", "get_index_all", "get_market_count", "get_stock_codes",
    "get_etf_codes", "get_etf_list", "get_workday", "get_workday_range",
    # MooTDX
    "moo_realtime_quote", "moo_minute_bar", "moo_daily_bar", "moo_kline",
    "moo_block_data", "moo_trade_dates", "moo_etf_list",
}


def remove_cache_ttl_key(line: str) -> str:
    """如果行内出现非 FUYAO 工具名，去掉其 cache_ttl_key 子句。"""
    m = re.match(r"\s*-\s*\{name:\s*(\w+)\s*,", line)
    if not m:
        return line
    name = m.group(1)
    if name in NON_FUYAO_TOOLS:
        # 去掉 ", cache_ttl_key: <anything>"
        return re.sub(r",\s*cache_ttl_key:\s*\w+", "", line)
    return line


def remove_announcement_ttl(text: str) -> str:
    """从 cache.ttl 段删除 announcement 行。"""
    return re.sub(r"^\s*announcement:\s*\d+\s*\n", "", text, flags=re.MULTILINE)


def main():
    text = YAML.read_text(encoding="utf-8")
    out_lines = []
    removed = 0
    for line in text.splitlines(keepends=True):
        new = remove_cache_ttl_key(line)
        if new != line:
            removed += 1
        out_lines.append(new)
    new_text = "".join(out_lines)
    before_ann = new_text.count("announcement")
    new_text = remove_announcement_ttl(new_text)
    after_ann = new_text.count("announcement")
    YAML.write_text(new_text, encoding="utf-8")
    print(f"Removed cache_ttl_key from {removed} non-FUYAO tools")
    print(f"Removed 'announcement' references: {before_ann} -> {after_ann}")


if __name__ == "__main__":
    main()