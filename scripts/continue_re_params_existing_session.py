#!/usr/bin/env python3
"""Open an existing TALOS session and continue RE params submission only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from talos_cc_frontend_runner import (
    DEFAULT_BASE_URL,
    DEFAULT_CHROME,
    DEFAULT_CHROME_FOR_TESTING,
    get_workflow_state,
    sleep,
)
from talos_re_frontend_runner import submit_re_params, wait_for_submission_state
from talos_re_frontend_runner import ask_progress_questions_if_running


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("session_id")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--browser-executable", default="auto")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--slow-mo", type=int, default=0)
    parser.add_argument("--temperature", default="35")
    parser.add_argument("--duration", default="1")
    parser.add_argument("--tube-count", type=int, default=5)
    parser.add_argument("--output", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    executable = args.browser_executable
    if executable == "auto":
        if Path(DEFAULT_CHROME_FOR_TESTING).exists():
            executable = DEFAULT_CHROME_FOR_TESTING
        elif Path(DEFAULT_CHROME).exists():
            executable = DEFAULT_CHROME
        else:
            executable = ""

    with sync_playwright() as playwright:
        launch_kwargs = {"headless": not args.headed, "slow_mo": args.slow_mo}
        if executable:
            launch_kwargs["executable_path"] = executable
        browser = playwright.chromium.launch(**launch_kwargs)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        try:
            open_session(page, args.base_url, args.session_id)
            before = get_workflow_state(args.base_url, args.session_id)
            before_tasks = before.get("tasks") or []
            before_run = (before_tasks[0].get("latest_run") or {}) if before_tasks else {}
            submit_re_params(page, args.temperature, args.duration, args.tube_count)
            after = wait_for_submission_state(args.base_url, args.session_id, timeout=120)
            after_tasks = after.get("tasks") or []
            after_run = (after_tasks[0].get("latest_run") or {}) if after_tasks else {}
            is_new_run = (
                bool(after_run.get("lab_server_id"))
                and after_run.get("lab_server_id") != before_run.get("lab_server_id")
            )
            if is_new_run:
                progress_questions, progress_questions_skipped = ask_progress_questions_if_running(page, args.base_url, args.session_id)
            else:
                progress_questions, progress_questions_skipped = [], "existing run already in session"
            result = {
                "session_id": args.session_id,
                "before": before,
                "after": after,
                "progress_questions": progress_questions,
                "progress_questions_skipped": progress_questions_skipped,
            }
            if args.output:
                Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            browser.close()


if __name__ == "__main__":
    main()
