# Phoenix 标注要求

> 本文只描述 Phoenix 标注口径、落点、annotation name、异常判定和读写规则。TALOS 前端操作流程见 `talos_operation_guide.md`。

## 1. 默认原则

Phoenix 标注默认原则：只标异常，不标正常。

- User Admittance、Intention Detection、Tool Call / Tool Selection 都符合预期时，可以不在 Phoenix 中打对应标注。
- 正常项仍应写入本地 `eval_turns` / review 文档，用于计算分母和复盘。
- Phoenix 只保留需要人工 review 的问题标注，或少量明确的 golden baseline。
- `Overview=Good` 默认不主动打。
- `expected_within_domain`、`expected_within_capacity`、`exp_match_type`、`expected_plan` 在结果正确时默认不主动打，除非本轮明确需要 golden baseline。

## 2. 标注落点

所有人工 annotation 都落在该 turn 的 root span：

- `name=LangGraph`
- `kind=CHAIN`
- `parent_id=None`
- 即后端 `turn_root_spans.root_span_id`

获取方式：

1. 后端在 `turn_root_spans (turn_id, session_id, root_span_id)` 记录 turn root span。
2. 拿到 `turn_id` 后查 root span id。
3. Phoenix OTLP 摄入可能有 5-15 秒延迟，POST 前先轮询 spans API 确认 span 可见。

## 3. Phoenix REST API

写入：

```http
POST http://192.168.12.239:6006/v1/span_annotations?sync=true
```

读回：

```http
GET http://192.168.12.239:6006/v1/projects/UHJvamVjdDoy/span_annotations?span_ids=<span_id>
```

请求体示例：

```json
{
  "data": [
    {
      "name": "Overview",
      "annotator_kind": "HUMAN",
      "result": {
        "label": "Bad",
        "score": null,
        "explanation": "方案确认后 agent 声称任务已提交，但 workflow-state 中 latest_run=null。"
      },
      "metadata": {
        "case_id": "conv-001-turn-1",
        "session_id": "<talos_session_id>",
        "turn_id": "<talos_turn_id>"
      },
      "identifier": "conv-001-turn-1:Overview",
      "span_id": "<root_span_id>"
    }
  ]
}
```

`identifier` 必须稳定，建议 `<case_id>:<annotation_name>`。重复提交同一 identifier 时 Phoenix 会更新原 annotation，避免重复标注。

## 4. Annotation Name 与取值

### 4.1 User Admittance

| annotation.name | 取值 | 含义 |
|---|---|---|
| `expected_within_domain` | `true` / `false` | 此问题是否在 agent 业务域内 |
| `expected_within_capacity` | `true` / `false` | 准入节点对能力边界的处理是否符合预期 |

`expected_within_capacity` 的口径：

- 支持任务被接受：`true`
- 不支持任务被正确拒绝：`true`
- 支持任务被错误拒绝：`false`
- 不支持任务被错误接受：`false`

正确准入默认不打 Phoenix 标注；只写本地评估记录。需要 golden baseline 时可标。

### 4.2 Intention Detection

| annotation.name | 取值 | 含义 |
|---|---|---|
| `exp_match_type` | `query` / `execution` / `reject` | 期望意图类型 |

意图正确默认不打 Phoenix 标注；意图识别错误或 golden baseline 才标。

### 4.3 Planner

| annotation.name | 类型 | 取值 |
|---|---|---|
| `expected_plan` | Freeform / Categorical | `cc` / `re` / `tlc` / `lcms` 或逗号分隔列表，例如 `cc,re` |

Planner 正确默认不打；计划错误或 golden baseline 才标。

### 4.4 Query Agent 工具选择

| annotation.name | 类型 | 取值 |
|---|---|---|
| `tool_selection_accuracy` | Categorical | 期望工具名，例如 `get_robot_status` |

只在工具漏调、错调、多调时标。工具调用正确时，不在 Phoenix 标 `tool_selection_accuracy`。

`result.explanation` 格式：

```text
actual: <实际调用工具>; expected: <期望工具>; <原因>
```

### 4.5 Answer Quality Correction

| annotation.name | 类型 | 取值 |
|---|---|---|
| `AnswerQuality-Correction` | Freeform | 人工写修正建议 |

用于说明该轮回答应该怎么改。常与 `Overview=Bad` 一起出现。

`AnswerQuality-Reliability`、`AnswerQuality-Completeness`、`AnswerQuality-PromptCompliance` 虽然 Phoenix UI 中存在，但人工不需要打，由离线 LLM 评分。

### 4.6 Overview

| annotation.name | 取值 | 含义 |
|---|---|---|
| `Overview` | `Good` / `Bad` | 该 turn agent 回复总体好坏 |

- `Overview=Bad`：该轮 agent 回复总体明显错误时打。
- `Overview=Good`：默认不主动打，除非前端 thumb-up 或将来需要基线集。

## 5. Overview=Bad 触发条件

命中任一即可打 `Overview=Bad`：

- A1 答非所问：用户问 X，agent 回 Y，或反复重复同一拒绝模板。
- C2/C3 编造数据：例如 lab 未出 LC-MS / 称重数据时给纯度、Rs、质量、回收率。
- D1 中英文混杂或暴露开发者术语：例如 `step`、`skill`、`return_code=0`、`task_id=xxx`。
- D2 直接堆 JSON / key=value。
- E1 跨会话历史依赖：例如“上次 RXN045...”开场。
- G 工具选错：同时打 `tool_selection_accuracy`。
- 状态不一致：agent 声称任务已提交/正在执行，但 workflow-state 中 `latest_run=null` 或仍停在 `collecting_spec` / `collecting_params`。

轻微措辞问题、缺少非关键信息等不触发 `Overview=Bad`，避免噪声。

## 6. 工具选择判定

当用户问的是工具典型问题时，agent 必须调用对应工具，不能凭空回答。

| 工具 | 典型问题 | 触发场景 |
|---|---|---|
| `get_robot_status` | 机器人在做什么？空闲吗？在哪个工位？ | 机器人 / robot / 工位 |
| `get_running_experiments` | 有哪些实验在跑？CC 系统在忙吗？ | 当前实验 / 在跑 / 仪器忙不忙 |
| `get_material_inventory` | 还有多少 cartridge？哪些试管架？还剩什么物料？ | 库存 / 剩余 / cartridge |
| `get_involved_materials` | 这个任务用到了哪些物料？某 task_id 涉及的设备状态 | 给定 task_id + 物料 / 设备 |
| `get_task_status` | 这个任务什么状态？任务参数是什么？ | 给定 task_id + 状态 / 参数 |
| `get_task_detail` | 任务跑到第几步？哪一步失败？每一步返回了什么？ | 给定 task_id + 步骤 / 失败原因 |
| `list_tasks` | 最近有哪些任务？失败任务？运行中的任务？ | 列表 / 多个 / 最近 |
| `get_task_entity_events` | 任务过程中 cartridge 经历了什么？设备在该任务中事件？ | 给定 task_id + 实体变化 |
| `get_entity_events` | 这台设备 / 这个 cartridge 历史？某机器人的轨迹？ | 给定 entity + 跨任务历史 |
| `get_recent_events` | 过去一小时实验室发生了什么？最近 24 小时事件？ | 时间窗口 + 全局事件 |

违规类型：

- G1 漏调：应调工具但未调。
- G2 工具不匹配：调用工具与期望工具不一致。
- G3 语义混淆：例如 `get_task_status` vs `get_task_detail`。
- G4 多调或漏调：无依据展开调用，或一问多意图时缺关键工具。

如果 G1-G4 任一不通过：

1. 在 turn root span 打 `tool_selection_accuracy`。
2. `result.label` 写期望工具名。
3. `result.explanation` 写 `actual` 与 `expected`。
4. 同时可打 `Overview=Bad`。
5. 本地记录 `expected_tool`、`actual_tools`、`tool_match=false`、`tool_annotation_span_id`。

工具调用正确时，只在本地记录 `tool_match=true`，Phoenix 不打 `tool_selection_accuracy`。

## 7. 执行中查询异常的标注方式

执行中追问的发起方式属于 TALOS 操作要求，详见 `talos_operation_guide.md`。Phoenix 只在这些追问暴露问题时标注：

- 工具漏调、错调、多调：打 `tool_selection_accuracy`，必要时同时打 `Overview=Bad`。
- 回答编造纯度、Rs、最终质量、回收率等下游数据：打 `Overview=Bad`。
- 直接暴露 lab 步骤英文名，例如 `setup_cartridges` / `start_evaporation`：视严重程度打 `Overview=Bad`。
- 工具调用正确且回答真实：不打 Phoenix 标注，只写入本地 review 记录。

## 8. issues 字段

本地 `eval_turns.issues` 用分号拼接问题码和说明。

维度：

- A：任务理解 / 流程对齐
- B：推荐依据 / 执行参数
- C：工具调用 / 数据真实性
- D：用户友好性 / 措辞
- E：历史 / 上下文
- F：角色对位
- G：工具选择正确性

示例：

```text
B2 主动追问化合物热稳定性（spec 之外）; D1 回复中混入 return_code=0
```

## 9. 落库规则

每个 turn 评估后：

- `eval_turns.issues` 记录问题。
- `eval_turns.annotated_in_phoenix` 记录是否已打 Phoenix 标注。
- 正确但未标注的 turn 仍应保留本地评估结果。
- `eval_runs.notes` 写整轮总结，例如 “3/15 turn 触发 Overview=Bad”。
- 工具错误写 `tool_match=false` 和 `tool_annotation_posted=true`。

## 10. 评分汇总

按本地评估结果 + Phoenix 问题标注统计：

- `Overview=Bad` turn 数 / 总 turn 数 → 总体 Bad 率。
- `tool_match=false` turn 数 / 期望调工具 turn 数 → 工具选错率。
- Phoenix 中的 `tool_selection_accuracy` 只表示工具问题样本，不表示所有工具检查样本。

## 11. 常见标注示例

| 场景 | Phoenix 标注 |
|---|---|
| 用户问“机器人现在在干嘛”，agent 没调工具直接回答 | `tool_selection_accuracy` label=`get_robot_status`; explanation=`actual: none; expected: get_robot_status; G1 漏调工具`；同时 `Overview=Bad` |
| 用户问“任务跑到第几步”，agent 调了 `get_task_status` | `tool_selection_accuracy` label=`get_task_detail`; explanation=`actual: get_task_status; expected: get_task_detail; G3 概览 vs 明细混淆` |
| conducting 阶段编造纯度/回收率 | `Overview=Bad`; explanation=`C2/C3 conducting 阶段编造下游数据` |
| 负例请求被接受并生成 plan | `Overview=Bad`; explanation=`A1 准入错误，负例应 reject` |
| 不支持任务被正确拒绝 | 默认不标；如做 baseline，则 `expected_within_domain=true`、`expected_within_capacity=true`、`exp_match_type=reject` |
