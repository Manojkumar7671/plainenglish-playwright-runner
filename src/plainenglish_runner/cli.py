from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # .env support is optional at runtime
    load_dotenv = None

from .fixture import FixtureAgent, FixtureBrowser
from .runner import OpenAICompatibleAgent, OpenAICompatibleClient, PlaywrightBrowser, TestRunner

FIXTURES = ("phptravels_customer_login", "phptravels_flight_search", "negative_expected_failure", "cloudqa_form")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a plain-English browser test using Playwright and an OpenAI-compatible API.")
    parser.add_argument("test_file", help="UTF-8 text file containing a natural-language browser test")
    parser.add_argument("--fixture", choices=FIXTURES, help="Run a deterministic local simulation; no browser, website, or LLM API is contacted")
    parser.add_argument("--max-steps", type=int, default=20, help="Maximum action count (default: 20)")
    parser.add_argument("--timeout", type=float, default=45.0, help="LLM request timeout in seconds (default: 45)")
    parser.add_argument("--browser-timeout-ms", type=int, default=15000, help="Per-operation Playwright timeout in milliseconds")
    parser.add_argument("--headed", action="store_true", help="Show Chromium for live mode")
    parser.add_argument("--audit", default="audit.jsonl", help="Append redacted action metadata to this JSONL file")
    args = parser.parse_args(argv)

    if load_dotenv:
        load_dotenv()
    try:
        natural_language = Path(args.test_file).read_text(encoding="utf-8").strip()
        if not natural_language:
            raise ValueError("Test file is empty")
        if args.fixture:
            browser = FixtureBrowser(args.fixture)
            agent = FixtureAgent(args.fixture)
        else:
            agent = OpenAICompatibleAgent(OpenAICompatibleClient.from_env(timeout=args.timeout))
            browser = PlaywrightBrowser(timeout_ms=args.browser_timeout_ms, headless=not args.headed)
        try:
            result = TestRunner(browser, agent, max_steps=args.max_steps, audit_path=args.audit).run(natural_language)
        finally:
            browser.close()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["result"] == "PASS" else 1
    except Exception as exc:
        # Never print environment values or request headers.
        print(f"Runner error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
