"""验证 gateway 启动时合并 upstreams.yaml 与 tools_tdx_tq_local.yaml。"""
import sys
from pathlib import Path

import pytest

# 把 src 加进 path（若 conftest 未配置）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.gateway_server import GatewayServer  # noqa: E402


def test_load_all_tools_merges_both_sources(tmp_path, monkeypatch):
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
    gen = tmp_path / "tools_tdx_tq_local.yaml"
    gen.write_text("""
tools:
  - name: generated_tool
    description: from codegen
    routing: generated_tool
    upstream_tool_mapping:
      tdx_tq_local: generated_tool
""", encoding="utf-8")

    # 把 cwd 切到 tmp_path，使 _load_all_tools 用相对路径能找到 gen
    monkeypatch.chdir(tmp_path)
    # 同时把 cfg 也复制成相对路径名
    (tmp_path / "upstreams.yaml").write_text(cfg.read_text(encoding="utf-8"), encoding="utf-8")

    server = GatewayServer(config_path="upstreams.yaml", strict_env=False, strict_validation=False)
    # 触发配置加载
    server.config = server.config  # 已加载；无需重做
    tools = server._load_all_tools()
    names = {t["name"] for t in tools}
    assert "manual_tool" in names
    assert "generated_tool" in names
