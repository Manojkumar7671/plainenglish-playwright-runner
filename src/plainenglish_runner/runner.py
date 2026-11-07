from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit


ALLOWED_ACTIONS = {"click", "fill", "select", "goto", "assert", "finish"}
MAX_PAGE_TEXT = 6000
MAX_ELEMENTS = 80


@dataclass
class Element:
    id: str
    role: str
    label: str = ""
    text: str = ""


@dataclass
class PageView:
    url: str
    title: str
    text: str
    elements: list[Element]

    def compact(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "visible_text": self.text[:MAX_PAGE_TEXT],
            "interactive_elements": [asdict(element) for element in self.elements[:MAX_ELEMENTS]],
        }


@dataclass
class TestSpec:
    goal: str
    success_criteria: list[str]
    start_url: str | None = None


@dataclass
class Action:
    action: str
    element_id: str | None = None
    value: str | None = None
    url: str | None = None
    expected_text: str | None = None


class Browser(Protocol):
    def goto(self, url: str) -> None: ...
    def read_page(self) -> PageView: ...
    def execute(self, action: Action, view: PageView) -> None: ...
    def close(self) -> None: ...


class Agent(Protocol):
    def plan(self, natural_language: str) -> TestSpec: ...
    def next_action(self, spec: TestSpec, view: PageView, history: list[dict[str, Any]]) -> Action: ...
    def judge(self, spec: TestSpec, view: PageView, history: list[dict[str, Any]], errors: list[str]) -> dict[str, str]: ...


class OpenAICompatibleClient:
    """Minimal OpenAI-compatible chat-completions transport; all endpoints/config come from env."""

    def __init__(self, base_url: str, model: str, api_key: str, timeout: float = 45.0):
        self.base_url = base_url.strip().rstrip("/")
        self.model = model.strip()
        self.api_key = api_key
        self.timeout = timeout
        if not self.base_url or not self.model or not self.api_key:
            raise ValueError("OPENAI_BASE_URL, OPENAI_MODEL, and OPENAI_API_KEY must all be set")
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("OPENAI_BASE_URL must be an http(s) base URL")

    @classmethod
    def from_env(cls, timeout: float = 45.0) -> "OpenAICompatibleClient":
        return cls(
            base_url=os.environ.get("OPENAI_BASE_URL", ""),
            model=os.environ.get("OPENAI_MODEL", ""),
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            timeout=timeout,
        )

    def chat_json(self, system: str, user: str) -> dict[str, Any]:
        url = self.base_url + "/chat/completions"
        body = json.dumps({
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Do not expose request headers or API key in errors.
            raise RuntimeError(f"LLM API returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"LLM API request failed: {type(exc).__name__}") from None
        try:
            content = payload["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
            if not isinstance(content, str):
                raise ValueError("content is not text")
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE)
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError("JSON root is not an object")
            return result
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            raise RuntimeError("LLM response was not valid JSON in the expected chat-completions shape") from None


class OpenAICompatibleAgent:
    PLAN_SYSTEM = """You translate a browser test request into a concise objective. Return JSON only: {\"goal\": string, \"success_criteria\": [string], \"start_url\": string|null}. Extract an explicit HTTP(S) URL if present; otherwise start_url is null. Do not invent credentials, URLs, or outcomes. Preserve expected-negative intent (the expected result may be failure)."""
    ACTION_SYSTEM = """You are a cautious Playwright test controller. Based only on the test, current visible page, and history, return exactly one JSON object with keys action, element_id, value, url, expected_text. action must be click, fill, select, goto, assert, or finish. Use only an element id listed in interactive_elements for click/fill/select. Use goto only for an explicit test URL. fill/select values must be requested by the test or already supplied in its text; never invent passwords or personal data. For assertions use expected_text. Return finish when complete or blocked. Do not output commentary."""
    JUDGE_SYSTEM = """Judge whether the stated browser test goal and success criteria were actually met. Consider execution errors and expected-negative criteria. Return JSON only: {\"result\":\"PASS\" or \"FAIL\",\"reason\": concise string}. Do not infer success from attempted actions. If evidence is insufficient, return FAIL."""

    def __init__(self, client: OpenAICompatibleClient):
        self.client = client

    def plan(self, natural_language: str) -> TestSpec:
        data = self.client.chat_json(self.PLAN_SYSTEM, json.dumps({"test_request": natural_language}))
        goal = data.get("goal")
        criteria = data.get("success_criteria")
        start_url = data.get("start_url")
        if not isinstance(goal, str) or not goal.strip() or not isinstance(criteria, list) or not all(isinstance(x, str) for x in criteria):
            raise ValueError("LLM plan must include a non-empty goal and string-list success_criteria")
        if start_url is not None and not isinstance(start_url, str):
            raise ValueError("LLM plan start_url must be a string or null")
        return TestSpec(goal.strip(), criteria, start_url)

    def next_action(self, spec: TestSpec, view: PageView, history: list[dict[str, Any]]) -> Action:
        data = self.client.chat_json(self.ACTION_SYSTEM, json.dumps({
            "goal": spec.goal,
            "success_criteria": spec.success_criteria,
            "current_page": view.compact(),
            "history": history[-12:],
        }, ensure_ascii=False))
        return validate_action(data)

    def judge(self, spec: TestSpec, view: PageView, history: list[dict[str, Any]], errors: list[str]) -> dict[str, str]:
        data = self.client.chat_json(self.JUDGE_SYSTEM, json.dumps({
            "goal": spec.goal, "success_criteria": spec.success_criteria,
            "final_page": view.compact(), "history": history[-20:], "execution_errors": errors,
        }, ensure_ascii=False))
        result = str(data.get("result", "")).upper()
        reason = data.get("reason")
        if result not in {"PASS", "FAIL"} or not isinstance(reason, str) or not reason.strip():
            raise ValueError("LLM judge must return result PASS/FAIL and a non-empty reason")
        return {"result": result, "reason": reason.strip()}


def validate_action(data: dict[str, Any]) -> Action:
    if not isinstance(data, dict):
        raise ValueError("Action must be a JSON object")
    action = data.get("action")
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"Unsupported action: {action!r}")
    element_id, value, url, expected_text = (data.get(k) for k in ("element_id", "value", "url", "expected_text"))
    for key, item in (("element_id", element_id), ("value", value), ("url", url), ("expected_text", expected_text)):
        if item is not None and not isinstance(item, str):
            raise ValueError(f"{key} must be a string or null")
    if action in {"click", "fill", "select"} and not element_id:
        raise ValueError(f"{action} requires element_id")
    if action in {"fill", "select"} and value is None:
        raise ValueError(f"{action} requires value")
    if action == "goto":
        if not url:
            raise ValueError("goto requires url")
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("goto accepts only credential-free http(s) URLs")
    if action == "assert" and not expected_text:
        raise ValueError("assert requires expected_text")
    return Action(action, element_id, value, url, expected_text)


def resolve_env_placeholder(value: str) -> tuple[str, bool]:
    """Resolve a whole-value ${ENV:NAME} reference without logging the resolved value."""
    match = re.fullmatch(r"\$\{ENV:([A-Za-z_][A-Za-z0-9_]*)\}", value)
    if not match:
        return value, False
    key = match.group(1)
    if key not in os.environ:
        raise ValueError(f"Required environment variable {key} is not set")
    return os.environ[key], True


class AuditLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, step: int, action: Action, status: str, detail: str = "") -> None:
        safe = {"action": action.action, "element_id": action.element_id}
        if action.action == "goto" and action.url:
            safe["destination_host"] = urlsplit(action.url).hostname
        if action.action == "assert":
            safe["assertion"] = "matched" if status == "ok" else "not_matched"
        item = {
            "timestamp": datetime.now(timezone.utc).isoformat(), "step": step,
            "action": safe, "status": status, "detail": detail[:240],
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")


class TestRunner:
    def __init__(self, browser: Browser, agent: Agent, max_steps: int = 20, audit_path: str | Path = "audit.jsonl"):
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        self.browser = browser
        self.agent = agent
        self.max_steps = max_steps
        self.audit = AuditLog(audit_path)

    def run(self, natural_language: str) -> dict[str, Any]:
        spec = self.agent.plan(natural_language)
        if spec.start_url:
            initial_navigation = validate_action({"action": "goto", "url": spec.start_url})
            self.browser.goto(initial_navigation.url or "")
        history: list[dict[str, Any]] = []
        errors: list[str] = []
        terminated = False
        view = self.browser.read_page()
        for step in range(1, self.max_steps + 1):
            action = validate_action(asdict(self.agent.next_action(spec, view, history)))
            if action.action == "finish":
                self.audit.record(step, action, "ok", "agent finished")
                history.append({"step": step, "action": "finish", "status": "ok"})
                terminated = True
                break
            if action.action == "assert":
                matched = action.expected_text in view.text
                status = "ok" if matched else "failed"
                detail = "assertion matched" if matched else "assertion did not match visible page text"
                self.audit.record(step, action, status, detail)
                history.append({"step": step, "action": "assert", "status": status, "detail": detail})
                if not matched:
                    errors.append(detail)
            else:
                try:
                    if action.action == "goto":
                        self.browser.goto(action.url or "")
                    else:
                        element = next((item for item in view.elements if item.id == action.element_id), None)
                        if element is None:
                            raise ValueError("element_id was not in the latest visible interactive-element list")
                        value = action.value
                        is_secret = False
                        if value is not None:
                            value, is_secret = resolve_env_placeholder(value)
                        self.browser.execute(Action(action.action, action.element_id, value, action.url, action.expected_text), view)
                    self.audit.record(step, action, "ok", "action executed" + ("; secret value redacted" if action.value and action.value.startswith("${ENV:") else ""))
                    history.append({"step": step, "action": action.action, "status": "ok"})
                except Exception as exc:
                    message = str(exc)[:200]
                    errors.append(f"step {step}: {message}")
                    self.audit.record(step, action, "failed", message)
                    history.append({"step": step, "action": action.action, "status": "failed", "detail": message})
            # Re-read after every attempted operation, including assertion/failed operation.
            view = self.browser.read_page()
        else:
            errors.append(f"maximum step limit ({self.max_steps}) reached without finish")
        try:
            verdict = self.agent.judge(spec, view, history, errors)
        except Exception as exc:
            verdict = {"result": "FAIL", "reason": f"Final evaluation failed: {type(exc).__name__}: {str(exc)[:180]}"}
        if not terminated:
            verdict["result"] = "FAIL"
            if "maximum step limit" in " ".join(errors):
                verdict["reason"] = f"Maximum step limit reached. {verdict['reason']}"
        return {
            "result": verdict["result"], "reason": verdict["reason"],
            "goal": spec.goal, "steps": len(history), "terminated": terminated,
            "errors": errors, "final_url": view.url,
        }


class PlaywrightBrowser:
    """Synchronous Playwright adapter. Install browsers with `playwright install chromium`."""

    def __init__(self, timeout_ms: int = 15000, headless: bool = True):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Playwright is not installed. Run pip install -r requirements.txt and playwright install chromium.") from exc
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=headless)
        self._page = self._browser.new_page()
        self._page.set_default_timeout(timeout_ms)
        self._counter = 0

    def goto(self, url: str) -> None:
        self._page.goto(url, wait_until="domcontentloaded")

    def read_page(self) -> PageView:
        raw = self._page.evaluate("""() => {
          const selectors = 'button,a,input,textarea,select,[role="button"],[role="link"],[role="checkbox"],[role="radio"],[role="combobox"]';
          const nodes = Array.from(document.querySelectorAll(selectors));
          nodes.forEach(el => el.removeAttribute('data-perunner-id'));
          const visible = nodes.filter(el => {
            const r = el.getBoundingClientRect(), s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && !el.disabled;
          }).slice(0, 80);
          visible.forEach((el, i) => el.setAttribute('data-perunner-id', String(i)));
          return {
            url: location.href, title: document.title,
            text: document.body ? document.body.innerText.slice(0, 6000) : '',
            elements: visible.map((el, i) => {
              const tag = el.tagName.toLowerCase();
              const role = el.getAttribute('role') || ({button:'button',a:'link',input: (el.type==='submit'||el.type==='button'?'button':(el.type==='checkbox'?'checkbox':(el.type==='radio'?'radio':'textbox'))),textarea:'textbox',select:'combobox'}[tag] || tag);
              const labels = el.labels ? Array.from(el.labels).map(x => x.innerText).join(' ').trim() : '';
              const label = el.getAttribute('aria-label') || labels || el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('name') || '';
              const text = (el.innerText || (el.type==='submit' ? el.value : '') || '').trim().slice(0, 120);
              return {id:'e'+String(i+1).padStart(2,'0'), role, label:label.slice(0,120), text};
            })
          };
        }""")
        self._counter = len(raw["elements"])
        return PageView(raw["url"], raw["title"], raw["text"], [Element(**item) for item in raw["elements"]])

    def execute(self, action: Action, view: PageView) -> None:
        match = re.fullmatch(r"e(\d{2})", action.element_id or "")
        if not match:
            raise ValueError("Invalid element id")
        index = int(match.group(1)) - 1
        if index < 0 or index >= len(view.elements):
            raise ValueError("Element id is out of range")
        selector = f'[data-perunner-id="{index}"]'
        locator = self._page.locator(selector).first
        role = view.elements[index].role
        if action.action == "click":
            if role not in {"button", "link", "checkbox", "radio"}:
                raise ValueError("click is restricted to buttons, links, checkboxes, and radios")
            locator.click()
        elif action.action == "fill":
            if role != "textbox":
                raise ValueError("fill is restricted to textboxes")
            locator.fill(action.value or "")
        elif action.action == "select":
            if role != "combobox":
                raise ValueError("select is restricted to comboboxes")
            locator.select_option(label=action.value)
        else:
            raise ValueError("Unsupported browser operation")

    def close(self) -> None:
        self._browser.close()
        self._playwright.stop()
