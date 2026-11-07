from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .runner import Action, Element, PageView, TestSpec


@dataclass
class FixtureBrowser:
    """A deliberately local simulation. It is not a recording of any live website."""
    scenario: str

    def __post_init__(self) -> None:
        self.url = f"fixture://{self.scenario}"
        self.title = "Offline Playwright Runner Fixture"
        self.state = "initial"
        self.values: dict[str, str] = {}

    def goto(self, url: str) -> None:
        # Fixture navigation is local: external addresses are intentionally rejected.
        if not url.startswith("fixture://"):
            raise ValueError("Offline fixture mode refuses external navigation")
        self.url = url

    def read_page(self) -> PageView:
        if self.scenario == "phptravels_customer_login":
            if self.state == "dashboard":
                return self._view("Dashboard", "Customer Dashboard | Logout", [])
            return self._view("Customer Login", "Customer Login Email Password Login", [
                Element("e01", "textbox", "Email"), Element("e02", "textbox", "Password"), Element("e03", "button", "Login", "Login")
            ])
        if self.scenario == "phptravels_flight_search":
            if self.state == "results":
                return self._view("Flight Results", "Flight search results: Bangkok to London. No live fares are shown in fixture mode.", [])
            return self._view("Flight Search", "Search flights", [
                Element("e01", "textbox", "From"), Element("e02", "textbox", "To"), Element("e03", "button", "Search", "Search")
            ])
        if self.scenario == "negative_expected_failure":
            return self._view("Empty Results", "Search completed. No booking confirmation is present in this fixture.", [])
        if self.scenario == "cloudqa_form":
            if self.state == "submitted":
                return self._view("CloudQA Form Fixture", "Form submitted successfully (offline fixture simulation).", [])
            return self._view("CloudQA Form Fixture", "Automation Practice Form", [
                Element("e01", "textbox", "Name"), Element("e02", "textbox", "Email"),
                Element("e03", "combobox", "Country"), Element("e04", "button", "Submit", "Submit")
            ])
        raise ValueError(f"Unknown fixture scenario: {self.scenario}")

    def _view(self, title: str, text: str, elements: list[Element]) -> PageView:
        return PageView(self.url, title, text, elements)

    def execute(self, action: Action, view: PageView) -> None:
        if action.action == "fill":
            self.values[action.element_id or ""] = action.value or ""
            return
        if action.action == "select":
            self.values[action.element_id or ""] = action.value or ""
            return
        if action.action != "click":
            raise ValueError(f"Fixture does not support action {action.action}")
        if self.scenario == "phptravels_customer_login" and action.element_id == "e03":
            if self.values.get("e01") and self.values.get("e02"):
                self.state = "dashboard"
            else:
                raise ValueError("Fixture login requires both fields to be filled")
        elif self.scenario == "phptravels_flight_search" and action.element_id == "e03":
            if self.values.get("e01") and self.values.get("e02"):
                self.state = "results"
            else:
                raise ValueError("Fixture search requires both route fields to be filled")
        elif self.scenario == "cloudqa_form" and action.element_id == "e04":
            if self.values.get("e01") and self.values.get("e02"):
                self.state = "submitted"
            else:
                raise ValueError("Fixture form requires name and email")
        else:
            raise ValueError("Fixture action does not match an available control")

    def close(self) -> None:
        return None


class FixtureAgent:
    """Deterministic demonstration controller; it does not call an LLM."""
    def __init__(self, scenario: str):
        self.scenario = scenario
        self.actions = self._script(scenario)
        self.position = 0

    @staticmethod
    def _script(scenario: str) -> list[Action]:
        if scenario == "phptravels_customer_login":
            return [
                Action("fill", "e01", "offline-fixture-account"),
                Action("fill", "e02", "offline-fixture-only"),
                Action("click", "e03"), Action("assert", expected_text="Customer Dashboard"), Action("finish")
            ]
        if scenario == "phptravels_flight_search":
            return [
                Action("fill", "e01", "Bangkok"), Action("fill", "e02", "London"),
                Action("click", "e03"), Action("assert", expected_text="Flight search results"), Action("finish")
            ]
        if scenario == "negative_expected_failure":
            return [Action("assert", expected_text="Booking confirmation"), Action("finish")]
        if scenario == "cloudqa_form":
            return [
                Action("fill", "e01", "Fixture Example"), Action("fill", "e02", "fixture@example.invalid"),
                Action("select", "e03", "United States"), Action("click", "e04"),
                Action("assert", expected_text="Form submitted successfully"), Action("finish")
            ]
        raise ValueError(f"Unknown fixture scenario: {scenario}")

    def plan(self, natural_language: str) -> TestSpec:
        goal = " ".join(natural_language.split())
        return TestSpec(goal, ["Follow the stated test intent and assess the resulting page."])

    def next_action(self, spec: TestSpec, view: PageView, history: list[dict[str, Any]]) -> Action:
        if self.position >= len(self.actions):
            return Action("finish")
        action = self.actions[self.position]
        self.position += 1
        return action

    def judge(self, spec: TestSpec, view: PageView, history: list[dict[str, Any]], errors: list[str]) -> dict[str, str]:
        if self.scenario == "negative_expected_failure":
            return {"result": "FAIL", "reason": "Fixture expected-failure case: the requested booking confirmation is absent. This is an offline simulation, not a live-site result."}
        if self.scenario == "phptravels_customer_login" and "Customer Dashboard" in view.text:
            return {"result": "PASS", "reason": "Offline fixture reached its simulated dashboard state; no live authentication was performed."}
        if self.scenario == "phptravels_flight_search" and "Flight search results" in view.text:
            return {"result": "PASS", "reason": "Offline fixture reached its simulated flight-results state; no live fares were queried."}
        if self.scenario == "cloudqa_form" and "Form submitted successfully" in view.text:
            return {"result": "PASS", "reason": "Offline fixture reached its simulated form-submission state; no live website was used."}
        return {"result": "FAIL", "reason": "Offline fixture did not reach its expected simulated state."}
