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
        if add_btn.count() and add_btn.is_visible(timeout=2000):
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
    has succeeded). The function will wait briefly for the params panel
    keywords to render before acting.
    """
    from talos_re_frontend_runner import submit_re_params as _api_submit_re
    print(f"[UI] submit_re_params_via_ui: t={temperature_c}°C dur={duration_min}min tubes=1-{tube_count}")
    try:
        _api_submit_re(page, temperature_c, duration_min, tube_count)
        print("[UI] submit_re_params_via_ui: OK (confirm clicked)")
        return True
    except Exception as exc:
        print(f"[UI] submit_re_params_via_ui: ERROR — {exc}")
        return False
