"""Verify the local loopback execution boundary without requiring a live source."""

from types import SimpleNamespace
from uuid import uuid4

from app.web.services import batch_sync_scope as scope


def test_execution_scope_requires_both_a_valid_boot_and_a_network_namespace(tmp_path, monkeypatch):
    boot_file = tmp_path / "boot_id"
    monkeypatch.setattr(scope, "_BOOT_ID", boot_file)
    namespace = SimpleNamespace(st_dev=7, st_ino=18)
    monkeypatch.setattr(scope, "_NETWORK_NS", SimpleNamespace(stat=lambda: namespace))
    assert scope.local_batch_sync_scope() is None
    boot_file.write_text("not a boot ID", encoding="ascii")
    assert scope.local_batch_sync_scope() is None
    boot_file.write_text(str(uuid4()), encoding="ascii")
    initial = scope.local_batch_sync_scope()
    assert initial and len(initial) == 64
    assert scope.local_batch_sync_scope() == initial
    namespace.st_ino = 19
    assert scope.local_batch_sync_scope() not in (None, initial)
    namespace.st_ino = 18
    boot_file.write_text(str(uuid4()), encoding="ascii")
    assert scope.local_batch_sync_scope() not in (None, initial)
    monkeypatch.setattr(scope, "_NETWORK_NS", tmp_path / "missing-netns")
    assert scope.local_batch_sync_scope() is None
