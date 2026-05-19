#!/usr/bin/env python3
"""
Smoke runner for the 2026-05-18 link-validation batch.

Drives conv briefs through the live TALOS frontend via the Mac Tailscale relay
(100.118.16.118), captures per-turn agent output + tool calls from Phoenix root
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

TALOS_BASE = "http://100.118.16.118:8080"
PHOENIX_BASE = "http://100.118.16.118:6006"
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


def send_chat(page, text):
    wait_textarea_enabled(page)
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
    log(f"  [chat>] {text[:90]}")


def new_session(page):
    for t in ["新对话", "New Chat"]:
        try:
            b = page.get_by_text(t, exact=True).first
            if b.count() and b.is_visible(timeout=2000):
                b.click(); time.sleep(2); break
        except Exception:
            continue
    sid = page.evaluate(
        "keys => { for (const k of keys){ const v=localStorage.getItem(k); if(v) return v;} return ''; }",
        SESSION_KEYS,
    )
    return sid or "unknown"


def body_text(page):
    try:
        return page.locator("body").inner_text(timeout=2000)
    except Exception:
        return ""


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
            confirm_cc_spec_via_ui(page, tlc_image=TLC_IMAGE, rf_value=args.rf)
            rec["panel_action"] = "confirm_cc_spec"; stage = "cc_submit"

        elif stage == "cc_submit" and dispatch_intent(ut) and any(x in bt for x in ["管理插槽", "硅胶柱规格"]):
            if not args.allow_dispatch:
                rec["panel_action"] = "submit_cc_SKIPPED_gated"; dispatch_gated = True; stage = "blocked"
                log("  [panel] CC submit gated (no --allow-dispatch)")
            else:
                from talos_panel_ui import submit_cc_via_ui
                submit_cc_via_ui(page, slot_label=args.slot_label, slot_id=args.slot_id)
                rec["panel_action"] = "submit_cc"; submitted_tasks.append("cc"); stage = "cc_wait"
                # Guide §4.3 HARD rule: column spec must be silica_12g. Verify.
                ct = None
                for _ in range(8):
                    time.sleep(3)
                    for tkx in (api_workflow_state(session_id).get("tasks") or []):
                        if "cc" in (tkx.get("task_type") or ""):
                            ct = ((tkx.get("user_params") or tkx.get("params")) or {}
                                  ).get("column_type")
                    if ct:
                        break
                rec["cc_column_type"] = ct
                if ct and ct != "silica_12g":
                    cc_spec_violation = ct
                    log(f"  [GUARD] §4.3 VIOLATION column_type={ct} (must be silica_12g)")
                else:
                    log(f"  [GUARD] §4.3 column_type={ct}")

        elif (stage == "re_spec" and (params_given or is_ccre)
              and any(x in bt for x in ["旋蒸参数", "溶剂体系", "水浴温度", "压力梯度"])):
            from talos_panel_ui import confirm_re_spec_via_ui
            confirm_re_spec_via_ui(page)
            rec["panel_action"] = "confirm_re_spec"; stage = "re_submit"

        elif stage == "re_submit" and dispatch_intent(ut) and any(x in bt for x in ["茄形瓶", "添加茄形瓶"]):
            if not args.allow_dispatch:
                rec["panel_action"] = "submit_re_SKIPPED_gated"; dispatch_gated = True; stage = "blocked"
                log("  [panel] RE submit gated (no --allow-dispatch)")
            else:
                from talos_panel_ui import submit_re_via_ui
                submit_re_via_ui(page)
                rec["panel_action"] = "submit_re"; submitted_tasks.append("re"); stage = "re_wait"

        if rec.get("panel_action") in ("approve_plan", "confirm_cc_spec", "submit_cc",
                                       "confirm_re_spec", "submit_re"):
            _dismiss_modal(page)

        rec["stage_after"] = stage
        turns_out.append(rec)

        # Non-blocking: let subsequent progress-query turns fire DURING conducting.
        if stage == "cc_wait" and _task_terminal(session_id, "cc"):
            log("  [lab] CC reached terminal")
            stage = "re_spec" if is_re else "done"
        elif stage == "re_wait" and _task_terminal(session_id, "re"):
            log("  [lab] RE reached terminal")
            stage = "done"

    # Loop ended: finalize any still-running submitted lab task(s).
    if stage == "cc_wait":
        _wait_lab_terminal(session_id, want="cc")
        stage = "re_spec_unreached" if is_re else "done"
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
        "turns": turns_out, "workflow_state": state, "phoenix_spans": spans,
    }


TERMINAL = ("completed", "failed", "cancelled", "discarded", "timeout")


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

    out = args.out or str(ROOT / "eval_outputs" / f"smoke_{cst_stamp('%Y%m%d_%H%M')}.json")
    Path(out).write_text(json.dumps(results, ensure_ascii=False, indent=2))
    log(f"\nSUMMARY -> {out}")
    for cid, r in results.items():
        if "error" in r:
            log(f"  {cid}: ERROR {r['error']}")
        else:
            log(f"  {cid}: sess={r['session_id']} stage={r['final_stage']} "
                f"submitted={r['submitted_tasks']} planner_mismatch={r['planner_mismatch']} "
                f"cc_spec_violation={r.get('cc_spec_violation')} "
                f"gated={r['dispatch_gated']} spans={len(r.get('phoenix_spans') or [])}")


if __name__ == "__main__":
    main()
