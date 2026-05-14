#!/usr/bin/env python3
"""
Browser-driven Talos eval smoke test: conv-001 (CC) and conv-005 (RE).

CRITICAL RULES (from memory + talos_operation_guide.md / phoenix_annotation_criteria.md):
- Panel actions (approve plan, confirm spec, submit) are NOT chat messages
- "可以"/"嗯" in user_turns are PANEL ACTIONS, must be replaced with UI clicks
- After clicking 批准方案, SKIP the confirmation chat message
- Wait for "批准方案" button to appear before clicking it
"""

import json, sys, time, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime
BATCH_STAMP = datetime.now().strftime("%H%M")

from talos_panel import update_session_title

with open("/tmp/eval_briefs.json") as f:
    BRIEFS = {b["conv_id"]: b for b in json.load(f)}

# These user texts are panel actions, NOT chat messages
PANEL_ACTION_TEXTS = {"可以", "嗯", "好的", "批准", "确认", "行", "没问题", "OK", "ok"}


# ---------------------------------------------------------------------------
# Browser helpers
# ---------------------------------------------------------------------------

def wait_for_textarea_enabled(page, timeout=90):
    """Wait until chat textarea is enabled (agent done processing)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            ta = page.get_by_placeholder("向 TALOS 发送消息...").first
            if ta.get_attribute("disabled") is None:
                return True
        except Exception:
            return True
        time.sleep(1)
    return False


def send_message(page, text: str):
    """Type message in chat textarea and press Enter."""
    if not wait_for_textarea_enabled(page):
        print("  [WARN] Textarea never enabled!")
    ta = page.get_by_placeholder("向 TALOS 发送消息...").first
    ta.wait_for(state="visible", timeout=5000)
    ta.click()
    time.sleep(0.2)
    ta.fill(text)
    time.sleep(0.3)
    ta.press("Enter")
    time.sleep(1.5)
    print(f"  [Chat] {text[:80]}")


def wait_for_body_keyword(page, keyword: str, timeout=30):
    """Wait until keyword appears in page body text."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            body = page.text_content("body") or ""
            if keyword in body:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def click_button_by_text(page, text: str, timeout=10):
    """Click a visible button by exact text match."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            btn = page.get_by_text(text, exact=True).first
            if btn.is_visible():
                btn.click()
                time.sleep(2)
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


# ---------------------------------------------------------------------------
# Panel actions
# ---------------------------------------------------------------------------

def approve_plan_in_panel(page):
    """Wait for plan to be ready and click '批准方案'."""
    print("  [Panel] Waiting for plan generation...")
    # First wait for "处理中" to disappear or "批准方案" to appear
    if wait_for_body_keyword(page, "批准方案", timeout=30):
        print("  [Panel] Clicking '批准方案'...")
        return click_button_by_text(page, "批准方案", timeout=5)
    print("  [Panel] '批准方案' not found!")
    return False


def confirm_spec_in_panel(page, is_cc=True):
    """Click confirm in spec panel."""
    time.sleep(2)
    # Wait for panel to show confirm button
    for text in ["确认", "确认修改"]:
        if click_button_by_text(page, text, timeout=5):
            return True
    return False


def do_cc_submit(page):
    """Perform CC submit via UI."""
    from talos_panel_ui import submit_cc_via_ui
    submit_cc_via_ui(page, slot_label="备料架L4层样品柱002位", slot_id="bic_09B_l4_002")


def do_re_submit(page):
    """Perform RE submit via UI (with bug fixes)."""
    from talos_panel_ui import submit_re_via_ui
    submit_re_via_ui(page)


# ---------------------------------------------------------------------------
# Run one conversation
# ---------------------------------------------------------------------------

def run_conv(conv_id: str, browser):
    brief = BRIEFS[conv_id]
    category = brief["category"]
    is_cc = "过柱" in category
    is_re = "旋蒸" in category

    print(f"\n{'='*60}")
    print(f"Running {conv_id} | {category}")
    print(f"Scene: {brief['scene']}")

    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    page.goto("http://192.168.12.239:8080/", timeout=30000, wait_until="load")
    time.sleep(4)

    # Click "新对话"
    page.get_by_text("新对话", exact=True).first.click()
    time.sleep(2)

    # Extract session_id
    session_id = "unknown"
    try:
        sid = page.evaluate("() => localStorage.getItem('talos-active-session') || ''")
        if sid:
            session_id = sid
            title = f"[{BATCH_STAMP}-eval]-{conv_id}"
            update_session_title(session_id, title)
    except:
        pass
    print(f"  Session: {session_id}")

    user_turns = brief["user_turns"]
    results = {"conv_id": conv_id, "session_id": session_id, "turns": []}
    plan_approved = False
    spec_confirmed = False
    task_submitted = False
    turn_count = 0

    for i, turn in enumerate(user_turns):
        if page.is_closed():
            print("  [ERROR] Page closed!")
            break

        user_idx = turn["user_idx"]
        user_text = turn["user_text"]
        expected_tool = turn.get("expected_tool")

        # Skip panel-action messages (replace with UI clicks)
        if user_text.strip() in PANEL_ACTION_TEXTS:
            print(f"\n  --- Turn {i+1}/{len(user_turns)} [idx={user_idx}] (PANEL ACTION, skipping chat) ---")
            # If plan not yet approved and this is a confirmation, do it now
            if not plan_approved:
                print(f"  User text '{user_text}' → approving plan via panel")
                approve_plan_in_panel(page)
                plan_approved = True
                time.sleep(2)
            results["turns"].append({
                "user_idx": user_idx,
                "user_text": user_text,
                "action": "panel_approval_skipped",
                "expected_tool": expected_tool,
            })
            continue

        print(f"\n  --- Turn {i+1}/{len(user_turns)} [idx={user_idx}] ---")
        print(f"  User: {user_text[:120]}")

        # Send chat message
        send_message(page, user_text)
        turn_count += 1

        # Wait for agent to finish
        wait_for_body_keyword(page, "实验工作流", timeout=60)
        time.sleep(2)

        body = page.text_content("body") or ""
        print(f"  Agent response length: {len(body)} chars")

        results["turns"].append({
            "user_idx": user_idx,
            "user_text": user_text,
            "expected_tool": expected_tool,
            "was_chat_message": True,
        })

        # ---- Panel actions based on state ----

        # 1. After seed message: check if plan is ready and approve
        if not plan_approved and turn_count == 1:
            time.sleep(2)
            body = page.text_content("body") or ""
            if "批准方案" in body:
                print("  [Panel] Plan ready, approving...")
                approve_plan_in_panel(page)
                plan_approved = True
            else:
                print("  [Panel] Plan not yet ready, will check after next turn...")

        # 2. Try approving plan if not yet done
        if not plan_approved:
            body = page.text_content("body") or ""
            if "批准方案" in body:
                print("  [Panel] Approving plan (delayed)...")
                approve_plan_in_panel(page)
                plan_approved = True

        # 3. CC: confirm spec with TLC upload
        if is_cc and plan_approved and not spec_confirmed:
            if any(kw in user_text for kw in ["SMILES", "上样", "TLC", "Rf", "mg", "阿司匹林"]):
                time.sleep(3)
                body = page.text_content("body") or ""
                if any(kw in body for kw in ["过柱参数", "推荐依据", "硅胶柱", "识别面板"]):
                    print("  [Panel] Confirming CC spec (TLC)...")
                    confirm_spec_in_panel(page, is_cc=True)
                    spec_confirmed = True

        # 4. CC: submit task
        if is_cc and spec_confirmed and not task_submitted:
            if any(kw in user_text for kw in ["准确", "就按", "下发"]):
                time.sleep(3)
                body = page.text_content("body") or ""
                if any(kw in body for kw in ["过柱参数", "确认修改", "管理插槽"]):
                    print("  [Panel] Submitting CC via UI...")
                    do_cc_submit(page)
                    task_submitted = True

        # 5. RE: confirm spec
        if is_re and plan_approved and not spec_confirmed:
            if any(kw in user_text for kw in ["ml", "溶剂", "PE", "EA", "DCM"]):
                time.sleep(3)
                body = page.text_content("body") or ""
                if any(kw in body for kw in ["推荐依据", "旋蒸参数", "溶剂体系"]):
                    print("  [Panel] Confirming RE spec...")
                    confirm_spec_in_panel(page, is_cc=False)
                    spec_confirmed = True

        # 6. RE: submit after "下发"
        if is_re and spec_confirmed and not task_submitted:
            if "下发" in user_text:
                time.sleep(3)
                body = page.text_content("body") or ""
                print(f"  [Debug] '茄形瓶' in body: {'茄形瓶' in body}")
                print(f"  [Debug] '确认修改' in body: {'确认修改' in body}")
                if "旋蒸参数" in body or "茄形瓶" in body or "确认修改" in body:
                    print("  [Panel] Submitting RE via UI (BUG FIX: chip+tube verify)...")
                    do_re_submit(page)
                    task_submitted = True
                else:
                    print("  [Panel] RE params panel not visible yet, checking agent state...")
                    # The agent might need more conversation before reaching params phase

        # ---- Wait for lab if dispatched ----
        if task_submitted and i >= len(user_turns) - 3:
            print("  [Lab] Waiting for completion...")
            waited = 0
            while waited < 600:
                time.sleep(15)
                waited += 15
                try:
                    body = page.text_content("body") or ""
                    if any(kw in body for kw in ["完成", "已完成", "回收率", "旋蒸完成", "管路清洗"]):
                        print(f"  [Lab] Done after {waited}s")
                        break
                except:
                    pass
                if waited % 60 == 0:
                    print(f"  [Lab] Still waiting... ({waited}s)")

    page.close()
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    target = sys.argv[1:] if len(sys.argv) > 1 else ["conv-001", "conv-005"]
    print(f"Target: {target} | Batch: [{BATCH_STAMP}-eval]")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        all_results = {}

        try:
            for cid in target:
                try:
                    results = run_conv(cid, browser)
                    all_results[cid] = results
                except Exception as e:
                    print(f"[ERROR] {cid}: {e}")
                    import traceback
                    traceback.print_exc()
                    all_results[cid] = {"error": str(e)}
        finally:
            time.sleep(3)
            browser.close()

    print(f"\n{'='*60}")
    print("SUMMARY")
    for cid, r in all_results.items():
        sid = r.get("session_id", "?")
        turns = len(r.get("turns", []))
        err = r.get("error", "")
        status = "ERROR" if err else "OK"
        print(f"  {cid}: session={sid}, turns={turns}, status={status}")

    out = f"/tmp/talos_smoke_{BATCH_STAMP}.json"
    with open(out, "w") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()
