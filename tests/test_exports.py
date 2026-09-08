from pathlib import Path
from types import SimpleNamespace
import subprocess

from PIL import Image
import pytest

from libresprite_mcp.libresprite_proxy import LibrespriteProxy
from libresprite_mcp.mcp_server import MCPServer
from libresprite_mcp.protocol import RelayConfig


@pytest.fixture
def exporter(tmp_path, monkeypatch):
    executable = tmp_path / "Libre Sprite.exe"
    executable.touch()
    monkeypatch.setenv("LIBRESPRITE_MCP_EXECUTABLE", str(executable))
    server = MCPServer(LibrespriteProxy(RelayConfig(allowed_root=str(tmp_path))))
    calls = []
    def call(operation, **payload):
        calls.append(operation)
        if operation == "get_sprite_info":
            return {"width": 2, "height": 2, "frame_count": 2, "active_frame": 1}
        assert operation == "save_copy"
        Path(payload["path"]).write_bytes(b"\0" * 4 + b"\xe0\xa5" + b"\0" * 122)
        return {"saved": True}
    server.call = call
    return server, calls


def test_export_atomic_and_fixed_native_arguments(exporter, tmp_path, monkeypatch):
    server, calls = exporter
    target = tmp_path / "name & spaces.png"
    target.write_bytes(b"old destination")
    def run(args, **kwargs):
        assert kwargs["timeout"] == 30 and kwargs.get("shell", False) is False
        assert args[1] == "--batch" and args[4:6] == ["--frame-range", "1,1"]
        assert "--script" not in args
        assert target.read_bytes() == b"old destination"
        Image.new("RGBA", (2, 2), (12, 34, 56, 255)).save(args[3])
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, "run", run)
    assert server.export(str(target), "png")["exported"]
    assert calls == ["get_sprite_info", "save_copy"]
    with Image.open(target) as image:
        assert image.getpixel((0, 0)) == (12, 34, 56, 255)
    assert not list(tmp_path.glob(".libresprite-export-*"))


@pytest.mark.parametrize("failure", ["exit", "absent", "corrupt", "timeout"])
def test_export_failure_preserves_destination(exporter, tmp_path, monkeypatch, failure):
    server, _ = exporter
    target = tmp_path / "keep.gif"
    target.write_bytes(b"preserve me")
    def run(args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 30)
        if failure == "corrupt":
            Path(args[-1]).write_bytes(b"not a GIF")
        return SimpleNamespace(returncode=1 if failure == "exit" else 0)
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises((RuntimeError, OSError, subprocess.TimeoutExpired)):
        server.export(str(target), "gif")
    assert target.read_bytes() == b"preserve me"
    assert not list(tmp_path.glob(".libresprite-export-*"))


def test_export_checks_scope_frame_and_extension_before_saving(exporter, tmp_path):
    server, calls = exporter
    for path, frame in [(tmp_path.parent / "outside.png", 0), (tmp_path / "bad.gif", 0),
                        (tmp_path / "bad.png", 2), (tmp_path / "bad.png", -1)]:
        with pytest.raises(ValueError):
            server.export(str(path), "png", frame)
    assert "save_copy" not in calls


def test_missing_executable_fails_without_touching_editor(exporter, tmp_path, monkeypatch):
    server, calls = exporter
    monkeypatch.setenv("LIBRESPRITE_MCP_EXECUTABLE", str(tmp_path / "missing"))
    assert server.export(str(tmp_path / "test.gif"), "gif")["code"] == "EXPORT_EXECUTABLE_REQUIRED"
    assert not calls


def test_missing_snapshot_is_not_success(exporter, tmp_path):
    server, _ = exporter
    original = server.call
    server.call = lambda operation, **payload: {"saved": True} if operation == "save_copy" else original(operation)
    with pytest.raises(RuntimeError, match="did not create"):
        server.export(str(tmp_path / "missing.gif"), "gif")
