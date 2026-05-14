#!/usr/bin/env python3
"""Phoenix annotations for batch [20260513-1727]."""

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
        "case_id": "20260513-1727-conv002-cc-params",
        "title": "[20260513-1727-conv002]",
        "session_id": "4428ce21-311e-491d-83a6-4b95964355d2",
        "match_text": "过柱预填参数已确认",
        "annotations": [
            ("Overview", "Bad", "CC spec 确认后未生成执行参数；workflow-state 仍为 `cc_agent.phase=collecting_spec`，`params=null`，无法进入样品柱/12g 参数确认和 lab 下发。"),
            ("AnswerQuality-Correction", "CC spec 确认后应进入 `collecting_params` 并生成可提交的 CC 参数；若参数服务超时，应给出明确失败状态并允许可靠重试。", "人工修正建议。"),
        ],
    },
    {
        "case_id": "20260513-1727-conv004-cc-mcp-timeout",
        "title": "[20260513-1727-conv004]",
        "session_id": "97d6fa14-0ba2-4466-b378-f9de40c43aaf",
        "match_text": "COLUMN CHROMATOGRAPHY 参数获取失败",
        "annotations": [
            ("Overview", "Bad", "CC spec 确认后参数获取服务超时，前端显示 `COLUMN CHROMATOGRAPHY 参数获取失败 / MCP 服务响应时间过长`，workflow-state 回退并停留在 `collecting_spec`。"),
            ("AnswerQuality-Correction", "参数服务超时时应清晰保留可重试状态；测试侧不能把该会话算作已完成，也不能继续下发 lab。", "人工修正建议。"),
        ],
    },
    {
        "case_id": "20260513-1727-conv012-re-tail-waiting",
        "title": "[20260513-1727-conv012]",
        "session_id": "5a01afa0-5694-4689-8732-6e3e8c43a56d",
        "match_text": "旋蒸实验参数已确认",
        "annotations": [
            ("Overview", "Bad", "CC+RE 会话中 RE 已下发并进入执行，但 run 长时间停在 `conducting/waiting`，前三步 completed，`end_evaporation` 持续 pending，未进入 terminal 状态。"),
            ("AnswerQuality-Correction", "RE lab run 完成前三步后应继续执行 `end_evaporation` 或返回可诊断失败；不能长期保持 waiting/pending 阻塞批量测试队列。", "人工修正建议。"),
        ],
    },
]


def fetch_root_spans(limit: int = 1000) -> list[dict]:
    response = requests.get(
        f"{PHOENIX_BASE}/v1/projects/{PROJECT_ID}/spans",
        params={"parent_id": "null", "name": "LangGraph", "limit": limit},
        **REQ,
    )
    response.raise_for_status()
    return response.json().get("data", [])


def find_span(case: dict, retries: int = 12) -> dict:
    for _ in range(retries):
        matches = []
        for span in fetch_root_spans():
            text = json.dumps(span, ensure_ascii=False)
            if case["session_id"] in text and case["match_text"] in text:
                matches.append(span)
        if matches:
            matches.sort(key=lambda s: s.get("start_time") or "", reverse=True)
            return matches[0]
        time.sleep(5)
    raise RuntimeError(f"span not found for {case['case_id']}")


def make_annotation(case: dict, span_id: str, name: str, label: str, explanation: str) -> dict:
    return {
        "name": name,
        "annotator_kind": "HUMAN",
        "result": {"label": label, "score": None, "explanation": explanation},
        "metadata": {
            "case_id": case["case_id"],
            "session_id": case["session_id"],
            "title": case["title"],
            "batch": "20260513-1727",
        },
        "identifier": f"codex-20260513-1727:{case['case_id']}:{name}",
        "span_id": span_id,
    }


def post_annotations(data: list[dict]) -> list[dict]:
    response = requests.post(
        f"{PHOENIX_BASE}/v1/span_annotations",
        params={"sync": "true"},
        json={"data": data},
        **REQ,
    )
    response.raise_for_status()
    return response.json().get("data", [])


def main() -> None:
    output = []
    annotations = []
    for case in CASES:
        span = find_span(case)
        span_id = span["context"]["span_id"]
        trace_id = span["context"]["trace_id"]
        for name, label, explanation in case["annotations"]:
            annotations.append(make_annotation(case, span_id, name, label, explanation))
        output.append({**case, "span_id": span_id, "trace_id": trace_id})
    posted = post_annotations(annotations)
    for annotation, posted_item in zip(annotations, posted):
        annotation["annotation_id"] = posted_item.get("id")
    result = {"cases": output, "posted": annotations}
    Path("/tmp/phoenix_batch_20260513_1727.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
