"""Tests for _sanitize_sync_message."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.gateway_server import _sanitize_sync_message


def test_path_windows():
    msg = 'File "C:\\Users\\admin\\project\\src\\foo.py" line 1'
    out = _sanitize_sync_message(msg)
    assert out == 'File "[PATH]" line 1', out


def test_token_jwt():
    msg = "failed: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.signature"
    out = _sanitize_sync_message(msg)
    assert out == "failed: [TOKEN]", out


def test_token_sk_prefix():
    msg = "error: sk-1234567890abcdefghij1234"
    out = _sanitize_sync_message(msg)
    assert "[TOKEN]" in out, out


def test_passthrough():
    msg = "板块列表为空"
    out = _sanitize_sync_message(msg)
    assert out == msg


def test_truncate():
    msg = "x" * 1000
    out = _sanitize_sync_message(msg)
    assert len(out) == 500
    assert out.endswith("...")


def test_empty():
    assert _sanitize_sync_message("") == ""


if __name__ == "__main__":
    for fn in list(locals().values()):
        if callable(fn) and fn.__name__.startswith("test_"):
            fn()
            print(f"  OK  {fn.__name__}")
    print("all passed")
