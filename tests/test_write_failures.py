"""Exercise write failure boundaries without claiming crash-safe transactions."""

from pathlib import Path

import pytest

from slbt.catalog import review
from slbt.export import reconcile


def test_staging_failure_leaves_originals_and_removes_temporary_files(tmp_path, monkeypatch):
    first, second = tmp_path / "first.yml", tmp_path / "second.yml"
    first.write_bytes(b"original")
    temporary_file = review.tempfile.NamedTemporaryFile
    calls = 0

    def fail_second_stage(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PermissionError("injected staging failure")
        return temporary_file(*args, **kwargs)

    monkeypatch.setattr(review.tempfile, "NamedTemporaryFile", fail_second_stage)
    with pytest.raises(PermissionError, match="injected staging failure"):
        review.apply_changes({first: b"updated", second: b"new"})
    assert first.read_bytes() == b"original"
    assert not second.exists()
    assert not list(tmp_path.glob(".slbt-*"))


def test_replacement_failure_rolls_back_existing_and_new_files(tmp_path, monkeypatch):
    first, second, third = (tmp_path / name for name in ("first.yml", "second.yml", "third.yml"))
    first.write_bytes(b"original first")
    third.write_bytes(b"original third")
    replace = review.os.replace

    def fail_third_replace(source, destination):
        if destination == third:
            raise PermissionError("injected replacement failure")
        return replace(source, destination)

    monkeypatch.setattr(review.os, "replace", fail_third_replace)
    with pytest.raises(PermissionError, match="injected replacement failure"):
        review.apply_changes({first: b"updated first", second: b"new", third: b"updated third"})
    assert first.read_bytes() == b"original first"
    assert not second.exists()
    assert third.read_bytes() == b"original third"
    assert not list(tmp_path.glob(".slbt-*"))


def test_export_write_failure_can_leave_partial_outputs(tmp_path, monkeypatch):
    """Document the current per-file boundary; export does not provide rollback."""
    destination = tmp_path / "generated"
    first, second = destination / "models/first.sql", destination / "models/second.sql"
    first.parent.mkdir(parents=True)
    first.write_bytes(b"original first")
    second.write_bytes(b"original second")
    write_bytes = Path.write_bytes

    def fail_second_write(path, data):
        if path == second:
            raise PermissionError("injected export failure")
        return write_bytes(path, data)

    monkeypatch.setattr(Path, "write_bytes", fail_second_write)
    with pytest.raises(PermissionError, match="injected export failure"):
        reconcile(destination, "dbt", {"models/first.sql": b"updated first", "models/second.sql": b"updated second"})
    assert first.read_bytes() == b"updated first"
    assert second.read_bytes() == b"original second"
