#!/usr/bin/env python3
"""
Smoke runner for the 2026-05-18 link-validation batch.

Drives conv briefs through the live TALOS frontend via the Mac Tailscale relay
(100.84.102.34), captures per-turn agent output + tool calls from Phoenix root
spans, and snapshots workflow-state. Phoenix annotation is a SEPARATE pass.

Safety:
- --allow-dispatch is required before any final lab-submit panel click.
  Without it, dispatch convs run conversation + plan approve + spec confirm,
  but STOP before the final submit (records state instead of burning the lab).
- headless by default (this host has no display).
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright

CST = ZoneInfo("Asia/Shanghai")


def cst_stamp(fmt):
    return datetime.now(CST).strftime(fmt)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

TALOS_BASE = "http://100.84.102.34:8080"
PHOENIX_BASE = "http://100.84.102.34:6006"
PROJECT_ID = "UHJvamVjdDoy"
TLC_IMAGE = str(ROOT / "demo.jpeg")
DATASET = ROOT / "agent_eval_dataset.json"
REQ = {"proxies": {"http": None, "https": None}, "timeout": 15}

CAT_CN = {
    "single_column": "单过柱",
    "single_rotovap": "单旋蒸",
    "column_then_rotovap": "过柱+旋蒸",
    "query": "查询",
    "negative": "负例",
}
PANEL_ACTION_TEXTS = {"可以", "嗯", "好的", "批准", "确认", "行", "没问题", "OK", "ok", "对", "准确", "好"}
SESSION_KEYS = ["talos-active-session", "copilotkit-thread-id", "activeSessionId", "currentSessionId"]


def log(m): print(f"[smoke] {m}", flush=True)


# --------------------------------------------------------------------------- briefs
def build_briefs(conv_ids):
    data = json.loads(DATASET.read_text())
    by_id = {c["id"]: c for c in data["conversations"]}
    briefs = []
    for cid in conv_ids:
        c = by_id[cid]
        turns = c["turns"]
        user_turns = []
        for i, t in enumerate(turns):
            if t["role"] != "user":
                continue
            nxt = turns[i + 1] if i + 1 < len(turns) else {}
            user_turns.append({
                "user_idx": i,
                "user_text": t["content"],
                "expected_tool": (nxt.get("query_payload") or {}).get("tool"),
                "expect_plan": "plan_payload" in nxt,
                "expect_dispatch": (nxt.get("dispatch_payload") or {}).get("task_type"),
            })
        briefs.append({
            "conv_id": cid,
            "category": CAT_CN.get(c["category"], c["category"]),
            "category_raw": c["category"],
            "scene": c.get("scenario", ""),
            "user_turns": user_turns,
        })
    return briefs


# --------------------------------------------------------------------------- api
def api_create_session(title):
    r = requests.post(f"{TALOS_BASE}/api/sessions", json={"title": title}, **REQ)
    r.raise_for_status()
    return r.json()["id"]


def api_workflow_state(session_id):
    try:
        r = requests.get(f"{TALOS_BASE}/api/sessions/{session_id}/workflow-state", **REQ)
        return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}


def api_plan_executors(session_id):
    st = api_workflow_state(session_id)
    plan = (st or {}).get("plan") or {}
    execs = set()
    for n in (plan.get("plan_draft") or plan.get("nodes") or []):
        e = n.get("executor") or n.get("task_type") or ""
        if e:
            execs.add(e)
    return execs


def api_set_title(session_id, title):
    try:
        requests.put(f"{TALOS_BASE}/api/sessions/{session_id}", json={"title": title}, **REQ)
    except Exception:
        pass


def phoenix_root_spans(limit=300):
    r = requests.get(f"{PHOENIX_BASE}/v1/projects/{PROJECT_ID}/spans",
                      params={"parent_id": "null", "name": "LangGraph", "limit": limit}, **REQ)
    r.raise_for_status()
    return r.json().get("data", [])


def phoenix_trace_spans(trace_id, limit=300):
    r = requests.get(f"{PHOENIX_BASE}/v1/projects/{PROJECT_ID}/spans",
                      params={"trace_id": trace_id, "limit": limit}, **REQ)
    r.raise_for_status()
    return r.json().get("data", [])


# --------------------------------------------------------------------------- browser
def wait_textarea_enabled(page, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            ta = page.locator("textarea").first
            if ta.count() == 0 or ta.get_attribute("disabled") is None:
                return True
        except Exception:
            return True
        time.sleep(1)
    return False


def wait_chat_send_ready(page, timeout=180):
    """Wait until BOTH the textarea is enabled AND `.chat-send-btn` is not
    disabled. The send button's disabled state is the front-end's signal of
    'agent busy generating' — when it's off, Enter goes to a no-op handler
    and our chat is silently swallowed. Returns True when both are ready."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            state = page.evaluate("""() => {
                const ta = document.querySelector('textarea');
                if (!ta) return {ta: false, btn: false};
                const btn = document.querySelector('.chat-send-btn');
                return {
                    ta: !ta.disabled,
                    btn: btn ? !btn.disabled : true,
                };
            }""")
            if state.get("ta") and state.get("btn"):
                return True
        except Exception:
            return True
        time.sleep(1)
    return False


OVERLAY_SEL = "div.fixed.inset-0.z-50, div[class*='fixed'][class*='inset-0'][class*='z-50']"


def _overlay_visible(page) -> bool:
    try:
        ov = page.locator(OVERLAY_SEL).first
        return bool(ov.count()) and ov.is_visible(timeout=400)
    except Exception:
        return False


def _dismiss_modal(page, tries: int = 6) -> bool:
    """Close a leftover modal/backdrop that would block chat input.
    Click a confirm/close control inside it; fall back to Escape."""
    for _ in range(tries):
        if not _overlay_visible(page):
            return True
        clicked = False
        for t in ["确认修改", "确认", "确定", "完成", "保存", "关闭", "知道了"]:
            try:
                b = page.locator(f"div.fixed.inset-0.z-50 button:has-text('{t}')").first
                if b.count() and b.is_visible(timeout=300):
                    b.click(); clicked = True; time.sleep(1.2); break
            except Exception:
                continue
        if not clicked:
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            time.sleep(1)
    still = _overlay_visible(page)
    if still:
        log("  [modal] WARNING: overlay still present after dismiss attempts")
    return not still


def send_chat(page, text, max_retries=3):
    """Fill the chat textarea and press Enter, verifying the message was
    actually submitted (textarea cleared).

    Why the verification matters: when `.chat-send-btn` is disabled
    (front-end's "agent busy" signal), the Enter key handler turns into a
    no-op — fill happens but submit doesn't, and the message is silently
    lost. Pre-2026-05-20 send_chat only checked `textarea.disabled` and
    printed `[chat>]` regardless of whether the chat actually posted,
    masking these losses. Now we wait for send-btn to be ready, then
    verify the textarea cleared after Enter; on failure, retry up to
    max_retries times before raising."""
    for attempt in range(1, max_retries + 1):
        if not wait_chat_send_ready(page, timeout=180):
            log(f"  [chat] send NOT ready (textarea or send-btn disabled), attempt {attempt}")
        if _overlay_visible(page):
            log("  [modal] overlay before chat send -> dismissing")
            _dismiss_modal(page)
        ta = page.locator("textarea[placeholder*='TALOS'], textarea").first
        ta.wait_for(state="visible", timeout=10000)
        try:
            ta.click(timeout=8000)
        except Exception:
            _dismiss_modal(page)
            ta.click(timeout=8000)
        time.sleep(0.2)
        ta.fill(text); time.sleep(0.3)
        ta.press("Enter"); time.sleep(2)
        # Verify textarea cleared (= submit happened).
        try:
            remaining = page.evaluate("() => (document.querySelector('textarea') || {}).value || ''")
        except Exception:
            remaining = ""
        if not remaining.strip():
            log(f"  [chat>] {text[:90]}")
            return
        log(f"  [chat] attempt {attempt}: Enter swallowed (textarea still has {remaining[:30]!r}), retrying")
        # Clear and wait a bit before retry.
        try:
            ta.fill("")
        except Exception:
            pass
        time.sleep(3)
    log(f"  [chat] GIVING UP after {max_retries} attempts: {text[:90]}")
    raise RuntimeError(f"send_chat failed to post message after {max_retries} retries: {text[:60]}")


def new_session(page):
    for t in ["新对话", "New Chat"]:
        try:
            b = page.get_by_text(t, exact=True).first
            if b.count() and b.is_visible(timeout=2000):
                b.click()
                # The click triggers a SPA route change. On slower hosts the
                # bare time.sleep(2) wasn't enough and the subsequent
                # page.evaluate hit "Execution context was destroyed". Wait
                # for the DOM to settle first.
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                except Exception:
                    pass
                time.sleep(2)
                break
        except Exception:
            continue
    # Retry the evaluate a few times: even after domcontentloaded the SPA may
    # still be hydrating and a context-destroyed race can fire once.
    last_exc: Exception | None = None
    for _ in range(5):
        try:
            sid = page.evaluate(
                "keys => { for (const k of keys){ const v=localStorage.getItem(k); if(v) return v;} return ''; }",
                SESSION_KEYS,
            )
            return sid or "unknown"
        except Exception as exc:
            msg = str(exc)
            if "Execution context was destroyed" in msg or "navigation" in msg.lower():
                last_exc = exc
                time.sleep(1.5)
                continue
            raise
    raise last_exc if last_exc else RuntimeError("new_session: evaluate failed")


def body_text(page):
    try:
        return page.locator("body").inner_text(timeout=2000)
    except Exception:
        return ""


def _re_button_visible(page) -> bool:
    """True only when the '+ 添加茄形瓶' button is rendered (i.e. the step is
    active), not just when the string appears as a pending timeline label."""
    for sel in ("button:has-text('+ 添加茄形瓶')",
                "[role='button']:has-text('+ 添加茄形瓶')"):
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible(timeout=400):
                return True
        except Exception:
            continue
    return False


# --------------------------------------------------------------------------- run one conv
def run_conv(brief, browser, args):
    cid = brief["conv_id"]
    cat = brief["category"]
    is_cc = "过柱" in cat
    is_re = "旋蒸" in cat
    started = datetime.now(timezone.utc).isoformat()
    log(f"\n{'='*58}\n{cid} | {cat} | {brief['scene'][:50]}")

    page = browser.new_page(viewport={"width": 1680, "height": 1050})
    page.goto(TALOS_BASE + "/", wait_until="load", timeout=60000)
    time.sleep(3)
    session_id = new_session(page)
    title = f"[{cst_stamp('%Y%m%d-%H%M')}-{cid}]"
    api_set_title(session_id, title)
    log(f"  session={session_id} title={title}")

    is_ccre = is_cc and is_re
    stage = "await_plan" if (is_cc or is_re) else "chat_only"
    planner_mismatch = False
    submitted_tasks = []
    dispatch_gated = False
    params_given = False
    cc_spec_violation = None  # guide §4.3: column spec must be silica_12g
    turns_out = []

    def dispatch_intent(t):
        return t in PANEL_ACTION_TEXTS or any(w in t for w in ["下发", "开始", "就按", "提交"])

    for k, turn in enumerate(brief["user_turns"]):
        if page.is_closed():
            break
        ut = turn["user_text"].strip()
        if any(kw in ut for kw in ["SMILES", "上样", "mg", "Rf", "展开剂", "PE:EA",
                                   "PE/EA", "DCM", "ml", "合并液", "溶剂体积"]):
            params_given = True
        log(f"  --- turn {k+1}/{len(brief['user_turns'])} idx={turn['user_idx']} "
            f"stage={stage} exp_tool={turn['expected_tool']} ---")

        # CC→RE handoff: in a CC+RE conv, if we're still in cc_wait and the
        # upcoming turn is NOT a progress query (expected_tool is None), block
        # until CC reaches terminal so stage can advance to re_spec before the
        # RE spec/submit panel needs to be active.
        if (is_ccre and stage == "cc_wait" and turn.get("expected_tool") is None
                and not _task_terminal(session_id, "cc")):
            log(f"  [handoff] turn {k+1} non-progress-query in cc_wait -> "
                f"block-waiting CC terminal before send")
            _wait_lab_terminal(session_id, want="cc")
            final_ct = _read_cc_column_type(session_id)
            if final_ct == "silica_12g":
                cc_spec_violation = None
            elif final_ct:
                cc_spec_violation = final_ct
            log(f"  [GUARD] §4.3 column_type at CC terminal: {final_ct}")
            stage = "re_spec"
            log(f"  [handoff] stage advanced to re_spec")

        send_chat(page, ut)
        wait_textarea_enabled(page, timeout=150)
        time.sleep(2)

        rec = {**turn, "panel_action": None, "stage_before": stage}
        bt = body_text(page)

        if stage == "await_plan" and "批准方案" in bt:
            if is_ccre:
                execs = api_plan_executors(session_id)
                if not ({"cc_agent", "re_agent"} <= execs):
                    planner_mismatch = True
                    rec["panel_action"] = "planner_mismatch_no_approve"
                    rec["plan_executors"] = sorted(execs)
                    stage = "blocked"
                    log(f"  [panel] CC+RE expected, plan execs={sorted(execs)} -> NOT approving")
                else:
                    page.get_by_text("批准方案", exact=True).first.click(timeout=8000)
                    rec["panel_action"] = "approve_plan"; stage = "cc_spec"; time.sleep(3)
            else:
                page.get_by_text("批准方案", exact=True).first.click(timeout=8000)
                rec["panel_action"] = "approve_plan"
                stage = "cc_spec" if is_cc else "re_spec"
                time.sleep(3)

        elif (stage == "cc_spec" and params_given
              and any(x in bt for x in ["点击打开识别面板", "过柱参数预填", "重新识别"])):
            from talos_panel_ui import confirm_cc_spec_via_ui
            ok = confirm_cc_spec_via_ui(page, tlc_image=TLC_IMAGE, rf_value=args.rf,
                                        base_url=TALOS_BASE, session_id=session_id)
            if ok:
                rec["panel_action"] = "confirm_cc_spec"; stage = "cc_submit"
            else:
                rec["panel_action"] = "confirm_cc_spec_TIMEOUT"
                log("  [panel] CC spec confirm did not advance cc_agent — staying at cc_spec")

        elif stage == "cc_submit" and dispatch_intent(ut):
            # Wait for the CC params panel to be ready before submitting. Use
            # API state as the authoritative ready signal (cc_agent.phase ==
            # 'collecting_params'). Body text alone is unreliable: the panel
            # may take 10-30s to render after spec confirm, and a snapshot at
            # "可以" time can miss it on slower hosts.
            cc_ready = False
            cc_deadline = time.time() + 60
            while time.time() < cc_deadline:
                if _cc_task_phase(session_id) == "collecting_params":
                    cc_ready = True
                    break
                time.sleep(3)
            if not cc_ready:
                rec["panel_action"] = "submit_cc_SKIPPED_not_ready"
                log("  [panel] cc_agent never reached collecting_params — CC submit skipped")
            elif not args.allow_dispatch:
                rec["panel_action"] = "submit_cc_SKIPPED_gated"; dispatch_gated = True; stage = "blocked"
                log("  [panel] CC submit gated (no --allow-dispatch)")
            else:
                from talos_panel_ui import submit_cc_via_ui
                submit_cc_via_ui(page, slot_label=args.slot_label, slot_id=args.slot_id)
                rec["panel_action"] = "submit_cc"; submitted_tasks.append("cc"); stage = "cc_wait"
                # §4.3 transient read for diagnostics only; backend may write
                # the slot's installed-cartridge spec first (e.g. silica_40g)
                # and override to silica_12g once the run starts. The terminal
                # reading at CC completion is authoritative — defer the
                # violation check to there.
                ct = None
                for _ in range(8):
                    time.sleep(3)
                    ct = _read_cc_column_type(session_id) or ct
                    if ct:
                        break
                rec["cc_column_type_post_submit"] = ct
                log(f"  [GUARD] §4.3 column_type post-submit (transient): {ct}")

        elif (stage == "re_spec" and (params_given or is_ccre)
              and any(x in bt for x in ["旋蒸参数", "溶剂体系", "水浴温度", "压力梯度"])):
            # Gate the in-loop trigger on workflow-state too: CC summary cards
            # leak "溶剂体系" into body text after CC completes, which used to
            # fire this branch prematurely (re_agent still not_started ->
            # confirm_re_spec wastes 180s clicking nothing). Now also gate on
            # spec completeness: if any RE_REQUIRED_SPEC_FIELDS is null, panel
            # 确认 silently rejects (conv-008 / volume_ml=null case) — defer
            # to post-loop _drive_re_after_cc which handles supplements.
            ph_now, _ = _re_task_phase(session_id)
            spec_now = _re_task_spec(session_id)
            missing_now = _spec_missing(spec_now)
            spec_ready = (ph_now == "collecting_params") or (
                ph_now == "collecting_spec" and not missing_now
            )
            if not spec_ready:
                log(f"  [re-spec] in-loop skipped: phase={ph_now} "
                    f"missing={missing_now} → post-loop will drive")
                rec["panel_action"] = "confirm_re_spec_DEFERRED"
                rec["re_spec_phase"] = ph_now
                rec["re_spec_missing"] = missing_now
            else:
                from talos_panel_ui import confirm_re_spec_via_ui
                ok = confirm_re_spec_via_ui(page, TALOS_BASE, session_id)
                if ok:
                    rec["panel_action"] = "confirm_re_spec"; stage = "re_submit"
                else:
                    rec["panel_action"] = "confirm_re_spec_TIMEOUT"
                    log("  [panel] RE spec confirm timed out in-loop — post-loop finalize will retry")

        elif stage == "re_submit" and dispatch_intent(ut) and _re_button_visible(page):
            if not args.allow_dispatch:
                rec["panel_action"] = "submit_re_SKIPPED_gated"; dispatch_gated = True; stage = "blocked"
                log("  [panel] RE submit gated (no --allow-dispatch)")
            else:
                from talos_panel_ui import submit_re_params_via_ui
                ok = submit_re_params_via_ui(page)
                if ok:
                    rec["panel_action"] = "submit_re"; submitted_tasks.append("re"); stage = "re_wait"
                else:
                    rec["panel_action"] = "submit_re_FAILED"; stage = "re_submit"

        if rec.get("panel_action") in ("approve_plan", "confirm_cc_spec", "submit_cc",
                                       "confirm_re_spec", "submit_re"):
            _dismiss_modal(page)

        rec["stage_after"] = stage
        turns_out.append(rec)

        # Non-blocking: let subsequent progress-query turns fire DURING conducting.
        if stage == "cc_wait" and _task_terminal(session_id, "cc"):
            log("  [lab] CC reached terminal")
            final_ct = _read_cc_column_type(session_id)
            if final_ct == "silica_12g":
                cc_spec_violation = None
            elif final_ct:
                cc_spec_violation = final_ct
            log(f"  [GUARD] §4.3 column_type at CC terminal: {final_ct}")
            stage = "re_spec" if is_re else "done"
        elif stage == "re_wait" and _task_terminal(session_id, "re"):
            log("  [lab] RE reached terminal")
            stage = "done"

    # Loop ended: finalize any still-running submitted lab task(s).
    if stage == "cc_wait":
        _wait_lab_terminal(session_id, want="cc")
        final_ct = _read_cc_column_type(session_id)
        if final_ct == "silica_12g":
            cc_spec_violation = None
        elif final_ct:
            cc_spec_violation = final_ct
        log(f"  [GUARD] §4.3 column_type at CC terminal (post-loop): {final_ct}")
        stage = "re_spec_unreached" if is_re else "done"

    # Post-loop RE finalize: brief turns alone may not push live agent through
    # the panel-driven RE flow. If we still need RE, send an articulated
    # startup prompt (idempotent if backend already advanced), then drive
    # spec confirm via state-driven helper, then optionally dispatch.
    #
    # Skip when CC was gated (no --allow-dispatch in a CC+RE conv): CC never
    # ran, so an RE startup prompt would be lying ("过柱完成") and gets
    # rejected. For RE-only convs (single_rotovap) we always try.
    re_finalize_log: dict | None = None
    re_should_finalize = is_re and (
        ("cc" in submitted_tasks) or not is_cc  # CC actually submitted, OR RE-only conv
    )
    if re_should_finalize and stage in ("re_spec", "re_submit", "re_spec_unreached"):
        re_finalize_log = _drive_re_after_cc(page, brief, session_id, args, submitted_tasks)
        if re_finalize_log.get("reached_collecting_params"):
            if re_finalize_log.get("submitted_re"):
                stage = "re_wait"
            elif re_finalize_log.get("dispatch_gated"):
                stage = "re_collecting_params_no_dispatch"
                dispatch_gated = True
            else:
                stage = re_finalize_log.get("final_stage", "re_submit")
        else:
            stage = re_finalize_log.get("final_stage", "re_spec_failed")

    if stage == "re_wait":
        _wait_lab_terminal(session_id, want="re")
        stage = "done"

    time.sleep(3)
    state = api_workflow_state(session_id)
    page.close()
    ended = datetime.now(timezone.utc).isoformat()

    spans = _collect_spans(brief, started, ended)
    return {
        "conv_id": cid, "category": cat, "category_raw": brief["category_raw"],
        "session_id": session_id, "title": title,
        "started_utc": started, "ended_utc": ended,
        "final_stage": stage, "planner_mismatch": planner_mismatch,
        "submitted_tasks": submitted_tasks, "dispatch_gated": dispatch_gated,
        "cc_spec_violation": cc_spec_violation,
        "re_finalize": re_finalize_log,
        "turns": turns_out, "workflow_state": state, "phoenix_spans": spans,
    }


TERMINAL = ("completed", "failed", "cancelled", "discarded", "timeout")


def _read_cc_column_type(session_id):
    """Read column_type from the CC task's user_params/params. Returns None if
    no CC task is recorded yet."""
    for tkx in (api_workflow_state(session_id).get("tasks") or []):
        if "cc" in (tkx.get("task_type") or ""):
            return ((tkx.get("user_params") or tkx.get("params")) or {}
                    ).get("column_type")
    return None


def _task_run_status(session_id, want=None):
    """Return latest_run.status for the task whose task_type contains `want`
    ('cc'/'re'), else the last task. None if not found."""
    st = api_workflow_state(session_id)
    tasks = st.get("tasks") or []
    if not tasks:
        return None
    pick = tasks[-1]
    if want:
        for tk in tasks:
            if want in (tk.get("task_type") or ""):
                pick = tk
    return (pick.get("latest_run") or {}).get("status")


def _task_terminal(session_id, want=None):
    return (_task_run_status(session_id, want) or "") in TERMINAL


def _wait_lab_terminal(session_id, want=None, timeout=1200):
    log(f"  [lab] waiting for {want or 'task'} terminal (poll 15s)...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(15)
        s = _task_run_status(session_id, want)
        if (s or "") in TERMINAL:
            log(f"  [lab] {want or 'task'} terminal status={s}")
            return s
    log("  [lab] WAIT TIMEOUT")
    return "timeout"


# ---------------------------------------------------------------------------
# RE post-loop drive (state-machine driven; survives live agent variance)
# ---------------------------------------------------------------------------

def _re_task(session_id):
    """Return the re_agent task dict (or {}), so callers can read phase/spec
    together without round-tripping the API multiple times."""
    for t in (api_workflow_state(session_id).get("tasks") or []):
        if "re" in (t.get("task_type") or ""):
            return t
    return {}


def _re_task_phase(session_id):
    """Return (phase, run_status) for the re_agent task, or (None, None)."""
    t = _re_task(session_id)
    if not t:
        return None, None
    return t.get("phase"), (t.get("latest_run") or {}).get("status")


def _re_task_spec(session_id):
    """Return the re_agent task spec dict (or {})."""
    return _re_task(session_id).get("spec") or {}


# Fields that must be non-null in re_agent.spec before the panel 确认 will
# actually advance backend to collecting_params. Confirmed empirically:
#   - solvent_ratio=null  → silent reject (SMOKE_README §80, ratio-null case)
#   - volume_ml=null      → silent reject (2026-05-21 conv-008 case)
#   - solvents=null/empty → same shape; included for safety
# If you discover another null-field silent-reject, add it here.
RE_REQUIRED_SPEC_FIELDS = ("solvents", "solvent_ratio", "volume_ml")


def _spec_missing(spec):
    """Return list of RE_REQUIRED_SPEC_FIELDS that are absent / null / empty."""
    out = []
    for k in RE_REQUIRED_SPEC_FIELDS:
        v = spec.get(k)
        if v is None or v == [] or v == {} or v == "":
            out.append(k)
    return out


# NOTE: `_compose_re_supplement` was removed 2026-05-21 after the volume_ml
# silent-reject case (conv-008) revealed chat-based supplements are unreliable
# — they may be filtered by admittance and never reach the spec. RE spec gaps
# are now filled deterministically via panel UI inputs; see
# `talos_panel_ui.fill_re_spec_via_ui` and `_drive_re_after_cc` below.


# RE spec defaults used by the UI-fill fallback. Volume is per-run random in
# 100-300 mL (see _drive_re_after_cc). Solvent system defaults to PE/EA 1:1 —
# this matches the most common brief and is overridden if a future brief
# carries different chemistry.
RE_DEFAULT_SOLVENTS = ["PE", "EA"]
RE_DEFAULT_RATIOS = [1.0, 1.0]


def _cc_task_phase(session_id):
    """Return cc_agent phase string ('collecting_spec' / 'collecting_params' /
    'conducting' / 'done'), or None if task absent."""
    for t in (api_workflow_state(session_id).get("tasks") or []):
        if "cc" in (t.get("task_type") or ""):
            return t.get("phase")
    return None


def _compose_re_nudge(brief, volume_ml: int):
    """Compose a fully-articulated RE startup prompt from brief context.

    Why each piece matters (verified empirically 2026-05-20):
      - "过柱完成，下一步做旋蒸" + intent: triggers admittance=yes (not
        rejected as bare confirmation) and starts re_agent.
      - **solvent ratio** (`PE:EA=1:1`): without an explicit ratio the agent
        leaves `spec.solvent_ratio=null`, and then panel 确认 clicks don't
        advance backend to collecting_params (silent reject).
      - volume + container type: avoids agent asking follow-up questions
        ("装在什么瓶里？") that stall re_phase at not_started.
      - thermal stability: avoids agent asking for thermal constraints.

    `volume_ml` is randomized per run (100-300) so we exercise different
    panel values; the same number is later used as the UI-fill fallback
    if the agent's spec ends up with volume_ml=null.
    """
    scene = (brief.get("scene") or "")
    thermal = "化合物热稳定性正常，没有分解温度约束。"
    if any(k in scene for k in ["热敏", "分解", "70 度", "70°C", "60 度", "60°C"]):
        thermal = "化合物对温度较敏感，请压低水浴温度（≤30°C）以避免分解。"
    return (
        "好的，过柱完成，开始旋蒸。"
        f"{thermal}"
        f"合并液约 {volume_ml} ml，体系是 PE 和 EA，比例 PE:EA = 1:1，装在 250 ml 茄形瓶里。"
        "请生成旋蒸执行参数推荐（水浴温度、压力梯度），我会在右侧面板确认。"
    )


def _wait_chat_ready(page, session_id, timeout=120):
    """After CC terminal the chat textarea is briefly disabled while the
    backend transitions. Poll until it's interactive again (mirrors
    talos_cc_re_frontend_runner.wait_for_chat_ready_or_refresh)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            ta = page.locator("textarea").first
            if ta.count() and ta.get_attribute("disabled") is None:
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def _drive_re_after_cc(page, brief, session_id, args, submitted_tasks):
    """RE finalize: engage agent, then deterministically fill any missing
    spec fields via panel UI inputs (NOT chat supplements).

    Why UI fill instead of chat: the chat-based supplement approach we tried
    on 2026-05-21 (conv-008 retry) failed — bare "补充 spec：xxx" messages
    were filtered by Talos admittance and the agent never wrote the value
    into `spec.volume_ml`. Direct DOM manipulation of the panel inputs
    bypasses admittance entirely and is deterministic. The RE spec panel
    exposes:
      - 溶剂体积 (mL): single `<input type=number>` (label-anchored)
      - solvent table: `<select>` + ratio `<input>` per row, with `+ 添加溶剂`
        to add new rows
    See `talos_panel_ui.fill_re_spec_via_ui` (and the 2026-05-21 panel-DOM
    capture under `eval_outputs/panel_doms_*/`) for the exact selectors.

    Flow:
      A. engage agent — if phase in (None, not_started), send full nudge
         (random 100-300 mL volume in the wording) and wait for phase to
         leave not_started.
      B. wait briefly (~15s) for agent to populate spec from chat context.
      C. read spec — for each RE_REQUIRED_SPEC_FIELDS still null, fill via
         panel UI (volume_ml -> the random number from A; solvents/ratio ->
         RE_DEFAULT_*). Verify spec became complete via API.
      D. confirm spec via UI (clicks the panel 确认 button, advances backend
         to collecting_params).
      E. dispatch gate — if --allow-dispatch, submit final params.
    """
    rec = {"phase_seq": [], "messages_sent": []}

    def record(tag):
        ph, st_ = _re_task_phase(session_id)
        spec = _re_task_spec(session_id)
        missing = _spec_missing(spec)
        prev = rec["phase_seq"][-1] if rec["phase_seq"] else None
        if (not prev or prev.get("phase") != ph
                or prev.get("missing") != missing
                or prev.get("at") != tag):
            rec["phase_seq"].append({
                "at": tag, "phase": ph, "run_status": st_,
                "missing": missing, "spec": spec,
            })
        return ph, spec, missing

    # Per-run random volume used both in the nudge text and as the UI-fill
    # value if spec.volume_ml ends up null. Same number both places so the
    # conversation transcript and the panel agree.
    volume_ml = random.randint(100, 300)
    rec["volume_ml_choice"] = volume_ml

    log("  [re-finalize] start")
    if not _wait_chat_ready(page, session_id, timeout=120):
        log("  [re-finalize] WARNING: chat textarea never went enabled")
    record("start")

    # ---- Phase A: engage agent + ensure it knows the full spec ---------
    # Send the nudge whenever EITHER:
    #   (a) phase is not_started — engagement needed, OR
    #   (b) phase is collecting_spec but spec has missing fields — agent
    #       hasn't "heard" those values, so its internal recommendation is
    #       still partial. Without this, even after we type the missing
    #       value into the panel input (frontend onChange → backend field
    #       update succeeds), backend's confirm-spec gate rejects because
    #       the agent's last-issued recommendation is still incomplete.
    #       Empirically (2026-05-25 conv-008 with d8de433): UI fill made
    #       backend.spec.volume_ml=192, but 5x 确认 clicks + chat fallback
    #       all failed to advance phase. Root cause: the nudge path that
    #       gets agent to re-issue a complete recommendation was skipped
    #       because phase had already moved past not_started.
    # The nudge body is chemistry-rich natural language (volume/ratio/
    # solvent/container) so admittance treats it as chemistry intent and
    # lets it through, even at collecting_spec.
    ph, spec, missing = record("pre_engage")
    rec["nudge_sent"] = False
    needs_nudge = ph in (None, "not_started") or (
        ph == "collecting_spec" and missing)
    if needs_nudge:
        nudge = _compose_re_nudge(brief, volume_ml)
        log(f"  [re-finalize] phase={ph} missing={missing} "
            f"volume_choice={volume_ml}; sending nudge: {nudge[:80]}...")
        try:
            send_chat(page, nudge)
            rec["nudge_sent"] = True
            rec["nudge_text"] = nudge
            rec["messages_sent"].append({"kind": "nudge", "text": nudge})
        except Exception as exc:
            log(f"  [re-finalize] nudge send failed: {exc}")
            rec["nudge_error"] = str(exc)
            rec["final_stage"] = "re_spec_failed"
            return rec
        # Success criteria after nudge: either phase advanced to
        # collecting_params (rare — agent auto-confirms) OR spec is complete
        # at collecting_spec (the common case — agent re-issued full
        # recommendation, waiting for our 确认 click). Old code only
        # checked phase, which works for engagement-from-not_started but
        # not for completing-an-incomplete-spec at collecting_spec.
        engage_deadline = time.time() + 180
        engaged = False
        while time.time() < engage_deadline:
            time.sleep(6)
            ph, spec, missing = record("waiting_engage")
            if ph == "collecting_params":
                engaged = True
                break
            if ph == "collecting_spec" and not missing:
                engaged = True
                break
        if not engaged:
            # Don't bail; fall through to UI-fill + confirm as a final
            # backup. The nudge may still have helped (agent partially
            # updated spec); UI fill closes the remaining gap.
            log(f"  [re-finalize] nudge engage budget exhausted; phase={ph} "
                f"missing={missing} — falling through to UI-fill backup")
    else:
        log(f"  [re-finalize] phase={ph} spec complete (or past collecting_spec); "
            f"skipping nudge")

    # ---- Phase B: brief grace for agent to populate spec ---------------
    # If agent ingests the nudge / brief turns fast, spec may already be
    # complete; if not, give it 15s before deciding to UI-fill.
    log("  [re-finalize] waiting 15s for agent to populate spec from chat...")
    grace_deadline = time.time() + 15
    while time.time() < grace_deadline:
        time.sleep(3)
        ph, spec, missing = record("waiting_spec_natural")
        if ph == "collecting_params" or (ph == "collecting_spec" and not missing):
            break

    # ---- Phase C: deterministic UI fill for any remaining missing fields
    ph, spec, missing = record("pre_ui_fill")
    if ph == "collecting_params":
        log(f"  [re-finalize] phase={ph}; spec already past collecting_spec")
    elif ph == "collecting_spec" and not missing:
        log(f"  [re-finalize] spec complete via chat; spec={spec}")
    elif ph == "collecting_spec" and missing:
        log(f"  [re-finalize] phase={ph} with missing={missing}; "
            f"filling via panel UI (volume={volume_ml}, "
            f"solvents={RE_DEFAULT_SOLVENTS}, ratios={RE_DEFAULT_RATIOS})")
        fill_kwargs = {}
        if "volume_ml" in missing:
            fill_kwargs["volume_ml"] = volume_ml
        if "solvents" in missing or "solvent_ratio" in missing:
            fill_kwargs["solvents"] = list(RE_DEFAULT_SOLVENTS)
            fill_kwargs["ratios"] = list(RE_DEFAULT_RATIOS)
        from talos_panel_ui import fill_re_spec_via_ui
        ui_result = fill_re_spec_via_ui(page, **fill_kwargs)
        rec["ui_fill"] = ui_result
        if not ui_result.get("ok"):
            log(f"  [re-finalize] UI fill failed: {ui_result.get('errors')}")
            rec["final_stage"] = "re_spec_failed"
            rec["missing_at_giveup"] = missing
            rec["spec_at_giveup"] = spec
            return rec
        # Poll backend for spec to catch up with the keystroke fill. With
        # real keystrokes React fires onChange and the frontend posts the
        # value to backend → spec usually updates within a few seconds. If
        # 15s isn't enough we still proceed to confirm — confirm_re_spec_via_ui
        # will be the final arbiter (and will tell us via re_spec_failed if
        # backend still didn't pick it up).
        verify_deadline = time.time() + 15
        synced = False
        while time.time() < verify_deadline:
            ph, spec, missing = record("post_ui_fill")
            if (ph == "collecting_params") or (ph == "collecting_spec" and not missing):
                synced = True
                log(f"  [re-finalize] UI fill propagated to backend; spec={spec}")
                break
            time.sleep(3)
        if not synced:
            ph, spec, missing = record("post_ui_fill_timeout")
            log(f"  [re-finalize] WARN: UI fill did not propagate to backend "
                f"within 15s — proceeding to confirm anyway. missing={missing} "
                f"spec={spec}")
            rec["ui_fill_propagation_timeout"] = True
            rec["spec_after_ui_fill"] = spec
    else:
        log(f"  [re-finalize] unexpected phase={ph}; treating as failure")
        rec["final_stage"] = "re_spec_failed"
        return rec

    # ---- Phase D: confirm spec via UI -----------------------------------
    from talos_panel_ui import confirm_re_spec_via_ui
    ok = confirm_re_spec_via_ui(page, TALOS_BASE, session_id)
    record("post_confirm_spec")
    if not ok:
        log("  [re-finalize] confirm_re_spec_via_ui returned False")
        rec["final_stage"] = "re_spec_failed"
        return rec
    rec["reached_collecting_params"] = True
    rec["final_stage"] = "re_submit"

    # ---- Phase E: dispatch gate -----------------------------------------
    if not args.allow_dispatch:
        log("  [re-finalize] dispatch gated (no --allow-dispatch) — stopping at collecting_params")
        rec["dispatch_gated"] = True
        return rec

    from talos_panel_ui import submit_re_params_via_ui
    if submit_re_params_via_ui(page):
        rec["submitted_re"] = True
        submitted_tasks.append("re")
        record("post_submit_re")
        rec["final_stage"] = "re_wait"
    else:
        log("  [re-finalize] submit_re_params_via_ui FAILED")
        rec["submitted_re"] = False
        rec["final_stage"] = "re_submit_failed"
    return rec


def _collect_spans(brief, started, ended):
    """Match this conv's root spans by input.value containing a user-turn text,
    within the run window. Pull child tool spans per trace for actual tool calls."""
    try:
        roots = phoenix_root_spans(limit=400)
    except Exception as e:
        log(f"  [phoenix] root fetch failed: {e}")
        return {"error": str(e)}
    keys = [t["user_text"][:24] for t in brief["user_turns"] if len(t["user_text"]) >= 6]
    out = []
    for s in roots:
        st = s.get("start_time", "")
        if not (started <= st <= ended):
            continue
        attrs = s.get("attributes", {}) or {}
        inp = str(attrs.get("input.value", "") or attrs.get(
            "llm.input_messages.0.message.content", ""))
        if not any(k in inp for k in keys):
            continue
        tid = s.get("context", {}).get("trace_id")
        tools = []
        try:
            for cs in phoenix_trace_spans(tid):
                if cs.get("span_kind") == "TOOL" or (cs.get("name", "") in {
                    "get_robot_status", "get_running_experiments", "get_material_inventory",
                    "get_involved_materials", "get_task_status", "get_task_detail",
                    "list_tasks", "get_task_entity_events", "get_entity_events",
                    "get_recent_events"}):
                    tools.append(cs.get("name"))
        except Exception:
            pass
        agent_reply = user_msg = ""
        ov = {}
        try:
            ov = json.loads(attrs.get("output.value", "") or "{}")
            for m in (ov.get("messages") or []):
                d = (m.get("data") or {})
                if m.get("type") == "ai" and d.get("content"):
                    agent_reply = d["content"]
                if m.get("type") == "human" and d.get("content"):
                    user_msg = d["content"]
        except Exception:
            pass
        out.append({
            "span_id": s.get("context", {}).get("span_id"),
            "trace_id": tid, "start_time": st,
            "turn_id": ov.get("turn_id"), "turn_index": ov.get("turn_index"),
            "user_msg": user_msg or inp[:200],
            "agent_reply": agent_reply,
            "admittance": ov.get("admittance"),
            "admittance_state": ov.get("admittance_state"),
            "intention": ov.get("intention"),
            "mode": ov.get("mode"),
            "execution_status": ov.get("execution_status"),
            "actual_tools": sorted(set(t for t in tools if t)),
        })
    out.sort(key=lambda x: x["start_time"])
    log(f"  [phoenix] matched {len(out)} root spans in window")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("conv_ids", nargs="+")
    ap.add_argument("--allow-dispatch", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--rf", default="0.35")
    ap.add_argument("--slot-id", default="bic_09B_l4_002")
    ap.add_argument("--slot-label", default="备料架L4层样品柱002位")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    briefs = build_briefs(args.conv_ids)
    Path("/tmp/eval_briefs.json").write_text(json.dumps(briefs, ensure_ascii=False))
    log(f"targets={args.conv_ids} allow_dispatch={args.allow_dispatch}")

    results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        try:
            for b in briefs:
                try:
                    results[b["conv_id"]] = run_conv(b, browser, args)
                except Exception as e:
                    import traceback; traceback.print_exc()
                    results[b["conv_id"]] = {"conv_id": b["conv_id"], "error": str(e)}
        finally:
            browser.close()

    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2))
        log(f"\nSUMMARY (also written to {args.out}):")
    else:
        log(f"\nSUMMARY (stdout-only; pass --out PATH to also write JSON):")
    for cid, r in results.items():
        if "error" in r:
            log(f"  {cid}: ERROR {r['error']}")
        else:
            rf = r.get("re_finalize") or {}
            log(f"  {cid}: sess={r['session_id']} stage={r['final_stage']} "
                f"submitted={r['submitted_tasks']} planner_mismatch={r['planner_mismatch']} "
                f"cc_spec_violation={r.get('cc_spec_violation')} "
                f"gated={r['dispatch_gated']} spans={len(r.get('phoenix_spans') or [])}"
                f" re_final={rf.get('final_stage')}"
                f" re_nudge_sent={rf.get('nudge_sent')}"
                f" re_phases={[p.get('phase') for p in (rf.get('phase_seq') or [])]}")


if __name__ == "__main__":
    main()
