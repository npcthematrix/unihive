"""Test mootdx2 offline tools - black box test"""
import asyncio
import sys
sys.path.insert(0, 'src')

from src.unihive.api.mootdx2_client import MooTDX2Client, MooTDX2Config
from src.unihive.models.mootdx2_config import MooTDX2Settings, ServerConfig
from src.unihive.api.upstream_client import ToolResult
from src.unihive.core.blocking_pool import get_blocking_executor


def create_client() -> MooTDX2Client:
    """Create a properly configured MooTDX2 client"""
    servers = [
        ServerConfig(host='101.33.225.16', port=7709),
        ServerConfig(host='81.71.32.47', port=7709),
    ]
    settings = MooTDX2Settings(
        market='std',
        tdxdir='D:/new_tdx_mock',
        servers=servers,
        auto_select_fastest_server=False
    )
    config = MooTDX2Config(
        name='mootdx2',
        market='std',
        settings=settings
    )
    return MooTDX2Client(config)


async def test_get_workday():
    """Test get_workday - offline calculation"""
    print("=== Test: get_workday ===")
    client = create_client()

    # Manually init metrics
    client._metrics = {"total_requests": 0, "total_errors": 0, "error_counts": {}}
    client._metrics_lock = None

    result = await client.get_workday('20240901', 1)
    print(f"  success: {result.success}")
    print(f"  data: {result.data}")
    print(f"  error: {result.error}")
    print()


async def test_get_workday_range():
    """Test get_workday_range - offline calculation"""
    print("=== Test: get_workday_range ===")
    client = create_client()
    client._metrics = {"total_requests": 0, "total_errors": 0, "error_counts": {}}
    client._metrics_lock = None

    result = await client.get_workday_range('20240901', '20240905')
    print(f"  success: {result.success}")
    print(f"  data: {result.data}")
    print(f"  error: {result.error}")
    print()


async def test_get_code_list():
    """Test get_code_list - reads local block file"""
    print("=== Test: get_code_list (offline) ===")
    client = create_client()
    client._metrics = {"total_requests": 0, "total_errors": 0, "error_counts": {}}
    client._metrics_lock = None
    client._status = "HEALTHY"  # Fake status

    result = await client.get_code_list('sz')
    print(f"  success: {result.success}")
    if result.data:
        print(f"  records: {len(result.data)}")
        print(f"  sample: {result.data[:3]}")
    else:
        print(f"  error: {result.error}")
    print()


async def test_search_stock():
    """Test search_stock - reads local block file"""
    print("=== Test: search_stock (offline) ===")
    client = create_client()
    client._metrics = {"total_requests": 0, "total_errors": 0, "error_counts": {}}
    client._metrics_lock = None
    client._status = "HEALTHY"

    result = await client.search_stock('平安')
    print(f"  success: {result.success}")
    if result.data:
        print(f"  records: {len(result.data)}")
        print(f"  sample: {result.data[:3]}")
    else:
        print(f"  error: {result.error}")
    print()


async def test_get_daily():
    """Test get_daily - reads local .day file"""
    print("=== Test: get_daily (offline) ===")
    client = create_client()
    client._metrics = {"total_requests": 0, "total_errors": 0, "error_counts": {}}
    client._metrics_lock = None

    result = await client.get_daily(symbol='000001')
    print(f"  success: {result.success}")
    if result.data:
        print(f"  records: {len(result.data)}")
        print(f"  sample: {result.data[:2]}")
    else:
        print(f"  error: {result.error}")
    print()


async def test_get_custom_sector_list():
    """Test get_custom_sector_list - reads T0002/blocknew"""
    print("=== Test: get_custom_sector_list (offline) ===")
    client = create_client()
    client._metrics = {"total_requests": 0, "total_errors": 0, "error_counts": {}}
    client._metrics_lock = None

    result = await client.get_custom_sector_list()
    print(f"  success: {result.success}")
    if result.data:
        print(f"  records: {len(result.data)}")
        print(f"  sample: {result.data[:2]}")
    else:
        print(f"  error: {result.error}")
    print()


async def main():
    print("Testing MooTDX2 Offline Tools")
    print("=" * 50)
    print()

    await test_get_workday()
    await test_get_workday_range()
    await test_get_code_list()
    await test_search_stock()
    await test_get_daily()
    await test_get_custom_sector_list()

    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
