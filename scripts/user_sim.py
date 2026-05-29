"""LLM user-simulator for the smoke runner.

Drives the *user* side of a TALOS conversation adaptively. Instead of
replaying the dataset's scripted user turns verbatim, it reads what the
TALOS agent actually said and generates a contextually-appropriate reply,
using the scripted turn only as a hint of the user's intent / the info the
user has on hand.

Why: the old runner sent `brief.user_turns[k]` verbatim regardless of the
agent's reply — so if the agent asked something the script didn't
anticipate, the runner still fired the next scripted line ("agent asks about
the weather, script says 'where's the bus stop', runner sends 'where's the
bus stop'"). The script is a reference, not a teleprompter.

Engines (pick with USER_SIM_ENGINE / --user-sim-engine):
- `codex`  — shell out to `codex exec --output-last-message` (OpenAI-backed,
             uses the codex CLI's own auth/quota; NO Anthropic key needed).
- `claude` — shell out to `claude -p` (Claude Code print mode; subscription
             auth).
- `api`    — Anthropic Messages API via the SDK; key from
             WWY_ANTHROPIC_API_KEY (preferred) or ANTHROPIC_API_KEY.

CLI engines (codex/claude) are the sanctioned non-interactive mode of those
tools — no API key wrangling, and they draw on whatever quota the CLI is
already logged into (e.g. codex → OpenAI, sidestepping Anthropic rate
limits). If the chosen engine is unavailable or a call fails, `make_engine`
/ `decide_reply` return None and callers fall back to the scripted turn, so
the deterministic path always works.

Scope: only the chat *wording* is generated here. The smoke runner's
stage/panel logic still keys off the scripted `ut`, so deterministic panel
actions (approve plan / confirm spec / dispatch) are unaffected.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import Optional

# Default Anthropic model for the `api` engine. CLI engines use whatever
# model their own config selects (codex → its configured model; claude →
# the CLI default), so --user-sim-model only affects the api engine.
DEFAULT_MODEL = "claude-opus-4-7"

# Default engine. `api` keeps the original behavior; set USER_SIM_ENGINE or
# pass --user-sim-engine to switch.
DEFAULT_ENGINE = "codex"

# Per-call timeout for CLI engines (codex boots + reasons; be generous).
_CLI_TIMEOUT_S = 150

# Frozen system prompt — for the api engine it's the cached system block;
# for CLI engines it's prepended to the per-turn prompt. Keep byte-stable.
_SYSTEM = """你在扮演一名化学实验室研究员，正在和实验室助手 TALOS 对话，完成一次纯化实验（过柱 / 旋蒸 / 分析 / 称重等）。

你的任务：**读 TALOS 刚刚说的话，自然地回一句把实验推进下去**。下面会给你一份「你手上的实验信息」和「这一步你原本打算说的话」，后者只是参考，不要照念——要贴合 TALOS 实际说的内容。

规则：
- 用简短、口语化的中文，像研究员发消息一样，一两句话就够。
- TALOS 问到你信息里有的东西（SMILES、上样量、TLC Rf、溶剂体系、合并液体积等），就把对应的值告诉它。
- TALOS 问到你信息里**没有**的东西（某个你没准备的约束/参数），就自然地说「没特别要求 / 按常规来就行」，**不要编一个具体数值**。
- TALOS 在等你确认（方案、预填参数、执行参数），就简短确认（如「可以」「行」「就这样下发」）。
- 不要替 TALOS 做它的活——不要自己报推荐的柱规格/梯度/温度/压力，你是提需求和拍板的人。
- **只输出你要发给 TALOS 的那一句话**，不要加引号、不要解释、不要写「我会说：」之类。"""


# --------------------------------------------------------------------------- key
# API-engine key env vars, priority order. WWY_ANTHROPIC_API_KEY is the
# project-scoped key — set it so this eval uses a dedicated key without
# colliding with a generic ANTHROPIC_API_KEY other tools may use.
_KEY_ENV_VARS = ("WWY_ANTHROPIC_API_KEY", "wwy_anthropic_api_key",
                 "ANTHROPIC_API_KEY")


def _resolve_api_key():
    for name in _KEY_ENV_VARS:
        v = os.environ.get(name)
        if v:
            return v
    return None


# --------------------------------------------------------------------------- engine
def resolve_engine(prefer: Optional[str] = None) -> str:
    """Engine name: explicit arg > USER_SIM_ENGINE env > DEFAULT_ENGINE."""
    return (prefer or os.environ.get("USER_SIM_ENGINE") or DEFAULT_ENGINE).strip().lower()


def make_engine(prefer: Optional[str] = None) -> Optional[dict]:
    """Return an opaque engine handle, or None when the engine is
    unavailable (→ caller falls back to scripted turns).

    Handle shapes:
      {"kind": "api", "client": <anthropic.Anthropic>}
      {"kind": "codex"}
      {"kind": "claude"}
    """
    engine = resolve_engine(prefer)

    if engine == "codex":
        if shutil.which("codex"):
            return {"kind": "codex"}
        print("[user-sim] engine=codex but `codex` not on PATH", flush=True)
        return None

    if engine == "claude":
        if shutil.which("claude"):
            return {"kind": "claude"}
        print("[user-sim] engine=claude but `claude` not on PATH", flush=True)
        return None

    if engine == "api":
        key = _resolve_api_key()
        if not key:
            return None
        try:
            import anthropic
        except Exception:
            return None
        try:
            if key.startswith("sk-ant-oat"):
                # OAuth access tokens authenticate via Authorization: Bearer
                # (auth_token=), not the x-api-key header (api_key=).
                client = anthropic.Anthropic(auth_token=key)
            else:
                client = anthropic.Anthropic(api_key=key)
        except Exception:
            return None
        return {"kind": "api", "client": client}

    print(f"[user-sim] unknown engine {engine!r}", flush=True)
    return None


# Back-compat alias: older callers used make_client() for the api engine.
def make_client():
    return make_engine("api")


# --------------------------------------------------------------------------- prompt
def _brief_facts(brief) -> str:
    """Flatten the brief into a 'what the user knows / plans to say' block.

    The scripted user turns collectively carry every datum the user has
    (SMILES, amounts, Rf, system, …) plus the natural progression of the
    conversation, so we hand the whole list to the model as reference."""
    scene = (brief.get("scene") or "").strip()
    head = f"实验场景：{scene}\n\n" if scene else ""
    lines = []
    for t in brief.get("user_turns", []):
        txt = (t.get("user_text") or "").strip()
        if txt:
            lines.append(f"- {txt}")
    body = "你在这次实验里准备好的信息 / 按顺序打算表达的话：\n" + "\n".join(lines)
    return head + body


def _user_block(brief, scripted_turn, agent_reply, history) -> str:
    convo = ""
    for role, text in (history or [])[-8:]:
        who = "你" if role == "user" else "TALOS"
        convo += f"{who}：{text}\n"
    intent = (scripted_turn or "").strip() or "（无特定计划，顺着对话走）"
    return (
        f"{_brief_facts(brief)}\n\n"
        f"——\n"
        f"最近的对话：\n{convo or '（还没开始）'}\n"
        f"TALOS 刚刚说：{(agent_reply or '').strip() or '（暂无新内容）'}\n\n"
        f"这一步你原本打算说的（仅供参考，不要照念）：{intent}\n\n"
        f"现在请输出你这一句要回复 TALOS 的话："
    )


# --------------------------------------------------------------------------- engine calls
def _call_api(client, user_block, model) -> Optional[str]:
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=300,
            system=[{"type": "text", "text": _SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_block}],
        )
    except Exception as exc:
        print(f"[user-sim] api error: {exc}", flush=True)
        return None
    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    return (text or "").strip() or None


def _call_codex(prompt) -> Optional[str]:
    """codex exec, final message captured via --output-last-message (avoids
    the banner / token-footer noise on stdout)."""
    if not shutil.which("codex"):
        return None
    fd, path = tempfile.mkstemp(prefix="user_sim_codex_", suffix=".txt")
    os.close(fd)
    try:
        subprocess.run(
            ["codex", "exec", "--output-last-message", path, prompt],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=_CLI_TIMEOUT_S, check=False,
        )
        with open(path, "r", encoding="utf-8") as f:
            return (f.read().strip() or None)
    except Exception as exc:
        print(f"[user-sim] codex error: {exc}", flush=True)
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _call_claude_cli(prompt) -> Optional[str]:
    """claude -p (print mode) — prints the reply text to stdout."""
    if not shutil.which("claude"):
        return None
    try:
        r = subprocess.run(
            ["claude", "-p", prompt],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=_CLI_TIMEOUT_S, check=False,
        )
        return (r.stdout or "").strip() or None
    except Exception as exc:
        print(f"[user-sim] claude cli error: {exc}", flush=True)
        return None


# --------------------------------------------------------------------------- entry
def decide_reply(engine, brief, scripted_turn, agent_reply, history,
                 *, model: Optional[str] = None) -> Optional[str]:
    """Generate the user's next chat message.

    Args:
      engine: handle from make_engine() (None → returns None).
      brief: the conv brief (scene + user_turns).
      scripted_turn: the dataset's intended text for this step (reference).
      agent_reply: TALOS's latest reply text (what we're responding to).
      history: list of (role, text) tuples, role in {"user","agent"}.
      model: api-engine model override (ignored by CLI engines).

    Returns the generated reply, or None on any failure (caller falls back
    to scripted_turn)."""
    if engine is None:
        return None
    kind = engine.get("kind") if isinstance(engine, dict) else "api"
    user_block = _user_block(brief, scripted_turn, agent_reply, history)

    if kind == "api":
        client = engine["client"] if isinstance(engine, dict) else engine
        model = model or os.environ.get("USER_SIM_MODEL") or DEFAULT_MODEL
        return _call_api(client, user_block, model)

    # CLI engines get system + per-turn block as one prompt.
    prompt = f"{_SYSTEM}\n\n{user_block}"
    if kind == "codex":
        return _call_codex(prompt)
    if kind == "claude":
        return _call_claude_cli(prompt)
    return None
