#!/usr/bin/env python3
"""
Judge a smoke run JSON against phoenix_annotation_criteria.md and the dataset.

DRY-RUN by default: prints a per-turn verdict table + the Phoenix annotations
that WOULD be written. Pass --write to actually POST to Phoenix (outward-facing,
shared dev instance) — only with explicit human approval.

Usage:
  python3 scripts/judge_20260518.py eval_outputs/smoke_conv001_20260518.json
  python3 scripts/judge_20260518.py <run.json> --write
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
# Phoenix relay IP rotates (Mac Tailscale re-log). Resolve from env at import;
# --phoenix-base can override in main(). Fallback default may be stale.
DEFAULT_PHOENIX_BASE = "http://100.84.102.34:6006"
PHOENIX_BASE = os.environ.get("PHOENIX_BASE", DEFAULT_PHOENIX_BASE).rstrip("/")
PROJECT_ID = "UHJvamVjdDoy"
REQ = {"proxies": {"http": None, "https": None}, "timeout": 20}

DATASET = json.loads((ROOT / "agent_eval_dataset.json").read_text())
CONVS = {c["id"]: c for c in DATASET["conversations"]}

# Downstream-only metrics that a CC-only / pre-LCMS / pre-weighing run must NOT state.
FABRICATION_TOKENS = ["回收率", "纯度", "Rs", "半峰宽", "峰宽", "回收质量", "最终质量", "收率"]
DEV_LEAK_TOKENS = ["return_code", "task_id=", "task_type=", "status=working", "skill=",
                   "entity_type=", "only_available=", "in_progress", "collecting_spec",
                   "collecting_params", "=0", "step_"]


def expected_tools_for(conv_id):
    """user_idx -> expected tool, from the dataset's next-assistant query_payload."""
    turns = CONVS[conv_id]["turns"]
    out = {}
    for i, t in enumerate(turns):
        if t["role"] == "user" and i + 1 < len(turns):
            qp = (turns[i + 1].get("query_payload") or {})
            if qp.get("tool"):
                out[i] = qp["tool"]
    return out


def category_chain_done(conv_id):
    """Which task types this conv actually runs (to flag fabricated downstream data)."""
    cat = CONVS[conv_id]["category"]
    return {
        "single_column": {"column"},
        "single_rotovap": {"rotovap"},
        "column_then_rotovap": {"column", "rotovap"},
        "query": set(),
        "negative": set(),
    }.get(cat, set())


def judge_conv(conv_id, r):
    exp = expected_tools_for(conv_id)
    done = category_chain_done(conv_id)
    # CC/RE-only or query never produces LC-MS purity / weighing recovery numbers.
    can_have_downstream = {"lcms", "weighing"} & done
    verdicts = []
    anns = []
    for sp in r.get("phoenix_spans") or []:
        # the dataset user_idx this span corresponds to (match by user_msg substring)
        umsg = sp.get("user_msg") or ""
        uidx = None
        for i, t in enumerate(CONVS[conv_id]["turns"]):
            if t["role"] == "user" and t["content"][:18] and t["content"][:18] in umsg:
                uidx = i
                break
        et = exp.get(uidx)
        at = sp.get("actual_tools") or []
        reply = sp.get("agent_reply") or ""
        tid = sp.get("turn_id")
        sid = r.get("session_id")
        case_id = f"{conv_id}-turn-{uidx}"
        issues = []

        # G1-G4 tool selection
        tool_bad = False
        if et:
            if not at:
                issues.append(f"G1 漏调 (expected {et})")
                tool_bad = True
            elif et not in at:
                issues.append(f"G2/G3 工具不匹配 actual={at} expected={et}")
                tool_bad = True
        if tool_bad and tid:
            anns.append({
                "name": "tool_selection_accuracy", "label": et,
                "explanation": f"actual: {','.join(at) or 'none'}; expected: {et}; "
                               f"{issues[-1]}",
                "case_id": case_id, "session_id": sid, "turn_id": tid,
                "span_id": sp.get("span_id"),
            })

        # C2/C3 fabricated downstream data
        if not can_have_downstream and any(tok in reply for tok in FABRICATION_TOKENS):
            hit = [t for t in FABRICATION_TOKENS if t in reply]
            issues.append(f"C2/C3 编造下游数据 {hit} (链路未跑 LC-MS/称重)")

        # D1 dev-term leak
        leak = [t for t in DEV_LEAK_TOKENS if t in reply]
        if leak:
            issues.append(f"D1 暴露开发者术语 {leak}")

        overview_bad = bool(issues)
        if overview_bad and tid:
            anns.append({
                "name": "Overview", "label": "Bad",
                "explanation": "; ".join(issues)[:480],
                "case_id": case_id, "session_id": sid, "turn_id": tid,
                "span_id": sp.get("span_id"),
            })
        verdicts.append({
            "user_idx": uidx, "user": umsg[:40], "expected_tool": et,
            "actual_tools": at, "issues": issues or ["OK"],
        })

    # planner mismatch (CC+RE)
    if r.get("planner_mismatch"):
        anns.append({
            "name": "expected_plan", "label": "cc,re",
            "explanation": "首轮要求 CC+RE，但 plan 未同时包含 cc_agent 与 re_agent",
            "case_id": f"{conv_id}-plan", "session_id": r.get("session_id"),
            "turn_id": None, "span_id": None,
        })
    return verdicts, anns


def post_annotation(a):
    body = {"data": [{
        "name": a["name"], "annotator_kind": "HUMAN",
        "result": {"label": a["label"], "score": None, "explanation": a["explanation"]},
        "metadata": {k: a[k] for k in ("case_id", "session_id", "turn_id") if a.get(k)},
        "identifier": f"{a['case_id']}:{a['name']}", "span_id": a["span_id"],
    }]}
    resp = requests.post(f"{PHOENIX_BASE}/v1/span_annotations?sync=true", json=body, **REQ)
    resp.raise_for_status()
    return resp.json()


def main():
    global PHOENIX_BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("run_json")
    ap.add_argument("--write", action="store_true",
                    help="POST to Phoenix (outward-facing; requires human approval)")
    ap.add_argument("--phoenix-base", default=None,
                    help="Phoenix base URL; overrides $PHOENIX_BASE. "
                         "Relay IP rotates — set this each session.")
    args = ap.parse_args()
    if args.phoenix_base:
        PHOENIX_BASE = args.phoenix_base.rstrip("/")

    data = json.loads(Path(args.run_json).read_text())
    all_anns = []
    for conv_id, r in data.items():
        if "error" in r:
            print(f"\n### {conv_id}: RUN ERROR -> {r['error']}")
            continue
        print(f"\n### {conv_id} | session={r.get('session_id')} "
              f"stage={r.get('final_stage')} submitted={r.get('submitted_tasks')}")
        verdicts, anns = judge_conv(conv_id, r)
        for v in verdicts:
            flag = "OK" if v["issues"] == ["OK"] else "⚠"
            print(f"  [{flag}] idx={v['user_idx']} exp={v['expected_tool']} "
                  f"act={v['actual_tools']} :: {'; '.join(v['issues'])} | {v['user']}")
        for a in anns:
            print(f"  -> ANNOTATION {a['name']}={a['label']} "
                  f"id={a['case_id']}:{a['name']} :: {a['explanation'][:120]}")
        all_anns += anns

    print(f"\n=== {len(all_anns)} proposed annotations "
          f"({'WRITE' if args.write else 'DRY-RUN, not posted'}) ===")
    if args.write:
        for a in all_anns:
            if not a.get("span_id"):
                print(f"  SKIP (no span_id): {a['case_id']}:{a['name']}")
                continue
            try:
                post_annotation(a)
                print(f"  POSTED {a['case_id']}:{a['name']}")
            except Exception as e:
                print(f"  FAIL {a['case_id']}:{a['name']} -> {e}")


if __name__ == "__main__":
    main()
