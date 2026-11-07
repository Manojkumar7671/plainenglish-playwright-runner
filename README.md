# Plain-English Playwright Runner

A small Python runner that turns a natural-language browser test into a goal and success criteria, repeatedly asks an OpenAI-compatible chat-completions API for one structured action, executes that action in Playwright, rereads the visible page, and requests a final PASS/FAIL judgment. It also includes deterministic local fixtures so the full control loop can be demonstrated without network access, browser installation, or API credentials.

**Fixture PASS/FAIL results are simulations, not results from PHPTravels or CloudQA.** No live credentials or live-site outcomes are included in this repository.

## Requirements and installation

- Python 3.10+
- For live mode: a Chromium installation supported by Playwright, access to the configured compatible API, and authorization to test the target site.

```bash
cd /home/ubuntu/projects/plainenglish-playwright-runner
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
```

Set `OPENAI_BASE_URL`, `OPENAI_MODEL`, and `OPENAI_API_KEY` in `.env` (or export them in the environment). The base URL should be the provider's API base, normally including its version path when applicable; the client appends `/chat/completions`. There are deliberately no baked-in endpoint, model, or credentials. The configured endpoint must accept OpenAI-compatible chat-completions requests, bearer authentication, and JSON-object response mode.

Keep `.env` private and never commit it. The PHPTravels example references account credentials only as `${ENV:PHPTRAVELS_EMAIL}` and `${ENV:PHPTRAVELS_PASSWORD}`. At execution time the runner resolves a whole-value `${ENV:NAME}` fill reference from the process environment and does not add the resolved value to the action history or audit log. Do not paste actual passwords into test files or prompts. Page content is sent to the configured LLM provider for action selection and final evaluation; do not use this mode with confidential pages unless that provider and its data handling are approved for the content.

## Run a deterministic offline example

The fixture path runs with no external services and no environment setup:

```bash
python -m plainenglish_runner.cli examples/phptravels_customer_login.txt --fixture phptravels_customer_login --audit /tmp/login-audit.jsonl
python -m plainenglish_runner.cli examples/phptravels_flight_search.txt --fixture phptravels_flight_search --audit /tmp/flights-audit.jsonl
python -m plainenglish_runner.cli examples/cloudqa_form.txt --fixture cloudqa_form --audit /tmp/cloudqa-audit.jsonl
python -m plainenglish_runner.cli examples/negative_expected_failure.txt --fixture negative_expected_failure --audit /tmp/negative-audit.jsonl
```

Each case exercises local fixture controls/actions. The login fixture accepts synthetic values and switches to a fictional dashboard; the flight fixture renders fictional results; the CloudQA fixture simulates form submission. They do not contact external websites. The negative fixture deliberately lacks the requested booking confirmation and returns **FAIL** (CLI exit code 1) by design.

## Run live tests

```bash
python -m plainenglish_runner.cli examples/phptravels_customer_login.txt --max-steps 16 --timeout 45
python -m plainenglish_runner.cli examples/phptravels_flight_search.txt --max-steps 16
python -m plainenglish_runner.cli examples/cloudqa_form.txt --max-steps 16
```

For the PHPTravels login test, first set the authorized account's email/password in `.env` or the environment. The CloudQA sample route may vary by workspace; replace the URL in its test file with the authorized practice form URL. The examples are starting points, not a claim that a current live route works. Sites may change, block automation, require consent, or present captchas/2FA; this runner does not bypass those controls. A CAPTCHA or unavailable service should be handled manually or reported as blocked, not treated as a successful test. Live flight-search wording explicitly prohibits booking or payment.

Optional CLI flags:

- `--headed`: show Chromium instead of running headless.
- `--max-steps N`: action limit (default 20); reaching the limit forces FAIL.
- `--timeout SECONDS`: per-LLM-request timeout (default 45).
- `--browser-timeout-ms MS`: per-operation Playwright timeout (default 15000).
- `--audit PATH`: JSONL action audit destination (default `audit.jsonl`).

Exit codes: `0` means PASS, `1` means the test result is FAIL, and `2` means setup/runner error. The JSON output contains the final result/reason, goal, number of actions, errors, and final URL. Audit entries contain timestamp, action type, element ID, outcome, and a small amount of safe metadata. They omit form values and full URLs (only a goto destination hostname is retained).

## Natural-language prompt format

Create a UTF-8 `.txt` file with the test request. Be specific about the start URL, requested steps, and observable pass criteria. Reference sensitive values using whole-value environment placeholders; never include their values directly. Example:

```text
Open https://example.test/login. Enter ${ENV:TEST_EMAIL} in the email field and ${ENV:TEST_PASSWORD} in the password field. Submit and pass only if the dashboard heading is visible. Do not print or store credentials.
```

The planner converts the request to a goal, string-list success criteria, and optional start URL. For each turn, the action prompt supplies that goal, the latest page URL/title/visible text, the compact visible-interactive list (`id`, `role`, `label`, `text`), and recent action history. The page is then reread after each attempted operation. The final prompt asks for a reasoned verdict based on the criteria, latest page, and execution errors; insufficient evidence should fail.

The allowed action JSON shape is:

```json
{"action":"click|fill|select|goto|assert|finish","element_id":null,"value":null,"url":null,"expected_text":null}
```

Examples:

```json
{"action":"fill","element_id":"e01","value":"${ENV:TEST_EMAIL}","url":null,"expected_text":null}
{"action":"assert","element_id":null,"value":null,"url":null,"expected_text":"Dashboard"}
{"action":"finish","element_id":null,"value":null,"url":null,"expected_text":null}
```

Actions are validated before execution. Element-targeted actions must reference IDs in the most recently collected visible-interactive list; click/fill/select are restricted to compatible roles. Navigation is limited to credential-free HTTP(S) URLs. There is no arbitrary JavaScript, raw selector, shell command, upload, or payment action. These controls reduce risk but do not make an LLM/browser sandbox a security boundary; run against trusted pages and restrict the environment/network appropriately.

## Tests

Run the unit suite with:

```bash
python -m unittest discover -s tests -v
```

The tests exercise offline PASS/FAIL paths, max-step enforcement, action validation, safe navigation checks, redacted audit output, and OpenAI-compatible response parsing. The live browser and provider are not contacted by the unit tests.
