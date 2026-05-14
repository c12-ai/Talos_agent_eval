#!/usr/bin/env python3
"""Annotate the two 2026-05-13 TALOS smoke cases in Phoenix."""

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
        "case_id": "phx-anno-test-001",
        "title": "[20260513-phx-anno-test-001]",
        "session_id": "e9578107-cb57-4d1d-8ce5-e5c013af540f",
        "user": "我想做一个 Suzuki 偶联反应，能帮我下发吗？",
        "kind": "golden_negative",
    },
    {
        "case_id": "phx-anno-test-002",
        "title": "[20260513-phx-anno-test-002]",
        "session_id": "f963f0bf-0dce-404d-809d-f4ae6c1450bf",
        "user": "机器人现在在干嘛？",
        "kind": "query_robot_status",
    },
]


def fetch_root_spans(limit=200) -> list[dict]:
    response = requests.get(
        f"{PHOENIX_BASE}/v1/projects/{PROJECT_ID}/spans",
        params={"parent_id": "null", "name": "LangGraph", "limit": limit},
        **REQ,
    )
    response.raise_for_status()
    return response.json().get("data", [])


def span_text(span: dict) -> str:
    return json.dumps(span, ensure_ascii=False)


def find_span_for_case(case: dict, retries=12) -> dict:
    for _ in range(retries):
        spans = fetch_root_spans(300)
        matches = []
        for span in spans:
            text = span_text(span)
            attrs = span.get("attributes") or {}
            if case["session_id"] in text or attrs.get("metadata.thread_id") == case["session_id"]:
                if case["user"] in text or case["title"] in text or case["session_id"] in text:
                    matches.append(span)
        if matches:
            # Latest matching span first by start_time.
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


def fetch_trace_spans(trace_id: str) -> list[dict]:
    response = requests.get(
        f"{PHOENIX_BASE}/v1/projects/{PROJECT_ID}/spans",
        params={"trace_id": trace_id, "limit": 200},
        **REQ,
    )
    response.raise_for_status()
    return response.json().get("data", [])


def main() -> None:
    results = []
    annotations_to_post = []

    for case in CASES:
        span = find_span_for_case(case)
        span_id = span["context"]["span_id"]
        trace_id = span["context"]["trace_id"]
        attrs = span.get("attributes") or {}
        trace_spans = fetch_trace_spans(trace_id)
        text = span_text({"root": span, "trace_spans": trace_spans})
        actual_tools = []
        if "get_running_experiments" in text:
            actual_tools.append("get_running_experiments")
        if "get_robot_status" in text:
            actual_tools.append("get_robot_status")

        case_result = {
            **case,
            "span_id": span_id,
            "trace_id": trace_id,
            "span_url": f"{PHOENIX_BASE}/projects/{PROJECT_ID}/spans/{span_id}",
            "actual_tools": actual_tools,
            "annotation_plan": [],
        }

        if case["kind"] == "golden_negative":
            # Golden-baseline annotations: normal correct result, but explicitly
            # tagged so reviewer can inspect the Phoenix API workflow.
            rows = [
                ("expected_within_domain", "true", "Suzuki 偶联属于化学/实验室领域请求，因此在领域内。"),
                ("expected_within_capacity", "true", "Talos 当前只支持过柱和旋蒸；该请求超出可下发范围，实际已正确拒绝，符合能力边界处理预期。"),
                ("exp_match_type", "reject", "期望意图为拒绝不支持的合成反应请求，且不生成 lab 任务。"),
            ]
            for name, label, explanation in rows:
                annotations_to_post.append(
                    {
                        "name": name,
                        "annotator_kind": "HUMAN",
                        "result": {"label": label, "score": None, "explanation": explanation},
                        "metadata": {
                            "case_id": case["case_id"],
                            "session_id": case["session_id"],
                            "title": case["title"],
                            "mark_reason": "golden_baseline",
                        },
                        "identifier": f"codex-20260513-{case['case_id']}:{name}",
                        "span_id": span_id,
                    }
                )
                case_result["annotation_plan"].append({"name": name, "label": label, "explanation": explanation})

        elif case["kind"] == "query_robot_status":
            # Query result looked correct and used the expected tool, so no
            # tool_selection_accuracy annotation is needed. Add a golden
            # intention tag only, to demonstrate the "normal query baseline"
            # path without marking tool correctness noise.
            annotations_to_post.append(
                {
                    "name": "exp_match_type",
                    "annotator_kind": "HUMAN",
                    "result": {
                        "label": "query",
                        "score": None,
                        "explanation": "用户询问机器人当前状态，期望意图为查询；回复给出了机器人空闲、位置和运行任务概况。",
                    },
                    "metadata": {
                        "case_id": case["case_id"],
                        "session_id": case["session_id"],
                        "title": case["title"],
                        "mark_reason": "golden_baseline",
                        "expected_tool": "get_robot_status",
                        "actual_tools": ",".join(actual_tools) if actual_tools else "not_found_in_root_span_text",
                    },
                    "identifier": f"codex-20260513-{case['case_id']}:exp_match_type",
                    "span_id": span_id,
                }
            )
            case_result["annotation_plan"].append(
                {
                    "name": "exp_match_type",
                    "label": "query",
                    "explanation": "查询意图 golden baseline；工具无异常不打 tool_selection_accuracy。",
                }
            )
            case_result["tool_annotation"] = "未打；按新规则，工具调用无异常时不标 tool_selection_accuracy。"

        results.append(case_result)

    posted = post_annotations(annotations_to_post)
    for item, posted_item in zip(annotations_to_post, posted):
        item["annotation_id"] = posted_item.get("id")

    for result in results:
        result["annotations_readback"] = get_annotations(result["span_id"])

    output = {
        "posted": annotations_to_post,
        "cases": results,
    }
    Path("/tmp/phoenix_annotate_two_cases_20260513.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
