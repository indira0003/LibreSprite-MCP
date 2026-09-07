from pathlib import Path
import pytest
from libresprite_mcp.protocol import (
    RelayConfig, new_request_id, new_session_token, validate_operation,
    normalize_allowed_path, SAFE_OPERATIONS
)

def test_loopback_only():
    RelayConfig(host="127.0.0.1")
    with pytest.raises(ValueError):
        RelayConfig(host="0.0.0.0")

def test_request_ids_unique():
    assert new_request_id() != new_request_id()

def test_session_tokens_unique_and_long():
    a,b = new_session_token(), new_session_token()
    assert a != b and len(a) >= 32

def test_safe_blocks_run_script():
    with pytest.raises(ValueError):
        validate_operation("run_script", "safe")

def test_dev_allows_run_script():
    validate_operation("run_script", "dev")

def test_unknown_operation_blocked():
    with pytest.raises(ValueError):
        validate_operation("totally_not_real", "safe")

def test_safe_operations_do_not_include_run_script():
    assert "run_script" not in SAFE_OPERATIONS

def test_path_scope(tmp_path):
    root=tmp_path/"root"; root.mkdir()
    inside=root/"a.png"
    assert Path(normalize_allowed_path(str(inside), str(root))).parent == root
    outside=tmp_path/"outside.png"
    with pytest.raises(ValueError):
        normalize_allowed_path(str(outside), str(root))


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_timeouts_must_be_finite_and_positive(value):
    with pytest.raises(ValueError):
        RelayConfig(timeout=value)
    with pytest.raises(ValueError):
        RelayConfig(lease_seconds=value)
