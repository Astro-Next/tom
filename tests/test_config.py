import json
import tempfile
from pathlib import Path

import pytest

from tom.config import (
    generate_default_settings,
    load_settings,
    parse_interval_seconds,
    validate_time,
)


class TestParseIntervalSeconds:
    def test_minutes(self):
        assert parse_interval_seconds("30m") == 1800

    def test_hours(self):
        assert parse_interval_seconds("2h") == 7200

    def test_days(self):
        assert parse_interval_seconds("1d") == 86400

    def test_single_unit(self):
        assert parse_interval_seconds("1m") == 60

    def test_invalid_format(self):
        with pytest.raises(ValueError, match="Invalid interval"):
            parse_interval_seconds("30")

    def test_invalid_unit(self):
        with pytest.raises(ValueError, match="Invalid interval"):
            parse_interval_seconds("30x")

    def test_empty(self):
        with pytest.raises(ValueError, match="Invalid interval"):
            parse_interval_seconds("")

    def test_zero(self):
        with pytest.raises(ValueError, match="must be positive"):
            parse_interval_seconds("0m")


class TestValidateTime:
    def test_valid(self):
        assert validate_time("22:00") == "22:00"

    def test_midnight(self):
        assert validate_time("00:00") == "00:00"

    def test_end_of_day(self):
        assert validate_time("23:59") == "23:59"

    def test_invalid_hour(self):
        with pytest.raises(ValueError, match="Invalid time"):
            validate_time("25:00")

    def test_invalid_minute(self):
        with pytest.raises(ValueError, match="Invalid time"):
            validate_time("22:60")

    def test_invalid_format(self):
        with pytest.raises(ValueError, match="Invalid time"):
            validate_time("10pm")


class TestLoadSettings:
    def _write_config(self, tmp: Path, data: dict) -> Path:
        p = tmp / ".tom" / "settings.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps(data))
        return p

    def test_load_defaults(self, tmp_path):
        data = generate_default_settings()
        path = self._write_config(tmp_path, data)
        settings = load_settings(path)
        assert settings.id == data["id"]
        assert settings.patrol.interval == "30m"
        assert settings.retro.interval == "1d"
        assert settings.retro.time == "22:00"
        assert settings.agent.timeout == "30m"
        assert settings.agent.max_retries == 2
        assert settings.dev.concurrent == 2
        assert settings.review.concurrent == 2

    def test_custom_values(self, tmp_path):
        data = {
            "id": "test-123",
            "patrol": {"interval": "1h"},
            "retro": {"interval": "2d", "time": "09:00"},
            "agent": {"timeout": "3h", "maxRetries": 5},
            "dev": {"concurrent": 4},
            "review": {"concurrent": 1},
        }
        path = self._write_config(tmp_path, data)
        settings = load_settings(path)
        assert settings.id == "test-123"
        assert settings.patrol.interval == "1h"
        assert settings.retro.interval == "2d"
        assert settings.retro.time == "09:00"
        assert settings.agent.timeout == "3h"
        assert settings.agent.max_retries == 5
        assert settings.dev.concurrent == 4
        assert settings.review.concurrent == 1

    def test_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_settings(tmp_path / "nonexistent.json")

    def test_missing_id(self, tmp_path):
        path = self._write_config(tmp_path, {"patrol": {}})
        with pytest.raises(ValueError, match="missing required field"):
            load_settings(path)

    def test_invalid_interval(self, tmp_path):
        data = generate_default_settings()
        data["patrol"]["interval"] = "bad"
        path = self._write_config(tmp_path, data)
        with pytest.raises(ValueError, match="Invalid interval"):
            load_settings(path)

    def test_invalid_time(self, tmp_path):
        data = generate_default_settings()
        data["retro"]["time"] = "25:00"
        path = self._write_config(tmp_path, data)
        with pytest.raises(ValueError, match="Invalid time"):
            load_settings(path)

    def test_invalid_max_retries(self, tmp_path):
        data = generate_default_settings()
        data["agent"]["maxRetries"] = -1
        path = self._write_config(tmp_path, data)
        with pytest.raises(ValueError, match="positive integer"):
            load_settings(path)

    def test_invalid_concurrent(self, tmp_path):
        data = generate_default_settings()
        data["review"]["concurrent"] = 0
        path = self._write_config(tmp_path, data)
        with pytest.raises(ValueError, match="positive integer"):
            load_settings(path)

    def test_not_a_json_object(self, tmp_path):
        p = tmp_path / ".tom" / "settings.json"
        p.parent.mkdir(parents=True)
        p.write_text('"just a string"')
        with pytest.raises(ValueError, match="JSON object"):
            load_settings(p)


class TestGenerateDefaultSettings:
    def test_has_id(self):
        data = generate_default_settings()
        assert isinstance(data["id"], str)
        assert len(data["id"]) == 12

    def test_unique_ids(self):
        a = generate_default_settings()
        b = generate_default_settings()
        assert a["id"] != b["id"]

    def test_roundtrip(self, tmp_path):
        data = generate_default_settings()
        p = tmp_path / "settings.json"
        p.write_text(json.dumps(data))
        settings = load_settings(p)
        assert settings.id == data["id"]
