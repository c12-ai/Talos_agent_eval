#!/usr/bin/env python3
"""
SAFE CC params-panel + slot-dialog DOM capture (no lab dispatch).

Drives conv-001 up through CC spec confirm + the dispatch-intent turn, then:
  1. snapshots every <button>/<select> and the panel containing 硅胶柱规格,
  2. finds the real 管理插槽 trigger (button whose text contains 插槽), clicks it,
  3. snapshots the opened slot dialog.
Never clicks 保存/确认/submit -> no lab run.

Outputs (all under /tmp/slotcap_):
  panel.html, buttons.json, selects.json, dialog.html, dialog_buttons.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright

from smoke_runner_20260518 import (
    TALOS_BASE, TLC_IMAGE, build_briefs, new_session, api_set_title,
    send_chat, wait_textarea_enabled, body_text, cst_stamp,
)
from talos_panel_ui import confirm_cc_spec_via_ui, _set_column_type_12g

SNAP_JS = r"""
() => {
  const vis = e => { const r=e.getBoundingClientRect();
    return r.width>0 && r.height>0; };
  const btns = Array.from(document.querySelectorAll(
      "button,[role='button'],[role='option'],[role='tab']")).map(e => ({
    tag:e.tagName, role:e.getAttribute('role'),
    text:(e.innerText||'').trim().replace(/\s+/g,' ').slice(0,60),
    cls:((e.className||'')+'').slice(0,140),
    disabled:!!e.disabled, vis:vis(e),
  }));
  const sels = Array.from(document.querySelectorAll('select')).map(s => ({
    cls:((s.className||'')+'').slice(0,120), vis:vis(s),
    options:Array.from(s.options).map(o=>({v:o.value,t:(o.textContent||'').trim()})),
    value:s.value,
  }));
  // panel containing 硅胶柱规格
  let panel=null;
  const lab=Array.from(document.querySelectorAll('*')).find(
      e=>(e.textContent||'').includes('硅胶柱规格'));
  if(lab){ let n=lab; for(let i=0;i<10&&n;i++){
      if(/panel|card|right|workflow|aside/i.test((n.className||'')+'')){panel=n;break;}
      n=n.parentElement;} if(!panel) panel=lab.closest('div'); }
  return {buttons:btns, selects:sels,
          panel: panel? panel.outerHTML.slice(0,40000):''};
}
"""

DIALOG_JS = r"""
() => {
  const a=Array.from(document.querySelectorAll('*')).find(
      e=>/管理样品柱插槽|样品柱插槽|插槽/.test((e.textContent||''))
         && (e.className||'').match(/dialog|modal|fixed|inset-0/i));
  let dlg=a;
  if(!dlg){ const t=Array.from(document.querySelectorAll('*')).find(
      e=>(e.textContent||'').includes('管理样品柱插槽'));
      dlg = t ? (t.closest("[class*='fixed']")||t.closest('div')) : null; }
  if(!dlg) return {found:false};
  const vis=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0;};
  const els=[];
  dlg.querySelectorAll('*').forEach(e=>{
    const t=(e.innerText||'').trim().replace(/\s+/g,' ').slice(0,50);
    const cls=((e.className||'')+'');
    if(e.tagName==='BUTTON'||e.getAttribute('role')==='button'
       ||/cursor-pointer|slot|card/i.test(cls)
       ||/备料架|样品柱|001|002|保存|确认|插槽/.test(t)){
      els.push({tag:e.tagName,role:e.getAttribute('role'),
        text:t, cls:cls.slice(0,150),
        ariaChecked:e.getAttribute('aria-checked'),
        dataState:e.getAttribute('data-state'),
        disabled:!!e.disabled, vis:vis(e)});
    }
  });
  return {found:true, outer:dlg.outerHTML.slice(0,40000), elements:els};
}
"""


def main():
    turns = build_briefs(["conv-001"])[0]["user_turns"]
    O = Path("/tmp")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1680, "height": 1050})
        page.goto(TALOS_BASE + "/", wait_until="load", timeout=60000)
        time.sleep(3)
        sid = new_session(page)
        api_set_title(sid, f"[{cst_stamp('%Y%m%d-%H%M')}-slotcap]")
        print(f"session={sid}")

        plan_ok = spec_ok = pg = False
        for t in turns:
            ut = t["user_text"].strip()
            if any(k in ut for k in ["SMILES", "上样", "mg", "Rf", "PE/EA", "PE:EA"]):
                pg = True
            send_chat(page, ut)
            wait_textarea_enabled(page, timeout=150)
            time.sleep(2)
            bt = body_text(page)
            if not plan_ok and "批准方案" in bt:
                page.get_by_text("批准方案", exact=True).first.click(timeout=8000)
                plan_ok = True; print("approved plan"); time.sleep(3); continue
            if plan_ok and not spec_ok and pg and any(
                    x in bt for x in ["点击打开识别面板", "过柱参数预填", "重新识别"]):
                confirm_cc_spec_via_ui(page, tlc_image=TLC_IMAGE, rf_value="0.35")
                spec_ok = True; print("spec confirmed"); time.sleep(2); continue
            if spec_ok and any(w in ut for w in ["下发", "就按", "开始", "提交"]):
                print("waiting for CC PARAMS panel to render (silica spec / 管理插槽)...")
                params_ready = False
                for i in range(45):  # up to ~90s
                    b2 = body_text(page)
                    sel_has_silica = page.evaluate(
                        """() => Array.from(document.querySelectorAll('select'))
                            .some(s => Array.from(s.options).some(o =>
                              /silica|12g|24g|40g|120g/i.test((o.value||'')+(o.textContent||''))))"""
                    )
                    if sel_has_silica or any(k in b2 for k in
                                             ["硅胶柱规格", "管理插槽", "样品柱", "管理样品柱插槽"]):
                        params_ready = True
                        print(f"  params panel detected after ~{i*2}s "
                              f"(silica_select={sel_has_silica})")
                        break
                    time.sleep(2)
                if not params_ready:
                    print("  WARNING: params panel never rendered within 90s")
                print("snapshotting CC params panel (no submit)...")
                time.sleep(2)
                snap = page.evaluate(SNAP_JS)
                (O / "slotcap_panel.html").write_text(snap.get("panel", "") or "")
                (O / "slotcap_buttons.json").write_text(
                    json.dumps(snap.get("buttons", []), ensure_ascii=False, indent=1))
                (O / "slotcap_selects.json").write_text(
                    json.dumps(snap.get("selects", []), ensure_ascii=False, indent=1))
                btns = snap.get("buttons", [])
                print(f"  buttons={len(btns)} selects={len(snap.get('selects') or [])}")
                # Enumerate ANY element (not just <button>) whose text mentions 插槽,
                # excluding the long sidebar history items.
                slotels = page.evaluate(
                    r"""() => { const out=[];
                      document.querySelectorAll('*').forEach(e=>{
                        const t=(e.innerText||'').trim().replace(/\s+/g,' ');
                        if(t.length<=12 && t.includes('插槽')){
                          const r=e.getBoundingClientRect();
                          out.push({tag:e.tagName, role:e.getAttribute('role'),
                            text:t, cls:((e.className||'')+'').slice(0,120),
                            vis:r.width>0&&r.height>0});
                        }});
                      return out; }"""
                )
                (O / "slotcap_slottrigger.json").write_text(
                    json.dumps(slotels, ensure_ascii=False, indent=1))
                print(f"  slot-text elements (<=12 chars): "
                      f"{[(e['tag'],e['text']) for e in slotels[:8]]}")
                opened = False
                for sttext in ["管理插槽", "管理样品柱插槽", "插槽"]:
                    try:
                        loc = page.get_by_text(sttext, exact=False).filter(
                            visible=True).first
                        if loc.count():
                            loc.click(timeout=5000)
                            opened = True
                            print(f"  clicked slot trigger by text {sttext!r}")
                            break
                    except Exception as e:
                        print(f"  click text {sttext!r} failed: {e}")
                for _ in range(15):
                    if "管理样品柱插槽" in body_text(page):
                        break
                    time.sleep(1)
                time.sleep(2)
                dlg = page.evaluate(DIALOG_JS)
                (O / "slotcap_dialog.html").write_text(dlg.get("outer", "") or "")
                (O / "slotcap_dialog_buttons.json").write_text(
                    json.dumps(dlg.get("elements", []), ensure_ascii=False, indent=1))
                print(f"  dialog found={dlg.get('found')} "
                      f"elements={len(dlg.get('elements') or [])}")
                break
        time.sleep(1)
        browser.close()
        print("done (no 保存/确认/submit; no lab run)")


if __name__ == "__main__":
    main()
