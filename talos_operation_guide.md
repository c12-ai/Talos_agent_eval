# TALOS 测试操作说明

> 本文只描述如何操作 TALOS 前端、右侧实验工作流面板、任务下发和批量调度。Phoenix 标注规则见 `phoenix_annotation_criteria.md`。

## 1. 会话命名

每次批量测试使用统一前缀：

```text
[YYYYMMDD-HHMM-convXXX]
```

- `YYYYMMDD-HHMM` 使用批次启动时刻，Asia/Shanghai。
- `convXXX` 使用输入文档里的 case id，例如 `conv001`、`conv015`。
- 同一批次的多个会话使用同一个时间前缀，只改变 conv id。
- 会话标题必须能在 TALOS 左侧会话列表中看到。

## 2. User Simulator 行为

`agent_eval_review.md` / `eval_inputs/test_inputs.md` 是参考素材，不是必须逐字发送的脚本。

User simulator 应该像真实化学家一样自然回应：

- agent 问 SMILES、上样量、TLC：从输入用例中抽取对应信息回答。
- agent 给 plan / spec 推荐：用自然语言表达批准、否定或调整，不要说“我已点确认”。
- agent 给 params 推荐：表达“下发”“开始”或需要调整的参数。
- 任务下发后：主动问 1-3 个进度问题，例如“机器人现在在干嘛？”“任务跑到第几步了？”。
- 任务完成、进度问询足够、或多任务流程全部下发后，可以 `STOP`。

硬约束：

- 不说“我点了确认”“我提交了”等替面板操作的话。
- 不输出 `[CONFIRM]`、system、tool 等系统标记。
- 不暴露“测评”“模拟用户”“LLM”等元信息。
- 保持化学家身份和实验语气。

## 3. 面板动作触发原则

TALOS 的方案批准、推荐依据确认、推荐参数确认必须通过右侧实验工作流面板触发。只发用户文本不会可靠推进 state。

| 面板动作 | 触发条件 |
|---|---|
| `approve_plan` | `state.plan` 出现且 plan 未确认 |
| `confirm_cc_spec` | `cc.phase=collecting_spec` 且 CC spec 关键字段已存在 |
| `submit_cc_via_ui` | `cc.phase=collecting_params` 且用户已表达下发意愿 |
| `confirm_re_spec` | `re.phase=collecting_spec` 且 RE spec 关键字段已存在 |
| `submit_re_via_ui` | `re.phase=collecting_params` 且用户已表达下发意愿 |

`submit` 类动作必须等用户表达下发意愿，避免参数一出现就自动提交。

## 4. CC / 过柱操作要求

### 4.1 TLC 图片确认

过柱推荐依据确认阶段，TLC 信息必填。统一流程：

1. 在“过柱参数预填”里点击“点击打开识别面板”。
2. 上传固定图片：`/Users/wuwenyan/Desktop/demo.jpeg`。
3. Rf 值使用当前测试用例给出的目标值；如果面板已预填正确值，保持不变。
4. 点击弹窗“确认”，再点击参数预填面板底部“确认”。

每次 CC/过柱用例都使用这张默认图片，不临时生成图片，不跳过 TLC 图片步骤。

### 4.2 样品柱插槽

过柱参数确认时，必须先确保样品柱 slot 已安装，再选择样品柱：

1. 点击“管理插槽”。
2. 在“管理样品柱插槽”弹窗里点击测试用例指定的目标槽位。
3. 如果使用 `bic_09B_l4_002`，必须安装 002 的柱子，不能安装 001 后再选择 002。
4. 点击“保存”关闭弹窗。
5. 在“选样品柱”下拉框中选择包含目标 slot 的样品柱。
6. 点击“确认修改”提交。

关键不变量：

- slot 状态在 lab 后端持久化、跨会话保留。
- helper 必须幂等检查已装/未装状态，避免再次点击把柱子卸掉。
- 当前弹窗只渲染 001 / 002 两个槽位，数据集里用 002 的都按同样逻辑处理。

### 4.3 硅胶柱规格

过柱参数确认页的“硅胶柱规格”只能选 `12g`。

- 即使 agent 推荐 `24g` 或 `40g`，提交前也必须在前端改成 `12g`。
- 原因：lab 当前可用耗材只保证 `silica_12g` 可下发。
- 选择其他规格可能触发 `No unused silica cartridge available for spec ...`。

## 5. RE / 旋蒸操作要求

### 5.1 RE spec → params

旋蒸推荐依据确认后，需要进入旋蒸参数确认阶段。

- 优先通过右侧面板点击确认。
- 如果 UI 点击无法推进，应记录当前 `workflow-state`，不要假定任务已下发。
- 只有当 `re.phase=collecting_params` 且 `re.params != null` 时，才进入最终参数提交。

### 5.2 RE 参数提交

旋蒸最终下发必须通过 UI 点击，保证前端显示和后端 `user_input` 一致：

1. 点击“+ 添加茄形瓶”。
2. 选择颜色 chip“瓶 1”，确认 chip 已高亮。
3. 点击 1-5 号试管，把它们配置到瓶 1。
4. 气压梯度时长按用例要求设置；快速验证流程时可全部设为 1 分钟。
5. 点击“确认修改”提交。

lab 真实接收的 `user_input` 类似：

```python
{"flasks": ["500ml"], "collect_config": [1, 1, 1, 1, 1]}
```

不要只通过 state 注入跳过 UI。state 注入会导致前端面板显示和后端状态不一致，用户 review 时会看不到实际操作。

## 6. 任务完成与状态机推进

lab 完成任务后，agent 内部 `state.cc.phase` / `state.re.phase` 不一定自动更新。正确做法：

1. 轮询 lab run / workflow-state，确认 lab run 真实状态。
2. lab run 到 terminal 状态后，再通过系统确认 turn 触发 agent 状态机推进。

单 CC 或单 RE 收尾通常 1 个确认 turn 即可。

当前执行模式是：**一个会话在批准方案后，只按该方案里已经确认的任务执行**。如果方案只包含 CC，则该会话只执行 CC；如果方案只包含 RE，则该会话只执行 RE。方案批准并开始执行后，不能再在同一会话里追加新任务。此时用户要求“接着做旋蒸 / 再加一个过柱”时，TALOS 提示新建一个会话是合理行为，不记录为问题，也不打 Phoenix 异常标注。

## 7. CC + RE 组合流程

对于“过柱 + 旋蒸”用例，必须在批准方案前让方案同时包含 CC 和 RE。不能先批准一个只包含 CC 的方案，等 CC 完成后再在同一会话追加 RE。

流程：

1. 首轮用户消息就明确请求“先过柱再旋蒸”，并给出 CC 和 RE 的必要条件。
2. 等 agent 生成 plan。
3. 批准方案前必须检查 plan 是否包含两个任务：`cc_agent` 和 `re_agent`。
4. 如果 plan 只包含 CC 或只包含 RE，且测试目标是 CC+RE，则不要批准该方案；应记录为 planner/plan mismatch，并按 Phoenix 标注要求标 `expected_plan=cc,re`。
5. 只有 plan 中已经包含 CC+RE，才点击“批准方案”。
6. 按右侧面板顺序完成 CC spec/params、提交 CC。
7. CC lab run terminal 后，按该已批准方案继续推进到 RE task。
8. 按右侧面板完成 RE spec/params、提交 RE。
9. RE lab 任务进入 `conducting` 后，主动插入 1-3 个执行中追问。

首轮 CC+RE 用户消息示例：

```text
我想做一个先过柱再旋蒸的纯化流程。过柱条件：化合物 SMILES 是 CC(=O)Oc1ccccc1C(=O)O，上样 200 mg，TLC Rf=0.35，展开剂 PE:EA=3:1。旋蒸条件：把过柱收集到的目标馏分合并，合并液 50 mL，溶剂 PE:EA=3:1，水浴 35 度，收集 1-5 号试管，气压梯度每段时长都设为 1 分钟。请生成包含过柱和旋蒸两个任务的方案，我会在右侧面板确认。
```

禁止：

- 批准只包含 CC 的方案后，再在同一会话里追加 RE。
- 把 TALOS 对“方案执行完后请新建会话”的提示标记为问题。
- 把 `[CONFIRM] 实验已完成`、系统确认 turn、任务完成提示或进度查询当作 RE 发起。
- 在 plan 未包含 RE 的情况下，把“没有 RE task”标成 TALOS 执行异常；真正的问题应发生在批准前的 planner 检查阶段。

成功判据：

- 批准前 `workflow-state.plan.plan_draft` 或右侧任务列表明确包含 CC 和 RE 两个任务。
- 执行过程中 `workflow-state.tasks` 依次出现 `cc_agent` 和 `re_agent`。
- CC 和 RE 都有对应 lab run，且按顺序 terminal。

失败判据：

- 首轮用户明确要求 CC+RE，但 plan 只生成 CC 或只生成 RE。
- plan 包含 CC+RE，但批准后未按方案推进到第二个任务。
- plan 包含 CC+RE，但 RE spec/params 面板无法确认或 lab run 无法下发。

### 7.1 自动化实现方案

测试脚本必须显式维护 CC + RE 会话状态：

```text
plan_generated
  -> plan_checked_contains_cc_re
  -> plan_approved
  -> cc_submitted
  -> cc_completed
  -> re_plan_ready
  -> re_submitted
  -> re_completed
```

推荐实现：

1. `plan_generated`：等待 agent 生成方案。
2. `plan_checked_contains_cc_re`：读取右侧任务列表或 `workflow-state.plan.plan_draft`，确认包含 `cc_agent` 和 `re_agent`。
3. 如果 plan 不包含 CC+RE，不点击批准方案；记录 planner mismatch。
4. `plan_approved`：只有检查通过后才批准方案。
5. `cc_submitted` / `cc_completed`：完成 CC 面板提交并等待 CC lab run terminal。
6. `re_plan_ready`：按已批准方案推进到 RE task，等待 RE spec/params 面板。
7. `re_submitted` / `re_completed`：提交 RE 并等待 RE lab run terminal。

归因规则：

- 如果批准前 plan 只包含 CC，后续同会话追加 RE 被拒绝是合理行为，不标异常。
- 如果首轮明确要求 CC+RE 但 plan 只包含 CC，应标 planner/plan mismatch，而不是执行阶段 RE 异常。
- 如果 plan 已包含 CC+RE，批准后未出现 `re_agent`，才按 TALOS 的计划执行/状态推进异常标注。
- 已进入 `re_plan_ready`，但右侧面板无法提交：按 TALOS 面板或参数确认异常记录。

## 8. 执行中主动追问

每个真实下发的 lab 任务进入 `conducting` 后，必须主动插入 1-3 个追问轮。这个要求是批量测试的硬门禁，不是可选人工习惯。

自动化脚本必须实现一个固定阶段：

```text
submit_*_via_ui
  -> wait_for_submission_state(latest_run.lab_server_id != null)
  -> post_submit_progress_queries
  -> wait_for_lab_terminal
```

`post_submit_progress_queries` 的规则：

1. 读取 `workflow-state`，确认 `latest_run.lab_server_id` 已存在。
2. 如果 `latest_run.status` 不在 `completed / failed / cancelled / discarded / timeout`，必须在左侧对话框发送至少 1 个、默认 2 个进度追问。
3. 追问完成后，脚本结果里必须写入 `progress_questions` 列表。
4. 如果任务太快完成，导致没有机会在执行中追问，必须在异常/评估记录里写明 `progress_questions_skipped: task already terminal`。
5. 如果脚本没有执行这个阶段，本会话不能算完整跑通。

示例：

| 追问 | 期望工具 / 处理 |
|---|---|
| “机器人现在在干嘛？” | `get_robot_status` |
| “这个任务跑到第几步了？” | `get_task_detail` |
| “过柱机 1 号忙吗？” / “实验室在跑啥？” | `get_running_experiments` |
| “还要多久结束？” | 基于当前步骤估算，也可调用 `get_task_detail` |
| “最近半小时这个任务发生了什么？” | `get_recent_events` 或 `get_task_entity_events` |

追问必须发生在 lab run 真实状态处于执行中或等待中。任务已完成后再问，不算执行中追问。

执行中回答要求：

- 可以回答机器人位置、状态、当前步骤、已耗时、预计剩余。
- 不能编造纯度、Rs、最终质量、回收率等下游结果，除非工具或后续任务已有真实结果。
- lab 步骤名必须中文化，不要直接吐 `setup_cartridges` / `start_evaporation`。
- 如果这些追问暴露工具选择或回答质量问题，再按 `phoenix_annotation_criteria.md` 标注。

## 9. Lab 串行约束

TALOS lab service 同一时刻只能有 1 个执行中任务。

批量测试可以同时打开多个会话、并行完成对话和参数预填，但最终下发物理任务必须串行：

1. 下发前获取全局锁，例如 `/tmp/eval_dispatch.lock`。
2. 确认当前没有其他任务处于 `submitting` / `conducting`。
3. 点击当前会话右侧面板最终确认。
4. 等待 lab run 完成或失败。
5. 额外冷却 2 分钟。
6. 释放锁，让下一个会话下发。

如果脚本崩溃或检测到多个任务同时执行，应立即停止所有 subagent，并通知人工介入。

## 10. 多会话 subagent 批量执行顺序

可以用 subagent 同时发起多个 TALOS 会话，例如一次打开 5 个会话并让它们各自完成对话、生成 plan、进入参数预填。但 **lab 任务提交必须按会话顺序串行**，不是只按单个 task 串行。

### 10.1 核心规则

如果同时发起 `conv1` 到 `conv5`：

1. `conv1` 拥有提交权。
2. `conv2` 到 `conv5` 可以继续对话、等待 agent 回复、准备右侧面板，但不能点击任何最终下发按钮。
3. 只有 `conv1` 的全部 lab 任务都完成后，`conv2` 才能提交第一个 lab 任务。
4. 只有 `conv2` 的全部 lab 任务都完成后，`conv3` 才能提交。
5. 依次类推，直到该批次结束。

“全部 lab 任务”指该会话计划里的所有真实执行任务。例如：

- 单过柱会话：CC 完成后，该会话释放提交权。
- 单旋蒸会话：RE 完成后，该会话释放提交权。
- 过柱 + 旋蒸会话：CC 完成后不能释放提交权；必须继续在同一个会话里完成 RE，RE 也完成后才释放提交权。

### 10.2 不允许的行为

- `conv1` 的 CC 还在执行时，`conv2` 不能执行 `submit_cc_via_ui` 或 `submit_re_via_ui`。
- `conv1` 的 CC 完成但 RE 还没下发或还没完成时，`conv2` 仍不能提交任务。
- 不能因为 `conv2` 的参数面板已经准备好，就抢先点击“确认修改”。
- 不能让多个 subagent 各自只盯自己的 workflow-state 后自行提交；必须有批次级调度器控制提交权。

### 10.3 推荐调度模型

每个会话一个 subagent，另有一个调度器维护批次队列：

```text
queue = [conv1, conv2, conv3, conv4, conv5]
active_submit_owner = conv1
```

每个 subagent 可以并行做：

- 创建会话和设置标题。
- 发送用户消息。
- 等 agent 回复。
- 批准 plan。
- 准备 spec / params 面板。
- 记录当前 workflow-state。
- 等待自己的提交权。

只有 `active_submit_owner` 可以做：

- `submit_cc_via_ui`
- `submit_re_via_ui`
- 任何会导致 lab run 创建的最终确认按钮

当前 owner 完成自己会话中的所有 lab 任务后，调度器把提交权交给下一个会话。

### 10.4 CC + RE 会话的提交权

如果当前 owner 是过柱 + 旋蒸会话：

1. 批准方案前检查 plan 已经包含 CC 和 RE。
2. 如果 plan 只包含 CC 或只包含 RE，不批准方案，记录 planner mismatch。
3. plan 检查通过后批准方案。
4. 提交 CC。
5. 等 CC lab run terminal。
6. 按已批准方案推进到 RE task。
7. 确认 RE spec。
8. 提交 RE。
9. 等 RE lab run terminal。
10. 该会话完成，释放提交权。

只有第 10 步完成后，下一个会话才能提交任何 lab 任务。

### 10.5 实现建议

- 使用批次级文件锁，例如 `/tmp/eval_dispatch.lock`，但锁的持有粒度是“整个会话的所有 lab 任务”，不是单个 lab task。
- 锁 owner 写入当前会话 id，例如 `20260513-1140-conv1`。
- 非 owner subagent 只能轮询等待，不得点击最终确认。
- 调度器同时轮询 owner 的 `workflow-state` 和 lab run status。
- 对 CC + RE owner，会话调度器必须持有一个显式状态，例如 `plan_generated -> plan_checked_contains_cc_re -> plan_approved -> cc_submitted -> cc_completed -> re_ready -> re_submitted -> re_completed`。
- 如果 plan 检查不包含 CC+RE，不能点击批准方案；该会话停止并记录 planner mismatch。
- 如果已经批准了只包含 CC 的方案，该会话 CC 完成后就结束；后续 RE 必须新建会话，不能把同会话追加 RE 被拒绝记录为问题。
- owner 会话失败、超时或被人工中断时，调度器停止整个批次，不自动跳到下一个会话，避免污染后续评测。

## 11. 出错处理

任一 step 抛异常、等待超时、面板 helper 返回 error：

1. 停止当前会话的自动化流程。
2. 记录 `workflow-state`、session_id、run_id、当前页面状态。
3. 不把未下发的任务记为通过。
4. 如 agent 文本与后端状态不一致，例如声称“任务已提交”但 `latest_run=null`，应交给 Phoenix 标注流程标 `Overview=Bad`。

### 11.1 服务异常记录

后续每个批次测试都必须额外产出一份服务异常记录文档，路径放在 `eval_outputs/`，文件名建议：

```text
service_exception_log_YYYYMMDD_HHMM.md
```

只要测试过程中观察到任何服务异常，都要记录。服务异常包括但不限于：

- TALOS 前端无响应、输入框长期禁用、按钮点击无效果。
- 用户发送消息后 agent 无回复或回复为空。
- 右侧工作流面板没有生成预期 plan/task。
- `workflow-state` 与前端或 agent 文本明显不一致。
- Phoenix trace 显示模型服务、工具服务、lab service、上传服务、Phoenix API 等异常。
- lab run 长时间卡在某一步，或返回 failed/error。
- 图片上传、插槽管理、参数确认等服务端接口失败。

每条异常至少记录以下字段：

| 字段 | 说明 |
|---|---|
| 时间点 | 使用 Asia/Shanghai 精确到分钟；能定位到 trace 时同时记录 UTC 时间。 |
| 批次 / 会话 | 记录批次名、会话标题、session_id。 |
| 操作 | 当时正在做什么，例如“发送首轮 CC 请求”“点击批准方案”“上传 TLC 图片”“CC 完成后主动发起 RE”。 |
| 异常表现 | 用户可见表现和后端表现都要写，例如“前端无回复、右侧无任务、workflow-state plan=null/tasks=[]”。 |
| 证据 | 记录可复查证据，例如 workflow-state 摘要、Phoenix span_id/trace_id、错误文本、run_id、页面状态。 |
| 尝试过的解决方式 | 写明做过什么排查或恢复，例如刷新会话、重新打开 session、轮询 workflow-state、查询 Phoenix trace、重试点击。 |
| 是否解决 | `已解决` / `未解决` / `绕过`，并写一句结论。 |
| 后续处理 | 是否写入 Phoenix 标注，是否停止该会话，是否阻塞整个批次。 |

异常记录文档只写人工可读摘要，不保存大段 JSON。原始 JSON 只可作为临时调试文件，批次结束后清理。

模板：

```md
## 异常 1：一句话后无回复且右侧无任务

- 时间点：
- 批次 / 会话：
- 操作：
- 异常表现：
- 证据：
- 尝试过的解决方式：
- 是否解决：
- 后续处理：
```

## 12. TLC 任务

TLC 子流程暂不作为本轮重点评测。涉及 TLC 的 conv 出现 TLC 步骤时，按当前默认占位逻辑处理，不额外展开 TLC 自动化。
