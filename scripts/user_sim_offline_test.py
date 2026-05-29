#!/usr/bin/env python3
"""Offline smoke-test for the user-simulator (scripts/user_sim.py).

Does NOT touch the lab relay or Playwright. Feeds hand-written fake TALOS
replies to `decide_reply` and prints `scripted → generated` side by side, so
you can judge the user-sim's reply quality and prompt tuning in isolation —
before wiring it into a live run.

Needs only ANTHROPIC_API_KEY:
    export ANTHROPIC_API_KEY=...
    python3 scripts/user_sim_offline_test.py
    python3 scripts/user_sim_offline_test.py --conv conv-008 --model claude-haiku-4-5

Each scenario is a (scripted_turn, fake_agent_reply) pair. The scripted turn
is what the dataset would have replayed verbatim; the fake agent reply is
what TALOS "just said". We want the generated reply to fit the agent reply,
using the scripted turn only as an intent hint. The last few scenarios are
deliberately OFF-SCRIPT — the agent asks something the dataset never
anticipated — to check the model adapts instead of parroting the script.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from user_sim import make_client, decide_reply, DEFAULT_MODEL  # noqa: E402
from smoke_runner_20260518 import build_briefs  # noqa: E402


# (label, scripted_turn, fake_agent_reply)
# scripted_turn mirrors what conv-008's dataset replays at that step.
SCENARIOS = [
    ("提供过柱推荐依据（on-script）",
     "SMILES Clc1ccc(cc1)C(=O)NC1CCN(CC1)c1nc2ccccc2n1C，400 mg，Rf 0.3，PE:EA 1:1",
     "好的，先做过柱。请提供：化合物 SMILES、上样量、TLC 的 Rf 值和展开剂体系。"),

    ("确认过柱执行参数（on-script）",
     "可以",
     "根据上样量 400 mg 和 Rf 0.3，我推荐 12 g 硅胶柱，PE/EA 梯度从 10% 起爬到 40%。确认就下发。"),

    ("问进度（on-script）",
     "过柱到第几分钟了？",
     "过柱任务已下发，机器人正在架设硅胶柱。"),

    ("旋蒸前补依据（on-script）",
     "热稳定性正常",
     "过柱完成，主峰捕获在 tube 5-7。接下来旋蒸，合并液体系沿用 PE/EA 1:1，体积约多少？"),

    # ---- OFF-SCRIPT: agent asks things the dataset never anticipated -----
    ("agent 追问脚本没有的约束（酸敏感）",
     "可以",  # script would just say "可以"
     "这个化合物含 Boc 和苯并咪唑，过柱时对酸敏感吗？需要我在洗脱剂里加三乙胺中和吗？"),

    ("agent 反问一个用户没准备的数值（柱长）",
     "下发",
     "下发前确认一下：你希望硅胶柱床高用标准的还是加长？加长能提高分离度但耗时更久。"),

    ("agent 给了选择题",
     "可以",
     "旋蒸水浴温度我可以设 35°C 或 40°C，40°C 更快但你这化合物若热敏建议 35°C，你定哪个？"),

    ("agent 报了个异常",
     "过柱到第几分钟了？",
     "过柱机 1 号在润柱阶段报了一个压力偏高的告警，已自动重试并恢复，目前正常洗脱中。"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--conv", default="conv-008",
                    help="brief id to source the user's facts from")
    ap.add_argument("--model", default=None,
                    help="override model (default claude-opus-4-7 / $USER_SIM_MODEL)")
    args = ap.parse_args()

    client = make_client()
    if client is None:
        print("ERROR: no Anthropic client — set ANTHROPIC_API_KEY (and "
              "`pip install anthropic`). This offline test needs a key.",
              file=sys.stderr)
        sys.exit(1)

    brief = build_briefs([args.conv])[0]
    model = args.model or DEFAULT_MODEL
    print(f"=== user-sim offline test ===  conv={args.conv}  model={model}\n")

    # Thread a running history so multi-turn context behaves like a real run.
    history = []
    for label, scripted, agent_reply in SCENARIOS:
        history.append(("agent", agent_reply))
        gen = decide_reply(client, brief, scripted, agent_reply, history,
                           model=model)
        history.append(("user", gen or scripted))

        print(f"### {label}")
        print(f"  TALOS 说    : {agent_reply}")
        print(f"  脚本原文    : {scripted}")
        print(f"  模拟器生成  : {gen if gen is not None else '<None — 会回落脚本>'}")
        print()


if __name__ == "__main__":
    main()
