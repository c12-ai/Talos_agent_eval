# Agent 回复评价标准（v0 草稿）

> 用于在 `eval_turns.issues` 字段记录每轮 agent 回复发现的问题，并据此向 Phoenix 落标注。
> 后续可与 CLAUDE.md / claude_MVP.md 对齐细化。
> Phoenix 标注体系来源：[Phoenix 中创建数据集](https://carbon12.feishu.cn/wiki/XndZwjRXHiKyPEkPzhpcMucynBb)（飞书文档）。

## 评估对应的 Phoenix 标注体系（按 agent 流程节点）

整个 agent 工作流分 5 个评估节点。每个节点有专属的标注名（`annotation.name`），所有标注**统一落在该 turn 的根 span（LangGraph CHAIN，`turn_root_spans.root_span_id`）**。

### 节点 1：User Admittance（用户准入）

判定用户输入是否「在域内」「在能力内」。

**北极星指标**：Task Success Rate（用混淆矩阵呈现）：
- Within Domain Confusion Matrix（actual_true / actual_false × pred_true / pred_false）
- Within Capacity Confusion Matrix（同上）

**人工标注**（Categorical）：
| annotation.name | 取值 | 含义 |
|---|---|---|
| `expected_within_domain` | `true` / `false` | 此问题是否在 agent 业务域内 |
| `expected_within_capacity` | `true` / `false` | 此问题在能力范围内吗（可被处理） |

### 节点 2：Intention Detection（意图检测）

判定 goal type 是否匹配。

**北极星指标**：accuracy = `expected_goal_type == matched_goal_type / N`，分 In-context（如 CC、Steaming）/ Out-context 两端。

**人工标注**（Categorical）：
| annotation.name | 取值 | 含义 |
|---|---|---|
| `exp_match_type` | `query` / `execution` / `reject` | 期望的意图类型 |

### 节点 3：Query Agent（查询路径）

**指标**：
- Tool Selection Accuracy（工具调用正确性，参考 `mcp-tools.md`）
- Context Relevancy / Context Accuracy（**LLM 评判，不需人工**，用 BERT / ROUGE-L 等）
- Answer Quality (Trustworthiness)，1-5 分制：Reliability / Completeness / Prompt Compliance（**这三项也由 LLM 打分，不需人工**）

**人工标注**：
| annotation.name | 类型 | 取值 | 含义 |
|---|---|---|---|
| `tool_selection_accuracy` | Categorical | 期望的工具名（如 `get_robot_status`）| query_agent 应选用的工具 |
| `AnswerQuality-Correction` | Freeform | 自由文本 | 该轮回答的修正/补充意见（人工写） |

> 注：`AnswerQuality-Reliability/Completeness/PromptCompliance` 这三项虽然在 Phoenix UI 中存在 1-5 滑条，但**人工不需要打**，由 LLM 离线评分。

### 节点 4：Specialist Agents（CC / RE / TLC 执行路径）

**北极星指标**：E2E Success Rate。失败原因分三类：
1. Mismatched intention（在节点 2 已度量）
2. **Failed structured output**（如特殊字符截断了 streaming）—— 需要建一个自定义 span 覆盖 `spec → execute` 全过程，若中途断流即视为失败
3. **Invalid parameters**（参数无效）—— 用更强模型重新从 input 提取参数值，与 agent 给出的参数对比，不一致即失败

**Answer Quality (Trustworthiness)**：1-5 分制 Reliability / Completeness / Prompt Compliance（同 Query Agent，LLM 评分）

### 节点 5：Planner（计划生成）

**指标**：exact match score。比对 `actual_output_plan` 与 `expected_plan`。

**人工标注**：
| annotation.name | 类型 | 取值 | 含义 |
|---|---|---|---|
| `expected_plan` | Freeform | 逗号分隔的枚举列表 | 期望的计划，词表 `cc / re / tlc / lcms`，例：`cc,re,tlc` |

笔记本会拿 trace 上预测的 plan 与该期望对比算 exact-match accuracy。

### 节点 6：执行中查询（任务 conducting 期间的进度问询）★ 重点维度

任务下发后到 lab 完成之间通常 10-20 分钟，**真实用户在这期间会主动问**：

- 「机器人现在在干嘛？」 → 期望 `get_robot_status`
- 「这个任务跑到第几步了？」 → 期望 `get_task_detail`
- 「过柱机 1 号忙吗 / 实验室在跑啥？」 → 期望 `get_running_experiments`
- 「还要多久结束？」 → agent 应基于当前步骤 + 任务参数估算（也可调 `get_task_detail` 看剩余步骤）
- 「最近半小时这个任务发生了什么？」 → 期望 `get_recent_events` 或 `get_task_entity_events`

**评测要点：**

1. **G 维度（工具选择）**：上述问题必须调对应工具，调错算 G3
2. **C2 维度（不编造）**：lab 还没出 LC-MS / 称重等下游数据时，不能凭空给纯度 / 回收率 / 质量。conducting 期间能给的只有：
   - 机器人位置 / 状态
   - 当前正在执行的步骤名（如"主洗脱中"、"装样中"）
   - 步骤已耗时 / 预计剩余
   - 不能给：纯度、Rs、最终质量、回收率（这些要 LC-MS / 称重）
3. **D1 维度（语言）**：不允许把 lab 步骤名直接吐 (如 `setup_cartridges` / `start_evaporation`)，必须中文翻译

**自动化生成**：评测脚本 `run_conv.py` 在 `wait_for_lab_done_via_pg` 期间会每 ~3 min 穿插 1 个进度问询轮，依次循环上述典型问题。每个进度轮都进 `eval_turns` + Phoenix 标注。

### 整轮总评（覆盖全节点）

| annotation.name | 取值 | 含义 |
|---|---|---|
| `Overview` | `Good` / `Bad` | 该 turn agent 回复总体好坏，与前端 thumb up/down 同源；Bad 时 explanation 写理由（"notes"） |

---

## 评估维度（issues 字段记录哪些维度有问题）

每轮按以下维度逐项检查，把发现的问题以分号串拼写到 `issues`：

### A. 任务理解 / 流程对齐
- A1 是否正确识别用户意图（任务下发 / 查询 / 负例）
- A2 是否输出 plan（任务下发场景应在第 2 轮给出 `📋 plan`）
- A3 多任务流程顺序是否正确（TLC → column → lcms → fraction → rotovap → weighing）
- A4 是否在每个任务前先「确认推荐依据 → 推荐执行参数」

### B. 推荐依据 / 执行参数
- B1 推荐依据字段是否覆盖（参考 `BIC/任务执行参数总结.md`）
- B2 是否主动询问 spec 之外字段（违反 §7）
- B3 用户主动告知的化学约束（热敏 / 易氧化 / Boc 等）是否被合理使用
- B4 推荐执行参数是否合理（柱规格、梯度、压力档等）

### C. 工具调用 / 数据真实性
- C1 是否调用了合适的工具（参考 mcp-tools.md 语义边界）
- C2 工具回复字段是否在 spec 范围内（不编造纯度 / Rs / 半峰宽等）
- C3 完成通知是否符合任务链时序（过柱完成时不能有 LC-MS 才有的纯度）

### D. 用户友好性 / 措辞
- D1 是否中英文混杂（`step` / `skill` / `mcp-tools 未暴露` 等开发者术语禁止出现）
- D2 是否直接堆 JSON / key=value
- D3 长度是否匹配信息量

### E. 历史 / 上下文
- E1 是否依赖跨会话历史（"上次失败"等开场禁止）
- E2 用户已给的信息是否被复述确认而不是重复询问

### F. 角色对位
- F1 用户表达是否与角色匹配（研究员问具体技术 / 小组长问全局调度）
- F2 agent 回复是否针对该角色给到合适粒度

### G. 工具选择正确性 ★ 重点维度

**对照基准**：`mcp-tools.md` 列出的 10 个工具，每个工具下都有「典型问题」。当用户问的属于这些典型问题中的某一类时，agent 应当调用对应的工具。

| 工具 | 典型问题摘录 | 关键词 / 触发场景 |
|---|---|---|
| `get_robot_status` | 机器人在做什么？空闲吗？在哪个工位？ | 机器人 / robot / 工位 |
| `get_running_experiments` | 有哪些实验在跑？CC 系统在忙吗？ | 现在 / 当前 / 在跑 |
| `get_material_inventory` | 还有多少 cartridge？哪些试管架？还剩什么物料？ | 库存 / 剩 / 还有多少 / cartridge |
| `get_involved_materials` | 这个任务用到了哪些物料？某 task_id 涉及的设备状态 | 给定 task_id + 物料 / 设备 |
| `get_task_status` | 这个任务什么状态？任务参数是什么？ | 给定 task_id + 状态 / 参数 |
| `get_task_detail` | 任务跑到第几步？哪一步失败？每一步返回了什么？ | 给定 task_id + 步骤 / 失败原因 |
| `list_tasks` | 最近有哪些任务？失败任务？运行中的任务？ | 列表 / 多个 / 最近 |
| `get_task_entity_events` | 任务过程中 cartridge 经历了什么？设备在该任务中事件？ | 给定 task_id + 实体变化 |
| `get_entity_events` | 这台设备 / 这个 cartridge 历史？某机器人的轨迹？ | 给定 entity + 历史轨迹（跨任务）|
| `get_recent_events` | 过去一小时实验室发生了什么？最近 24 小时事件？ | 时间窗口 + 全局 |

**判定规则**：

- G1 **是否调用了工具**：用户问的若属于上述典型问题，agent 必须调用工具，不能凭空回答
- G2 **是否调用了正确工具**：调用的工具与「期望工具」一致（按上表关键词判定）
- G3 **是否调用错语义工具**：常见混淆——
  - `get_material_inventory`（全局库存）vs `get_involved_materials`（任务专属）
  - `get_task_status`（概览/参数）vs `get_task_detail`（步骤明细）
  - `get_entity_events`（跨任务历史）vs `get_task_entity_events`（任务期间）
- G4 **是否多调或漏调**：一问多调（无依据展开）或一问该调没调

**违规标记机制（Phoenix annotation）**：

- 当 G1/G2/G3/G4 任一不通过时，在 Phoenix 该 turn 的 **turn root span**（即 `name=LangGraph` / `kind=CHAIN` / `parent_id=None` 的顶层 span，与后端 `/api/sessions/{sid}/annotations` 的 Overview Good/Bad 落点一致）打一条 annotation：
  - `name`: `tool_selection_accuracy`
  - `annotator_kind`: `HUMAN`
  - `result.label`: 期望调用的工具名（如 `get_task_status`）
  - `result.explanation`: `actual: <实际调用的工具>; expected: <期望工具>`
- 同时把 `expected_tool` / `actual_tools` / `tool_match=false` / `tool_annotation_span_id` 写入 `eval_turns` 表
- 如果属于完全应当调而 agent 凭空回答（G1 漏调）则 `actual_tools=none`

**怎么拿到 turn root span_id**：

- 后端在 `stage_dispatcher` 节点会把当前 turn 的根 OTel span 写入 Postgres 表 `turn_root_spans (turn_id, session_id, root_span_id)`
- 评估脚本拿到 `turn_id` 后，直接 `SELECT root_span_id FROM turn_root_spans WHERE turn_id=%s AND session_id=%s` 即可
- Phoenix OTLP 摄入有 ~5-15s 时延，POST annotation 前需先轮询 `/v1/projects/UHJvamVjdDoy/spans` 确认 span 已可见

**Phoenix REST**：

- 写入：`POST http://192.168.12.239:6006/v1/span_annotations?sync=true`，body 见上
- 读回：`GET /v1/projects/UHJvamVjdDoy/span_annotations?span_ids=<span_id>`

**示例**：

| 用户问 | 期望工具 | 若 agent 实际调 | annotation |
|---|---|---|---|
| 「机器人现在在做什么？」 | `get_robot_status` | `get_running_experiments` | `label=get_robot_status, explanation=actual: get_running_experiments; G3 把"机器人状态"误判为"实验快照"` |
| 「task_id=xxx 这个任务跑到第几步了？」 | `get_task_detail` | `get_task_status` | `label=get_task_detail, explanation=actual: get_task_status; G3 概览 vs 明细混淆` |
| 「过去一小时实验室发生了什么？」 | `get_recent_events` | （未调任何工具，直接回答）| `label=get_recent_events, explanation=actual: none; G1 漏调工具` |

## issues 写法示例

```
B2 主动追问化合物热稳定性（spec 之外）; D1 回复中混入"return_code=0"
```

## 落库规则与 Phoenix 标注

每个 turn 评估完后，按以下规则同时写库 + 标 Phoenix（标注落点都是该 turn 的 root span：`name=LangGraph` / `kind=CHAIN` / `parent_id=None`）：

### Phoenix 标注：`Overview` —— 总体好坏（与前端 thumb up/down 共用）

- **label `Bad`** → 该轮 agent 回复**总体不好**时打。理由写入 `result.explanation`。前端 thumb-down 也走这条路径。
- **label `Good`** → 我们**不主动打**（前端 thumb-up 才打），除非将来需要做基线集
- 触发 Bad 的情形（命中任一即打）：
  - **D1 中英文混杂**：回复里有 `step` / `skill` / `return_code=0` / `mcp-tools 未暴露` / `task_id=xxx` 这种用户读不懂的开发者术语
  - **D2 直接堆 JSON / key=value**：把工具参数原样吐出
  - **A1 答非所问**：用户问 X，agent 回 Y 或者反复重复同一拒绝模板
  - **C2/C3 编造数据**：完成通知里给了纯度/Rs/质量等下游任务才有的字段
  - **E1 跨会话历史依赖**："上次 RXN045..." 这种开场
  - **G 工具选错**：详见下方 G 维度（同时打 `tool_selection_accuracy`）

  其余 issues（措辞瑕疵、缺少非关键信息等）不触发 Overview=Bad，避免噪声。

### Phoenix 标注：`tool_selection_accuracy` —— 工具选错（仅限 G 维度）

详见 §G。这是 G 维度专属的标注，与 `Overview=Bad` 可以并存。

### 数据库写入

- `eval_runs.notes` 写整 conv 的总结（如 "3/15 turn 触发 Overview=Bad"）
- `eval_turns.issues` 串拼该轮所有维度的违规码（如 "D1 出现 task_id=xxx; G3 把任务概览误判为任务明细"）
- 是否打了 Overview=Bad 通过 `eval_turns.annotated_in_phoenix` 标记
- 工具选错通过 `eval_turns.tool_match=false` + `tool_annotation_posted=true` 标记

## 评分汇总

按 Phoenix 标注口径统计：
- `Overview=Bad` 的 turn 数 / 总 turn 数 → 总体 Bad 率
- `tool_selection_accuracy` 的 turn 数 / 期望调工具的 turn 数 → 工具选错率

## User simulator 行为（评测脚本如何"扮演用户"）

> 这一节定义评测脚本怎么发用户消息。**核心：brief 是起点 + 参考，不是脚本**。

### S1. brief.user_turns 是参考剧本，不是必发的固定脚本

`/tmp/eval_briefs.json` 里每个 conv 的 `user_turns` 表达"这个场景下用户大致会问什么、提供什么信息"。它是**信息源 + 期望工具映射**，不是必须严格逐字逐序发出的脚本。

> 历史教训：早期一版 run_conv.py 把 user_turns 当严格脚本，跳过中间提供 spec 信息的轮次（比如 conv-001 的 [5] "化合物 X 200mg TLC 0.35 PE:EA 3:1"），导致 cc.spec 全 None、agent 无法生成 plan、Playwright 找不到面板按钮、submit 超时。**别再当脚本用**。

### S2. 用 LLM 扮演用户，根据 agent 回复自然回应

评测脚本应当用 LLM (Claude haiku 4.5 或更高) 当 user simulator：

- **输入**：brief.scene + brief.user_turns（参考素材）+ 对话历史 + 当前 state
- **输出**：下一句用户消息（中文，化学家口吻），或 `STOP` 结束对话

**LLM 应该自然回应**：
- agent 问 SMILES/上样量/TLC → 从 brief.user_turns 里抽对应值给（保持 conv 内一致性）
- agent 给 plan / spec 推荐 → 用化学家语气表达"批准/否定/调整"（**别说"我已点确认"**——面板动作由脚本自动做）
- agent 给 params 推荐 → 表达"下发"或"再调整某个参数"
- 任务下发后 → 自然问进度（"机器人在干嘛？"/"跑到第几步？"）；这种轮次 LLM 应该尽量从 brief.user_turns 找对应 expected_tool 的问题表达
- 对话收尾时机：任务已下发 + 进度问过 1-2 次 + （CC+RE conv）RE 也下发完 → 输出 `STOP`

### S3. 面板动作由脚本自动触发，不由 user simulator 表达

| 面板动作 | 触发条件（state-driven，不靠 user simulator 文字） |
|---|---|
| `approve_plan` | `state.plan` 出现 AND `state.plan.user_approval=False` |
| `confirm_cc_spec` | `state.cc.phase=collecting_spec` AND `cc.spec` 关键字段（sample_amount_g/solvents/solvent_ratio/rf_values）都非空 |
| `submit_cc_via_ui` | `state.cc.phase=collecting_params` AND `cc.params != None` AND user simulator 最近一句表达过下发意愿（启发式：含"下发/开始/可以"等） |
| `confirm_re_spec` | `state.re.phase=collecting_spec` AND `re.spec` 关键字段非空 |
| `submit_re` | `state.re.phase=collecting_params` AND `re.params != None` AND user simulator 表达过下发意愿 |

**注意**：`submit` 类动作多 1 个用户意愿条件——避免 params 一来就立刻 submit，让 LLM 有机会先表达"对参数不满意要调整"等场景。

### S4. CC + RE 组合 conv：CC 完成后用户必须主动发起 RE 对话

对于 `category=过柱+旋蒸` 的 conv（数据集里 11 个），plan 包含 `cc_agent + re_agent` 两个任务。**CC 任务完成后，agent 不会自动开始问 RE 的条件**——它只会等用户主动开口。User simulator 必须：

1. **CC lab 任务完成** + **2 个 [CONFIRM]** advance（让 cursor 推到 RE）→ 此时 `state.re.phase=collecting_spec`，但 `state.re.spec` 字段都是 None
2. **必须发一条对话消息**，主动说"现在做旋蒸 / 接着做旋蒸"，并**给出旋蒸需要的条件**（合并液体积、体系比例、温度等）。比如：
   - "现在做旋蒸：合并液大约 180 ml，体系沿用 PE/EA 4:1，水浴 35 度"
   - "把过柱出来的合并液（约 200 ml，PE/EA 3:1）旋蒸浓缩到干"
3. agent 抽到 RE spec → state 转到等 confirm_re_spec → 后续 panel 动作 / submit_re

**禁止做的**：CC done 后只发 [CONFIRM] 然后等——agent 不会自己起头问。也不要用进度问询（"机器人在干嘛"）当作 RE 触发——那会被路由到 query agent，不会激活 RE。

> 这条对话 turn 在评测里**算一个完整 user turn**，要进 eval_turns + Phoenix 标注（expected_tool=None，因为它不是工具典型问题）。

### S5. user simulator 的硬约束（LLM 提示词里必含）

- **绝不替面板说话**：禁说"我点了确认"/"我提交了"等
- **不出 [CONFIRM] / system / tool 这种系统标记**
- **不暴露元信息**：禁说"你是 LLM"/"按测评要求"/"我是模拟用户"
- **保持化学家身份**：术语自然、关注实验本身
- **STOP 时机**：自己判断对话该结束就输出 `STOP`，不要无限拖

---

## 操作层面的要求（非评测内容，但为完成评测必须遵守）

> 这一节不是要评测的维度，而是「**为了让评测真的跑起来、产出有效数据**」必须照做的操作流程。
> 历史教训：早期一批 conv 把 CC/RE 任务下发"以为"靠对话文本就能完成，结果会话停在 `phase=collecting_params` 前的状态，agent 后续的"任务完成通知"自然没发生——那些 turn 的 G/B/C 维度评测全部失效。务必避免重蹈覆辙。

### O1. CC/RE 任务下发必须通过右侧面板交互（不能只靠对话文字）

Talos 前端的"方案批准 / 推荐依据确认 / 推荐参数确认（任务下发）"三步必须由右侧面板触发，**仅发用户文本不会推进 state**。自动化脚本根据每一步的实际可靠性，**混合使用 AG-UI body 的 state 注入** 和 **Playwright UI click**：

| 面板动作 | 实现方式 | 模块 / 函数 |
|---|---|---|
| 方案批准 | state 注入：`plan.user_approval=true` + `[CONFIRM] 用户已通过工作流面板批准计划` | `talos_panel.py: approve_plan()` |
| CC 推荐依据确认（含 TLC 图片） | 优先 UI click：打开 TLC 识别面板 → 上传固定默认图片 `/Users/wuwenyan/Desktop/demo.jpeg` → 确认 Rf 值 → 底部点 `确认`；必要时 state 注入兜底 | 主：`talos_panel_ui.py: confirm_cc_spec_via_ui()`；备：`talos_panel.py: confirm_cc_spec()` |
| **CC 任务下发** | **Playwright UI click**：开会话 → `管理插槽` → 装 slot 001 → `保存` → 选下拉框样品柱 → `确认修改` | `talos_panel_ui.py: submit_cc_via_ui()` |
| RE 推荐依据确认 | state 注入：`re.phase="collecting_params"` + `re.params=null`；失败时 UI 兜底 | 主：`talos_panel.py: confirm_re_spec()`；备：`talos_panel_ui.py: confirm_re_spec_via_ui()` |
| **RE 任务下发** | **Playwright UI click**：`+ 添加茄形瓶` → 选 chip "瓶 1"（含 verify）→ 点 N 个试管（含 className verify）→ `确认修改`。详见 §O3 | `talos_panel_ui.py: submit_re_via_ui()` |
| **lab 任务完成等待** | **PG 轮询**：查 `runs.status` 直到 completed/failed | `talos_panel.py: wait_for_lab_done_via_pg()` |
| **状态机推进** | 发 [CONFIRM] turn 唤醒 graph 跑 specialist `_conduct` 同步 phase | `talos_panel.py: advance_via_confirm()` |

> 哪些步骤继续走 state 注入：
> - `approve_plan` 已验证 ≥10 次稳定（CHAIN 路径官方支持）
>
> 哪些步骤改走 UI click（state 注入会失败或前端体验差）：
> - `confirm_cc_spec`：前端 TLC 信息为必填项。默认打开 TLC 图片识别面板，上传 `/Users/wuwenyan/Desktop/demo.jpeg`，Rf 使用对话 / brief 中给出的目标值（例如 `0.40`）；没有明确值时使用面板默认值并保持可见确认。state 注入只作为 UI 上传不可用时的兜底。
> - `submit_cc`：lab 后端要求 sample_cartridge_location 对应槽位**已装柱**，state 注入只填字段不装柱 → lab 拒。UI 走"管理插槽 → 装柱 → 保存"才是真实的装柱路径
> - `confirm_re_spec`：state 注入触发 MCP fetch 偶尔报"已回退到编辑状态"。UI 点击触发的 fetch 路径稳定
> - `submit_re`：必须 UI 真点 `+ 添加茄形瓶` + 选 chip + 选试管 + `确认修改`。state 注入也能让 lab 接到任务，但前端面板显示不会同步刷新（看着像没操作直接提交了），用户体验奇怪。**默认走 UI click**

### O1.5 CC TLC 图片确认：固定使用默认图片

CC 推荐依据确认阶段，右侧面板的 TLC 信息是必填项。自动化 / 手工测试统一按下面流程处理：

1. 在"过柱参数预填"里点击 **"点击打开识别面板"**
2. 在"TLC 图片识别"弹窗上传固定图片 **`/Users/wuwenyan/Desktop/demo.jpeg`**
3. Rf 值使用当前测试用例给出的目标值；若面板已预填正确值（例如 `0.4`），保持不变
4. 点击弹窗里的 **"确认"**，再点击参数预填面板底部的 **"确认"**

这个默认图片路径是本机固定测试资产，每次 CC/过柱用例都使用同一张图即可；不要临时生成图片，也不要跳过 TLC 图片步骤。

### O2. CC 样品柱选择前置：自动化脚本通过 UI click 完成"管理插槽 → 装柱 → 保存"

CC 推荐参数确认时，"选样品柱"下拉框默认是空的（柱子还没装到机器人插槽）。手工 / 自动化流程都遵循：

1. 点击参数面板右侧的 **"管理插槽"** 按钮（`get_by_text("管理插槽", exact=True)`）
2. 弹窗"管理样品柱插槽"里点击目标空槽位 **`备料架L4层样品柱001位`**（`bic_09B_l4_001`）
3. 点击 **"保存"** 关闭弹窗
4. "选样品柱"下拉框现在出现一个新选项 `bic_09B_l4_001 → 样品柱 40g #xxx (备料架L4层样品柱001位)`，下拉选中
5. 点击 **"确认修改"**（注意是"确认修改"，不是"确认" —— v6 面板在用户改了下拉值后会自动切换按钮文字）

**不按这个流程走，下发会因 cartridge 不可用而失败。**

**两个关键不变量：**

- **装柱状态在 lab 后端持久化、跨会话保留** —— 一旦 slot 001 装好了，下个会话不需要重装。但实现层面 helper 必须做幂等检查：通过 slot button 的 className 判断
  - 已装：`border-emerald-` / `bg-emerald-50`
  - 空闲：`border-slate-200` / `border-dashed`
  - 已装就跳过点击，避免点 toggle 把它卸掉
- **"管理样品柱插槽"弹窗当前只渲染 001 / 002 两个槽位**（不是 6 个），数据集里其他 conv 用 `bic_09B_l4_002` 时也得用同样的逻辑

封装在 `talos_panel_ui.py`:
- `_ensure_slot_installed(page, slot_label)` 幂等装柱
- `_select_cartridge(page, slot_id)` 找到带该 slot 的下拉框并选中
- `_click_confirm_submit(page)` 候选 ["确认修改","确认","下发","提交"] 找到能点的就点

### O2.5 CC 硅胶柱规格：参数确认页只能选 12g

CC 推荐参数确认阶段，右侧面板里的 **"硅胶柱规格"** 必须统一选择 **`12g`**，即使 agent / 参数推荐里给出 `24g` 或 `40g`，提交前也必须在前端改成 `12g`。

原因：lab 后端当前可用耗材只保证 `silica_12g` 可下发；选择 `24g` 会出现类似下面的失败：

```text
No unused silica cartridge available for spec 'silica_24g'
```

操作要求：

1. 进入"过柱参数确认"页后，先检查 **"硅胶柱规格"** 下拉框
2. 如果不是 `12g`，必须通过 UI click / select 在前端改为 `12g`
3. 再继续执行"管理插槽"、选择样品柱和 `确认修改`

### O3. RE 下发：UI click（含茄形瓶 + 试管选择）

**默认走 UI click 路径** `talos_panel_ui.submit_re_via_ui(...)` —— 这条路用 Playwright 真点击右侧面板，前端显示状态和后端 user_input 完全一致，用户在浏览器里看到的就是真实操作过程。

**操作步骤**（必须全部完成，缺一个 lab 都拒绝）：

1. **添加茄形瓶**：点击参数面板里的 `+ 添加茄形瓶` 按钮。**至少 1 个**（lab 当前只支持 1 个 500ml）；agent 也会主动提示"系统目前只支持1个500ml茄形瓶"
2. **选中颜色 chip**：找到刚加的瓶子对应的颜色 chip（文字是 **`瓶 1`** —— 不是 `F1` / `Flask 1`），点击它进入"涂色模式"。**助手代码会验证 chip 是否真被选中**（看 className 是否含 `ring` / `shadow` 等高亮 class）；没选中就重点 1 次
3. **选试管入瓶**：点击至少 **1 个试管**（默认涂 5 个 tube 给"瓶 1"），剩下的 tube 默认进废液。**助手代码会按 tube 编号顺序点击 1, 2, 3, 4, 5**，并 verify 每个 tube 的 className 是否变化（变化才算涂色生效）
4. **点 `确认修改`** 提交：v6 面板在用户改了任何值后会自动把按钮文字从"确认"切到"确认修改"，候选关键词 `["确认修改", "确认", "下发", "提交"]`

**lab 真实接收的 user_input** 类似：
```python
{"flasks": ["500ml"], "collect_config": [1, 1, 0, 0, 0]}
# collect_config 长度 = tube 涂色总数；值 0=废液，N=瓶 N
```

> **不要走 state 注入**：早期我们写过 `submit_re()`（state 注入版），它直接给 backend 注入 `user_input` + `[CONFIRM]`，跳过 UI click。但前端 React 组件不会因 state 注入刷新，**面板显示和 backend 的 user_input 不同步**——用户在浏览器看着觉得"瓶子和试管都没选，怎么提交了"，体验奇怪。当前默认 `submit_re_via_ui`（UI click）保留 state 注入版本作 fallback。

### O3.5 RE spec → params：state 注入 + UI 兜底

state 注入路径：`confirm_re_spec(client, sid, priors)` —— 设 `re.phase=collecting_params, re.params=None` + 发 `[CONFIRM] ... (RE spec)`。多次跑通过。

**偶尔会出现 "已回退到编辑状态" 错误**（lab MCP fetch params 失败）。出现时退回 UI click 兜底：`talos_panel_ui.confirm_re_spec_via_ui(client, sid, session_title)` 点底部"确认"按钮 + 轮询等 params 落地。

### O3.6 lab 任务完成与 agent 状态机推进（重点 bug 与解法）

**问题**：lab 完成任务后会通过 message queue 通知 agent，consumer 把 PG `runs.status=completed` 写好，但 **agent 内部 state.cc.phase / state.re.phase 不会自动更新**。它的 phase=DONE 只在 specialist 的 `_conduct` handler 里更新，而 `_conduct` 仅在用户发**带 [CONFIRM] 前缀的消息**（`mode=execution`）时才被路由到。换句话说：

- ❌ 一直 poll `state.cc.phase` 等 done 是徒劳的
- ✅ 应该 poll PG `runs.status` 看 lab 真实状态，然后**主动发 [CONFIRM] 触发 graph 推进**

**实现**：

| Helper | 用途 |
|---|---|
| `wait_for_lab_done_via_pg(lab_server_id, timeout_sec=900)` | 直接查 PG，返回 (ok, status)；status ∈ {completed, failed, cancelled, timeout} |
| `advance_via_confirm(client, sid, repeat=1)` | 发 N 个 `[CONFIRM]` turn，让 graph 跑通 specialist `_conduct` → `task_finalize` |

**单 CC 收尾**：1 个 [CONFIRM] 即可 → cc.phase=DONE → task_finalize 标记 plan 任务完成。

**单 RE 收尾**：1 个 [CONFIRM] 即可。

**CC + RE 切换**：CC done 后需要 **2 个 [CONFIRM]**：
1. 第 1 个：cc._conduct 看到 terminal_status=COMPLETED → cc.phase=DONE → task_finalize → cursor 推到 1
2. 第 2 个：stage_dispatcher 路由到 next task (re_agent) → state.re 初始化 phase=collecting_spec、task_id 赋值

**spec 抽取**：第 2 个 [CONFIRM] 后 re.spec 字段是 None（系统不会自动从历史对话里抽取）。需要用户**再发一条 chat 消息**包含旋蒸条件（合并液体积、体系比例、温度等），re_agent 才会抽到 spec。

> 这条 spec 抽取的 chat turn 在评测里也算一个 user turn（计入 record_and_annotate）。

### O4. TLC 任务暂时不评

TLC 子流程相关的会话和 step 暂时跳过（不在本轮评测范围内）。涉及 TLC 的 conv 出现 TLC 步骤就让它走默认占位（`tlc_image_url` 用 placeholder URL，MCP 不真去 GET）。

### O5. Lab 一次只能跑 1 个任务

详见下方 §"Lab 串行约束"。所有 subagent 共享 `/tmp/eval_dispatch.lock` 文件锁，下发包在 `with DispatchLock(conv_id):` 内，任务跑完后自动 sleep 2 min 再释放锁。

### O6. 出错处理

任意 step 抛异常 / `wait_for_task_done` 超时 / 面板 helper 返回 `error` 字段非空：

1. **立刻停止当前 subagent**
2. 通过 `talos_panel.feishu_notify(text)` 发一条飞书消息（`feishu-cli msg send` 调本地 CLI；`receive_id` 是吴雯妍的 open_id）
3. 把错误写入 `eval_runs.notes`
4. 同 conv 不重试

---

## Lab 串行约束（重要）

Talos 后端的 lab service **同一时刻只能有 1 个执行中的任务**。多 conv 并行评测时必须遵守：

- 任意 conv 在准备 `state.cc.phase="submitting"` / `state.re.phase="submitting"` 之前，先查全局 lab 状态：若有任务 `phase ∈ {submitting, conducting}` 即视为占用
- 占用时**等待**：轮询直到该任务 `phase=done`
- 任务结束后**额外冷却 2 min** 再允许下个 conv 下发
- 实现建议：所有 subagent 共享一个全局文件锁（`/tmp/eval_dispatch.lock`）+ `get_running_experiments` 工具调用作交叉确认
- 一旦评测脚本崩溃 / 检测到不一致（如下发时仍有任务 in_progress），**立即停止**所有 subagent，通过飞书消息通知人工介入

## 待细化

- 把 B / C 的 spec 字段精确化到字段名级别
- 把 D 的禁词作为机器可识别正则
- F 的角色判定可能需要从 conv-XXX 的索引描述提取（"研究员 / 小组长 / 总监 / 运营"）
