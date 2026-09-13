# Repository Guidelines

UniHive is a Python MCP gateway that aggregates four financial data upstreams (mootdx2, tdx_quant, fuyao/THS, omni) behind a single `/mcp` endpoint plus a web control console.

## Project Structure & Module Organization

- `src/unihive/` — the Python package (src layout). `api/` holds upstream clients, `core/` holds router, registry, tool loader, and MCP factory, `storage/` the SQLite cache, `utils/` config loading, console API, and logging, `sync/` the OMNI board sync, `gateway_server.py` the CLI entrypoint.
- `config/upstreams.yaml` — single source of truth for upstreams, routing chain, gateway and cache settings; `config/tools_*.yaml` define per-upstream tools.
- `tests/` — pytest suite mirroring module names. `console.html` + `static/console/` — control console SPA. `scripts/` — startup and health helpers. `docs/` — architecture notes and dated handoffs.

## Build, Test, and Development Commands

```bash
uv sync                                        # install deps from uv.lock
python -m pytest tests -v                      # full test suite
python -m src.unihive.gateway_server --transport http --port 18080
python -m src.unihive.gateway_server --transport stdio
python scripts/healthcheck.py                  # probe configured upstreams
```

The console and MCP endpoint share `http://127.0.0.1:18080`. `src.gateway_server` still appears in `scripts/start_gateway.ps1`, `console.html`, and a few tests, but the valid module path after the package refactor is `src.unihive.gateway_server`.

## Coding Style & Naming Conventions

- 4-space indentation, PEP 8, roughly 88 columns. No linter or formatter is configured — match surrounding code.
- Prefer `from __future__ import annotations` with PEP 604 unions (`str | None`); keep type hints on public functions.
- `snake_case` modules and functions, `PascalCase` classes, `UPPER_SNAKE_CASE` constants, `_leading_underscore` for private helpers. Docstrings are written in Chinese in existing modules.
- Tool names are `snake_case` and prefixed by upstream: `fuyao_*`, `mootdx2_*`, `ths_*`, `omni_*`.

## Testing Guidelines

- pytest with `pytest-asyncio` (`asyncio_mode = "auto"`, `testpaths = ["tests"]` in `pyproject.toml`).
- Name files `tests/test_<module>.py`, classes `TestSomething`, functions `test_behavior`.
- Reuse `tests/conftest.py` fixtures (`gateway_server_minimal`, `config_path`) and `monkeypatch.setenv` for credential-dependent clients.
- Tests must stay offline by default; do not make live upstream calls (see `tests/test_thsdk_client_offline.py`).

## Commit & Pull Request Guidelines

- Use Conventional Commits with a scope: `fix(thsdk): ...`, `feat(console): ...`, `refactor(mootdx2): ...`. Imperative subject, then bullet details for multi-part changes.
- PRs should state the affected upstream or console surface, list the `pytest` commands run, link related notes in `docs/handoffs/`, and include console screenshots for UI changes.

## Security & Configuration Tips

- Never commit `.env`, `account.session`, or `logs/*`; document new variables in `.env.example`.
- YAML values reference environment variables as `${VAR}` and resolution is strict — a missing variable aborts startup. Keep `FUYAO_API_KEY`, `THS_USERNAME`/`THS_PASSWORD` (both or neither), and console auth variables in sync with your local `.env`.
