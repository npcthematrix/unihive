import pytest
from pathlib import Path


class TestMooTDX2ClientBlockQuery:
    """Block query tests — TDX dat files may not exist on all systems."""

    def test_stocks_in_block_returns_empty_when_no_tdxdir(self):
        """When block dat files don't exist (no TDX dir), returns empty list gracefully."""
        from src.mootdx2_client import MooTDX2Client, MooTDX2Config
        config = MooTDX2Config(name="test", market="std")
        client = MooTDX2Client(config)
        # TDX dir not configured → should return empty list
        result = client._stocks_in_block_sync("银行板块", 0)
        assert result == []

    def test_blocks_for_stock_returns_empty_when_no_tdxdir(self):
        """When block dat files don't exist, returns empty list gracefully."""
        from src.mootdx2_client import MooTDX2Client, MooTDX2Config
        config = MooTDX2Config(name="test", market="std")
        client = MooTDX2Client(config)
        result = client._blocks_for_stock_sync("600000", "sh")
        assert isinstance(result, list)
        assert result == []


def test_block_dat_files_exist():
    """Check if TDX block dat files exist and list them."""
    from mootdx2.consts import DEFAULT_TDXDIR
    block_dir = Path(DEFAULT_TDXDIR) / "vipdoc" / "block"
    if block_dir.exists():
        files = list(block_dir.glob("block_*.dat"))
        print(f"Found block files: {files}")
    else:
        print(f"Block dir not found: {block_dir}")
    # Don't fail — files may not exist on all systems
