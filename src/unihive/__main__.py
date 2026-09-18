"""支持 ``python -m unihive config lint``。

注意: 网关启动入口仍是 ``unihive.gateway_server:main``
(console script ``unihive`` 与 scripts/start_gateway.ps1)。
"""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
