#!/usr/bin/env python3
"""
TALOS eval runner — runs one or more conv briefs through the real frontend.

Key rules (from memory):
- user_turns is a COMPLETE script — every turn must be processed
- Panel action texts (可以/嗯/准确 etc.) are sent as chat AND trigger panel clicks
- Session title format: [HHMM-eval]-conv-XXX
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable

import requests
from playwright.sync_api import Page, sync_playwright


DEFAULT_BASE_URL = "http://192.168.12.239:8080"
DEFAULT_TLC_IMAGE = "/Users/wuwenyan/Desktop/demo.jpeg"

# Texts that indicate the user is approving/confirming via panel
PANEL_ACTION_TEXTS = {"可以", "嗯", "准确", "对", "好的", "批准", "确认", "行", "没问题", "OK", "ok", "对的", "好"}

BATCH_STAMP = datetime.now().strftime("%H%M")


def log(msg: str) -> None:
    print(f"[eval] {msg}", flush=True)


def sleep(sec: float) -> None:
    time.sleep(sec)


def api_base(base_url: str) -> str:
    return base_url.rstrip("/") + "/api"


def update_session_title(base_url: str, session_id: str, title: str) -> None:
    if not session_id or session_id == "unknown":
        return
    try:
        r = requests.put(
            f"{api_base(base_url)}/sessions/{session_id}",
            json={"title": title},
            timeout=10,
            proxies={"http": None, "https": None},
        )
        r.raise_for_status()
    except Exception:
        pass


def get_workflow_state(base_url: str, session_id: str) -> dict:
    if not session_id or session_id == "unknown":
        return {}
    try:
        r = requests.get(
            f"{api_base(base_url)}/sessions/{session_id}/workflow-state",
            timeout=10,
            proxies={"http": None, "https": None},
        )
        if r.status_code != 200:
            return {}
        return r.json()
    except Exception:
        return {}


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


def click_first_visible_text(page: Page, texts: Iterable[str], timeout: float = 20) -> str | None:
    text_list = list(texts)

    def find_and_click():
        for text in text_list:
            candidates = [
                page.get_by_role("button", name=text).first,
                page.get_by_text(text, exact=True).first,
                page.locator(f"button:has-text('{text}')").first,
            ]
            for loc in candidates:
                try:
                    if loc.count() and loc.is_visible(timeout=500):
                        loc.click()
                        return text
                except Exception:
                    continue
        return None

    try:
        clicked = wait_until(find_and_click, timeout=timeout, description=f"visible text button {text_list}")
    except TimeoutError:
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
    log(f"  clicked: {clicked}")
    sleep(1)
    return clicked


def wait_for_textarea_enabled(page: Page, timeout: float = 90) -> bool:
    def check():
        ta = page.locator("textarea").first
        if ta.count() == 0:
            return True
        return ta.get_attribute("disabled") is None

    try:
        wait_until(check, timeout=timeout, description="textarea enabled")
        return True
    except TimeoutError:
        return False


def send_chat_message(page: Page, text: str) -> None:
    log(f"  [Chat] {text[:100]}")
    wait_for_textarea_enabled(page)
    textarea = page.locator("textarea[placeholder*='TALOS'], textarea").first
    textarea.wait_for(state="visible", timeout=10000)
    textarea.click()
    sleep(0.2)
    textarea.fill(text)
    sleep(0.3)
    textarea.press("Enter")
    sleep(2)


def create_new_conversation(page: Page) -> str:
    log("creating new conversation")
    click_first_visible_text(page, ["新对话", "New Chat"], timeout=30)
    sleep(2)
    session_id = page.evaluate(
        """() => {
            const keys = ['copilotkit-thread-id', 'talos-active-session', 'activeSessionId', 'currentSessionId'];
            for (const key of keys) {
              const value = localStorage.getItem(key);
              if (value) return value;
            }
            return '';
        }"""
    )
    log(f"  session_id={session_id}")
    return session_id or "unknown"


# ---------------------------------------------------------------------------
# Panel state detection
# ---------------------------------------------------------------------------

def has_approve_button(page: Page) -> bool:
    return "批准方案" in body_text(page)


def has_cc_spec_panel(page: Page) -> bool:
    body = body_text(page)
    return any(kw in body for kw in ["过柱参数", "推荐依据", "硅胶柱", "TLC", "识别面板"])


def has_re_spec_panel(page: Page) -> bool:
    body = body_text(page)
    return any(kw in body for kw in ["旋蒸参数", "溶剂体系", "水浴温度"])


def has_cc_params_panel(page: Page) -> bool:
    body = body_text(page)
    return any(kw in body for kw in ["管理插槽", "硅胶柱规格"])


def has_re_params_panel(page: Page) -> bool:
    body = body_text(page)
    return any(kw in body for kw in ["茄形瓶", "添加茄形瓶"])


def is_textarea_disabled(page: Page) -> bool:
    try:
        ta = page.locator("textarea").first
        return ta.get_attribute("disabled") is not None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Panel actions (using proven patterns)
# ---------------------------------------------------------------------------

def approve_plan_in_panel(page: Page) -> None:
    log("  [Panel] approving plan...")
    wait_until(lambda: has_approve_button(page), timeout=60, description="approve button visible")
    click_first_visible_text(page, ["批准方案"], timeout=10)


def confirm_cc_spec_in_panel(page: Page, tlc_image: str = DEFAULT_TLC_IMAGE, rf_value: str = "0.35") -> None:
    log("  [Panel] confirming CC spec...")
    wait_until(lambda: has_cc_spec_panel(page), timeout=90, description="CC spec panel")

    # Open TLC panel if needed
    if "点击打开识别面板" in body_text(page):
        click_first_visible_text(page, ["点击打开识别面板"], timeout=10)
        sleep(1)

    # Upload TLC image
    image_path = Path(tlc_image).expanduser()
    if image_path.exists():
        file_input = page.locator("input[type='file']").first
        try:
            file_input.set_input_files(str(image_path))
            sleep(2)
            log("  uploaded TLC image")
        except Exception as e:
            log(f"  TLC upload error: {e}")

    # Set Rf
    if rf_value:
        inputs = page.locator("input")
        try:
            for i in range(inputs.count()):
                field = inputs.nth(i)
                try:
                    if not field.is_visible(timeout=300):
                        continue
                    val = field.input_value(timeout=300) or ""
                    ph = field.get_attribute("placeholder") or ""
                    ar = field.get_attribute("aria-label") or ""
                    combined = " ".join([val, ph, ar]).lower()
                    if "rf" in combined or val in {"", "0", "0.0", "0.35"}:
                        field.fill(str(rf_value))
                        log(f"  filled Rf: {rf_value}")
                        break
                except Exception:
                    continue
        except Exception:
            pass

    # Double confirm
    click_first_visible_text(page, ["确认", "确认修改"], timeout=30)
    sleep(0.5)
    if any(t in body_text(page) for t in ["确认", "确认修改"]):
        click_first_visible_text(page, ["确认", "确认修改"], timeout=30)
    log("  CC spec confirmed")


def submit_cc_in_panel(page: Page, slot_label: str = "备料架L4层样品柱002位", slot_id: str = "bic_09B_l4_002") -> None:
    log("  [Panel] submitting CC...")
    wait_until(lambda: has_cc_params_panel(page), timeout=120, description="CC params panel")

    # Set 12g
    _set_column_type_12g(page)

    # Open slot management
    click_first_visible_text(page, ["管理插槽"], timeout=30)
    wait_until(lambda: "管理样品柱插槽" in body_text(page), timeout=10, description="slot dialog")
    _install_slot_idempotent(page, slot_label)
    click_first_visible_text(page, ["保存"], timeout=10)

    # Select cartridge
    _select_cartridge_fallback(page, slot_id)

    # Set 12g again (defensive)
    _set_column_type_12g(page)

    # Confirm
    click_first_visible_text(page, ["确认修改", "确认"], timeout=30)
    log("  CC submitted")


def confirm_re_spec_in_panel(page: Page) -> None:
    log("  [Panel] confirming RE spec...")
    wait_until(lambda: has_re_spec_panel(page), timeout=90, description="RE spec panel")
    sleep(1)
    click_first_visible_text(page, ["确认", "确认修改"], timeout=30)
    sleep(0.5)
    if any(t in body_text(page) for t in ["确认", "确认修改"]):
        click_first_visible_text(page, ["确认", "确认修改"], timeout=30)
    log("  RE spec confirmed")


def submit_re_in_panel(page: Page) -> None:
    log("  [Panel] submitting RE...")
    wait_until(lambda: has_re_params_panel(page), timeout=120, description="RE params panel")

    # Add flask
    try:
        add_btn = page.get_by_text("+ 添加茄形瓶", exact=False).first
        if add_btn.count() and add_btn.is_visible(timeout=2000):
            add_btn.click()
            sleep(1.5)
            log("  clicked '+ 添加茄形瓶'")
    except Exception:
        pass

    # Select '瓶 1' chip and verify
    chip = page.get_by_text("瓶 1", exact=True).first
    chip.click()
    sleep(0.5)
    cls_after = chip.evaluate("el => el.className") or ""
    if "ring" not in cls_after.lower() and "shadow" not in cls_after.lower():
        chip.click()
        sleep(0.5)
    log("  '瓶 1' chip selected")

    # Paint tubes 1-5
    for i in range(1, 6):
        try:
            tube = page.get_by_text(str(i), exact=True).first
            if tube.is_visible():
                before = tube.evaluate("el => el.className") or ""
                tube.click()
                sleep(0.3)
                after = tube.evaluate("el => el.className") or ""
                if before == after:
                    tube.click()
                    sleep(0.3)
        except Exception:
            continue
    log("  tubes painted")

    sleep(0.5)
    click_first_visible_text(page, ["确认修改", "确认"], timeout=30)
    log("  RE submitted")


# ---------------------------------------------------------------------------
# CC sub-helpers
# ---------------------------------------------------------------------------

def _set_column_type_12g(page: Page) -> None:
    text = body_text(page)
    if "12g" in text and "硅胶柱规格" in text:
        try:
            page.get_by_text("12g", exact=True).first.click(timeout=1000)
        except Exception:
            pass
        return

    # Native select
    try:
        sel = page.locator("select").first
        if sel.count() and sel.is_visible(timeout=500):
            opts = sel.locator("option").evaluate_all(
                """opts => opts.map(o => ({value: o.value, text: o.textContent || ''}))"""
            )
            for opt in opts:
                if "12g" in opt["text"] or "silica_12g" in opt["value"]:
                    sel.select_option(opt["value"])
                    sleep(1)
                    return
    except Exception:
        pass

    # Popover
    for sel_text in ["button:has-text('24g')", "button:has-text('40g')", "button:has-text('硅胶柱规格')"]:
        try:
            el = page.locator(sel_text).first
            if el.count() and el.is_visible(timeout=500):
                el.click()
                sleep(0.5)
                page.get_by_text("12g", exact=True).first.click(timeout=2000)
                sleep(1)
                return
        except Exception:
            continue


def _install_slot_idempotent(page: Page, slot_label: str) -> None:
    slot = page.get_by_text(slot_label, exact=False).first
    slot.wait_for(state="visible", timeout=10000)
    cls = slot.evaluate("el => el.className || ''")
    if "border-emerald" in cls or "bg-emerald-50" in cls:
        log(f"  slot '{slot_label}' already installed")
        return
    slot.click()
    sleep(1)
    log(f"  installed slot '{slot_label}'")


def _select_cartridge_fallback(page: Page, slot_id: str) -> None:
    # Layer 1: native select
    selects = page.locator("select")
    try:
        for i in range(selects.count()):
            sel = selects.nth(i)
            if not sel.is_visible(timeout=300):
                continue
            opts = sel.locator("option").evaluate_all(
                """opts => opts.map(o => ({value: o.value, text: o.textContent || ''}))"""
            )
            for opt in opts:
                if slot_id in opt["text"] or slot_id in opt["value"]:
                    sel.select_option(opt["value"])
                    sleep(1)
                    return
    except Exception:
        pass

    # Layer 2: combobox
    combos = page.locator("[role='combobox']")
    try:
        for i in range(combos.count()):
            combo = combos.nth(i)
            if not combo.is_visible(timeout=300):
                continue
            combo.click()
            sleep(0.5)
            opt = page.get_by_text(slot_id, exact=False).first
            if opt.is_visible(timeout=1000):
                opt.click()
                sleep(1)
                return
    except Exception:
        pass

    # Layer 3: direct text click
    try:
        page.get_by_text(slot_id, exact=False).first.click(timeout=2000)
        sleep(1)
    except Exception:
        log(f"  WARNING: could not select cartridge {slot_id}")


# ---------------------------------------------------------------------------
# Run one conv
# ---------------------------------------------------------------------------

def run_conv(brief: dict, browser, args: argparse.Namespace) -> dict:
    conv_id = brief["conv_id"]
    category = brief["category"]
    is_cc = "过柱" in category
    is_re = "旋蒸" in category

    log(f"\n{'='*60}")
    log(f"Running {conv_id} | {category}")
    log(f"Scene: {brief['scene']}")

    page = browser.new_page(viewport={"width": args.width, "height": args.height})
    page.goto(args.base_url, wait_until="load", timeout=60000)
    sleep(3)

    session_id = create_new_conversation(page)
    title = f"[{BATCH_STAMP}-eval]-{conv_id}"
    update_session_title(args.base_url, session_id, title)

    user_turns = brief["user_turns"]
    plan_approved = False
    spec_confirmed = False
    task_submitted = False
    results = []

    for i, turn in enumerate(user_turns):
        if page.is_closed():
            log("  ERROR: page closed!")
            break

        user_text = turn["user_text"].strip()
        expected_tool = turn.get("expected_tool")
        is_panel_text = user_text in PANEL_ACTION_TEXTS

        log(f"\n  --- Turn {i+1}/{len(user_turns)} [idx={turn['user_idx']}] ---")
        log(f"  text: '{user_text[:80]}' | panel_action={is_panel_text} | expected_tool={expected_tool}")

        # Send as chat message (ALL turns, including panel action texts)
        send_chat_message(page, user_text)

        # Wait for agent to finish processing
        wait_until(lambda: not is_textarea_disabled(page), timeout=120, description="agent done processing")
        sleep(1)

        turn_result = {
            "user_idx": turn["user_idx"],
            "user_text": user_text,
            "expected_tool": expected_tool,
            "was_chat_message": True,
        }

        # --- Determine panel actions based on state ---

        # 1. Plan approval
        if not plan_approved and has_approve_button(page):
            approve_plan_in_panel(page)
            plan_approved = True
            turn_result["panel_action"] = "approve_plan"

        # 2. If plan approved but spec not confirmed
        elif plan_approved and not spec_confirmed:
            if is_cc and has_cc_spec_panel(page):
                confirm_cc_spec_in_panel(page, args.tlc_image, args.rf)
                spec_confirmed = True
                turn_result["panel_action"] = "confirm_cc_spec"
            elif is_re and has_re_spec_panel(page):
                confirm_re_spec_in_panel(page)
                spec_confirmed = True
                turn_result["panel_action"] = "confirm_re_spec"

        # 3. If spec confirmed but task not submitted, and user text implies dispatch
        elif spec_confirmed and not task_submitted:
            dispatch_keywords = ["下发", "开始", "就按", "提交"]
            if is_panel_text or any(kw in user_text for kw in dispatch_keywords):
                if is_cc and has_cc_params_panel(page):
                    submit_cc_in_panel(page, args.slot_label, args.slot_id)
                    task_submitted = True
                    turn_result["panel_action"] = "submit_cc"
                elif is_re and has_re_params_panel(page):
                    submit_re_in_panel(page)
                    task_submitted = True
                    turn_result["panel_action"] = "submit_re"

        results.append(turn_result)

        # After submit, poll for lab completion
        if task_submitted:
            _wait_for_lab_completion(page, args.base_url, session_id)

    page.close()

    return {
        "conv_id": conv_id,
        "session_id": session_id,
        "category": category,
        "turns": results,
        "plan_approved": plan_approved,
        "spec_confirmed": spec_confirmed,
        "task_submitted": task_submitted,
    }


def _wait_for_lab_completion(page: Page, base_url: str, session_id: str, timeout: float = 600) -> None:
    log("  [Lab] waiting for completion (polling every 15s)...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        sleep(15)
        state = get_workflow_state(base_url, session_id)
        if state:
            tasks = state.get("tasks") or []
            if tasks:
                run = tasks[0].get("latest_run") or {}
                status = run.get("status", "")
                if status in ("completed", "failed", "cancelled", "discarded", "timeout"):
                    log(f"  [Lab] Done! status={status}")
                    return
        elapsed = time.time() - (deadline - timeout)
        if elapsed % 60 < 15:
            log(f"  [Lab] still waiting... ({elapsed:.0f}s)")
            try:
                body = body_text(page)
                for kw in ["完成", "已完成", "回收率", "旋蒸完成", "管路清洗"]:
                    if kw in body:
                        log(f"  [Lab] debug only: saw body keyword {kw!r} while waiting for run status")
                        break
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run TALOS eval briefs through frontend.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--tlc-image", default=DEFAULT_TLC_IMAGE)
    parser.add_argument("--rf", default="0.35")
    parser.add_argument("--slot-id", default="bic_09B_l4_002")
    parser.add_argument("--slot-label", default="备料架L4层样品柱002位")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--keep-open", action="store_true")
    parser.add_argument("--slow-mo", type=int, default=0)
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=1000)
    parser.add_argument("--output", default="", help="optional JSON output path")
    parser.add_argument("conv_ids", nargs="*", default=["conv-001", "conv-005"],
                        help="conv IDs to run (default: conv-001 conv-005)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    with open("/tmp/eval_briefs.json") as f:
        all_briefs = {b["conv_id"]: b for b in json.load(f)}

    targets = {cid: all_briefs[cid] for cid in args.conv_ids if cid in all_briefs}
    if not targets:
        log("ERROR: no valid conv IDs found")
        sys.exit(1)

    log(f"Targets: {list(targets.keys())} | Batch: [{BATCH_STAMP}-eval]")
    log(f"Base URL: {args.base_url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed, slow_mo=args.slow_mo)
        all_results = {}

        try:
            for cid, brief in targets.items():
                try:
                    result = run_conv(brief, browser, args)
                    all_results[cid] = result
                except Exception as e:
                    log(f"ERROR in {cid}: {e}")
                    import traceback
                    traceback.print_exc()
                    all_results[cid] = {"conv_id": cid, "error": str(e)}
        finally:
            if args.keep_open and args.headed:
                log("keeping browser open; press Ctrl+C to stop")
                try:
                    while True:
                        sleep(60)
                except KeyboardInterrupt:
                    pass
            browser.close()

    # Summary
    log(f"\n{'='*60}")
    log("SUMMARY")
    for cid, r in all_results.items():
        sid = r.get("session_id", "?")
        err = r.get("error", "")
        plan = r.get("plan_approved", False)
        spec = r.get("spec_confirmed", False)
        sub = r.get("task_submitted", False)
        status = "ERROR" if err else f"plan={plan} spec={spec} submit={sub}"
        log(f"  {cid}: session={sid} {status}")

    out_path = args.output or f"/tmp/talos_eval_{BATCH_STAMP}.json"
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    log(f"Saved to {out_path}")


if __name__ == "__main__":
    main()
