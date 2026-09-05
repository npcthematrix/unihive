import pytest
from src.mootdx2_client import MooTDX2Client, MooTDX2Config
from src.upstream_client import UpstreamStatus


class TestMooTDX2Client:
    def test_config_creation(self):
        config = MooTDX2Config(name="test", market="std")
        assert config.name == "test"
        assert config.market == "std"
