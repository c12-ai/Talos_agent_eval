#!/usr/bin/env python3
"""
Systematic panel-DOM capture for the four Talos workflow panels:

  - cc_spec   : CC TLC/SMILES recognition spec panel (collecting_spec)
  - cc_params : CC final-params panel with silica spec + slot install (collecting_params)
  - re_spec   : RE solvent/volume/ratio pre-fill panel (collecting_spec)
  - re_params : RE final-params panel with flask + tubes + temp/pressure (collecting_params)

For each panel we dump:
  * `{phase}_active.html`  — outerHTML of the active step card (.step-card-active or modal)
  * `{phase}_inputs.json`  — every visible <input>/<select>/<textarea>, with:
                              tag, type, value, placeholder, readonly, disabled,
                              aria-label, nearest-label text, a stable CSS path,
                              and whether it lives inside the active step card
  * `{phase}_buttons.json` — every visible button inside the active card
  * `{phase}_meta.json`    — workflow-state snapshot at capture time
  * `summary.md`           — readable per-phase "what can we directly fill" table

No `--allow-dispatch`; we stop before any submission, so no lab time is used.

Usage:
  python3 scripts/dump_panel_doms.py            # capture all 4 phases
  python3 scripts/dump_panel_doms.py --only cc  # just CC phases
  python3 scripts/dump_panel_doms.py --only re  # just RE phases
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright

from smoke_runner_20260518 import (
    TALOS_BASE, TLC_IMAGE, api_set_title, api_workflow_state,
    body_text, build_briefs, cst_stamp, new_session, send_chat,
    wait_textarea_enabled, _cc_task_phase, _re_task_phase, _re_task_spec,
)
from talos_panel_ui import confirm_cc_spec_via_ui, confirm_re_spec_via_ui


# ---------------------------------------------------------------------------
# Snapshot JS: walk all visible inputs/selects/textareas + active panel + modal
# ---------------------------------------------------------------------------

SNAPSHOT_JS = r"""
() => {
  const isVis = e => {
    const r = e.getBoundingClientRect();
    const s = window.getComputedStyle(e);
    return r.width > 0 && r.height > 0
      && s.visibility !== 'hidden' && s.display !== 'none';
  };

  // Build a short, stable-ish CSS path for an element (id > class chain).
  const cssPath = el => {
    const parts = [];
    let n = el;
    let depth = 0;
    while (n && n.nodeType === 1 && depth < 6) {
      let part = n.tagName.toLowerCase();
      if (n.id) { part += '#' + n.id; parts.unshift(part); break; }
      const cls = (n.className || '').toString().trim().split(/\s+/).slice(0, 2).filter(Boolean);
      if (cls.length) part += '.' + cls.join('.');
      const sib = n.parentElement
        ? Array.from(n.parentElement.children).filter(c => c.tagName === n.tagName)
        : [];
      if (sib.length > 1) part += `:nth-of-type(${sib.indexOf(n) + 1})`;
      parts.unshift(part);
      n = n.parentElement;
      depth++;
    }
    return parts.join(' > ');
  };

  // Nearest visible label text (label[for=id], wrapping label, or preceding text).
  const labelFor = el => {
    if (el.id) {
      const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lbl) return (lbl.innerText || '').trim().slice(0, 80);
    }
    const wrap = el.closest('label');
    if (wrap) return (wrap.innerText || '').trim().slice(0, 80);
    // Walk up a few levels and grab preceding text node siblings.
    let n = el;
    for (let i = 0; i < 4 && n; i++) {
      let p = n.previousElementSibling;
      while (p) {
        const t = (p.innerText || p.textContent || '').trim();
        if (t && t.length < 80) return t.slice(0, 80);
        p = p.previousElementSibling;
      }
      n = n.parentElement;
    }
    return '';
  };

  // Active step card (RE/CC use .step-card-active inside .timeline-step).
  const activeCard = document.querySelector('.step-card-active')
    || document.querySelector('.timeline-step:has(.step-card-active)');

  // Visible modal (CC TLC dialog, slot install, etc).
  const modal = Array.from(document.querySelectorAll(
      "div[class*='fixed'][class*='inset-0'], div.fixed.inset-0.z-50"
    )).find(isVis) || null;

  const root = activeCard || document.body;

  // Collect all visible form controls anywhere on the page.
  const controls = [];
  document.querySelectorAll('input, select, textarea').forEach(el => {
    if (!isVis(el)) return;
    const inActive = !!(activeCard && activeCard.contains(el));
    const inModal = !!(modal && modal.contains(el));
    const base = {
      tag: el.tagName.toLowerCase(),
      type: el.type || '',
      value: (el.value || '').toString().slice(0, 80),
      placeholder: el.placeholder || '',
      ariaLabel: el.getAttribute('aria-label') || '',
      readonly: !!el.readOnly,
      disabled: !!el.disabled,
      label: labelFor(el),
      cssPath: cssPath(el),
      inActiveCard: inActive,
      inModal: inModal,
    };
    if (el.tagName === 'SELECT') {
      base.options = Array.from(el.options).slice(0, 12).map(o => ({
        value: o.value, text: (o.textContent || '').trim().slice(0, 40),
      }));
    }
    controls.push(base);
  });

  // Collect buttons in active card + modal (these are click targets).
  const collectButtons = (root) => {
    if (!root) return [];
    return Array.from(root.querySelectorAll("button, [role='button']"))
      .filter(isVis)
      .map(b => ({
        text: (b.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 60),
        disabled: !!b.disabled,
        cssPath: cssPath(b),
      }));
  };

  return {
    activeCardHtml: activeCard ? activeCard.outerHTML.slice(0, 60000) : '',
    modalHtml: modal ? modal.outerHTML.slice(0, 60000) : '',
    activeButtons: collectButtons(activeCard),
    modalButtons: collectButtons(modal),
    controls,
    activeCardText: activeCard ? (activeCard.innerText || '').slice(0, 2000) : '',
    modalText: modal ? (modal.innerText || '').slice(0, 2000) : '',
  };
}
"""


def snapshot(page, phase: str, session_id: str, out_dir: Path) -> dict:
    """Capture DOM for the currently-active panel. Returns summary dict."""
    time.sleep(2)  # let any animation settle
    data = page.evaluate(SNAPSHOT_JS)

    (out_dir / f"{phase}_active.html").write_text(data.get("activeCardHtml") or "")
    (out_dir / f"{phase}_modal.html").write_text(data.get("modalHtml") or "")
    (out_dir / f"{phase}_inputs.json").write_text(
        json.dumps(data.get("controls") or [], ensure_ascii=False, indent=2))
    (out_dir / f"{phase}_buttons.json").write_text(json.dumps({
        "active_card": data.get("activeButtons") or [],
        "modal": data.get("modalButtons") or [],
    }, ensure_ascii=False, indent=2))

    state = api_workflow_state(session_id)
    meta = {
        "phase": phase,
        "session_id": session_id,
        "captured_at": datetime.now().isoformat(),
        "cc_phase": _cc_task_phase(session_id),
        "re_phase": _re_task_phase(session_id)[0],
        "re_spec": _re_task_spec(session_id),
        "active_card_text_snippet": (data.get("activeCardText") or "")[:400],
        "modal_text_snippet": (data.get("modalText") or "")[:400],
        "tasks": [{"task_type": t.get("task_type"), "phase": t.get("phase"),
                   "spec": t.get("spec"), "params": t.get("params") or t.get("user_params")}
                  for t in (state.get("tasks") or [])],
    }
    (out_dir / f"{phase}_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2))

    controls = data.get("controls") or []
    editable = [c for c in controls
                if (c.get("inActiveCard") or c.get("inModal"))
                and not c.get("readonly") and not c.get("disabled")]
    print(f"  [{phase}] captured: {len(controls)} controls "
          f"({len(editable)} editable in active/modal), "
          f"{len(data.get('activeButtons') or [])} active-buttons, "
          f"{len(data.get('modalButtons') or [])} modal-buttons")
    return {"phase": phase, "controls": controls,
            "active_buttons": data.get("activeButtons") or [],
            "modal_buttons": data.get("modalButtons") or []}


# ---------------------------------------------------------------------------
# Drive convs to each phase
# ---------------------------------------------------------------------------

def drive_cc_phases(page, out_dir: Path) -> list[dict]:
    """conv-001 (single_column): plan approve -> SMILES turn -> CC spec
    panel ready -> snapshot cc_spec; then confirm CC spec -> CC params panel
    ready -> snapshot cc_params. No submission."""
    print("\n[cc] driving conv-001 to CC spec / CC params...")
    page.goto(TALOS_BASE + "/", wait_until="load", timeout=60000)
    time.sleep(3)
    sid = new_session(page)
    api_set_title(sid, f"[{cst_stamp('%Y%m%d-%H%M')}-domcap-cc]")
    print(f"  session={sid}")

    turns = build_briefs(["conv-001"])[0]["user_turns"]
    plan_ok = spec_panel_ready = spec_confirmed = False
    captured = []
    params_given = False

    for t in turns:
        ut = t["user_text"].strip()
        if any(k in ut for k in ["SMILES", "上样", "mg", "Rf", "PE/EA", "PE:EA"]):
            params_given = True
        send_chat(page, ut)
        wait_textarea_enabled(page, timeout=150)
        time.sleep(2)
        bt = body_text(page)

        if not plan_ok and "批准方案" in bt:
            page.get_by_text("批准方案", exact=True).first.click(timeout=8000)
            plan_ok = True
            print("  approved plan")
            time.sleep(3)
            continue

        # CC spec panel visible? Snapshot BEFORE confirming.
        if plan_ok and params_given and not spec_panel_ready and any(
                x in bt for x in ["点击打开识别面板", "过柱参数预填", "重新识别"]):
            # Open the recognition modal so we capture both the timeline card
            # and the modal DOM at this phase.
            try:
                page.get_by_text("点击打开识别面板", exact=False).first.click(timeout=3000)
                time.sleep(2)
            except Exception:
                pass  # modal may already be visible from prior turn
            print("  cc spec panel visible -> snapshot")
            captured.append(snapshot(page, "cc_spec", sid, out_dir))
            spec_panel_ready = True
            # close modal (Escape) so confirm_cc_spec_via_ui can re-open it cleanly
            try:
                page.keyboard.press("Escape")
                time.sleep(1)
            except Exception:
                pass
            # Now confirm CC spec via baseline helper (upload TLC + fill Rf + click).
            print("  confirming CC spec (no submission)...")
            confirm_cc_spec_via_ui(page, tlc_image=TLC_IMAGE, rf_value="0.35",
                                    base_url=TALOS_BASE, session_id=sid)
            spec_confirmed = True
            time.sleep(3)
            continue

        # CC params panel ready -> snapshot.
        if spec_confirmed and _cc_task_phase(sid) == "collecting_params":
            # Wait briefly for the params panel to render.
            for _ in range(30):
                bt2 = body_text(page)
                if any(k in bt2 for k in ["硅胶柱规格", "管理插槽", "样品柱"]):
                    break
                time.sleep(2)
            print("  cc params panel visible -> snapshot (active card)")
            captured.append(snapshot(page, "cc_params", sid, out_dir))
            # Also open the slot-install dialog and capture its modal.
            print("  opening slot install dialog -> snapshot (modal)")
            opened = False
            for sttext in ["管理插槽", "管理样品柱插槽", "插槽"]:
                try:
                    loc = page.get_by_text(sttext, exact=False).filter(
                        visible=True).first
                    if loc.count():
                        loc.click(timeout=4000)
                        opened = True
                        time.sleep(2)
                        break
                except Exception:
                    continue
            if opened:
                captured.append(snapshot(page, "cc_params_slot_dialog", sid, out_dir))
                # Close the dialog so we don't accidentally trigger anything.
                try:
                    page.keyboard.press("Escape")
                    time.sleep(1)
                except Exception:
                    pass
            break  # done with CC phases

    return captured


def drive_re_phases(page, out_dir: Path) -> list[dict]:
    """conv-005 (single_rotovap): plan approve -> solvent params turn -> RE
    spec panel ready -> snapshot re_spec; then confirm RE spec -> RE params
    panel ready -> snapshot re_params. No submission."""
    print("\n[re] driving conv-005 to RE spec / RE params...")
    page.goto(TALOS_BASE + "/", wait_until="load", timeout=60000)
    time.sleep(3)
    sid = new_session(page)
    api_set_title(sid, f"[{cst_stamp('%Y%m%d-%H%M')}-domcap-re]")
    print(f"  session={sid}")

    turns = build_briefs(["conv-005"])[0]["user_turns"]
    plan_ok = spec_snapped = spec_confirmed = params_snapped = False
    captured = []

    for t in turns:
        ut = t["user_text"].strip()
        send_chat(page, ut)
        wait_textarea_enabled(page, timeout=150)
        time.sleep(2)
        bt = body_text(page)

        if not plan_ok and "批准方案" in bt:
            page.get_by_text("批准方案", exact=True).first.click(timeout=8000)
            plan_ok = True
            print("  approved plan")
            time.sleep(3)
            continue

        # RE spec panel ready -> snapshot BEFORE confirming.
        if plan_ok and not spec_snapped and _re_task_phase(sid)[0] == "collecting_spec":
            # Wait for the panel to render.
            for _ in range(30):
                bt2 = body_text(page)
                if any(k in bt2 for k in ["旋蒸参数预填", "旋蒸参数", "溶剂信息", "合并液"]):
                    break
                time.sleep(2)
            print("  re spec panel visible -> snapshot")
            captured.append(snapshot(page, "re_spec", sid, out_dir))
            spec_snapped = True
            print("  confirming RE spec (no submission)...")
            try:
                confirm_re_spec_via_ui(page, TALOS_BASE, sid)
                spec_confirmed = True
            except Exception as exc:
                print(f"  RE spec confirm failed: {exc}")
            time.sleep(3)
            continue

        if spec_confirmed and not params_snapped and _re_task_phase(sid)[0] == "collecting_params":
            # Wait for params panel.
            for _ in range(30):
                bt2 = body_text(page)
                if any(k in bt2 for k in ["添加茄形瓶", "茄形瓶", "水浴温度", "压力", "气压梯度"]):
                    break
                time.sleep(2)
            print("  re params panel visible -> snapshot")
            captured.append(snapshot(page, "re_params", sid, out_dir))
            params_snapped = True
            break

    return captured


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def write_summary(out_dir: Path, captured: list[dict]) -> None:
    lines = ["# Panel DOM Capture Summary", ""]
    lines.append(f"Captured: {datetime.now().isoformat()}")
    lines.append(f"Output dir: {out_dir}")
    lines.append("")
    for cap in captured:
        phase = cap["phase"]
        ctrls = cap.get("controls") or []
        in_panel = [c for c in ctrls if c.get("inActiveCard") or c.get("inModal")]
        editable = [c for c in in_panel
                    if not c.get("readonly") and not c.get("disabled")]
        lines.append(f"## {phase}")
        lines.append("")
        lines.append(f"- controls total: {len(ctrls)}; in active-card or modal: {len(in_panel)}; **editable: {len(editable)}**")
        lines.append("")
        if editable:
            lines.append("### Editable inputs in active panel / modal")
            lines.append("")
            lines.append("| tag | type | label | value | placeholder | aria-label | cssPath |")
            lines.append("|---|---|---|---|---|---|---|")
            for c in editable:
                lines.append("| {tag} | {type} | {label} | `{value}` | `{ph}` | {al} | `{path}` |".format(
                    tag=c.get("tag"), type=c.get("type"),
                    label=(c.get("label") or "").replace("|", "\\|").replace("\n", " "),
                    value=(c.get("value") or "").replace("|", "\\|"),
                    ph=(c.get("placeholder") or "").replace("|", "\\|"),
                    al=(c.get("ariaLabel") or "").replace("|", "\\|"),
                    path=(c.get("cssPath") or "").replace("|", "\\|"),
                ))
            lines.append("")
        else:
            lines.append("_(no editable inputs in active panel/modal — only click-driven controls)_")
            lines.append("")
        ro = [c for c in in_panel if c.get("readonly")]
        if ro:
            lines.append(f"### Readonly inputs in active panel / modal ({len(ro)})")
            lines.append("")
            for c in ro[:20]:
                lines.append(f"- {c.get('tag')} type={c.get('type')} value=`{c.get('value')}` label={c.get('label') or '—'}")
            lines.append("")
        active_btns = cap.get("active_buttons") or []
        modal_btns = cap.get("modal_buttons") or []
        if active_btns or modal_btns:
            lines.append(f"### Buttons (active card: {len(active_btns)}; modal: {len(modal_btns)})")
            lines.append("")
            for b in active_btns:
                lines.append(f"- [active] `{b.get('text')}`{' (disabled)' if b.get('disabled') else ''}")
            for b in modal_btns:
                lines.append(f"- [modal]  `{b.get('text')}`{' (disabled)' if b.get('disabled') else ''}")
            lines.append("")
        lines.append("")
    (out_dir / "summary.md").write_text("\n".join(lines))
    print(f"\n[summary] wrote {out_dir/'summary.md'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["cc", "re", "all"], default="all")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_dir = Path(args.out) if args.out else Path("eval_outputs") / f"panel_doms_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"output dir: {out_dir}")

    captured: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1680, "height": 1050})
            if args.only in ("cc", "all"):
                try:
                    captured += drive_cc_phases(page, out_dir)
                except Exception as exc:
                    import traceback; traceback.print_exc()
                    print(f"[cc] FAILED: {exc}")
                page.close()
                page = browser.new_page(viewport={"width": 1680, "height": 1050})
            if args.only in ("re", "all"):
                try:
                    captured += drive_re_phases(page, out_dir)
                except Exception as exc:
                    import traceback; traceback.print_exc()
                    print(f"[re] FAILED: {exc}")
        finally:
            browser.close()

    write_summary(out_dir, captured)
    print(f"done — {len(captured)} panels captured")


if __name__ == "__main__":
    main()
