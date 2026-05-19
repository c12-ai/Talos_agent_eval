#!/usr/bin/env python3
"""
SAFE validation of the rewritten CC slot/cartridge/12g automation.

Drives conv-001 to the CC params panel, runs every step of submit_cc_via_ui
EXCEPT the final lab-submitting 确认修改, then asserts end-state:
  - silica spec select == silica_12g  (guide §4.3)
  - sample cartridge select == bic_09B_l4_002
  - slot dialog closed (no overlay)
No 确认修改/确认 click -> NO lab run is created.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright

from smoke_runner_20260518 import (
    TALOS_BASE, TLC_IMAGE, build_briefs, new_session, api_set_title,
    send_chat, wait_textarea_enabled, body_text, cst_stamp,
)
import talos_panel_ui as P


def main():
    turns = build_briefs(["conv-001"])[0]["user_turns"]
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        page = b.new_page(viewport={"width": 1680, "height": 1050})
        page.goto(TALOS_BASE + "/", wait_until="load", timeout=60000)
        time.sleep(3)
        sid = new_session(page)
        api_set_title(sid, f"[{cst_stamp('%Y%m%d-%H%M')}-slotfix-validate]")
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
                P.confirm_cc_spec_via_ui(page, tlc_image=TLC_IMAGE, rf_value="0.35")
                spec_ok = True; print("spec confirmed"); time.sleep(2); continue
            if spec_ok and any(w in ut for w in ["下发", "就按", "开始", "提交"]):
                # wait for params panel
                for _ in range(45):
                    if page.evaluate(
                        "() => Array.from(document.querySelectorAll('select'))"
                        ".some(s=>Array.from(s.options).some(o=>"
                        "/silica_12g|silica_24g/.test(o.value)))"
                    ):
                        break
                    time.sleep(2)
                print(">>> running slot/cartridge/12g steps (NO final confirm)")
                P._set_column_type_12g(page)
                try:
                    page.get_by_role("button", name="管理插槽").first.click(timeout=20000)
                except Exception:
                    P._click_first_visible_text(page, ["管理插槽"], timeout=15)
                P._wait_until(lambda: "管理样品柱插槽" in body_text(page),
                              timeout=12, description="slot dialog")
                P._ensure_slot_installed(page, "备料架L4层样品柱002位")
                P._close_slot_dialog(page)
                P._select_cartridge(page, "bic_09B_l4_002")
                P._set_column_type_12g(page)
                time.sleep(1.5)

                # ---- assertions (no confirm clicked) ----
                state = page.evaluate(
                    """() => {
                      const sv=Array.from(document.querySelectorAll('select')).map(s=>s.value);
                      const overlay=!!document.querySelector('div.fixed.inset-0.z-50');
                      return {selectValues:sv, overlay};
                    }"""
                )
                sv = state["selectValues"]
                silica_ok = "silica_12g" in sv
                cart_ok = "bic_09B_l4_002" in sv
                dialog_closed = not state["overlay"]
                print(f"select values: {sv}")
                print(f"[CHECK] silica==silica_12g : {silica_ok}")
                print(f"[CHECK] cartridge==bic_09B_l4_002 : {cart_ok}")
                print(f"[CHECK] slot dialog closed : {dialog_closed}")
                ok = silica_ok and cart_ok and dialog_closed
                print(f"RESULT: {'PASS' if ok else 'FAIL'} "
                      f"(no 确认修改 clicked; no lab run created)")
                break
        time.sleep(1)
        b.close()


if __name__ == "__main__":
    main()
