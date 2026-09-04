# TokenWave TDX MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于 mootdx 库创建 MCP 工具，集成到网关，与 tdx-mcp-server、tdx_tq_local 并列提供数据源选择

**Architecture:**
- 新增 `type: python` 支持，直接在网关进程调用 mootdx
- 创建 `src/tokenwave_tdx_client.py`，模式 `fuyao_client.py`
- local 优先 + network 兜底模式

**Tech Stack:**
- Python 3.10+
- mootdx 库
- FastMCP

---

## 文件结构

```
src/
├── tokenwave_tdx_client.py    # 新增: mootdx 客户端封装
├── gateway_server.py          # 修改: 支持 type: python

config/
└── upstreams.yaml            # 修改: 添加 tokenwave_tdx upstream
```

---

## Task 1: 创建 tokenwave_tdx_client.py 基础结构

**Files:**
- Create: `src/tokenwave_tdx_client.py`

- [ ] **Step 1: 创建基础文件结构**

```python
"""
TokenWave TDX MCP Client
基于 mootdx 库的数据访问客户端，支持 local 和 network 模式
"""
import logging
from dataclasses import dataclass
from typing import Any

from .upstream_client import ToolResult, UpstreamStatus

logger = logging.getLogger(__name__)


@dataclass
class TokenWaveTdxConfig:
    """TokenWave TDX 配置"""
    name: str
    mode: str = "auto"  # auto | local | network


class TokenWaveTdxClient:
    """TokenWave TDX MCP 客户端"""

    def __init__(self, config: TokenWaveTdxConfig):
        self.config = config
        self.name = config.name
        self._status = UpstreamStatus.UNKNOWN
        self._local_client = None
        self._network_client = None

    @property
    def status(self) -> UpstreamStatus:
        return self._status

    @property
    def is_available(self) -> bool:
        return self._status == UpstreamStatus.AVAILABLE

    async def start(self):
        """初始化客户端"""
        # 初始化 local 和 network 客户端
        pass

    async def stop(self):
        """停止客户端"""
        pass

    async def call_tool(self, tool_name: str, params: dict) -> ToolResult:
        """调用工具"""
        pass
```

- [ ] **Step 2: 验证导入正常**

Run: `python -c "from src.tokenwave_tdx_client import TokenWaveTdxClient; print('OK')"`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add src/tokenwave_tdx_client.py
git commit -m "feat: create TokenWaveTdxClient base structure"
```

---

## Task 2: 实现 LocalClient (本地模式)

**Files:**
- Modify: `src/tokenwave_tdx_client.py`

- [ ] **Step 1: 添加 LocalClient 类**

```python
class LocalClient:
    """本地模式: 读取本机通达信数据文件"""

    def __init__(self, tdx_path: str = None):
        from mootdx import reader
        self.tdx_path = tdx_path
        self._reader = None

    def _get_reader(self):
        if self._reader is None:
            self._reader = reader.Reader(self.tdx_path)
        return self._reader

    def is_available(self) -> bool:
        """检查本地数据是否可用"""
        try:
            r = self._get_reader()
            return r is not None
        except Exception:
            return False

    def get_kline(self, stock_code: str, frequency: str = "daily", count: int = 100):
        """读取K线数据"""
        r = self._get_reader()
        # 实现...
        pass

    def get_minute(self, stock_code: str, frequency: str = "5min"):
        """读取分钟K线"""
        pass

    def get_realtime_quote(self, stock_code: str):
        """读取实时行情 (本地模式仅支持日线)"""
        pass
```

- [ ] **Step 2: 测试本地客户端初始化**

```python
# 测试代码
from src.tokenwave_tdx_client import LocalClient
client = LocalClient()
print(f"Local available: {client.is_available()}")
```

- [ ] **Step 3: Commit**

```bash
git add src/tokenwave_tdx_client.py
git commit -m "feat: implement LocalClient for local TDX data access"
```

---

## Task 3: 实现 NetworkClient (网络模式)

**Files:**
- Modify: `src/tokenwave_tdx_client.py`

- [ ] **Step 1: 添加 NetworkClient 类**

```python
class NetworkClient:
    """网络模式: 直连通达信行情服务器"""

    def __init__(self):
        from mootdx import quotes
        self._quotes = None

    def _get_quotes(self):
        if self._quotes is None:
            self._quotes = quotes.Quotes()
        return self._quotes

    def is_available(self) -> bool:
        """检查网络连接是否可用"""
        try:
            q = self._get_quotes()
            # 测试连接
            return True
        except Exception:
            return False

    def get_realtime_quote(self, stock_code: str):
        """获取实时行情"""
        q = self._get_quotes()
        # 实现...
        pass

    def get_kline(self, stock_code: str, frequency: str = "daily", count: int = 100):
        """获取K线数据"""
        pass

    def get_minute(self, stock_code: str, frequency: str = "5min"):
        """获取分钟K线"""
        pass

    def get_financial_data(self, stock_code: str, report_type: str = "income", count: int = 4):
        """获取财务数据"""
        pass

    def get_block_data(self, block_type: str):
        """获取板块数据"""
        pass
```

- [ ] **Step 2: 测试网络客户端**

```python
from src.tokenwave_tdx_client import NetworkClient
client = NetworkClient()
print(f"Network available: {client.is_available()}")
```

- [ ] **Step 3: Commit**

```bash
git add src/tokenwave_tdx_client.py
git commit -m "feat: implement NetworkClient for TDX remote data"
```

---

## Task 4: 实现工具方法封装

**Files:**
- Modify: `src/tokenwave_tdx_client.py`

- [ ] **Step 1: 在 TokenWaveTdxClient 中添加工具方法**

```python
async def get_realtime_quote(self, stock_code: str) -> ToolResult:
    """获取实时行情: local 优先，network 兜底"""
    # 1. 尝试 local
    if self._local_client and self._local_client.is_available():
        try:
            data = self._local_client.get_realtime_quote(stock_code)
            return ToolResult(
                success=True,
                data=data,
                meta={"mode": "local"}
            )
        except Exception as e:
            logger.warning(f"Local quote failed: {e}")

    # 2. 兜底 network
    if self._network_client and self._network_client.is_available():
        try:
            data = self._network_client.get_realtime_quote(stock_code)
            return ToolResult(
                success=True,
                data=data,
                meta={"mode": "network"}
            )
        except Exception as e:
            logger.error(f"Network quote failed: {e}")

    return ToolResult(
        success=False,
        error="无可用数据源"
    )
```

- [ ] **Step 2: 添加工具路由方法**

```python
async def call_tool(self, tool_name: str, params: dict) -> ToolResult:
    """工具路由"""
    method_map = {
        "get_realtime_quote": self.get_realtime_quote,
        "get_kline": self.get_kline,
        "get_minute_bar": self.get_minute_bar,
        "get_financial_data": self.get_financial_data,
        "get_block_data": self.get_block_data,
        "get_stock_info": self.get_stock_info,
        "get_trade_dates": self.get_trade_dates,
        "get_etf_list": self.get_etf_list,
    }

    method = method_map.get(tool_name)
    if not method:
        return ToolResult(success=False, error=f"Unknown tool: {tool_name}")

    return await method(**params)
```

- [ ] **Step 3: 实现其他 7 个工具方法**

按同样模式实现:
- get_kline
- get_minute_bar
- get_financial_data
- get_block_data
- get_stock_info
- get_trade_dates
- get_etf_list

- [ ] **Step 4: Commit**

```bash
git add src/tokenwave_tdx_client.py
git commit -m "feat: implement 8 tool methods with local/network fallback"
```

---

## Task 5: 网关集成 - 支持 type: python

**Files:**
- Modify: `src/gateway_server.py`

- [ ] **Step 1: 添加 TokenWaveTdxClient 导入**

```python
from .tokenwave_tdx_client import TokenWaveTdxClient, TokenWaveTdxConfig
```

- [ ] **Step 2: 在 initialize() 中添加 python 类型处理**

```python
if cfg.get("type") == "python":
    # TokenWave TDX 客户端
    tokenwave_cfg = TokenWaveTdxConfig(
        name=name,
        mode=cfg.get("mode", "auto"),
    )
    client = TokenWaveTdxClient(tokenwave_cfg)
```

- [ ] **Step 3: 验证代码正确**

Run: `python -c "from src.gateway_server import GatewayServer; print('OK')"`
Expected: OK

- [ ] **Step 4: Commit**

```bash
git add src/gateway_server.py
git commit -m "feat: add type: python support for TokenWaveTdxClient"
```

---

## Task 6: 配置更新 - 添加 tokenwave_tdx upstream

**Files:**
- Modify: `config/upstreams.yaml`

- [ ] **Step 1: 添加 upstream 配置**

```yaml
  tokenwave_tdx:
    enabled: true
    type: "python"
    mode: "auto"
    timeout_seconds: 30
    retry:
      max_attempts: 3
      backoff_base: 2
    capabilities:
      - realtime_quote
      - kline
      - minute_bar
      - financial_data
      - block_data
      - stock_info
      - trade_dates
      - etf_list
```

- [ ] **Step 2: 添加工具映射**

```yaml
  # TokenWave TDX 工具
  get_realtime_quote:                     {tokenwave_tdx: get_realtime_quote}
  get_kline:                               {tokenwave_tdx: get_kline}
  get_minute_bar:                          {tokenwave_tdx: get_minute_bar}
  get_financial_data:                     {tokenwave_tdx: get_financial_data}
  get_block_data:                         {tokenwave_tdx: get_block_data}
  get_stock_info:                         {tokenwave_tdx: get_stock_info}
  get_trade_dates:                        {tokenwave_tdx: get_trade_dates}
  get_etf_list:                          {tokenwave_tdx: get_etf_list}
```

- [ ] **Step 3: 运行测试**

Run: `pytest -q`
Expected: 126+ passed

- [ ] **Step 4: Commit**

```bash
git add config/upstreams.yaml
git commit -m "feat(config): add tokenwave_tdx upstream and 8 tool mappings"
```

---

## Task 7: 测试验证

**Files:**
- Create: `tests/test_tokenwave_tdx_client.py`

- [ ] **Step 1: 编写基础测试**

```python
import pytest
from src.tokenwave_tdx_client import TokenWaveTdxClient, TokenWaveTdxConfig


def test_client_init():
    """测试客户端初始化"""
    config = TokenWaveTdxConfig(name="test")
    client = TokenWaveTdxClient(config)
    assert client.name == "test"


def test_tool_routing():
    """测试工具路由"""
    # 测试 call_tool 路由
    pass
```

- [ ] **Step 2: 运行测试**

Run: `pytest tests/test_tokenwave_tdx_client.py -v`

- [ ] **Step 3: Commit**

```bash
git add tests/test_tokenwave_tdx_client.py
git commit -m "test: add TokenWaveTdxClient tests"
```

---

## 验收检查点

- [ ] 8 个工具全部实现
- [ ] local/network 模式自动切换
- [ ] 返回包含 meta 信息
- [ ] 错误返回结构化
- [ ] pytest 通过
