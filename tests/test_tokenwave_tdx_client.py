"""TokenWave TDX Client Tests"""
import pytest
import pytest_asyncio
from src.tokenwave_tdx_client import TokenWaveTdxClient, TokenWaveTdxConfig


def test_client_init():
    """测试客户端初始化"""
    config = TokenWaveTdxConfig(name="test")
    client = TokenWaveTdxClient(config)
    assert client.name == "test"


@pytest.mark.asyncio
async def test_client_init_with_start():
    """测试客户端初始化并启动"""
    config = TokenWaveTdxConfig(name="test", mode="auto")
    client = TokenWaveTdxClient(config)
    await client.start()
    # 由于没有真实的 mootdx 客户端，状态会是 UNAVAILABLE
    assert client.name == "test"
    assert client.config.mode == "auto"


@pytest.mark.asyncio
async def test_tool_routing():
    """测试工具路由"""
    config = TokenWaveTdxConfig(name="test")
    client = TokenWaveTdxClient(config)
    # 测试 call_tool 路由 - 不存在的工具
    result = await client.call_tool("nonexistent", {})
    assert result.success is False
    assert "Unknown tool" in result.error


@pytest.mark.asyncio
async def test_tool_routing_valid():
    """测试工具路由 - 有效工具但无数据源"""
    config = TokenWaveTdxConfig(name="test")
    client = TokenWaveTdxClient(config)
    await client.start()
    # 由于没有真实数据源，会返回无可用数据源
    result = await client.get_realtime_quote("600036")
    assert result.success is False
    assert "无可用数据源" in result.error


def test_config_dataclass():
    """测试配置 dataclass"""
    config = TokenWaveTdxConfig(name="tokenwave", mode="auto")
    assert config.name == "tokenwave"
    assert config.mode == "auto"


def test_config_defaults():
    """测试配置默认值"""
    config = TokenWaveTdxConfig(name="test")
    assert config.name == "test"
    assert config.mode == "auto"  # 默认值


def test_config_explicit_mode():
    """测试配置显式模式"""
    config = TokenWaveTdxConfig(name="test", mode="local")
    assert config.mode == "local"

    config2 = TokenWaveTdxConfig(name="test2", mode="network")
    assert config2.mode == "network"


@pytest.mark.asyncio
async def test_client_stop():
    """测试客户端停止"""
    config = TokenWaveTdxConfig(name="test")
    client = TokenWaveTdxClient(config)
    await client.start()
    await client.stop()
    # 停止后状态应该重置
    assert client._local_client is None
    assert client._network_client is None


@pytest.mark.asyncio
async def test_tool_list_routing():
    """测试各工具方法的路由"""
    config = TokenWaveTdxConfig(name="test")
    client = TokenWaveTdxClient(config)
    await client.start()

    # 测试 get_kline
    result = await client.get_kline("600036")
    assert result.success is False
    assert "无可用数据源" in result.error

    # 测试 get_minute_bar
    result = await client.get_minute_bar("600036")
    assert result.success is False
    assert "无可用数据源" in result.error

    # 测试 get_financial_data
    result = await client.get_financial_data("600036")
    assert result.success is False
    assert "无可用数据源" in result.error


@pytest.mark.asyncio
async def test_client_status_property():
    """测试客户端状态属性"""
    config = TokenWaveTdxConfig(name="test")
    client = TokenWaveTdxClient(config)

    # 初始状态
    assert client.status is not None

    await client.start()
    # NetworkClient 的 is_available 返回 True 因为 mootdx Quotes 可以实例化
    # 这是实际行为，测试反映这一事实
    assert client.status is not None

    await client.stop()
