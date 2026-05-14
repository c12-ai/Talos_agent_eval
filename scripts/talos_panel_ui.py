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

def _ensure_slot_installed(page, slot_label: str = "备料架L4层样品柱002位") -> bool:
    """Idempotent: install cartridge in the specified slot if not already installed."""
    slot = page.get_by_text(slot_label, exact=False).first
    try:
        slot.wait_for(state="visible", timeout=10000)
    except Exception:
        print(f"[UI] Slot '{slot_label}' not found")
        return False

    class_name = slot.evaluate("el => el.className || ''")
    if "border-emerald" in class_name or "bg-emerald-50" in class_name:
        print(f"[UI] Slot '{slot_label}' already installed, skipping")
        return True

    print(f"[UI] Installing cartridge in '{slot_label}'")
    slot.click()
    time.sleep(1)
    return True


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


def _set_column_type_12g(page) -> None:
    """Force silica column spec to 12g."""
    text = _body_text(page)
    if "12g" in text and "硅胶柱规格" in text:
        try:
            page.get_by_text("12g", exact=True).first.click(timeout=1000)
        except Exception:
            pass
        return

    # Try native select
    try:
        sel = page.locator("select").first
        if sel.count() and sel.is_visible(timeout=500):
            options = sel.locator("option").evaluate_all(
                """opts => opts.map(o => ({value: o.value, text: o.textContent || ''}))"""
            )
            for opt in options:
                if "12g" in opt["text"] or "silica_12g" in opt["value"]:
                    sel.select_option(opt["value"])
                    print("[UI] Set column type 12g via select")
                    time.sleep(1)
                    return
    except Exception:
        pass

    # Try button/popover
    for selector in ["button:has-text('24g')", "button:has-text('40g')", "button:has-text('硅胶柱规格')"]:
        try:
            el = page.locator(selector).first
            if el.count() and el.is_visible(timeout=500):
                el.click()
                time.sleep(0.5)
                page.get_by_text("12g", exact=True).first.click(timeout=2000)
                print("[UI] Set column type 12g via popover")
                time.sleep(1)
                return
        except Exception:
            continue
    print("[UI] Warning: could not explicitly set 12g")


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

    # Open slot management
    _click_first_visible_text(page, ["管理插槽"], timeout=30)
    _wait_until(lambda: "管理样品柱插槽" in _body_text(page), timeout=10, description="slot management dialog")

    _ensure_slot_installed(page, slot_label)
    _click_first_visible_text(page, ["保存"], timeout=10)

    _select_cartridge(page, slot_id)

    _set_column_type_12g(page)
    ok = _click_confirm_any(page)
    print(f"[UI] submit_cc_via_ui: done, confirm clicked={ok}")
    return ok


# ---------------------------------------------------------------------------
# CC TLC spec confirmation
# ---------------------------------------------------------------------------

def confirm_cc_spec_via_ui(page, tlc_image: str = "/Users/wuwenyan/Desktop/demo.jpeg", rf_value: str = "0.35") -> bool:
    """
    Confirm CC spec via UI:
    1. Click '点击打开识别面板'
    2. Upload TLC image
    3. Set Rf value
    4. Confirm TLC dialog
    5. Confirm spec panel
    """
    print("[UI] confirm_cc_spec_via_ui: starting...")

    if "点击打开识别面板" in _body_text(page):
        _click_first_visible_text(page, ["点击打开识别面板"], timeout=10)

    # Upload TLC image
    image_path = Path(tlc_image).expanduser()
    if image_path.exists():
        file_input = page.locator("input[type='file']").first
        try:
            file_input.set_input_files(str(image_path))
            time.sleep(2)
            print(f"[UI] Uploaded TLC image: {image_path}")
        except Exception as e:
            print(f"[UI] TLC upload error: {e}")

    # Fill Rf value
    if rf_value:
        candidates = page.locator("input")
        try:
            for i in range(candidates.count()):
                field = candidates.nth(i)
                try:
                    if not field.is_visible(timeout=300):
                        continue
                    val = field.input_value(timeout=300) or ""
                    placeholder = field.get_attribute("placeholder") or ""
                    aria = field.get_attribute("aria-label") or ""
                    combined = " ".join([val, placeholder, aria]).lower()
                    if "rf" in combined or val in {"", "0", "0.0", "0.35"}:
                        field.fill(str(rf_value))
                        print(f"[UI] Filled Rf: {rf_value}")
                        break
                except Exception:
                    continue
        except Exception:
            pass

    # Double confirm (TLC dialog + spec panel)
    _click_confirm_any(page)
    time.sleep(0.5)
    if any(t in _body_text(page) for t in ["确认", "确认修改"]):
        _click_confirm_any(page)

    print("[UI] confirm_cc_spec_via_ui: done")
    return True


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

def confirm_re_spec_via_ui(page) -> bool:
    """Confirm RE spec panel."""
    print("[UI] confirm_re_spec_via_ui: clicking confirm...")
    time.sleep(1)
    ok = _click_confirm_any(page)
    print(f"[UI] confirm_re_spec_via_ui: done, confirm clicked={ok}")
    return ok
