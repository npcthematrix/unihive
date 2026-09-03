#!/usr/bin/env python3
"""UniHive Gateway Health Check Script"""
import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from upstream_client import UpstreamStatus
import yaml


async def healthcheck():
    """Check gateway health status"""
    config_path = Path(__file__).parent.parent / "config" / "upstreams.yaml"

    if not config_path.exists():
        print("ERROR: Config file not found")
        return False

    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    upstreams = config.get("upstreams", {})
    print(f"Found {len(upstreams)} upstream configurations")

    all_healthy = True
    for name, cfg in upstreams.items():
        enabled = cfg.get("enabled", False)
        upstream_type = cfg.get("type", "unknown")
        status = "ENABLED" if enabled else "DISABLED"
        print(f"  [{name}] type={upstream_type} status={status}")
        if enabled:
            # In a real healthcheck, we would attempt to connect
            # For now, just report enabled status
            pass

    return all_healthy


if __name__ == "__main__":
    result = asyncio.run(healthcheck())
    sys.exit(0 if result else 1)
