#!/usr/bin/env python3
"""Create TALOS chat sessions via frontend and send one or more messages."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from talos_cc_frontend_runner import (
    DEFAULT_BASE_URL,
    DEFAULT_CHROME,
    DEFAULT_CHROME_FOR_TESTING,
    body_text,
    create_new_conversation,
    send_chat_message,
    sleep,
    update_session_title,
    wait_until,
)


def textarea_enabled(page) -> bool:
    try:
        textarea = page.locator("textarea[placeholder*='TALOS'], textarea").first
        return textarea.count() > 0 and textarea.get_attribute("disabled") is None
    except Exception:
        return False


def run_case(browser, base_url: str, title: str, messages: list[str]) -> dict:
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.goto(base_url, wait_until="load", timeout=60000)
    session_id = create_new_conversation(page)
    update_session_title(base_url, session_id, title)

    turn_results = []
    for message in messages:
        send_chat_message(page, message)
        wait_until(lambda: textarea_enabled(page), timeout=180, description="agent response complete")
        sleep(2)
        turn_results.append({"user": message, "body_tail": body_text(page)[-3000:]})

    page.close()
    return {"title": title, "session_id": session_id, "turns": turn_results}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--case", action="append", required=True, help="JSON object: {title,messages}")
    parser.add_argument("--browser-executable", default="auto")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--slow-mo", type=int, default=0)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cases = [json.loads(item) for item in args.case]

    executable = args.browser_executable
    if executable == "auto":
        if Path(DEFAULT_CHROME_FOR_TESTING).exists():
            executable = DEFAULT_CHROME_FOR_TESTING
        elif Path(DEFAULT_CHROME).exists():
            executable = DEFAULT_CHROME
        else:
            executable = ""

    with sync_playwright() as p:
        launch_kwargs = {"headless": not args.headed, "slow_mo": args.slow_mo}
        if executable:
            launch_kwargs["executable_path"] = executable
        browser = p.chromium.launch(**launch_kwargs)
        try:
            results = [run_case(browser, args.base_url, case["title"], case["messages"]) for case in cases]
        finally:
            browser.close()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
