import os
from pathlib import Path

import pytest

from tom.process import acquire_lock, is_running, release_lock, _seconds_until_time


class TestLockFiles:
    def test_acquire_and_check(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tom.process.Path.home", lambda: tmp_path)
        acquire_lock("test-proj", "patrol")

        running, pid = is_running("test-proj", "patrol")
        assert running is True
        assert pid == os.getpid()

    def test_release(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tom.process.Path.home", lambda: tmp_path)
        acquire_lock("test-proj", "patrol")
        release_lock("test-proj", "patrol")

        running, pid = is_running("test-proj", "patrol")
        assert running is False

    def test_not_running_when_no_lock(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tom.process.Path.home", lambda: tmp_path)
        running, pid = is_running("test-proj", "patrol")
        assert running is False
        assert pid is None

    def test_stale_lock_detected(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tom.process.Path.home", lambda: tmp_path)
        lock_dir = tmp_path / ".tom" / "test-proj"
        lock_dir.mkdir(parents=True)
        (lock_dir / "patrol.lock").write_text("999999999")

        running, pid = is_running("test-proj", "patrol")
        assert running is False
        assert pid == 999999999

    def test_independent_processes(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tom.process.Path.home", lambda: tmp_path)
        acquire_lock("test-proj", "patrol")
        acquire_lock("test-proj", "retro")

        p_running, _ = is_running("test-proj", "patrol")
        r_running, _ = is_running("test-proj", "retro")
        assert p_running is True
        assert r_running is True

        release_lock("test-proj", "patrol")
        p_running, _ = is_running("test-proj", "patrol")
        r_running, _ = is_running("test-proj", "retro")
        assert p_running is False
        assert r_running is True

        release_lock("test-proj", "retro")


class TestSecondsUntilTime:
    def test_returns_positive(self):
        result = _seconds_until_time("23:59")
        assert result > 0

    def test_returns_float(self):
        result = _seconds_until_time("12:00")
        assert isinstance(result, float)
