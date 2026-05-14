#!/usr/bin/env python3
"""Annotate the 2026-05-13 CC+RE two-case TALOS run in Phoenix."""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests


PHOENIX_BASE = "http://192.168.12.239:6006"
PROJECT_ID = "UHJvamVjdDoy"
REQ = {"proxies": {"http": None, "https": None}, "timeout": 20}

CASES = [
    {
        "case_id": "20260513-ccre-2case-conv1-re",
        "title": "[20260513-ccre-2case-conv1]",
        "session_id": "31f86707-7e8c-4ea9-80c1-86d05c8c8272",
        "user": "[CONFIRM] 实验已完成",
        "expected_plan": None,
        "overview_explanation": (
            "CC 已通过右侧面板完成并且 lab run 状态为 completed；但 workflow-state 中 CC task "
            "仍停留在 conducting，后续尝试发起 RE 未形成可检索的 Phoenix root span，也没有生成 "
            "RE plan/task，前端发送框长时间禁用。"
        ),
        "correction": (
            "CC lab run 完成后，应把 workflow-state 中对应 task 推进到终态并解锁会话；随后收到明确"
            "旋蒸条件时，应生成 RE 执行计划并在右侧面板进入 RE spec/params 确认流程。"
        ),
    },
    {
        "case_id": "20260513-ccre-2case-conv2-cc",
        "title": "[20260513-ccre-2case-conv2]",
        "session_id": "b4787874-3234-45c8-ad12-c8df51767338",
        "user": "帮我做一个过柱任务",
        "expected_plan": "cc",
        "overview_explanation": (
            "用户给出完整过柱条件，属于支持的执行任务；但该 turn 未出现“批准方案”，"
            "workflow-state 为 plan=null、tasks=[]，右侧面板无活跃任务，无法进入 CC/RE 流程。"
        ),
        "correction": (
            "对完整过柱请求应生成 CC plan 并显示右侧方案批准入口；批准后进入 TLC 图片、Rf、"
            "12g 硅胶柱和样品柱 002 等参数确认流程。"
        ),
    },
]


def fetch_root_spans(limit: int = 500) -> list[dict]:
    response = requests.get(
        f"{PHOENIX_BASE}/v1/projects/{PROJECT_ID}/spans",
        params={"parent_id": "null", "name": "LangGraph", "limit": limit},
        **REQ,
    )
    response.raise_for_status()
    return response.json().get("data", [])


def fetch_trace_spans(trace_id: str) -> list[dict]:
    response = requests.get(
        f"{PHOENIX_BASE}/v1/projects/{PROJECT_ID}/spans",
        params={"trace_id": trace_id, "limit": 200},
        **REQ,
    )
    response.raise_for_status()
    return response.json().get("data", [])


def span_text(span: dict) -> str:
    return json.dumps(span, ensure_ascii=False)


def find_span_for_case(case: dict, retries: int = 18) -> dict:
    for _ in range(retries):
        matches = []
        for span in fetch_root_spans():
            text = span_text(span)
            if case["session_id"] in text and case["user"] in text:
                matches.append(span)
        if matches:
            matches.sort(key=lambda s: s.get("start_time") or "", reverse=True)
            return matches[0]
        time.sleep(5)
    raise RuntimeError(f"span not found for {case['case_id']} {case['session_id']}")


def post_annotations(data: list[dict]) -> list[dict]:
    response = requests.post(
        f"{PHOENIX_BASE}/v1/span_annotations",
        params={"sync": "true"},
        json={"data": data},
        **REQ,
    )
    response.raise_for_status()
    return response.json().get("data", [])


def get_annotations(span_id: str) -> list[dict]:
    response = requests.get(
        f"{PHOENIX_BASE}/v1/projects/{PROJECT_ID}/span_annotations",
        params={"span_ids": span_id},
        **REQ,
    )
    response.raise_for_status()
    return response.json().get("data", [])


def make_annotation(case: dict, span_id: str, name: str, label: str, explanation: str) -> dict:
    return {
        "name": name,
        "annotator_kind": "HUMAN",
        "result": {"label": label, "score": None, "explanation": explanation},
        "metadata": {
            "case_id": case["case_id"],
            "session_id": case["session_id"],
            "title": case["title"],
            "mark_reason": "异常标注",
        },
        "identifier": f"codex-20260513-{case['case_id']}:{name}",
        "span_id": span_id,
    }


def main() -> None:
    cases_out = []
    annotations = []

    for case in CASES:
        span = find_span_for_case(case)
        span_id = span["context"]["span_id"]
        trace_id = span["context"]["trace_id"]
        trace_spans = fetch_trace_spans(trace_id)
        cases_out.append(
            {
                **case,
                "span_id": span_id,
                "trace_id": trace_id,
                "span_url": f"{PHOENIX_BASE}/projects/{PROJECT_ID}/spans/{span_id}",
                "trace_span_count": len(trace_spans),
            }
        )
        case_annotations = [
            make_annotation(case, span_id, "Overview", "Bad", case["overview_explanation"]),
            make_annotation(
                case,
                span_id,
                "AnswerQuality-Correction",
                case["correction"],
                "人工修正建议。",
            ),
        ]
        if case["expected_plan"]:
            case_annotations.insert(
                0,
                make_annotation(
                    case,
                    span_id,
                    "expected_plan",
                    case["expected_plan"],
                    f"期望生成 {case['expected_plan']} 计划；实际未生成对应 plan/task。",
                ),
            )
        annotations.extend(case_annotations)

    posted = post_annotations(annotations)
    for annotation, posted_item in zip(annotations, posted):
        annotation["annotation_id"] = posted_item.get("id")

    for item in cases_out:
        item["annotations_readback"] = get_annotations(item["span_id"])

    output = {"cases": cases_out, "posted": annotations}
    out_path = Path("eval_outputs/phoenix_annotate_20260513_ccre_2cases.json")
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
