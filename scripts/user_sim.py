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

Engine: Claude via the official Anthropic SDK. Requires ANTHROPIC_API_KEY.
If the key/SDK is unavailable or any call fails, `make_client` returns None
and `decide_reply` returns None — callers fall back to the scripted turn, so
the deterministic path still works with no key.

Scope: only the chat *wording* is generated here. The smoke runner's
stage/panel logic still keys off the scripted `ut`, so deterministic panel
actions (approve plan / confirm spec / dispatch) are unaffected.
"""
from __future__ import annotations

import os
from typing import Optional

# Default model. Per the claude-api skill, default to the most capable model;
# override per-run with USER_SIM_MODEL or --user-sim-model (Haiku/Sonnet are
# cheaper if cost matters: claude-haiku-4-5 / claude-sonnet-4-6).
DEFAULT_MODEL = "claude-opus-4-7"

# Frozen system prompt — kept byte-stable so prompt caching can reuse it
# across turns/convs. Do NOT interpolate per-turn data here (that would
# invalidate the cache); per-conv facts go in the user message.
_SYSTEM = """你在扮演一名化学实验室研究员，正在和实验室助手 TALOS 对话，完成一次纯化实验（过柱 / 旋蒸 / 分析 / 称重等）。

你的任务：**读 TALOS 刚刚说的话，自然地回一句把实验推进下去**。下面会给你一份「你手上的实验信息」和「这一步你原本打算说的话」，后者只是参考，不要照念——要贴合 TALOS 实际说的内容。

规则：
- 用简短、口语化的中文，像研究员发消息一样，一两句话就够。
- TALOS 问到你信息里有的东西（SMILES、上样量、TLC Rf、溶剂体系、合并液体积等），就把对应的值告诉它。
- TALOS 问到你信息里**没有**的东西（某个你没准备的约束/参数），就自然地说「没特别要求 / 按常规来就行」，**不要编一个具体数值**。
- TALOS 在等你确认（方案、预填参数、执行参数），就简短确认（如「可以」「行」「就这样下发」）。
- 不要替 TALOS 做它的活——不要自己报推荐的柱规格/梯度/温度/压力，你是提需求和拍板的人。
- **只输出你要发给 TALOS 的那一句话**，不要加引号、不要解释、不要写「我会说：」之类。"""


def make_client():
    """Return an Anthropic client, or None when the SDK or API key is absent.

    None is the signal to callers that user-sim is unavailable → fall back
    to scripted turns."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except Exception:
        return None
    try:
        return anthropic.Anthropic()
    except Exception:
        return None


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


def decide_reply(client, brief, scripted_turn, agent_reply, history,
                 *, model: Optional[str] = None) -> Optional[str]:
    """Generate the user's next chat message.

    Args:
      client: Anthropic client from make_client() (None → returns None).
      brief: the conv brief (scene + user_turns).
      scripted_turn: the dataset's intended text for this step (reference).
      agent_reply: TALOS's latest reply text (what we're responding to).
      history: list of (role, text) tuples, role in {"user","agent"}.
      model: override model id.

    Returns the generated reply text, or None on any failure (caller falls
    back to scripted_turn)."""
    if client is None:
        return None
    model = model or os.environ.get("USER_SIM_MODEL") or DEFAULT_MODEL

    convo = ""
    for role, text in (history or [])[-8:]:
        who = "你" if role == "user" else "TALOS"
        convo += f"{who}：{text}\n"

    intent = (scripted_turn or "").strip() or "（无特定计划，顺着对话走）"
    user_block = (
        f"{_brief_facts(brief)}\n\n"
        f"——\n"
        f"最近的对话：\n{convo or '（还没开始）'}\n"
        f"TALOS 刚刚说：{(agent_reply or '').strip() or '（暂无新内容）'}\n\n"
        f"这一步你原本打算说的（仅供参考，不要照念）：{intent}\n\n"
        f"现在请输出你这一句要回复 TALOS 的话："
    )

    try:
        # No thinking / sampling params — opus-4-7 runs thinking-off by
        # default and 400s on temperature/top_p/top_k. cache_control on the
        # frozen system prompt lets repeated turns reuse it.
        resp = client.messages.create(
            model=model,
            max_tokens=300,
            system=[{"type": "text", "text": _SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_block}],
        )
    except Exception as exc:
        print(f"[user-sim] decide_reply API error: {exc}", flush=True)
        return None

    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    text = (text or "").strip()
    return text or None
