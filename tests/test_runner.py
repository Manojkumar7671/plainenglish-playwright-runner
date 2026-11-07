from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plainenglish_runner.fixture import FixtureAgent, FixtureBrowser
from plainenglish_runner.runner import (
    Action, OpenAICompatibleClient, TestRunner, validate_action,
)


ROOT = Path(__file__).resolve().parents[1]


class ActionValidationTests(unittest.TestCase):
    def test_accepts_supported_click(self):
        action = validate_action({"action": "click", "element_id": "e03"})
        self.assertEqual(action.action, "click")

    def test_rejects_unsupported_action(self):
        with self.assertRaisesRegex(ValueError, "Unsupported action"):
            validate_action({"action": "evaluate", "value": "alert(1)"})

    def test_rejects_unsafe_navigation(self):
        for url in ("javascript:alert(1)", "file:///etc/passwd", "https://user:pass@example.com/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_action({"action": "goto", "url": url})

    def test_requires_action_target_and_required_fields(self):
        with self.assertRaisesRegex(ValueError, "element_id"):
            validate_action({"action": "click"})
        with self.assertRaisesRegex(ValueError, "expected_text"):
            validate_action({"action": "assert"})

    def test_rejects_unsafe_planner_start_url(self):
        class BadStartAgent(FixtureAgent):
            def plan(self, natural_language):
                from plainenglish_runner.runner import TestSpec
                return TestSpec("test", [], "javascript:alert(1)")

        with tempfile.TemporaryDirectory() as tmp:
            runner = TestRunner(FixtureBrowser("negative_expected_failure"), BadStartAgent("negative_expected_failure"), audit_path=Path(tmp) / "audit.jsonl")
            with self.assertRaisesRegex(ValueError, r"http\(s\)"):
                runner.run("test")


class FixtureRunnerTests(unittest.TestCase):
    def run_fixture(self, name: str) -> dict:
        test_text = (ROOT / "examples" / f"{name}.txt").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            runner = TestRunner(
                FixtureBrowser(name), FixtureAgent(name), max_steps=15,
                audit_path=Path(tmp) / "audit.jsonl",
            )
            result = runner.run(test_text)
        return result

    def test_login_simulation_passes_without_credentials(self):
        result = self.run_fixture("phptravels_customer_login")
        self.assertEqual(result["result"], "PASS")
        self.assertIn("no live authentication", result["reason"])

    def test_flight_search_simulation_passes(self):
        result = self.run_fixture("phptravels_flight_search")
        self.assertEqual(result["result"], "PASS")
        self.assertIn("no live fares", result["reason"])

    def test_cloudqa_form_simulation_passes(self):
        result = self.run_fixture("cloudqa_form")
        self.assertEqual(result["result"], "PASS")

    def test_intentional_negative_simulation_fails(self):
        result = self.run_fixture("negative_expected_failure")
        self.assertEqual(result["result"], "FAIL")
        self.assertTrue(any("did not match" in error for error in result["errors"]))

    def test_max_steps_forces_failure(self):
        name = "phptravels_flight_search"
        text = (ROOT / "examples" / f"{name}.txt").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            runner = TestRunner(FixtureBrowser(name), FixtureAgent(name), max_steps=1, audit_path=Path(tmp) / "audit.jsonl")
            result = runner.run(text)
        self.assertEqual(result["result"], "FAIL")
        self.assertIn("maximum step limit", result["reason"].lower())

    def test_audit_does_not_contain_action_values(self):
        class OneFillAgent(FixtureAgent):
            def __init__(self):
                self.position = 0
            def plan(self, text):
                from plainenglish_runner.runner import TestSpec
                return TestSpec("fill", [])
            def next_action(self, spec, view, history):
                if self.position == 0:
                    self.position += 1
                    return Action("fill", "e01", "sensitive-value")
                return Action("finish")
            def judge(self, spec, view, history, errors):
                return {"result": "PASS", "reason": "test"}

        with tempfile.TemporaryDirectory() as tmp:
            audit = Path(tmp) / "audit.jsonl"
            TestRunner(FixtureBrowser("phptravels_customer_login"), OneFillAgent(), audit_path=audit).run("test")
            content = audit.read_text(encoding="utf-8")
        self.assertNotIn("sensitive-value", content)


class APIClientTests(unittest.TestCase):
    def test_parses_openai_compatible_json_reply(self):
        class FakeResponse:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self):
                return json.dumps({"choices": [{"message": {"content": '{"result":"PASS"}'}}]}).encode()

        client = OpenAICompatibleClient("https://api.example.test/v1", "test-model", "secret", timeout=1)
        with patch("urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
            result = client.chat_json("system", "user")
        self.assertEqual(result, {"result": "PASS"})
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.example.test/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")

    def test_requires_explicit_api_configuration(self):
        with self.assertRaisesRegex(ValueError, "must all be set"):
            OpenAICompatibleClient("", "", "")


if __name__ == "__main__":
    unittest.main()
