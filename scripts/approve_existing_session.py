#!/usr/bin/env python3
"""Open an existing TALOS session and click the active plan approval button."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from talos_cc_frontend_runner import (
    DEFAULT_BASE_URL,
    DEFAULT_CHROME,
    DEFAULT_CHROME_FOR_TESTING,
    approve_plan,
    get_workflow_state,
    sleep,
)


def open_session(page, base_url: str, session_id: str) -> None:
    page.goto(base_url, wait_until="load", timeout=60000)
    page.evaluate(
        """sessionId => {
            localStorage.setItem('talos-active-session', sessionId);
            localStorage.setItem('copilotkit-thread-id', sessionId);
            localStorage.setItem('activeSessionId', sessionId);
            localStorage.setItem('currentSessionId', sessionId);
        }""",
        session_id,
    )
    page.reload(wait_until="load", timeout=60000)
    sleep(5)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("session_id")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--browser-executable", default="auto")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    executable = args.browser_executable
    if executable == "auto":
        if Path(DEFAULT_CHROME_FOR_TESTING).exists():
            executable = DEFAULT_CHROME_FOR_TESTING
        elif Path(DEFAULT_CHROME).exists():
            executable = DEFAULT_CHROME
        else:
            executable = ""

    with sync_playwright() as playwright:
        launch_kwargs = {"headless": True}
        if executable:
            launch_kwargs["executable_path"] = executable
        browser = playwright.chromium.launch(**launch_kwargs)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        try:
            open_session(page, args.base_url, args.session_id)
            before = get_workflow_state(args.base_url, args.session_id)
            approve_plan(page)
            sleep(5)
            after = get_workflow_state(args.base_url, args.session_id)
            result = {"session_id": args.session_id, "before": before, "after": after}
            if args.output:
                Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            browser.close()


if __name__ == "__main__":
    main()
