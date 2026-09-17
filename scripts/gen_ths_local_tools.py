# -*- coding: utf-8 -*-
"""按 FQGate OpenAPI (/docs) 再生成 config/tools_ths_local.yaml 中 87 条 fqgate spec。

输入快照 (上游运行时抓取, 见 docs):
- logs/_openapi.json    <- curl http://127.0.0.1:17281/openapi.json
- logs/_tools_list.sse  <- 上游 /mcp 的 tools/list SSE 响应
重写内容: required 标志 / 参数类型 / 参数描述 (+数组元素形状 / 枚举值)。
脚本幂等, 全量重写 87 行; fqgate 工具配置 2026-09-17 起独立于 upstreams.yaml。
"""
import json
import re
import sys

YAML_PATH = "config/tools_ths_local.yaml"
OPENAPI = json.load(open("logs/_openapi.json", encoding="utf-8"))
SCHEMAS = OPENAPI["components"]["schemas"]

# 34 个老工具无 description REST 提示, 人工对齐 (已逐一核对路径存在)
MANUAL_MAP = {
    "fqgate_market_market_health": ("GET", "/v1/market/health"),
    "fqgate_market_search_symbols": ("POST", "/v1/market/catalog/search-symbols"),
    "fqgate_market_market_data_block": ("POST", "/v1/market/realtime/block"),
    "fqgate_market_market_data_bond": ("POST", "/v1/market/realtime/bond"),
    "fqgate_market_market_data_cn": ("POST", "/v1/market/realtime/cn"),
    "fqgate_market_market_data_forex": ("POST", "/v1/market/realtime/forex"),
    "fqgate_market_market_data_fund": ("POST", "/v1/market/realtime/fund"),
    "fqgate_market_market_data_future": ("POST", "/v1/market/realtime/future"),
    "fqgate_market_market_data_hk": ("POST", "/v1/market/realtime/hk"),
    "fqgate_market_market_data_index": ("POST", "/v1/market/realtime/index"),
    "fqgate_market_quote": ("POST", "/v1/market/realtime/quote"),
    "fqgate_market_security_trading_status": ("POST", "/v1/market/realtime/trading-status"),
    "fqgate_market_market_data_uk": ("POST", "/v1/market/realtime/uk"),
    "fqgate_market_market_data_us": ("POST", "/v1/market/realtime/us"),
    "fqgate_market_cached_login": ("POST", "/v1/market/session/cached-login"),
    "fqgate_market_qr_login_begin": ("POST", "/v1/market/session/qr/begin"),
    "fqgate_market_intraday": ("POST", "/v1/market/history/intraday"),
    "fqgate_market_klines": ("POST", "/v1/market/history/klines"),
    "fqgate_market_news": ("POST", "/v1/market/information/news"),
    "fqgate_market_level2_best_ask_queue": ("POST", "/v1/market/level2/best-ask-queue"),
    "fqgate_market_level2_best_bid_queue": ("POST", "/v1/market/level2/best-bid-queue"),
    "fqgate_market_bid_ask_rank": ("POST", "/v1/market/level2/bid-ask-rank"),
    "fqgate_market_level2_buy_cancellations": ("POST", "/v1/market/level2/cancellations/buy"),
    "fqgate_market_level2_sell_cancellations": ("POST", "/v1/market/level2/cancellations/sell"),
    "fqgate_market_level2_depth": ("POST", "/v1/market/level2/depth"),
    "fqgate_market_enhanced_transactions": ("POST", "/v1/market/level2/enhanced-transactions"),
    "fqgate_market_order_detail_by_sequence": ("POST", "/v1/market/level2/order-detail-by-sequence"),
    "fqgate_market_level2_orders": ("POST", "/v1/market/level2/orders"),
    "fqgate_market_level2_transactions": ("POST", "/v1/market/level2/transactions"),
    "fqgate_market_order_book_ask": ("POST", "/v1/market/order-book/ask"),
    "fqgate_market_order_book_bid": ("POST", "/v1/market/order-book/bid"),
    "fqgate_market_wencai_base": ("POST", "/v1/market/selection/wencai-base"),
    "fqgate_market_logout": ("DELETE", "/v1/market/session"),
    "fqgate_market_clear_cached_login": ("DELETE", "/v1/market/session/saved-login"),
}

TYPE_MAP = {"string": "str", "integer": "int", "number": "float", "boolean": "bool", "array": "array"}


def resolve(s, depth=0):
    """解 $ref / 剥 nullable / 收敛 oneOf, 返回 {type, description, enum, items, example}。"""
    if depth > 8 or not isinstance(s, dict):
        return {}
    if "$ref" in s:
        return resolve(SCHEMAS.get(s["$ref"].split("/")[-1], {}), depth + 1)
    out = {k: v for k, v in s.items() if k in ("type", "description", "enum", "items", "example", "oneOf", "properties", "required")}
    if "properties" in out:
        out["properties"] = {k: resolve(v, depth + 1) for k, v in out["properties"].items()}
    if "$ref" in s.get("items", {}) or "oneOf" in s.get("items", {}):
        out["items"] = resolve(s["items"], depth + 1)
    elif isinstance(s.get("items"), dict):
        it = s["items"]
        out["items"] = {k: v for k, v in it.items() if k in ("type", "description", "example")}
    if "oneOf" in out:
        branches = [resolve(b, depth + 1) for b in out["oneOf"]]
        non_null = [b for b in branches if b.get("type") not in (None, "null")]
        if non_null:
            merged = non_null[0]
            merged.setdefault("description", out.get("description", ""))
            if out.get("description") and out["description"] not in (merged.get("description") or ""):
                merged["description"] = out["description"] or merged.get("description")
            return merged
    if isinstance(out.get("type"), list):
        ts = [t for t in out["type"] if t != "null"]
        out["type"] = ts[0] if ts else "string"
    if out.get("type") is None and "$ref" not in s:
        out["type"] = "object"  # 兜底
    return out


def shape_hint(item):
    """数组元素的形状提示, 拼进参数描述。"""
    if not isinstance(item, dict):
        return ""
    if item.get("type") in ("string", "integer", "number", "boolean"):
        ex = item.get("example")
        return f'元素为 {item["type"]}' + (f" (如 {ex})" if ex is not None and not isinstance(ex, (dict, list)) else "")
    props = item.get("properties") or {}
    if props:
        parts = []
        for k, v in props.items():
            r = resolve(v)
            d = (r.get("description") or "").rstrip("。")
            parts.append(f"{k}={d}" if d else k)
        return "元素为对象: {" + "; ".join(parts) + "}"
    return ""


def norm_desc(desc):
    return re.sub(r"\s+", " ", (desc or "").strip())


def spec_line(name, description, routing, params, dst):
    def q(s):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'

    def scalar(v):
        return v if re.fullmatch(r"[A-Za-z0-9_.-]+", str(v)) else q(str(v))

    pstr = ", ".join(
        "{name: %s, type: %s, required: %s%s, description: %s}"
        % (
            p["name"],
            p["type"],
            "true" if p["required"] else "false",
            (", enum: [%s]" % ", ".join(scalar(v) for v in p["enum"])) if p.get("enum") else "",
            q(p["description"]),
        )
        for p in params
    )
    return (
        "  - {name: %s, description: %s, routing: %s, params: [%s], "
        "upstream_tool_mapping: {THS-LOCAL-GATEWAY: %s}, data_source_type: %s}"
        % (name, q(description), routing, pstr, name, dst)
    )


def main():
    # 1) 上游 87 个 MCP 工具 (取 requestTimeoutMs 等 MCP 层参数)
    raw = open("logs/_tools_list.sse", encoding="utf-8").read()
    fq = {}
    for line in raw.splitlines():
        if line.startswith("data: "):
            body = json.loads(line[6:])
            for t in (body.get("result") or {}).get("tools") or []:
                if t["name"].startswith("fqgate_"):
                    fq[t["name"]] = t
    assert len(fq) == 87, len(fq)

    # 2) 映射: description 提示 + 人工表
    tool2rest = {}
    for name, t in fq.items():
        m = re.search(r"POST\s+(/v1/[^\s，。]+)", t.get("description") or "")
        p = m.group(1).rstrip("。，") if m else None
        if p and p in OPENAPI["paths"]:
            tool2rest[name] = ("POST", p)
    for name, pair in MANUAL_MAP.items():
        assert pair[1] in OPENAPI["paths"], pair
        tool2rest.setdefault(name, pair)
    assert set(tool2rest) == set(fq), sorted(set(fq) - set(tool2rest))

    # 3) 逐工具生成新 spec
    new_specs = {}
    for name, (method, path) in sorted(tool2rest.items()):
        op = OPENAPI["paths"][path][method.lower()]
        params = []
        # REST query/body 参数为权威
        for prm in op.get("parameters") or []:
            if prm.get("in") == "query":
                r = resolve(prm["schema"])
                params.append({
                    "name": prm["name"],
                    "type": TYPE_MAP.get(r.get("type"), "str"),
                    "required": prm.get("required", False),
                    "description": norm_desc(prm.get("description") or r.get("description")),
                })
        rb = (op.get("requestBody") or {}).get("content", {}).get("application/json", {})
        body_example = rb.get("example") or {}
        rb = rb.get("schema")
        if rb:
            r = resolve(rb)
            required = set(r.get("required") or [])
            for k, v in (r.get("properties") or {}).items():
                desc = norm_desc(v.get("description"))
                hint = shape_hint(v.get("items") or {})
                if hint:
                    sep = "" if desc.endswith("。") else ("，" if desc else "")
                    desc = desc + sep + hint
                ex = v.get("example")
                if not isinstance(ex, list):
                    ex = body_example.get(k)
                if isinstance(ex, list) and ex and isinstance(ex[0], (dict, int)):
                    desc += "，如 " + json.dumps(ex, ensure_ascii=False)
                params.append({
                    "name": k,
                    "type": TYPE_MAP.get(v.get("type"), "str"),
                    "required": k in required,
                    "description": desc,
                    "enum": v.get("enum"),
                })
        # MCP 层独有参数 (requestTimeoutMs) 保留在末尾
        mcp_props = ((fq[name].get("inputSchema") or {}).get("properties") or {})
        mcp_required = set((fq[name].get("inputSchema") or {}).get("required") or [])
        rest_names = {p["name"] for p in params}
        for k, v in mcp_props.items():
            if k in rest_names:
                continue
            params.append({
                "name": k,
                "type": {"string": "str", "integer": "int", "number": "float", "boolean": "bool", "array": "array"}.get(v.get("type"), "str"),
                "required": k in mcp_required,
                "description": norm_desc(v.get("description")) + ("，取值 ≥1" if k == "requestTimeoutMs" else ""),
            })
        new_specs[name] = (fq[name].get("description") or "", name, params, "online")

    # 4) 改写 yaml 中 fqgate spec 行
    lines = open(YAML_PATH, encoding="utf-8").read().splitlines(keepends=True)
    out, changed, kept = [], [], []
    name_re = re.compile(r"^  - \{name: (fqgate_[A-Za-z0-9_]+),")
    for line in lines:
        m = name_re.match(line)
        if m and m.group(1) in new_specs:
            name = m.group(1)
            desc, _, params, dst = new_specs[name]
            # 保留旧行里 REST 未覆盖的声明信息
            old_has_ttl = "cache_ttl_key:" in line
            new_line = spec_line(name, desc, name, params, dst)
            if old_has_ttl:  # fqgate 目前无 cache_ttl_key, 防御性保留
                new_line = new_line.replace(", data_source_type:", ", cache_ttl_key: historical, data_source_type:")
            out.append(new_line + "\n")
            changed.append(name)
        else:
            out.append(line)
    assert len(changed) == 87, (len(changed), sorted(set(new_specs) - set(changed)))
    open(YAML_PATH, "w", encoding="utf-8", newline="").writelines(out)
    print(f"rewrote {len(changed)} spec lines")


if __name__ == "__main__":
    main()
