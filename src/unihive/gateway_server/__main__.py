"""让 ``python -m src.unihive.gateway_server`` 可直接启动网关。

2026-09-19 拆包后 gateway_server 由模块变包 (src/unihive/gateway_server/),
但缺 __main__.py 导致 ``python -m`` 抛
"No module named src.unihive.gateway_server.__main__; ... cannot be directly executed"
(scripts/start_gateway.ps1 恰用此形式)。补此文件恢复 ``python -m`` 入口,
console script ``unihive`` (= unihive.gateway_server:main) 不受影响。
"""
import sys

from . import main

if __name__ == "__main__":
    sys.exit(main())