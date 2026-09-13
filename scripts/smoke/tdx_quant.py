#!/usr/bin/env python3
"""TdxQuant 上游手动烟测脚本。

用途：在真实 TdxW.exe + tqcenter.py 环境下，绕过 MCP/RPC 层直接验证
进程内 tq.* 调用是否工作。运行前确认：
  1. config/upstreams.yaml 中 tdx_quant.enabled=true
  2. tdx_root 指向真实通达信安装目录（含 PYPlugins/user/tqcenter.py）
  3. TdxW.exe 已启动并登录

使用：
  python scripts/smoke/tdx_quant.py                     # 默认探活 + 1 个查询
  python scripts/smoke/tdx_quant.py get_market_data    # 调用指定工具
  python scripts/smoke/tdx_quant.py --list              # 列出 54 个生成工具
  python scripts/smoke/tdx_quant.py --args code=000001  # 传参

退出码：
  0  成功
  1  配置/初始化失败
  2  探活失败
  3  工具调用失败
"""
import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402
from src.unihive.tdx_quant_client import TdxQuantClient  # noqa: E402
from src.unihive.tdx_quant_config import TdxQuantConfig, TdxQuantSettings  # noqa: E402


def load_tdx_quant_config() -> tuple[TdxQuantConfig, dict]:
    cfg_path = ROOT / "config" / "upstreams.yaml"
    if not cfg_path.exists():
        print(f"ERROR: {cfg_path} not found")
        sys.exit(1)
    with cfg_path.open(encoding="utf-8") as f:
        full = yaml.safe_load(f) or {}
    upstreams = full.get("upstreams", {})
    tdx_cfg = upstreams.get("tdx_quant")
    if not tdx_cfg:
        print("ERROR: tdx_quant upstream not in upstreams.yaml")
        sys.exit(1)
    if not tdx_cfg.get("enabled", False):
        print("ERROR: tdx_quant.enabled=false in upstreams.yaml")
        sys.exit(1)
    settings = TdxQuantSettings.from_dict(tdx_cfg)
    return TdxQuantConfig(name="tdx_quant", market=tdx_cfg.get("market", "std"), settings=settings), tdx_cfg


def list_generated_tools() -> list[str]:
    gen_path = ROOT / "config" / "tools_tdx_quant.yaml"
    if not gen_path.exists():
        return []
    with gen_path.open(encoding="utf-8") as f:
        gen = yaml.safe_load(f) or {}
    return [t["name"] for t in gen.get("tools", []) if t.get("name")]


def parse_kv_args(kv_strings: list[str]) -> dict:
    """把 ["code=000001", "count=5"] 解析成 {"code": "000001", "count": 5}。"""
    out: dict = {}
    for s in kv_strings:
        if "=" not in s:
            print(f"WARN: skipping malformed --args entry '{s}' (expected key=value)")
            continue
        k, v = s.split("=", 1)
        v = v.strip()
        if v.lower() in ("true", "false"):
            out[k] = v.lower() == "true"
        elif v.lstrip("-").isdigit():
            out[k] = int(v)
        else:
            out[k] = v
    return out


async def run_probe(client: TdxQuantClient) -> bool:
    """探活：调 tq.get_user_sector()，与 _health_loop 探活路径一致。"""
    print("[probe] calling tq.get_user_sector() ...")
    result = await client.call_tool("get_user_sector", {})
    if not result.success:
        print(f"[probe] FAIL: {result.error}")
        return False
    print(f"[probe] OK (duration={result.duration_ms}ms)")
    return True


async def run_tool(client: TdxQuantClient, tool_name: str, args: dict) -> bool:
    print(f"[call] {tool_name}({args}) ...")
    result = await client.call_tool(tool_name, args)
    if not result.success:
        print(f"[call] FAIL: {result.error}")
        return False
    data = result.data
    if isinstance(data, (list, dict)):
        preview = str(data)[:200]
        suffix = "..." if len(str(data)) > 200 else ""
        print(f"[call] OK (duration={result.duration_ms}ms) data={preview}{suffix}")
    else:
        print(f"[call] OK (duration={result.duration_ms}ms) data={data!r}")
    return True


async def main_async(args: argparse.Namespace) -> int:
    cfg, raw = load_tdx_quant_config()
    print(f"[init] tdx_root={cfg.settings.tdx_root}")
    print(f"[init] strategy_id={cfg.settings.strategy_id or '<fallback: client module path>'}")
    print(f"[init] tqcenter_dir={cfg.tqcenter_dir}")

    if not cfg.tqcenter_path.exists():
        print(f"ERROR: tqcenter.py not found at {cfg.tqcenter_path}")
        print("  Check tdx_root in config/upstreams.yaml — must point to a real TdxW install.")
        return 1

    client = TdxQuantClient(cfg)
    ok = await client.start()
    if not ok:
        print(f"[init] FAIL: start() returned False (status={client.status.value})")
        print("  See logs above. Likely tqcenter.py import error or TdxW.exe not running.")
        return 1
    print(f"[init] OK (status={client.status.value})")

    try:
        if args.list:
            tools = list_generated_tools()
            print(f"\n[list] {len(tools)} generated tools in config/tools_tdx_quant.yaml:")
            for name in tools:
                print(f"  - {name}")
            return 0

        if not await run_probe(client):
            return 2

        if args.tool:
            kv = parse_kv_args(args.args or [])
            if not await run_tool(client, args.tool, kv):
                return 3
        else:
            print("\n[skip] no --tool specified; probe-only smoke test complete.")
            print("       Try: python scripts/smoke/tdx_quant.py get_market_data --args code=000001")
        return 0
    finally:
        await client.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="TdxQuant manual smoke test")
    parser.add_argument("tool", nargs="?", help="tq.* tool name to call (e.g. get_market_data)")
    parser.add_argument("--args", nargs="*", help='kwargs as key=value, e.g. --args code=000001 count=5')
    parser.add_argument("--list", action="store_true", help="list 54 generated tools and exit")
    args = parser.parse_args()
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
