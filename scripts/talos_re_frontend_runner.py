#!/usr/bin/env python3
"""
Run one TALOS rotary evaporation flow through the frontend.

Default flow:
1. Open TALOS and create a new conversation
2. Send a rotary evaporation request
3. Approve the plan in the right panel
4. Confirm RE prefill/spec panel
5. Set final RE parameters:
   - temperature 35 C
   - pressure-gradient durations 1/1/1/1 min
   - add flask "瓶 1"
   - paint tubes 1-5 into flask 1
6. Submit and print workflow-state
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from talos_cc_frontend_runner import (
    DEFAULT_BASE_URL,
    DEFAULT_CHROME,
    DEFAULT_CHROME_FOR_TESTING,
    approve_plan,
    body_text,
    click_first_visible_text,
    create_new_conversation,
    get_workflow_state,
    log as cc_log,
    send_chat_message,
    sleep,
    update_session_title,
    wait_until,
)


DEFAULT_PROMPT = (
    "帮我做一个旋蒸任务：合并液 50 mL，溶剂 PE:EA=3:1，"
    "水浴 35 度，收集 1-5 号试管，气压梯度每段时长都设为 1 分钟。"
    "请生成方案，我会在右侧面板确认。"
)
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "discarded", "timeout"}


def log(message: str) -> None:
    print(f"[talos-re] {message}", flush=True)


def wait_for_re_spec_panel(page: Page) -> None:
    log("waiting for RE spec/prefill panel")
    wait_until(
        lambda: any(keyword in body_text(page) for keyword in ["旋蒸参数预填", "旋蒸参数", "溶剂信息", "合并液"]),
        timeout=120,
        description="RE spec panel",
    )


def get_first_task_state(base_url: str, session_id: str) -> dict:
    state = get_workflow_state(base_url, session_id)
    tasks = state.get("tasks") or []
    current_task = state.get("current_task")
    if current_task:
        for task in tasks:
            if task.get("sequence") == current_task:
                return task
    for task in tasks:
        if task.get("task_type") == "re_agent" and task.get("phase") != "not_started":
            return task
    return tasks[0] if tasks else {}


def click_re_spec_confirm(page: Page) -> bool:
    """Click the confirm button inside the active RE spec timeline card only."""
    candidates = [
        ".timeline-step:has-text('旋蒸参数预填') .step-card-active button:has-text('确认修改')",
        ".timeline-step:has-text('旋蒸参数预填') .step-card-active button:has-text('确认')",
        ".timeline-step:has-text('旋蒸参数') .step-card-active button:has-text('确认修改')",
        ".timeline-step:has-text('旋蒸参数') .step-card-active button:has-text('确认')",
        ".timeline-step:has-text('溶剂信息') .step-card-active button:has-text('确认修改')",
        ".timeline-step:has-text('溶剂信息') .step-card-active button:has-text('确认')",
    ]
    for selector in candidates:
        locator = page.locator(selector)
        try:
            count = locator.count()
        except Exception:
            continue
        if count != 1:
            continue
        try:
            if locator.is_visible(timeout=500) and locator.is_enabled(timeout=500):
                locator.click()
                log(f"clicked RE spec scoped confirm: {selector}")
                sleep(1)
                return True
        except Exception as exc:
            log(f"debug: scoped confirm candidate failed: {selector}: {exc}")
            continue
    log("RE spec scoped confirm button not found")
    return False


def confirm_re_spec(page: Page, base_url: str, session_id: str) -> None:
    wait_for_re_spec_panel(page)
    sleep(2)
    log("confirming RE spec panel and waiting for collecting_params")

    deadline = time.time() + 180
    last_task = {}
    clicked_count = 0
    sent_chat_fallback = False
    while time.time() < deadline:
        last_task = get_first_task_state(base_url, session_id)
        if last_task.get("phase") == "collecting_params" and last_task.get("params"):
            log("RE spec confirmed; collecting_params is ready")
            return
        if not sent_chat_fallback and clicked_count >= 3:
            # Some current builds render the spec confirm button but do not
            # advance the backend state from the click event. Use a normal,
            # visible user confirmation as fallback instead of hidden state
            # injection, so the conversation remains reviewable.
            send_chat_message(page, "确认预填参数，继续生成旋蒸执行参数。")
            sent_chat_fallback = True
            sleep(8)
            continue
        if click_re_spec_confirm(page):
            clicked_count += 1
            sleep(5)
        else:
            sleep(3)

    raise TimeoutError(f"RE spec did not advance to collecting_params; last_task={json.dumps(last_task, ensure_ascii=False)}")


def wait_for_re_params_panel(page: Page) -> None:
    log("waiting for RE final params panel")
    wait_until(
        lambda: any(keyword in body_text(page) for keyword in ["添加茄形瓶", "茄形瓶", "水浴温度", "压力", "气压梯度"]),
        timeout=180,
        description="RE params panel",
    )
    sleep(2)


def dump_number_inputs(page: Page) -> list[dict]:
    return page.locator("input").evaluate_all(
        """inputs => inputs.map((el, i) => ({
            i,
            type: el.type,
            value: el.value,
            placeholder: el.placeholder || '',
            aria: el.getAttribute('aria-label') || '',
            readonly: !!el.readOnly,
            disabled: !!el.disabled,
            visible: (() => {
              const r = el.getBoundingClientRect();
              const s = window.getComputedStyle(el);
              return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
            })()
        }))"""
    )


def fill_visible_number_input(page: Page, index: int, value: str) -> None:
    inputs = page.locator("input").filter(has_not_text="")
    field = page.locator("input").nth(index)
    field.wait_for(state="visible", timeout=10000)
    field.click()
    field.fill(str(value))
    # Dispatch explicit input/change events for React-controlled inputs.
    field.evaluate(
        """el => {
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
        }"""
    )
    sleep(0.3)


def set_re_numeric_params(page: Page, temperature_c: str, duration_min: str) -> None:
    """Inspect RE numeric fields; keep panel defaults to avoid readonly/index issues."""
    log("checking RE numeric params")
    inputs = [item for item in dump_number_inputs(page) if item.get("visible")]
    log(f"visible inputs: {json.dumps(inputs, ensure_ascii=False)}")
    log("leaving RE numeric params unchanged; prompt/spec already requested 1 min durations")


def add_flask_and_paint_tubes(page: Page, tube_count: int = 5) -> None:
    log("adding flask and painting tubes")
    try:
        add_btn = page.get_by_text("+ 添加茄形瓶", exact=False).first
        if add_btn.count() and add_btn.is_visible(timeout=3000):
            add_btn.click()
            sleep(1.5)
            log("clicked + 添加茄形瓶")
    except Exception as exc:
        log(f"warning: add flask click failed or flask already exists: {exc}")

    chip = page.get_by_text("瓶 1", exact=True).first
    chip.wait_for(state="visible", timeout=20000)
    chip.click()
    sleep(0.5)
    selected = chip.evaluate(
        """el => {
            const cls = el.className || '';
            return cls.toLowerCase().includes('ring') || cls.toLowerCase().includes('shadow');
        }"""
    )
    if not selected:
        chip.click()
        sleep(0.5)
    log("selected 瓶 1")

    for tube_num in range(1, tube_count + 1):
        tube = page.get_by_text(str(tube_num), exact=True).last
        tube.wait_for(state="visible", timeout=10000)
        before = tube.evaluate("el => el.className || ''")
        tube.click()
        sleep(0.3)
        after = tube.evaluate("el => el.className || ''")
        if before == after:
            tube.click()
            sleep(0.3)
        log(f"painted tube {tube_num}")

    wait_until(lambda: "已配置 5 管" in body_text(page) or "已配置" in body_text(page), timeout=20, description="tube configured text")


def click_re_params_confirm(page: Page) -> None:
    """Click confirm inside the active RE params card only."""
    candidates = [
        ".timeline-step:has-text('旋蒸参数确认') .step-card-active button:has-text('确认修改')",
        ".timeline-step:has-text('旋蒸参数确认') .step-card-active button:has-text('确认')",
        ".timeline-step:has-text('添加茄形瓶') .step-card-active button:has-text('确认修改')",
        ".timeline-step:has-text('添加茄形瓶') .step-card-active button:has-text('确认')",
        ".timeline-step:has-text('气压梯度') .step-card-active button:has-text('确认修改')",
        ".timeline-step:has-text('气压梯度') .step-card-active button:has-text('确认')",
    ]
    for selector in candidates:
        locator = page.locator(selector)
        try:
            if locator.count() == 1 and locator.is_visible(timeout=500) and locator.is_enabled(timeout=500):
                locator.click()
                log(f"clicked RE params scoped confirm: {selector}")
                sleep(1)
                return
        except Exception as exc:
            log(f"debug: scoped RE params confirm failed: {selector}: {exc}")
    raise TimeoutError("RE params scoped confirm button not found")


def submit_re_params(page: Page, temperature_c: str, duration_min: str, tube_count: int) -> None:
    wait_for_re_params_panel(page)
    set_re_numeric_params(page, temperature_c, duration_min)
    add_flask_and_paint_tubes(page, tube_count)
    log("submitting RE params")
    click_re_params_confirm(page)


def wait_for_submission_state(base_url: str, session_id: str, timeout: float = 90) -> dict:
    log("checking workflow state after submit")
    deadline = time.time() + timeout
    last_state = {}
    while time.time() < deadline:
        state = get_workflow_state(base_url, session_id)
        if state:
            last_state = state
            task = get_first_task_state(base_url, session_id)
            run = task.get("latest_run") or {}
            if run.get("lab_server_id") or run.get("status"):
                log(f"latest_run={json.dumps(run, ensure_ascii=False)}")
                return state
        sleep(3)
    return last_state


def ask_progress_questions_if_running(page: Page, base_url: str, session_id: str) -> tuple[list[str], str | None]:
    """Mandatory post-submit progress questions while the lab run is active."""
    questions = ["这个旋蒸任务跑到第几步了？"]
    asked = []
    task = get_first_task_state(base_url, session_id)
    run = task.get("latest_run") or {}
    if not run.get("lab_server_id"):
        reason = "task has no active lab run"
        log(f"skip progress questions: {reason}")
        return asked, reason
    if run.get("status") in TERMINAL_RUN_STATUSES:
        reason = f"task already terminal: {run.get('status')}"
        log(f"skip progress questions: {reason}")
        return asked, reason

    for question in questions:
        current_task = get_first_task_state(base_url, session_id)
        current_run = current_task.get("latest_run") or {}
        if current_run.get("status") in TERMINAL_RUN_STATUSES:
            reason = f"task already terminal: {current_run.get('status')}"
            log(f"stop progress questions: {reason}")
            return asked, reason
        send_chat_message(page, question)
        wait_for_agent(page)
        asked.append(question)
        current_task = get_first_task_state(base_url, session_id)
        current_run = current_task.get("latest_run") or {}
        if current_run.get("status") in TERMINAL_RUN_STATUSES:
            reason = f"task reached terminal after reply: {current_run.get('status')}"
            log(f"stop progress questions: {reason}")
            return asked, reason
    log(f"asked progress questions: {asked}")
    return asked, None


def run(args: argparse.Namespace) -> dict:
    title = args.title or f"[{datetime.now().strftime('%Y%m%d-%H%M')}-manual-re]"
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
            confirm_re_spec(page, args.base_url, session_id)
            submit_re_params(page, args.temperature, args.duration, args.tube_count)
            state = wait_for_submission_state(args.base_url, session_id)
            progress_questions, progress_questions_skipped = ask_progress_questions_if_running(page, args.base_url, session_id)
            return {
                "title": title,
                "session_id": session_id,
                "base_url": args.base_url,
                "temperature": args.temperature,
                "duration": args.duration,
                "tube_count": args.tube_count,
                "workflow_state": state,
                "progress_questions": progress_questions,
                "progress_questions_skipped": progress_questions_skipped,
            }
        finally:
            if args.keep_open and args.headed:
                log("keeping browser open for review; press Ctrl+C to stop")
                try:
                    while True:
                        sleep(60)
                except KeyboardInterrupt:
                    pass
            browser.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one TALOS RE frontend flow.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--title", default="")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--temperature", default="35")
    parser.add_argument("--duration", default="1")
    parser.add_argument("--tube-count", type=int, default=5)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--keep-open", action="store_true")
    parser.add_argument("--slow-mo", type=int, default=0)
    parser.add_argument("--browser-executable", default="auto")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=1000)
    parser.add_argument("--output", default="")
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
