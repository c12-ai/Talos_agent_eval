#!/usr/bin/env python3
"""Run one TALOS session that attempts CC then RE in the same conversation."""

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
    DEFAULT_TLC_IMAGE,
    approve_plan,
    body_text,
    create_new_conversation,
    get_workflow_state,
    send_chat_message,
    submit_cc_params,
    upload_tlc_and_confirm_spec,
    update_session_title,
    wait_until,
)
from talos_re_frontend_runner import confirm_re_spec, submit_re_params


DEFAULT_CC_PROMPT = (
    "帮我做一个过柱任务：化合物 SMILES 是 CC(=O)Oc1ccccc1C(=O)O，"
    "阿司匹林，上样 200 mg，TLC Rf=0.35，展开剂 PE:EA=3:1。"
    "请生成方案，我会在右侧面板确认。"
)
DEFAULT_RE_PROMPT = (
    "现在接着做旋蒸：合并液 50 mL，溶剂 PE:EA=3:1，"
    "水浴 35 度，收集 1-5 号试管，气压梯度每段时长都设为 1 分钟。"
    "请生成旋蒸方案，我会在右侧面板确认。"
)


def log(message: str) -> None:
    print(f"[talos-cc-re] {message}", flush=True)


def sleep(seconds: float) -> None:
    time.sleep(seconds)


def textarea_enabled(page: Page) -> bool:
    try:
        textarea = page.locator("textarea[placeholder*='TALOS'], textarea").first
        return textarea.count() > 0 and textarea.get_attribute("disabled") is None
    except Exception:
        return False


def wait_for_agent(page: Page, timeout: float = 180) -> None:
    wait_until(lambda: textarea_enabled(page), timeout=timeout, description="agent response complete")
    sleep(2)


def wait_for_chat_ready_or_refresh(page: Page, session_id: str, timeout: float = 180) -> bool:
    """Wait for the chat box to unlock; reload the same session once if needed."""
    try:
        wait_until(lambda: textarea_enabled(page), timeout=timeout, description="chat ready")
        return True
    except Exception:
        log("chat is still locked; reopening current session once")
        page.reload(wait_until="load", timeout=60000)
        page.evaluate(
            """sessionId => {
                localStorage.setItem('talos-active-session', sessionId);
                localStorage.setItem('activeSessionId', sessionId);
                localStorage.setItem('currentSessionId', sessionId);
                localStorage.setItem('copilotkit-thread-id', sessionId);
            }""",
            session_id,
        )
        page.reload(wait_until="load", timeout=60000)
        try:
            wait_until(lambda: textarea_enabled(page), timeout=60, description="chat ready after reload")
            return True
        except Exception:
            return False


def wait_for_re_requested_effect(base_url: str, session_id: str, page: Page, timeout: float = 180) -> dict:
    """Wait until a RE plan/task/panel appears after the explicit RE user request."""
    deadline = time.time() + timeout
    last_state = {}
    while time.time() < deadline:
        state = get_workflow_state(base_url, session_id)
        if state:
            last_state = state
            if any((task.get("task_type") == "re_agent") for task in (state.get("tasks") or [])):
                return {"kind": "re_agent_task", "state": state}
            plan_text = json.dumps(state.get("plan") or {}, ensure_ascii=False)
            if "旋蒸" in plan_text or "re_agent" in plan_text:
                return {"kind": "re_plan", "state": state}
        text = body_text(page)
        if "旋蒸参数" in text or "添加茄形瓶" in text or "批准方案" in text:
            return {"kind": "frontend_re_signal", "state": last_state, "body_tail": text[-3000:]}
        sleep(3)
    return {"kind": "timeout", "state": last_state, "body_tail": body_text(page)[-3000:]}


def task_summary(task: dict) -> dict:
    run = task.get("latest_run") or {}
    return {
        "id": task.get("id"),
        "task_type": task.get("task_type"),
        "phase": task.get("phase"),
        "spec": task.get("spec"),
        "params": task.get("params"),
        "user_params": task.get("user_params"),
        "user_input": task.get("user_input"),
        "lab_server_id": run.get("lab_server_id"),
        "run_status": run.get("status"),
        "steps": {k: v.get("status") for k, v in (run.get("steps") or {}).items()},
    }


def wait_for_task_terminal(base_url: str, session_id: str, task_index: int, timeout: float = 1200) -> dict:
    deadline = time.time() + timeout
    last_state = {}
    while time.time() < deadline:
        state = get_workflow_state(base_url, session_id)
        if state:
            last_state = state
            tasks = state.get("tasks") or []
            if len(tasks) > task_index:
                task = tasks[task_index]
                run = task.get("latest_run") or {}
                log(f"task[{task_index}] {json.dumps(task_summary(task), ensure_ascii=False)}")
                if run.get("status") in {"completed", "failed", "cancelled", "discarded"}:
                    return state
        sleep(10)
    raise TimeoutError(f"task {task_index} did not reach terminal state; last_state={json.dumps(last_state, ensure_ascii=False)}")


def find_task(state: dict, task_type: str) -> tuple[int, dict] | tuple[None, None]:
    for index, task in enumerate(state.get("tasks") or []):
        if task.get("task_type") == task_type:
            return index, task
    return None, None


def run_one(args: argparse.Namespace, title: str, cc_prompt: str, re_prompt: str) -> dict:
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
            log(f"using browser executable: {executable}")
        browser = playwright.chromium.launch(**launch_kwargs)
        context = browser.new_context(viewport={"width": args.width, "height": args.height})
        page = context.new_page()
        result = {
            "title": title,
            "base_url": args.base_url,
            "cc": {},
            "re": {},
            "queries": [],
            "errors": [],
        }
        try:
            page.goto(args.base_url, wait_until="load", timeout=60000)
            session_id = create_new_conversation(page)
            result["session_id"] = session_id
            update_session_title(args.base_url, session_id, title)

            log("starting CC")
            send_chat_message(page, cc_prompt)
            wait_for_agent(page)
            approve_plan(page)
            upload_tlc_and_confirm_spec(page, args.tlc_image, args.rf)
            submit_cc_params(page, args.base_url, args.slot_label, args.slot_id)
            cc_submitted_state = get_workflow_state(args.base_url, session_id)
            result["cc"]["submitted_state"] = cc_submitted_state

            try:
                send_chat_message(page, "机器人现在在干嘛？")
                wait_for_agent(page)
                result["queries"].append({"during": "cc", "user": "机器人现在在干嘛？", "body_tail": body_text(page)[-2500:]})
            except Exception as exc:
                result["errors"].append(f"cc_progress_query_failed: {exc}")

            state = wait_for_task_terminal(args.base_url, session_id, 0, timeout=args.cc_timeout)
            result["cc"]["terminal_state"] = state

            result["states"] = ["cc_submitted", "cc_completed"]
            log("waiting for CC completion confirmation and chat readiness before RE request")
            chat_ready = wait_for_chat_ready_or_refresh(page, session_id, timeout=180)
            result["re"]["chat_ready_before_request"] = chat_ready
            if not chat_ready:
                result["errors"].append("re_not_requested: chat did not unlock after CC completion")
                result["re"]["failed_state"] = get_workflow_state(args.base_url, session_id)
                result["re"]["body_tail"] = body_text(page)[-4000:]
                return result
            result["states"].append("chat_ready_for_re")

            log("starting RE in the same session")
            send_chat_message(page, re_prompt)
            result["states"].append("re_requested")
            result["re"]["request_prompt"] = re_prompt
            result["re"]["request_body_tail"] = body_text(page)[-2500:]

            re_signal = wait_for_re_requested_effect(args.base_url, session_id, page, timeout=240)
            result["re"]["request_effect"] = re_signal
            if re_signal.get("kind") == "timeout":
                result["errors"].append("re_requested_but_no_re_plan_or_task")
                return result

            try:
                wait_for_agent(page, timeout=240)
            except Exception as exc:
                result["errors"].append(f"re_agent_response_wait_failed_after_request: {exc}")
            if "批准方案" in body_text(page):
                approve_plan(page)
            try:
                confirm_re_spec(page, args.base_url, session_id)
                submit_re_params(page, args.temperature, args.duration, args.tube_count)
                re_submit_state = get_workflow_state(args.base_url, session_id)
                result["re"]["submitted_state"] = re_submit_state
                re_index, _ = find_task(re_submit_state, "re_agent")
                if re_index is not None:
                    try:
                        send_chat_message(page, "这个旋蒸任务跑到第几步了？")
                        wait_for_agent(page)
                        result["queries"].append({"during": "re", "user": "这个旋蒸任务跑到第几步了？", "body_tail": body_text(page)[-2500:]})
                    except Exception as exc:
                        result["errors"].append(f"re_progress_query_failed: {exc}")
                    result["re"]["terminal_state"] = wait_for_task_terminal(args.base_url, session_id, re_index, timeout=args.re_timeout)
            except Exception as exc:
                result["errors"].append(f"re_flow_failed: {exc}")
                result["re"]["failed_state"] = get_workflow_state(args.base_url, session_id)
                result["re"]["body_tail"] = body_text(page)[-4000:]

            return result
        except Exception as exc:
            result["errors"].append(f"flow_failed: {exc}")
            if result.get("session_id"):
                try:
                    result["failed_state"] = get_workflow_state(args.base_url, result["session_id"])
                except Exception as state_exc:
                    result["errors"].append(f"failed_state_read_failed: {state_exc}")
            try:
                result["body_tail"] = body_text(page)[-5000:]
            except Exception as body_exc:
                result["errors"].append(f"body_read_failed: {body_exc}")
            return result
        finally:
            if args.keep_open and args.headed:
                log("keeping browser open for review; press Ctrl+C to close")
                try:
                    while True:
                        sleep(60)
                except KeyboardInterrupt:
                    pass
            browser.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run CC then RE in one TALOS conversation.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--title", default="")
    parser.add_argument("--cc-prompt", default=DEFAULT_CC_PROMPT)
    parser.add_argument("--re-prompt", default=DEFAULT_RE_PROMPT)
    parser.add_argument("--tlc-image", default=DEFAULT_TLC_IMAGE)
    parser.add_argument("--rf", default="0.35")
    parser.add_argument("--slot-id", default="bic_09B_l4_002")
    parser.add_argument("--slot-label", default="备料架L4层样品柱002位")
    parser.add_argument("--temperature", default="35")
    parser.add_argument("--duration", default="1")
    parser.add_argument("--tube-count", type=int, default=5)
    parser.add_argument("--cc-timeout", type=int, default=1200)
    parser.add_argument("--re-timeout", type=int, default=900)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--keep-open", action="store_true")
    parser.add_argument("--slow-mo", type=int, default=0)
    parser.add_argument("--browser-executable", default="auto")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=1000)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    title = args.title or f"[{datetime.now().strftime('%Y%m%d-%H%M')}-cc-re]"
    result = run_one(args, title, args.cc_prompt, args.re_prompt)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
