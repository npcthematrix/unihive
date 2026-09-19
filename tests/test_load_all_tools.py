"""验证 gateway 启动时合并 upstreams.yaml 与 tools_tdx_tq_local.yaml。"""
import sys
from pathlib import Path

import pytest

# 把 src 加进 path（若 conftest 未配置）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.unihive.gateway_server import GatewayServer  # noqa: E402


def test_load_all_tools_merges_both_sources(tmp_path):
    # 临时 upstreams.yaml：含 1 个手工 tool
    cfg = tmp_path / "upstreams.yaml"
    cfg.write_text("""
upstreams:
  fuyao_meta:
    enabled: true
    type: http
    base_url: http://127.0.0.1:9999
tools:
  - name: manual_tool
    description: hand-written
    routing: manual_tool
    upstream_tool_mapping:
      fuyao_meta: manual_tool
""", encoding="utf-8")

    # 临时生成文件：含 1 个生成的 tool
    gen = tmp_path / "tools_tdx_quant.yaml"
    gen.write_text("""
tools:
  - name: generated_tool
    description: from codegen
    routing: generated_tool
    upstream_tool_mapping:
      tdx_quant: generated_tool
""", encoding="utf-8")

    # L3 (round 10 audit) 后相对 config_path 一律锚定项目根 (resolve_project_path),
    # 不再锚定 CWD; 库外临时配置走文档化的绝对路径逃生通道。gen 文件由
    # load_all_tools 按 config_path.parent 定位, 绝对路径下同样找得到。
    server = GatewayServer(config_path=str(cfg), strict_env=False, strict_validation=False)
    # 触发配置加载
    server.config = server.config  # 已加载；无需重做
    tools = server._load_all_tools()
    names = {t["name"] for t in tools}
    assert "manual_tool" in names
    assert "generated_tool" in names
