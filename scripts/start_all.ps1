# UniHive - Start Gateway (serves both MCP and Console UI on port 18081)
# Usage: .\scripts\start_all.ps1
#
# One process, one port: http://127.0.0.1:18081 → Console UI
#                           http://127.0.0.1:18081/mcp → MCP 接口

& "$PSScriptRoot\start_gateway.ps1" @args