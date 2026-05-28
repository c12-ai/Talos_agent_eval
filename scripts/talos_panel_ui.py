"""
TALOS panel UI automation via Playwright.

Adopts reliable DOM patterns from talos_cc_frontend_runner.py.
"""

import time
from pathlib import Path
from typing import Iterable


# ---------------------------------------------------------------------------
# Generic helpers (from talos_cc_frontend_runner.py)
# ---------------------------------------------------------------------------

def _body_text(page) -> str:
    try:
        return page.locator("body").inner_text(timeout=2000)
    except Exception:
        return ""


def _wait_until(predicate, timeout: float, interval: float = 0.5, description: str = "condition"):
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except Exception as exc:
            last_error = exc
        time.sleep(interval)
    if last_error:
        raise TimeoutError(f"timed out waiting for {description}: {last_error}")
    raise TimeoutError(f"timed out waiting for {description}")


def _click_first_visible_text(page, texts: Iterable[str], timeout: float = 20) -> str:
    def find_and_click():
        for text in texts:
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

    clicked = _wait_until(find_and_click, timeout=timeout, description=f"visible text button {list(texts)}")
    print(f"[UI] clicked: {clicked}")
    time.sleep(1)
    return clicked


def _click_confirm_any(page) -> bool:
    """Click any confirm/submit button visible in the panel."""
    for text in ["确认修改", "确认", "下发", "提交", "保存"]:
        try:
            btn = page.locator(f"button:has-text('{text}')").first
            if btn.count() and btn.is_visible(timeout=500):
                btn.click()
                time.sleep(1.5)
                return True
        except Exception:
            continue
    return False


# ---------------------------------------------------------------------------
# Slot management (CC)
# ---------------------------------------------------------------------------

def _slot_installed(slot) -> bool:
    """True if the slot <button> shows installed state.

    Real DOM (captured 2026-05-18): an installed slot button carries
    `border-emerald-400 bg-emerald-50`; an empty slot carries
    `border-slate-200 bg-slate-50 border-dashed`. The marker is on the
    <button> itself (slot text is a <span> child), so scan a few ancestors.
    """
    try:
        return bool(slot.evaluate(
            """el => { let n=el;
                for (let i=0;i<5 && n;i++){
                  const c=((n.className||'')+'');
                  if (/border-emerald|bg-emerald/.test(c)) return true;
                  n=n.parentElement; }
                return false; }"""
        ))
    except Exception:
        return False


def _close_slot_dialog(page) -> str:
    """Close the slot dialog correctly. `保存` is DISABLED (class
    `cursor-not-allowed`, no `disabled` attr) when no slot change was made,
    so clicking it blindly hangs. Click `保存` only when enabled, else `取消`.
    """
    try:
        save = page.locator("div.fixed.inset-0 button:has-text('保存')").first
        if save.count() and save.is_visible(timeout=800):
            cls = (save.get_attribute("class") or "")
            if "cursor-not-allowed" not in cls and "text-slate-400" not in cls:
                save.click(timeout=4000)
                print("[UI] slot dialog: 保存 (enabled)")
                time.sleep(1)
                return "saved"
    except Exception as e:
        print(f"[UI] 保存 check failed: {e}")
    for txt in ["取消"]:
        try:
            b = page.locator(f"div.fixed.inset-0 button:has-text('{txt}')").first
            if b.count() and b.is_visible(timeout=800):
                b.click(timeout=4000)
                print(f"[UI] slot dialog: {txt} (no change, save disabled)")
                time.sleep(1)
                return "cancelled"
        except Exception:
            continue
    try:
        page.keyboard.press("Escape")
        time.sleep(0.5)
    except Exception:
        pass
    return "escaped"


def _dump_slot_dialog(page):
    try:
        anchor = page.get_by_text("管理样品柱插槽", exact=False).first
        html = anchor.evaluate(
            """el => { let n=el;
                for (let i=0;i<8 && n;i++){
                  if (/dialog|modal|panel|fixed|inset-0/i.test((n.className||'')+'')) return n.outerHTML;
                  n=n.parentElement; }
                return (el.closest('div')||el).outerHTML; }"""
        )
        Path("/tmp/slot_dialog_dom.html").write_text(html or "")
        print(f"[UI] dumped slot dialog DOM ({len(html or '')} chars) -> /tmp/slot_dialog_dom.html")
    except Exception as e:
        print(f"[UI] slot dialog DOM dump failed: {e}")


def _ensure_slot_installed(page, slot_label: str = "备料架L4层样品柱002位") -> bool:
    """Idempotent + toggle-safe: never re-click an already-installed slot
    (guide §4.2 — re-click can UNINSTALL it). Verify the click actually
    flipped state before declaring success."""
    _dump_slot_dialog(page)  # keep DOM snapshot for diagnostics
    # The slot is a real <button> (text is a <span> child). Target the button.
    slot = page.locator("div.fixed.inset-0 button", has_text=slot_label).first
    try:
        slot.wait_for(state="visible", timeout=12000)
    except Exception:
        print(f"[UI] Slot button '{slot_label}' not found in dialog")
        return False

    if _slot_installed(slot):
        # Clicking toggles install<->remove (dialog subtitle: 点击插槽切换安装/移除).
        # Already installed + persisted in lab backend -> DO NOT click.
        print(f"[UI] Slot '{slot_label}' already installed (emerald) -> no click (toggle-safe)")
        return True

    print(f"[UI] Slot '{slot_label}' empty -> clicking to install once")
    try:
        slot.scroll_into_view_if_needed(timeout=3000)
        slot.click(timeout=8000)
        time.sleep(1.2)
    except Exception as e:
        print(f"[UI] slot install click failed: {e}")
        return False
    ok = _slot_installed(slot)
    print(f"[UI] slot '{slot_label}' install {'confirmed' if ok else 'NOT confirmed'}")
    return ok


def _select_cartridge(page, slot_id: str = "bic_09B_l4_002") -> bool:
    """Select cartridge from dropdown. 3-layer fallback as in working script."""
    # Layer 1: native select
    selects = page.locator("select")
    try:
        for i in range(selects.count()):
            sel = selects.nth(i)
            if not sel.is_visible(timeout=300):
                continue
            options = sel.locator("option").evaluate_all(
                """opts => opts.map(o => ({value: o.value, text: o.textContent || ''}))"""
            )
            for opt in options:
                if slot_id in opt["text"] or slot_id in opt["value"]:
                    sel.select_option(opt["value"])
                    print(f"[UI] Selected cartridge via native select: {opt['text']}")
                    time.sleep(1)
                    return True
    except Exception:
        pass

    # Layer 2: custom combobox
    comboboxes = page.locator("[role='combobox']")
    try:
        for i in range(comboboxes.count()):
            combo = comboboxes.nth(i)
            if not combo.is_visible(timeout=300):
                continue
            combo.click()
            time.sleep(0.5)
            option = page.get_by_text(slot_id, exact=False).first
            if option.is_visible(timeout=1000):
                option.click()
                print("[UI] Selected cartridge via combobox")
                time.sleep(1)
                return True
    except Exception:
        pass

    # Layer 3: click existing text
    try:
        el = page.get_by_text(slot_id, exact=False).first
        if el.is_visible(timeout=1000):
            el.click()
            print("[UI] Clicked existing cartridge text")
            time.sleep(1)
            return True
    except Exception:
        pass

    print(f"[UI] Could not select cartridge for {slot_id}")
    return False


def _set_column_type_12g(page) -> bool:
    """Force silica column spec to 12g (guide §4.3 — lab only has silica_12g).

    Must operate the ACTUAL 硅胶柱规格 control. The previous heuristic returned
    early whenever '12g' appeared anywhere on the page (it shows up in the
    agent's inventory chat), so the 24g recommendation reached the lab and
    failed with "No unused silica cartridge available for spec 'silica_24g'".
    """
    # Strategy A: any native <select> exposing a 12g option.
    try:
        sels = page.locator("select")
        for i in range(sels.count()):
            sel = sels.nth(i)
            if not sel.is_visible(timeout=300):
                continue
            opts = sel.locator("option").evaluate_all(
                "os => os.map(o => ({v:o.value, t:(o.textContent||'')}))"
            )
            for o in opts:
                if "12g" in o["t"] or "silica_12g" in o["v"]:
                    sel.select_option(o["v"])
                    time.sleep(0.8)
                    print("[UI] column spec -> 12g (native select)")
                    return True
    except Exception:
        pass

    # Strategy B: scoped to the 硅胶柱规格 control container — open its trigger
    # (showing current 24g/40g) then click the 12g option inside the popover.
    try:
        lab = page.locator(
            "xpath=//*[contains(normalize-space(.),'硅胶柱规格')][not(.//*[contains(text(),'硅胶柱规格')])]"
        ).first
        if lab.count():
            container = lab.locator("xpath=ancestor::*[self::div or self::section][1]")
            for trig in ["24g", "40g", "请选择", "硅胶柱规格"]:
                try:
                    t = container.locator(
                        f"button:has-text('{trig}'), [role='combobox']:has-text('{trig}')"
                    ).first
                    if t.count() and t.is_visible(timeout=400):
                        t.click()
                        time.sleep(0.5)
                        break
                except Exception:
                    continue
            for opt_sel in ["[role='option']", "li", "button", "[role='menuitem']", "div"]:
                try:
                    o = page.locator(f"{opt_sel}").filter(has_text="12g").first
                    if o.count() and o.is_visible(timeout=400):
                        o.click(timeout=2000)
                        time.sleep(0.8)
                        print(f"[UI] column spec -> 12g (scoped popover {opt_sel})")
                        return True
                except Exception:
                    continue
    except Exception:
        pass

    # Strategy C: a typed control (button/option/radio/label) whose text is 12g
    # — explicitly NOT plain chat text.
    for sel in ["button:has-text('12g')", "[role='option']:has-text('12g')",
                "[role='radio']:has-text('12g')", "label:has-text('12g')"]:
        try:
            el = page.locator(sel).first
            if el.count() and el.is_visible(timeout=400):
                el.click(timeout=2000)
                time.sleep(0.6)
                print(f"[UI] column spec -> 12g ({sel})")
                return True
        except Exception:
            continue

    print("[UI] WARNING: could NOT enforce 12g column spec (run will likely fail)")
    return False


# ---------------------------------------------------------------------------
# CC submit
# ---------------------------------------------------------------------------

def submit_cc_via_ui(page, slot_label: str = "备料架L4层样品柱002位", slot_id: str = "bic_09B_l4_002") -> bool:
    """
    Full CC submission via UI:
    1. Set column to 12g
    2. Open slot manager, install cartridge idempotently, save
    3. Select cartridge from dropdown
    4. Set 12g again (defensive)
    5. Click confirm
    """
    print("[UI] submit_cc_via_ui: starting...")
    _set_column_type_12g(page)

    # Open slot management (real DOM: <button>管理插槽</button>, blue-bordered)
    try:
        page.get_by_role("button", name="管理插槽").first.click(timeout=20000)
    except Exception:
        _click_first_visible_text(page, ["管理插槽"], timeout=15)
    _wait_until(lambda: "管理样品柱插槽" in _body_text(page), timeout=12,
                description="slot management dialog")

    _ensure_slot_installed(page, slot_label)
    # `保存` is disabled when no change was made -> close correctly, never hang.
    _close_slot_dialog(page)

    _select_cartridge(page, slot_id)

    _set_column_type_12g(page)  # defensive re-assert (guide §4.3)
    ok = _click_confirm_any(page)
    print(f"[UI] submit_cc_via_ui: done, confirm clicked={ok}")
    return ok


# ---------------------------------------------------------------------------
# CC TLC spec confirmation
# ---------------------------------------------------------------------------

def confirm_cc_spec_via_ui(page, tlc_image: str = "/Users/wuwenyan/Desktop/demo.jpeg",
                           rf_value: str = "0.35",
                           base_url: str = "", session_id: str = "") -> bool:
    """Confirm CC spec panel; backend must advance cc_agent.phase from
    `collecting_spec` to `collecting_params`.

    Stable implementation: delegates to talos_cc_frontend_runner's
    `upload_tlc_and_confirm_spec`, which uses **scoped selectors** (modal-
    confined TLC dialog confirm + active step-card 确认修改 button) instead
    of the page-level `_click_confirm_any`. The old approach silently
    clicked stale/wrong 确认 buttons, leaving backend at `collecting_spec`
    and downstream CC submit gates never firing.

    When base_url/session_id are provided, additionally polls workflow-
    state to verify cc_agent advanced; on miss, retries the scoped confirm
    once.
    """
    from talos_cc_frontend_runner import upload_tlc_and_confirm_spec, get_workflow_state
    print(f"[UI] confirm_cc_spec_via_ui: tlc={tlc_image} rf={rf_value} session={session_id or '(no-verify)'}")
    try:
        upload_tlc_and_confirm_spec(page, tlc_image, rf_value)
    except Exception as exc:
        print(f"[UI] confirm_cc_spec_via_ui: upload_tlc_and_confirm_spec ERROR — {exc}")
        return False

    if not (base_url and session_id):
        # Caller did not supply session info; rely on baseline's own waits.
        print("[UI] confirm_cc_spec_via_ui: done (no API verify)")
        return True

    # Verify backend actually advanced. Retry the scoped confirm once if not.
    from talos_cc_frontend_runner import click_cc_spec_confirm
    deadline = time.time() + 120
    retried = False
    while time.time() < deadline:
        st = get_workflow_state(base_url, session_id)
        for t in (st.get("tasks") or []):
            if "cc" in (t.get("task_type") or ""):
                if t.get("phase") in ("collecting_params", "conducting", "done"):
                    print(f"[UI] confirm_cc_spec_via_ui: OK (cc_agent.phase={t.get('phase')})")
                    return True
                break
        if not retried and time.time() - (deadline - 120) > 20:
            try:
                click_cc_spec_confirm(page)
                print("[UI] confirm_cc_spec_via_ui: retried scoped confirm once")
                retried = True
            except Exception as exc:
                print(f"[UI] confirm_cc_spec_via_ui: retry click failed — {exc}")
        time.sleep(3)
    print("[UI] confirm_cc_spec_via_ui: TIMEOUT — cc_agent stayed at collecting_spec")
    return False


# ---------------------------------------------------------------------------
# RE submit (with bug fixes from previous session)
# ---------------------------------------------------------------------------

def _find_flask_chip(page):
    try:
        return page.get_by_text("瓶 1", exact=True).first
    except Exception:
        return None


def _is_chip_selected(chip) -> bool:
    try:
        cls = chip.evaluate("el => el.className")
        return bool(cls and ("ring" in cls.lower() or "shadow" in cls.lower()))
    except Exception:
        return False


def _find_tube_buttons(page, count=5):
    tubes = []
    for i in range(1, count + 1):
        try:
            tube = page.get_by_text(str(i), exact=True).first
            if tube.is_visible():
                tubes.append(tube)
        except Exception:
            continue
    return tubes


def _click_tube_and_verify(tube, tube_num: int) -> bool:
    before = tube.evaluate("el => el.className") or ""
    tube.click()
    time.sleep(0.3)
    after = tube.evaluate("el => el.className") or ""
    if before == after:
        print(f"[UI] WARNING: Tube {tube_num} className did NOT change, retrying...")
        tube.click()
        time.sleep(0.3)
        after = tube.evaluate("el => el.className") or ""
    return before != after


def submit_re_via_ui(page) -> bool:
    """
    Full RE submission via UI clicks:
    1. Click '+ 添加茄形瓶' to add flask
    2. Click '瓶 1' chip and verify selection
    3. Click tubes 1-5 and verify each changes color
    4. Click confirm
    """
    print("[UI] submit_re_via_ui: starting...")

    # Add flask
    try:
        add_btn = page.get_by_text("+ 添加茄形瓶", exact=False).first
        # 2s→8s: on high-latency hosts the button's first render misses a 2s
        # window, and a missed click silently skips flask-add (downstream
        # '瓶 1' wait then times out).
        if add_btn.count() and add_btn.is_visible(timeout=8000):
            add_btn.click()
            time.sleep(1.5)
            print("[UI] Clicked '+ 添加茄形瓶'")
    except Exception as e:
        print(f"[UI] Error adding flask: {e}")

    # Select '瓶 1' chip
    chip = _find_flask_chip(page)
    if chip is None:
        print("[UI] ERROR: '瓶 1' chip not found!")
        return False

    chip.click()
    time.sleep(0.5)
    if not _is_chip_selected(chip):
        chip.click()
        time.sleep(0.5)
        if not _is_chip_selected(chip):
            print("[UI] ERROR: '瓶 1' chip not selected after retry!")
            return False
    print("[UI] '瓶 1' chip selected and verified")

    # Paint tubes 1-5
    tubes = _find_tube_buttons(page, count=5)
    if not tubes:
        print("[UI] ERROR: No tube buttons found!")
        return False

    painted = sum(1 for i, tube in enumerate(tubes) if _click_tube_and_verify(tube, i + 1))
    print(f"[UI] Painted {painted}/{len(tubes)} tubes")

    time.sleep(0.5)
    ok = _click_confirm_any(page)
    print(f"[UI] submit_re_via_ui: done, confirm clicked={ok}")
    return ok


# ---------------------------------------------------------------------------
# RE spec confirmation
# ---------------------------------------------------------------------------

def _re_final_panel_visible(page) -> bool:
    """The '+ 添加茄形瓶' control is a real button that appears only after
    the backend advances to the 添加茄形瓶 step. Substring scans against
    ``body.innerText`` falsely match the timeline's pending step titles
    ("添加茄形瓶" as a label), so check for the button itself."""
    for sel in ("button:has-text('+ 添加茄形瓶')",
                "[role='button']:has-text('+ 添加茄形瓶')"):
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible(timeout=400):
                return True
        except Exception:
            continue
    return False


# ---------------------------------------------------------------------------
# RE spec direct UI fill (deterministic; bypasses chat / agent admittance)
#
# Confirmed via 2026-05-21 panel-DOM capture (eval_outputs/panel_doms_*/):
#   - 溶剂体积 (mL): a single `<input type="number" min="2">` inside the
#     "溶剂信息" card, identified by sibling `<label>` text.
#   - Solvent table: each row has a `<select>` (solvent) + `<input type="number">`
#     (ratio); add new rows by clicking the `+ 添加溶剂` button inside the card.
#
# We mutate values via the native HTMLInputElement/HTMLSelectElement `value`
# setters and dispatch input+change events — React-controlled inputs miss
# updates if we just assign `.value` directly.
# ---------------------------------------------------------------------------

_RE_SPEC_DIAG_JS = r"""
() => {
  const activeCard = document.querySelector('.step-card-active');
  if (!activeCard) {
    return {has_step_card_active: false, number_inputs: []};
  }
  const inputs = Array.from(activeCard.querySelectorAll('input[type=number]'));
  return {
    has_step_card_active: true,
    number_inputs: inputs.map(i => {
      const r = i.getBoundingClientRect();
      return {
        min: i.min, max: i.max, step: i.step, value: i.value,
        placeholder: i.placeholder,
        label: (i.closest('.field-v6')?.querySelector('label')?.textContent || '').trim().slice(0, 40),
        visible: r.width > 0 && r.height > 0,
      };
    }),
  };
}
"""


def _count_re_spec_solvent_rows(page) -> int:
    return page.evaluate(r"""() => {
      const titles = Array.from(document.querySelectorAll('.card-section-title'));
      const title = titles.find(e => /溶剂信息/.test((e.textContent || '').trim()));
      const card = title && (title.closest('.card-section') || title.parentElement);
      if (!card) return -1;
      const table = card.querySelector('table');
      if (!table) return -1;
      return Array.from(table.querySelectorAll('tbody tr')).filter(
        tr => !(tr.textContent || '').includes('暂无溶剂数据')).length;
    }""")


def _fill_via_keystrokes(page, locator, value, *, label_for_log: str) -> bool:
    """Fill a React-controlled input by simulating real user keystrokes.

    Why not native JS setter: React tracks last-seen input value via an
    internal `_valueTracker`. If you set `.value` via the native
    Object.getOwnPropertyDescriptor setter and dispatch an 'input' event,
    React's tracker sees the value didn't "change from its perspective"
    (because the tracker holds whatever it last committed, not the DOM's
    current value) and skips onChange — so the backend never gets the
    update. Conv-008 / 2026-05-21 hit exactly this: volume_value='288' in
    the DOM but `spec.volume_ml` stayed null.

    Playwright `.type()` simulates real keypress events, which React's
    SyntheticEvent layer picks up correctly. We also tab/blur afterwards
    to flush any onBlur handlers."""
    try:
        locator.wait_for(state="visible", timeout=8000)
        locator.click()
        time.sleep(0.15)
        # Select-all, then type — typing over a selection replaces it
        # (browser-native behavior). For number inputs this is more reliable
        # than press('Delete'), which deletes one char to the right.
        locator.press("ControlOrMeta+a")
        time.sleep(0.05)
        locator.type(str(value), delay=20)
        time.sleep(0.2)
        # Blur to commit any onBlur-only handlers.
        page.keyboard.press("Tab")
        time.sleep(0.3)
        actual = locator.input_value()
        print(f"[UI] {label_for_log} typed: '{actual}' (target {value})")
        return True
    except Exception as exc:
        print(f"[UI] {label_for_log} type failed: {exc}")
        return False


def fill_re_spec_via_ui(page, *, volume_ml=None, solvents=None, ratios=None) -> dict:
    """Directly fill missing RE spec fields via panel inputs, bypassing chat
    and agent admittance.

    Uses Playwright `.type()` (real keystrokes) — NOT native JS setter — so
    React's internal value tracker sees the change and the frontend fires
    onChange → backend spec actually updates. See `_fill_via_keystrokes`
    for the why.

    Args:
      volume_ml: float | None — if set, fill the 溶剂体积 (mL) input.
      solvents: list[str] | None — e.g. ["PE", "EA"]. Adds rows via
        `+ 添加溶剂` if needed, then fills each row's solvent select.
      ratios: list[float] | None — same length as `solvents`; fills each
        row's ratio input.

    The caller is responsible for clicking the panel `确认` button afterwards
    (usually via `confirm_re_spec_via_ui`).
    """
    print(f"[UI] fill_re_spec_via_ui: volume_ml={volume_ml} "
          f"solvents={solvents} ratios={ratios}")

    result = {"ok": True, "errors": [], "diag": {}, "volume_filled": False,
              "rows_filled": 0}

    # Wait for the RE spec panel to actually render before touching DOM.
    try:
        page.locator(".step-card-active").first.wait_for(
            state="visible", timeout=15000)
    except Exception:
        print("[UI] fill_re_spec_via_ui: WARNING — no .step-card-active visible after 15s; "
              "proceeding anyway")

    # ---- Add solvent rows if needed (Playwright clicks) ----------------
    if solvents:
        existing = _count_re_spec_solvent_rows(page)
        if existing < 0:
            print("[UI] fill_re_spec_via_ui: 溶剂信息 card / table not found")
            result["errors"].append("溶剂信息 card not found")
            result["ok"] = False
        else:
            needed = max(0, len(solvents) - existing)
            if needed:
                print(f"[UI] fill_re_spec_via_ui: adding {needed} solvent row(s) "
                      f"(existing={existing}, target={len(solvents)})")
                for _ in range(needed):
                    try:
                        btn = page.locator(
                            ".step-card-active button:has-text('+ 添加溶剂')").first
                        btn.click(timeout=3000)
                        time.sleep(0.4)
                    except Exception as exc:
                        print(f"[UI] + 添加溶剂 click failed: {exc}")
                        result["errors"].append(f"+ 添加溶剂 click failed: {exc}")
                        result["ok"] = False
                        return result

    # ---- Fill volume_ml via real keystrokes ----------------------------
    if volume_ml is not None and result["ok"]:
        # Selector strategy: min="2" is unique to the volume input in the
        # current RE spec panel (ratio inputs in solvent rows use min="1");
        # confirmed via 2026-05-21 DOM captures for empty / no_volume / full
        # states. Fall back to label-anchored locator if that misses.
        vol_loc = page.locator(
            ".step-card-active input[type='number'][min='2']").first
        try:
            vol_loc.wait_for(state="visible", timeout=5000)
            found_by = "min=2 selector"
        except Exception:
            # Fallback: label whose text mentions 体积, then its sibling input.
            vol_loc = page.locator(
                ".step-card-active label:has-text('体积')").first.locator(
                "xpath=following-sibling::input[@type='number'][1]")
            try:
                vol_loc.wait_for(state="visible", timeout=5000)
                found_by = "label[体积] + sibling input"
            except Exception:
                # Final dump: caller can diagnose from the active card's
                # number inputs.
                diag = page.evaluate(_RE_SPEC_DIAG_JS)
                result["diag"].update(diag)
                result["errors"].append(
                    "volume input not found (min=2 + label fallback both failed)")
                result["ok"] = False
                print(f"[UI] volume input not found; diag={diag}")
                return result
        if _fill_via_keystrokes(page, vol_loc, volume_ml, label_for_log="溶剂体积 (mL)"):
            actual = vol_loc.input_value()
            result["volume_filled"] = True
            result["volume_value"] = actual
            result["diag"]["volume_found_by"] = found_by
        else:
            result["errors"].append("volume keystroke fill threw exception")
            result["ok"] = False

    # ---- Fill solvent rows via Playwright (select + keystrokes) --------
    if solvents and ratios and result["ok"]:
        rows_loc = page.locator(
            ".step-card-active table tbody tr.border-b").filter(
            has_not_text="暂无溶剂数据")
        row_count = rows_loc.count()
        n = min(len(solvents), row_count)
        for i in range(n):
            row = rows_loc.nth(i)
            try:
                row.locator("select").first.select_option(solvents[i])
                time.sleep(0.2)
            except Exception as exc:
                result["errors"].append(f"row {i} select_option({solvents[i]}) failed: {exc}")
                continue
            ratio_loc = row.locator("input[type='number']").first
            if _fill_via_keystrokes(page, ratio_loc, ratios[i],
                                     label_for_log=f"row{i} ratio"):
                result["rows_filled"] += 1
            else:
                result["errors"].append(f"row {i} ratio keystroke fill failed")
        if result["rows_filled"] < len(solvents):
            print(f"[UI] WARN: filled {result['rows_filled']}/{len(solvents)} rows")

    result["ok"] = not result.get("errors")
    print(f"[UI] fill_re_spec_via_ui: result={result}")
    # Brief pause for backend to receive the React-triggered onChange.
    time.sleep(1.5)
    return result


def confirm_re_spec_via_ui(page, base_url: str, session_id: str) -> bool:
    """Confirm RE spec panel; backend must advance from collecting_spec to
    collecting_params.

    Stable implementation: delegates to talos_re_frontend_runner.confirm_re_spec
    which is workflow-state-driven (polls API for phase=='collecting_params'
    instead of relying on DOM keyword matches). It performs a scoped click of
    the active step-card's 确认 button, retries up to ~3 times, and falls back
    to a visible chat confirmation if clicks fail to advance backend state.

    Precondition: re_agent must already be at collecting_spec (i.e. the user
    has sent enough chat to make the agent generate RE recommendations).
    The smoke_runner is responsible for sending an articulated RE prompt
    before calling this, if brief turns alone don't push re_agent past
    not_started.
    """
    from talos_re_frontend_runner import confirm_re_spec as _api_confirm_re_spec
    print(f"[UI] confirm_re_spec_via_ui: session={session_id}")
    try:
        _api_confirm_re_spec(page, base_url, session_id)
        print("[UI] confirm_re_spec_via_ui: OK (collecting_params reached)")
        return True
    except TimeoutError as exc:
        print(f"[UI] confirm_re_spec_via_ui: TIMEOUT — {exc}")
        return False
    except Exception as exc:
        print(f"[UI] confirm_re_spec_via_ui: ERROR — {exc}")
        return False


def submit_re_params_via_ui(page, temperature_c: str = "35",
                            duration_min: str = "1",
                            tube_count: int = 5) -> bool:
    """Submit the RE final-params panel (add flask, paint tubes, click
    confirm). Delegates to talos_re_frontend_runner.submit_re_params, which
    is the proven sequence used in standalone RE runs.

    Precondition: backend at collecting_params (i.e. confirm_re_spec_via_ui
    has succeeded).

    Why the pre-ensure step: baseline's add_flask_and_paint_tubes uses
    `is_visible(timeout=3000)` on the `+ 添加茄形瓶` button, which on
    slower hosts misses the button's first render window. If the click is
    skipped, the subsequent 20s wait for `瓶 1` times out. We pre-wait up
    to 60s for the add button, click it ourselves, then verify `瓶 1`
    actually rendered before delegating — baseline picks up from there.
    """
    from talos_re_frontend_runner import submit_re_params as _api_submit_re
    print(f"[UI] submit_re_params_via_ui: t={temperature_c}°C dur={duration_min}min tubes=1-{tube_count}")

    # Pre-ensure: wait for + 添加茄形瓶 to render, then click it ourselves.
    try:
        add_btn = page.locator("button:has-text('+ 添加茄形瓶')").first
        add_btn.wait_for(state="visible", timeout=60000)
        # Re-check 瓶 1 may already exist (flask added by previous attempt);
        # only click add if there's no 瓶 1 yet.
        bottle = page.get_by_text("瓶 1", exact=True).first
        if bottle.count() == 0 or not bottle.is_visible(timeout=500):
            add_btn.click()
            print("[UI] submit_re_params_via_ui: clicked '+ 添加茄形瓶'")
            time.sleep(1.5)
        else:
            print("[UI] submit_re_params_via_ui: '瓶 1' already exists, skipping add click")
        # Verify 瓶 1 rendered before delegating to baseline.
        page.get_by_text("瓶 1", exact=True).first.wait_for(state="visible", timeout=20000)
        print("[UI] submit_re_params_via_ui: '瓶 1' visible — delegating to baseline")
    except Exception as exc:
        print(f"[UI] submit_re_params_via_ui: pre-ensure ERROR — {exc}")
        return False

    try:
        _api_submit_re(page, temperature_c, duration_min, tube_count)
        print("[UI] submit_re_params_via_ui: OK (confirm clicked)")
        return True
    except Exception as exc:
        print(f"[UI] submit_re_params_via_ui: ERROR — {exc}")
        return False
