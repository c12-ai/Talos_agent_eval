#!/usr/bin/env python3
"""Open an existing TALOS session and dump visible controls for debugging."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


DEFAULT_BASE_URL = "http://192.168.12.239:8080"
DEFAULT_BROWSER = (
    "/Users/wuwenyan/Library/Caches/ms-playwright/chromium-1217/"
    "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("session_id")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--browser-executable", default=DEFAULT_BROWSER)
    parser.add_argument("--output", default="/tmp/talos_dom_debug.json")
    args = parser.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=args.browser_executable)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(args.base_url, wait_until="load", timeout=60000)
        page.evaluate(
            """sessionId => {
                localStorage.setItem('talos-active-session', sessionId);
                localStorage.setItem('copilotkit-thread-id', sessionId);
                localStorage.setItem('activeSessionId', sessionId);
                localStorage.setItem('currentSessionId', sessionId);
            }""",
            args.session_id,
        )
        page.reload(wait_until="load", timeout=60000)
        page.wait_for_timeout(5000)

        data = page.evaluate(
            """() => {
                const visible = el => {
                    const r = el.getBoundingClientRect();
                    const s = window.getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                };
                return {
                    url: location.href,
                    localStorage: Object.fromEntries(Object.entries(localStorage)),
                    bodyText: document.body.innerText,
                    buttons: Array.from(document.querySelectorAll('button')).map((el, i) => ({
                        i,
                        text: el.innerText,
                        disabled: el.disabled,
                        visible: visible(el),
                        className: el.className,
                    })),
                    selects: Array.from(document.querySelectorAll('select')).map((el, i) => ({
                        i,
                        visible: visible(el),
                        value: el.value,
                        selectedText: el.selectedOptions && el.selectedOptions[0] ? el.selectedOptions[0].textContent : '',
                        options: Array.from(el.options).map(o => ({value: o.value, text: o.textContent})),
                        className: el.className,
                    })),
                    inputs: Array.from(document.querySelectorAll('input')).map((el, i) => ({
                        i,
                        type: el.type,
                        value: el.value,
                        placeholder: el.placeholder,
                        disabled: el.disabled,
                        visible: visible(el),
                        className: el.className,
                    })),
                };
            }"""
        )
        Path(args.output).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"output": args.output, "buttons": len(data["buttons"]), "selects": len(data["selects"])}, ensure_ascii=False))
        browser.close()


if __name__ == "__main__":
    main()
