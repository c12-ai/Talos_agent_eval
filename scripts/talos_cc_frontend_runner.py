#!/usr/bin/env python3
"""
Run one TALOS column chromatography flow through the frontend.

This script is intentionally standalone so another agent can run it without
needing /tmp/eval_briefs.json. It drives the same visible user flow:

1. Open TALOS
2. Create a new conversation
3. Send a column chromatography request in the left chat
4. Approve the plan in the right panel
5. Open the TLC panel, upload the default TLC image, confirm
6. Open slot management, install/select the requested sample cartridge slot
7. Force silica column spec to 12g and submit

Default mode is headless to avoid stealing focus. Use --headed for visible review.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable

import requests
from playwright.sync_api import Page, sync_playwright


DEFAULT_BASE_URL = "http://192.168.12.239:8080"
DEFAULT_TLC_IMAGE = "/Users/wuwenyan/Desktop/demo.jpeg"
DEFAULT_PROMPT = (
    "帮我做一个过柱任务：化合物 SMILES 是 CC(=O)Oc1ccccc1C(=O)O，"
    "阿司匹林，上样 200 mg，TLC Rf=0.35，展开剂 PE:EA=3:1。"
    "请生成方案，我会在右侧面板确认。"
)
DEFAULT_CHROME_FOR_TESTING = (
    "/Users/wuwenyan/Library/Caches/ms-playwright/chromium-1217/"
    "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
)
DEFAULT_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def log(message: str) -> None:
    print(f"[talos-cc] {message}", flush=True)


def sleep(seconds: float) -> None:
    time.sleep(seconds)


def api_base(base_url: str) -> str:
    return base_url.rstrip("/") + "/api"


def update_session_title(base_url: str, session_id: str, title: str) -> None:
    if not session_id or session_id == "unknown":
        return
    url = f"{api_base(base_url)}/sessions/{session_id}"
    try:
        response = requests.put(
            url,
            json={"title": title},
            timeout=10,
            proxies={"http": None, "https": None},
        )
        response.raise_for_status()
        log(f"updated session title: {title}")
    except Exception as exc:
        log(f"warning: failed to update session title via API: {exc}")


def get_workflow_state(base_url: str, session_id: str) -> dict:
    if not session_id or session_id == "unknown":
        return {}
    try:
        response = requests.get(
            f"{api_base(base_url)}/sessions/{session_id}/workflow-state",
            timeout=10,
            proxies={"http": None, "https": None},
        )
        if response.status_code != 200:
            return {}
        return response.json()
    except Exception:
        return {}


def get_sample_cartridge_slot(base_url: str, slot_id: str) -> dict:
    try:
        response = requests.get(
            f"{base_url.rstrip('/')}/lab-api/equipment/locations/slots",
            params={"type": "sample_cartridge_slot"},
            timeout=10,
            proxies={"http": None, "https": None},
        )
        response.raise_for_status()
        for slot in response.json():
            if slot.get("id") == slot_id:
                return slot
    except Exception as exc:
        log(f"warning: failed to read lab slot state: {exc}")
    return {}


def ensure_sample_cartridge_slot_via_api(base_url: str, slot_id: str) -> None:
    """Idempotent lab-slot fallback used only when UI install did not stick."""
    log(f"API fallback: installing sample cartridge slot {slot_id}")
    response = requests.put(
        f"{base_url.rstrip('/')}/lab-api/equipment/locations/slots",
        json=[{"location_id": slot_id, "occupied": True, "consumable_spec": "sample_40g"}],
        timeout=10,
        proxies={"http": None, "https": None},
    )
    response.raise_for_status()


def wait_until(predicate, timeout: float, interval: float = 0.5, description: str = "condition"):
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except Exception as exc:
            last_error = exc
        sleep(interval)
    if last_error:
        raise TimeoutError(f"timed out waiting for {description}: {last_error}")
    raise TimeoutError(f"timed out waiting for {description}")


def body_text(page: Page) -> str:
    try:
        return page.locator("body").inner_text(timeout=2000)
    except Exception:
        return ""


def click_first_visible_text(page: Page, texts: Iterable[str], timeout: float = 20) -> str:
    text_list = list(texts)

    def find_and_click():
        for text in text_list:
            candidates = [
                page.get_by_role("button", name=text).first,
                page.get_by_text(text, exact=True).first,
                page.locator(f"button:has-text('{text}')").first,
            ]
            for locator in candidates:
                try:
                    if locator.count() and locator.is_visible(timeout=500):
                        locator.click()
                        return text
                except Exception:
                    continue
        return None

    try:
        clicked = wait_until(find_and_click, timeout=timeout, description=f"visible text button {text_list}")
    except TimeoutError:
        # Fallback for panel buttons that are visible in DOM but fail Playwright
        # actionability after select changes. This still respects disabled=true.
        clicked = page.evaluate(
            """texts => {
                const visible = el => {
                    const r = el.getBoundingClientRect();
                    const s = window.getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                };
                for (const text of texts) {
                    const btn = Array.from(document.querySelectorAll('button')).find(el =>
                        visible(el) && !el.disabled && (el.innerText || '').trim() === text
                    );
                    if (btn) {
                        btn.click();
                        return text;
                    }
                }
                return null;
            }""",
            text_list,
        )
        if not clicked:
            raise
    log(f"clicked: {clicked}")
    sleep(1)
    return clicked


def wait_for_text(page: Page, text: str, timeout: float = 60) -> None:
    wait_until(lambda: text in body_text(page), timeout=timeout, description=f"text {text!r}")


def create_new_conversation(page: Page) -> str:
    log("creating new conversation")
    click_first_visible_text(page, ["新对话", "New Chat"], timeout=30)
    sleep(2)
    session_id = page.evaluate(
        """() => {
            const keys = [
              'copilotkit-thread-id',
              'talos-active-session',
              'activeSessionId',
              'currentSessionId'
            ];
            for (const key of keys) {
              const value = localStorage.getItem(key);
              if (value) return value;
            }
            return '';
        }"""
    )
    if not session_id:
        session_id = "unknown"
    log(f"session_id={session_id}")
    return session_id


def send_chat_message(page: Page, text: str) -> None:
    log(f"sending chat message: {text}")
    textarea = page.locator("textarea[placeholder*='TALOS'], textarea").first
    textarea.wait_for(state="visible", timeout=30000)
    wait_until(
        lambda: textarea.get_attribute("disabled") is None,
        timeout=60,
        description="chat textarea enabled",
    )
    textarea.click()
    textarea.fill(text)
    textarea.press("Enter")
    sleep(2)


def approve_plan(page: Page) -> None:
    log("waiting for plan approval button")
    wait_for_text(page, "批准方案", timeout=90)
    click_first_visible_text(page, ["批准方案"], timeout=10)


def upload_tlc_and_confirm_spec(page: Page, tlc_image: str, rf_value: str) -> None:
    """Handle the first params panel: TLC 信息 + 溶剂信息 + 样品信息 → 确认修改."""
    log("waiting for first CC params panel (点击打开识别面板)")
    # Must wait specifically for the TLC button to appear, not just the panel
    wait_until(
        lambda: "点击打开识别面板" in body_text(page),
        timeout=120,
        description="TLC open button (点击打开识别面板)",
    )
    sleep(2)

    # Open TLC recognition modal
    click_first_visible_text(page, ["点击打开识别面板"], timeout=20)
    sleep(2)
    log("opened TLC recognition modal")

    # Upload TLC image inside modal
    image_path = Path(tlc_image).expanduser()
    if not image_path.exists():
        raise FileNotFoundError(f"TLC image not found: {image_path}")

    log(f"uploading TLC image: {image_path}")
    file_input = page.locator("input[type='file']").first
    file_input.wait_for(state="attached", timeout=15000)
    file_input.set_input_files(str(image_path))
    sleep(2)
    log("TLC image uploaded")
    fill_rf_value_if_possible(page, rf_value)

    # Close TLC modal - find the confirm button inside the modal dialog
    log("closing TLC modal")
    # The TLC modal is inside a [role="dialog"] - target confirm button within it
    modal_confirm = page.locator('[role="dialog"] button:has-text("确认")')
    modal_confirm.wait_for(state="visible", timeout=10000)
    modal_confirm.click()
    sleep(2)
    log("TLC modal confirmed")

    # Now confirm the active CC spec card only. A page-level text click can hit
    # stale/readonly cards and leave workflow-state in collecting_spec.
    log("clicking scoped 确认修改 on first CC params panel")
    click_cc_spec_confirm(page)


def click_cc_spec_confirm(page: Page) -> None:
    candidates = [
        ".timeline-step:has-text('过柱参数预填') .step-card-active button:has-text('确认修改')",
        ".timeline-step:has-text('过柱参数预填') .step-card-active button:has-text('确认')",
        ".timeline-step:has-text('TLC') .step-card-active button:has-text('确认修改')",
        ".timeline-step:has-text('TLC') .step-card-active button:has-text('确认')",
        ".timeline-step:has-text('推荐依据') .step-card-active button:has-text('确认修改')",
        ".timeline-step:has-text('推荐依据') .step-card-active button:has-text('确认')",
    ]
    for selector in candidates:
        locator = page.locator(selector)
        try:
            if locator.count() == 1 and locator.is_visible(timeout=500) and locator.is_enabled(timeout=500):
                locator.click()
                log(f"clicked CC spec scoped confirm: {selector}")
                sleep(1)
                return
        except Exception as exc:
            log(f"debug: scoped CC spec confirm failed: {selector}: {exc}")
    raise TimeoutError("CC spec scoped confirm button not found")


def fill_rf_value_if_possible(page: Page, rf_value: str) -> None:
    if rf_value is None:
        return
    # The TLC modal has changed a few times. Fill only a likely Rf input; if no
    # clear input is found, rely on the prompt-provided Rf and panel defaults.
    candidates = page.locator("input")
    try:
        count = candidates.count()
    except Exception:
        return
    for index in range(count):
        field = candidates.nth(index)
        try:
            if not field.is_visible(timeout=300):
                continue
            value = field.input_value(timeout=300)
            placeholder = field.get_attribute("placeholder") or ""
            aria = field.get_attribute("aria-label") or ""
            text = " ".join([value, placeholder, aria]).lower()
            if "rf" in text or value in {"", "0", "0.0", "0.35"}:
                field.fill(str(rf_value))
                log(f"filled Rf value in input #{index}: {rf_value}")
                return
        except Exception:
            continue
    log("warning: did not find a clear Rf input; leaving panel defaults")


def set_column_type_12g(page: Page) -> None:
    log("ensuring silica column spec is 12g")

    # Native selects: inspect all of them, not just the first. The sample
    # cartridge dropdown is also a select, so choosing the first visible select
    # is not reliable.
    selects = page.locator("select")
    try:
        for index in range(selects.count()):
            locator = selects.nth(index)
            if not locator.is_visible(timeout=500):
                continue
            values = locator.locator("option").evaluate_all(
                """opts => opts.map(o => ({value: o.value, text: o.textContent || ''}))"""
            )
            option = next(
                (
                    item
                    for item in values
                    if "12g" in item["text"] or "12 g" in item["text"] or "silica_12g" in item["value"]
                ),
                None,
            )
            if option:
                locator.select_option(option["value"])
                sleep(1)
                selected = locator.evaluate(
                    """el => {
                        const opt = el.selectedOptions && el.selectedOptions[0];
                        return opt ? {value: opt.value, text: opt.textContent || ''} : null;
                    }"""
                )
                log(f"selected column type candidate via select #{index}: {selected}")
                return
    except Exception as exc:
        log(f"warning: native select 12g selection failed: {exc}")

    # Custom select/popover. Click the currently selected non-12g value or the
    # nearest clickable ancestor of the column-spec text, then choose 12g.
    selectors = ["24g", "24 g", "40g", "40 g", "硅胶柱规格"]
    for text in selectors:
        try:
            locator = page.get_by_text(text, exact=False).last
            if not locator.count() or not locator.is_visible(timeout=500):
                continue
            locator.evaluate(
                """el => {
                    const clickable = el.closest('button,[role="button"],[role="combobox"],div');
                    (clickable || el).click();
                }"""
            )
            sleep(0.5)
            option = page.get_by_text("12g", exact=False).last
            if option.is_visible(timeout=2000):
                option.click()
                sleep(1)
                log("selected column type 12g from custom selector")
                return
        except Exception as exc:
            log(f"debug: custom 12g selector attempt via {text!r} failed: {exc}")
            continue
    log("warning: could not explicitly set 12g; verify manually before relying on result")


def install_slot_via_dialog(page: Page, base_url: str, slot_id: str, slot_label: str) -> None:
    log(f"opening slot manager and ensuring slot is installed: {slot_label}")
    # Use button locator specifically to avoid matching chat text
    click_first_visible_text(page, ["管理插槽"], timeout=60)
    wait_for_text(page, "管理样品柱插槽", timeout=20)
    sleep(1)

    ui_clicked = False
    try:
        # Find the actual slot button inside the dialog. Clicking the nested text
        # node can look successful but fail to toggle the slot state.
        dialog = page.locator('[role="dialog"]').last
        slot = dialog.locator("button").filter(has_text=slot_label).last
        slot.wait_for(state="visible", timeout=8000)
        class_name = slot.evaluate("el => el.className || ''")
        if "border-emerald" in class_name or "bg-emerald-50" in class_name:
            log("target slot already installed; leaving it unchanged")
        else:
            log("clicking target slot to install cartridge")
            slot.click()
            sleep(1)
        ui_clicked = True
    except Exception as exc:
        log(f"warning: could not click slot button in UI: {exc}")

    if ui_clicked:
        click_first_visible_text(page, ["保存"], timeout=20)
    else:
        # Close the dialog before API fallback/reload. If 保存 is visible, it is
        # safe to click even when no slot was toggled.
        try:
            click_first_visible_text(page, ["保存"], timeout=5)
        except Exception:
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
    sleep(2)

    slot_state = get_sample_cartridge_slot(base_url, slot_id)
    if not slot_state.get("occupied"):
        log("warning: UI slot install did not persist; using lab API fallback")
        ensure_sample_cartridge_slot_via_api(base_url, slot_id)
        page.reload(wait_until="load", timeout=60000)
        sleep(3)


def select_sample_cartridge(page: Page, slot_id: str) -> None:
    log(f"selecting sample cartridge: {slot_id}")
    sleep(1)

    # Prefer native select controls.
    selects = page.locator("select")
    try:
        for i in range(selects.count()):
            select = selects.nth(i)
            options = select.locator("option").evaluate_all(
                """opts => opts.map(o => ({value: o.value, text: o.textContent || ''}))"""
            )
            for option in options:
                if slot_id in option["text"] or slot_id in option["value"]:
                    select.select_option(option["value"])
                    log(f"selected cartridge via native select: {option['text']}")
                    sleep(1)
                    return
    except Exception:
        pass

    # Fallback for custom combobox.
    comboboxes = page.locator("[role='combobox']")
    try:
        for i in range(comboboxes.count()):
            combo = comboboxes.nth(i)
            if not combo.is_visible(timeout=300):
                continue
            combo.click()
            sleep(0.5)
            option = page.get_by_text(slot_id, exact=False).first
            if option.is_visible(timeout=1000):
                option.click()
                log("selected cartridge via combobox")
                sleep(1)
                return
    except Exception:
        pass

    # Final fallback: click visible text if already present.
    try:
        page.get_by_text(slot_id, exact=False).first.click(timeout=2000)
        log("clicked existing cartridge text")
        sleep(1)
        return
    except Exception:
        raise RuntimeError(f"could not select sample cartridge for {slot_id}")


def submit_cc_params(page: Page, base_url: str, slot_label: str, slot_id: str) -> None:
    """Handle the second params panel: 管理插槽 + 硅胶柱规格 → 确认修改."""
    log("waiting for second CC params panel (管理插槽 + 硅胶柱规格)")
    wait_until(
        lambda: any(keyword in body_text(page) for keyword in ["管理插槽", "硅胶柱规格"]),
        timeout=180,
        description="CC params panel (step: 管理插槽/硅胶柱规格)",
    )
    sleep(2)
    set_column_type_12g(page)
    install_slot_via_dialog(page, base_url, slot_id, slot_label)
    wait_until(
        lambda: any(keyword in body_text(page) for keyword in ["管理插槽", "硅胶柱规格", slot_id]),
        timeout=90,
        description="CC params panel after slot install",
    )
    select_sample_cartridge(page, slot_id)
    set_column_type_12g(page)
    # Some panel builds reset dependent selects when the column type changes.
    # Re-select the sample cartridge after forcing 12g so the submit button is
    # enabled with both required user inputs present.
    select_sample_cartridge(page, slot_id)
    sleep(1)
    log("clicking 确认修改 on second params panel")
    click_first_visible_text(page, ["确认修改", "确认"], timeout=30)


def wait_for_submission_state(base_url: str, session_id: str, timeout: float = 60) -> dict:
    log("checking workflow state after submit")
    deadline = time.time() + timeout
    last_state = {}
    while time.time() < deadline:
        state = get_workflow_state(base_url, session_id)
        if state:
            last_state = state
            tasks = state.get("tasks") or []
            if tasks:
                task = tasks[0]
                run = task.get("latest_run") or {}
                if run.get("lab_server_id") or run.get("status"):
                    log(f"latest_run={json.dumps(run, ensure_ascii=False)}")
                    return state
        sleep(3)
    return last_state


def ask_progress_questions_if_running(page: Page, base_url: str, session_id: str) -> list[str]:
    """Mandatory post-submit progress questions while the lab run is active."""
    questions = [
        "机器人现在在干嘛？",
        "这个过柱任务跑到第几步了？",
    ]
    terminal_statuses = {"completed", "failed", "cancelled", "discarded", "timeout"}
    asked = []
    state = get_workflow_state(base_url, session_id)
    tasks = state.get("tasks") or []
    run = (tasks[0].get("latest_run") or {}) if tasks else {}
    if not run.get("lab_server_id") or run.get("status") in terminal_statuses:
        log("skip progress questions: no active lab run")
        return asked

    for question in questions:
        current = get_workflow_state(base_url, session_id)
        current_tasks = current.get("tasks") or []
        current_run = (current_tasks[0].get("latest_run") or {}) if current_tasks else {}
        if current_run.get("status") in terminal_statuses:
            break
        send_chat_message(page, question)
        asked.append(question)
    log(f"asked progress questions: {asked}")
    return asked


def run(args: argparse.Namespace) -> dict:
    title = args.title or f"[{datetime.now().strftime('%Y%m%d-%H%M')}-manual-cc]"
    prompt = args.prompt or DEFAULT_PROMPT

    with sync_playwright() as playwright:
        launch_kwargs = {
            "headless": not args.headed,
            "slow_mo": args.slow_mo,
        }
        executable = args.browser_executable
        if executable == "auto":
            if Path(DEFAULT_CHROME_FOR_TESTING).exists():
                executable = DEFAULT_CHROME_FOR_TESTING
            elif Path(DEFAULT_CHROME).exists():
                executable = DEFAULT_CHROME
            else:
                executable = ""
        if executable:
            launch_kwargs["executable_path"] = executable
            log(f"using browser executable: {executable}")

        browser = playwright.chromium.launch(**launch_kwargs)
        context = browser.new_context(viewport={"width": args.width, "height": args.height})
        page = context.new_page()
        try:
            log(f"opening TALOS: {args.base_url}")
            page.goto(args.base_url, wait_until="load", timeout=60000)
            session_id = create_new_conversation(page)
            update_session_title(args.base_url, session_id, title)
            send_chat_message(page, prompt)
            approve_plan(page)
            upload_tlc_and_confirm_spec(page, args.tlc_image, args.rf)
            submit_cc_params(page, args.base_url, args.slot_label, args.slot_id)
            state = wait_for_submission_state(args.base_url, session_id)
            progress_questions = ask_progress_questions_if_running(page, args.base_url, session_id)

            result = {
                "title": title,
                "session_id": session_id,
                "base_url": args.base_url,
                "slot_id": args.slot_id,
                "slot_label": args.slot_label,
                "tlc_image": args.tlc_image,
                "workflow_state": state,
                "progress_questions": progress_questions,
            }
            return result
        finally:
            if args.keep_open and args.headed:
                log("keeping browser open for review; press Ctrl+C to stop the script")
                try:
                    while True:
                        sleep(60)
                except KeyboardInterrupt:
                    pass
            browser.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one TALOS CC frontend flow.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--title", default="")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--tlc-image", default=DEFAULT_TLC_IMAGE)
    parser.add_argument("--rf", default="0.35")
    parser.add_argument("--slot-id", default="bic_09B_l4_002")
    parser.add_argument("--slot-label", default="备料架L4层样品柱002位")
    parser.add_argument("--headed", action="store_true", help="show browser window")
    parser.add_argument("--keep-open", action="store_true", help="keep headed browser open after run")
    parser.add_argument("--slow-mo", type=int, default=0, help="Playwright slow_mo in ms")
    parser.add_argument(
        "--browser-executable",
        default="auto",
        help="browser executable path; default auto uses system Google Chrome when available",
    )
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=1000)
    parser.add_argument("--output", default="", help="optional JSON output path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"saved result: {output_path}")


if __name__ == "__main__":
    main()
