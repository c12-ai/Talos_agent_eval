# Phoenix API 标注配置

## 服务信息

- Phoenix base URL: `http://192.168.12.239:6006`
- Project identifier: `UHJvamVjdDoy`
- Traces 页面: `http://192.168.12.239:6006/projects/UHJvamVjdDoy/traces`
- OpenAPI: `http://192.168.12.239:6006/openapi.json`
- 当前目录未发现鉴权 token 配置；本环境可直接访问上述接口。

## Root Span 落点

所有评测 annotation 都写到该 turn 的 root span：

- `name=LangGraph`
- `kind=CHAIN`
- `parent_id=None`

后端会把 turn root span 写入 `turn_root_spans (turn_id, session_id, root_span_id)`。拿到 `turn_id` 后，用 `root_span_id` 作为 Phoenix `span_id`。

Phoenix OTLP 摄入可能有 5-15 秒延迟。写 annotation 前先轮询 spans 接口，确认 span 已经可见：

```bash
curl --noproxy '*' -sS \
  'http://192.168.12.239:6006/v1/projects/UHJvamVjdDoy/spans?parent_id=null&name=LangGraph&limit=100'
```

也可以用 trace 过滤：

```bash
curl --noproxy '*' -sS \
  'http://192.168.12.239:6006/v1/projects/UHJvamVjdDoy/spans?trace_id=<trace_id>&parent_id=null&name=LangGraph'
```

## 写入 Annotation

官方接口：

```http
POST /v1/span_annotations?sync=true
Content-Type: application/json
```

请求体是批量结构：

```json
{
  "data": [
    {
      "name": "Overview",
      "annotator_kind": "HUMAN",
      "result": {
        "label": "Bad",
        "score": null,
        "explanation": "G3 工具选错（actual: list_tasks; expected: get_task_status）"
      },
      "metadata": {
        "case_id": "conv-015-turn-1",
        "session_id": "<talos_session_id>",
        "turn_id": "<talos_turn_id>"
      },
      "identifier": "conv-015-turn-1:Overview",
      "span_id": "<root_span_id>"
    }
  ]
}
```

`identifier` 建议固定为 `<case_id>:<annotation_name>`，重复提交时 Phoenix 会更新同一条 annotation，避免批量重跑产生重复标注。

curl 示例：

```bash
curl --noproxy '*' -sS -X POST \
  'http://192.168.12.239:6006/v1/span_annotations?sync=true' \
  -H 'Content-Type: application/json' \
  -d '{
    "data": [
      {
        "name": "tool_selection_accuracy",
        "annotator_kind": "HUMAN",
        "result": {
          "label": "get_task_status",
          "score": null,
          "explanation": "actual: list_tasks; expected: get_task_status"
        },
        "metadata": {
          "case_id": "conv-015-turn-1",
          "session_id": "<talos_session_id>",
          "turn_id": "<talos_turn_id>"
        },
        "identifier": "conv-015-turn-1:tool_selection_accuracy",
        "span_id": "<root_span_id>"
      }
    ]
  }'
```

成功响应：

```json
{
  "data": [
    {
      "id": "<annotation_id>"
    }
  ]
}
```

## 读回 Annotation

```bash
curl --noproxy '*' -sS \
  'http://192.168.12.239:6006/v1/projects/UHJvamVjdDoy/span_annotations?span_ids=<root_span_id>'
```

读回字段包括：

- `id`
- `source`: `API` 或 `APP`
- `name`
- `annotator_kind`
- `result.label`
- `result.score`
- `result.explanation`
- `metadata`
- `identifier`
- `span_id`

## 标注名称与口径

具体判定标准以 `../phoenix_annotation_criteria.md` 为准。当前固定使用：

- `Overview`
  - `label=Bad`: 明显错误时写入；除非特别需要，不主动写 `Good`
- `tool_selection_accuracy`
  - `label=<expected_tool>`
  - `explanation=actual: <actual_tools>; expected: <expected_tool>; <reason>`
- `expected_plan`
  - 用于计划识别，label 可写 `cc`、`re`、`cc,re` 等
- `expected_within_domain`
- `expected_within_capacity`
- `exp_match_type`
- `AnswerQuality-Correction`

## 已验证样例

已读回历史 API 标注：

- span_id: `bb4595b6a354f2c7`
- `Overview=Bad`
- `tool_selection_accuracy=get_task_status`
- source: `API`
