from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from tom import __version__
from tom.config import Settings, parse_interval_seconds

_log = logging.getLogger("tom")


def _lock_dir(project_id: str) -> Path:
    return Path.home() / ".tom" / project_id


def _lock_path(project_id: str, process_name: str) -> Path:
    return _lock_dir(project_id) / f"{process_name}.lock"


def is_running(project_id: str, process_name: str) -> tuple[bool, int | None]:
    lock = _lock_path(project_id, process_name)
    if not lock.exists():
        return False, None
    try:
        pid = int(lock.read_text().strip())
    except (ValueError, OSError):
        return False, None
    try:
        os.kill(pid, 0)
        return True, pid
    except OSError:
        return False, pid


def acquire_lock(project_id: str, process_name: str) -> None:
    lock = _lock_path(project_id, process_name)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(str(os.getpid()))


def release_lock(project_id: str, process_name: str) -> None:
    lock = _lock_path(project_id, process_name)
    if lock.exists():
        lock.unlink()


def stop_process(project_id: str, process_name: str) -> bool:
    running, pid = is_running(project_id, process_name)
    if not running:
        if pid is not None:
            _log.warning("Stale lock for %s (pid %d is dead)", process_name, pid)
            release_lock(project_id, process_name)
        return False
    _log.info("Stopping %s (pid %d)", process_name, pid)
    os.kill(pid, signal.SIGTERM)
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass
    release_lock(project_id, process_name)
    _log.info("Stopped %s (pid %d)", process_name, pid)
    return True


async def run_patrol_loop(
    settings: Settings,
    project_root: str,
    default_branch: str,
    *,
    run_now: bool = False,
) -> None:
    from tom.github import GitHubClient
    from tom.patrol import run_patrol

    interval = parse_interval_seconds(settings.patrol.interval)
    shutdown = asyncio.Event()
    active_processes: dict[int, tuple[asyncio.subprocess.Process, asyncio.TimerHandle]] = {}

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, shutdown.set)
    loop.add_signal_handler(signal.SIGINT, shutdown.set)

    acquire_lock(settings.id, "patrol")
    _log.info("Tom v%s — patrol loop started (interval: %s)", __version__, settings.patrol.interval)

    try:
        if not run_now:
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                pass

        while not shutdown.is_set():
            client = GitHubClient()
            try:
                summary = await run_patrol(settings, project_root, default_branch, client, active_processes)
                text = summary.format()
                if text:
                    _log.info("Patrol summary:\n%s", text)
                else:
                    _log.info("Patrol: nothing to report")
            except Exception:
                _log.exception("Patrol cycle failed")
            finally:
                await client.close()

            try:
                await asyncio.wait_for(shutdown.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                pass
    finally:
        for number, (proc, timer) in active_processes.items():
            if proc.returncode is None:
                proc.terminate()
                _log.info("Terminated agent for #%d (pid %d)", number, proc.pid)
        active_processes.clear()
        release_lock(settings.id, "patrol")
        _log.info("Patrol loop stopped")


async def run_retro_loop(
    settings: Settings,
    project_root: str,
    *,
    run_now: bool = False,
) -> None:
    from tom.github import GitHubClient
    from tom.retro import run_retro

    interval = parse_interval_seconds(settings.retro.interval)
    shutdown = asyncio.Event()

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, shutdown.set)
    loop.add_signal_handler(signal.SIGINT, shutdown.set)

    acquire_lock(settings.id, "retro")
    _log.info("Tom v%s — retro loop started (interval: %s, time: %s)", __version__, settings.retro.interval, settings.retro.time)

    try:
        if not run_now:
            delay = _seconds_until_time(settings.retro.time)
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=delay)
                return
            except asyncio.TimeoutError:
                pass

        while not shutdown.is_set():
            client = GitHubClient()
            try:
                issue = await run_retro(settings, project_root, client)
                if issue:
                    _log.info("Retro issue created: #%d", issue["number"])
                else:
                    _log.info("Retro: nothing to report")
            except Exception:
                _log.exception("Retro cycle failed")
            finally:
                await client.close()

            delay = _seconds_until_time(settings.retro.time)
            if delay < 60:
                delay += interval
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=delay)
                return
            except asyncio.TimeoutError:
                pass
    finally:
        release_lock(settings.id, "retro")
        _log.info("Retro loop stopped")


def _seconds_until_time(time_str: str) -> float:
    parts = time_str.split(":")
    target = time(int(parts[0]), int(parts[1]))
    now = datetime.now()
    target_dt = now.replace(hour=target.hour, minute=target.minute, second=0, microsecond=0)
    if target_dt <= now:
        target_dt += timedelta(days=1)
    return (target_dt - now).total_seconds()
