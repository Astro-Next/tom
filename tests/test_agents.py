import json

import pytest

from tom.agents import AgentFailure, AgentSuccess, _parse_output
from tom.prompts import analyst_prompt, dev_prompt, pm_prompt, review_prompt


class TestParseOutput:
    def test_structured_output_field(self):
        stdout = json.dumps({"structured_output": {"decision": "need-dev", "type": "feature", "priority": "p1"}})
        result = _parse_output(stdout, "")
        assert isinstance(result, AgentSuccess)
        assert result.output["decision"] == "need-dev"

    def test_result_field(self):
        stdout = json.dumps({"result": {"status": "success", "prTitle": "Fix bug"}})
        result = _parse_output(stdout, "")
        assert isinstance(result, AgentSuccess)
        assert result.output["status"] == "success"

    def test_array_format_with_result_type(self):
        stdout = json.dumps([
            {"type": "text", "text": "thinking..."},
            {"type": "result", "result": json.dumps({"hasFindings": False, "title": None, "body": None})},
        ])
        result = _parse_output(stdout, "")
        assert isinstance(result, AgentSuccess)
        assert result.output["hasFindings"] is False

    def test_plain_dict(self):
        stdout = json.dumps({"decision": "blocked", "reason": "unclear"})
        result = _parse_output(stdout, "")
        assert isinstance(result, AgentSuccess)
        assert result.output["decision"] == "blocked"

    def test_invalid_json(self):
        result = _parse_output("not json at all", "some stderr")
        assert isinstance(result, AgentFailure)
        assert "non-JSON" in result.reason

    def test_non_dict_output(self):
        result = _parse_output('"just a string"', "")
        assert isinstance(result, AgentFailure)
        assert "not a dict" in result.reason

    def test_array_without_result(self):
        stdout = json.dumps([{"type": "text", "text": "hello"}])
        result = _parse_output(stdout, "")
        assert isinstance(result, AgentFailure)
        assert "No result message" in result.reason


class TestPrompts:
    def test_dev_prompt_fills_placeholders(self):
        result = dev_prompt(42, "proj-abc")
        assert "#42" in result
        assert "proj-abc" in result
        assert "{issue_number}" not in result
        assert "{project_id}" not in result

    def test_review_prompt_fills_placeholders(self):
        result = review_prompt(10, 42, "proj-abc")
        assert "#10" in result
        assert "#42" in result
        assert "proj-abc" in result

    def test_pm_prompt_fills_placeholders(self):
        result = pm_prompt(42, "proj-abc")
        assert "#42" in result
        assert "proj-abc" in result

    def test_analyst_prompt_fills_placeholders(self):
        result = analyst_prompt("#10, #11", "#42, #43", "proj-abc")
        assert "#10, #11" in result
        assert "#42, #43" in result
        assert "proj-abc" in result
