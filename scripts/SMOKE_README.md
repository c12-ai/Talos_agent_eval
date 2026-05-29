# Smoke Runner (conv-008 链路验证) · Handoff

`scripts/smoke_runner_20260518.py` 驱动 dataset 里的 conv 通过 live TALOS 前端跑一遍，产 Phoenix spans 供后续标注。CC+RE 类（如 `conv-008`）会真在 lab 上跑两段流水线。

最后一次端到端验证（2026-05-20）：

| 段 | session | run_id | 结果 |
|---|---|---|---|
| CC | `4d705cc8-60c7-49e5-bc21-8a602d4de004` | `7a513e74-e1f1-432f-861e-27e7b33ee763` | `completed` |
| RE | （同上） | `958770b8-baff-4225-923c-e52964192fdd` | `completed` |

## 依赖

```bash
pip install -r requirements.txt
playwright install chromium
```

Python ≥ 3.10。

**用户模拟器引擎（user-sim）**——默认引擎是 `codex`，所以跑通完整流程**需要先装好并登录 codex CLI**：

```bash
codex --version      # 确认已安装（本仓库验证用的是 codex-cli 0.130）
codex login          # 首次需登录授权（走 codex 自己的 OpenAI 额度，不需要 Anthropic key）
```

- codex 引擎不消耗 Anthropic 额度，也不吃 Anthropic 限流——这是默认选它的原因。
- 不想用 codex：`--user-sim-engine claude`（需装好 `claude` CLI 并登录）或 `--user-sim-engine api`（需 `pip install anthropic` + 设 `WWY_ANTHROPIC_API_KEY`）。
- 完全不想要模拟器：`--no-user-sim`，逐字回放脚本，**不依赖任何 CLI / key**。
- 引擎缺失（codex 不在 PATH / 未登录）时脚本不会崩，会打日志回落到脚本原文——但那样就不是"灵活回复"了，所以要跑通本意，先把 codex 装好。

详见下方「用户模拟器（user-sim）」段。

## 网络前置

脚本访问两个服务：TALOS API（默认 `:8080`）、Phoenix（默认 `:6006`）。这两个 base URL **不再写死在脚本里**——relay IP 每次 Tailscale 重登就换，写死会过期。

**配置方式**（优先级：CLI flag > 环境变量 > 脚本里的 fallback 默认值）：

```bash
# 方式 A：环境变量（推荐，一次 export 全程生效；judge 也读同样的 PHOENIX_BASE）
export TALOS_BASE=http://<relay-ip>:8080
export PHOENIX_BASE=http://<relay-ip>:6006
python3 scripts/smoke_runner_20260518.py conv-008

# 方式 B：CLI flag（单次覆盖）
python3 scripts/smoke_runner_20260518.py conv-008 \
    --talos-base http://<relay-ip>:8080 --phoenix-base http://<relay-ip>:6006
python3 scripts/judge_20260518.py <run.json> --phoenix-base http://<relay-ip>:6006
```

`<relay-ip>` 是**当前 Mac 的 Tailscale IP**，由 Mac 上 `ssh -L <relay-ip>:{8080,6006}:localhost:{8080,6006} newbox` 中转。IP 会变（Tailscale 重登就换）；变了先在 Mac 上 `tailscale ip -4` 重读，再 export / 传 flag——**不用改脚本**。脚本里的 fallback 默认值（`smoke_runner_20260518.py` 顶部 `DEFAULT_TALOS_BASE` / `DEFAULT_PHOENIX_BASE`）是最后兜底，大概率已经过期，别依赖它。

启动时脚本会打印一行 `endpoints: TALOS_BASE=... PHOENIX_BASE=...`，跑之前扫一眼确认连的是对的 IP。

## Lab 前置

跑 `--allow-dispatch` 前确认：

- 样品柱插槽 `bic_09B_l4_002`（"备料架L4层样品柱002位"）装着空闲的 12g 硅胶柱
- PE、EA 溶剂量足
- 旋蒸瓶（茄形瓶）就位
- lab 当前没人并行跑

## 运行

```bash
# 安全干跑（不真下发，~5 min；CC submit 在 panel 门禁停住，RE finalize 跳过）
python3 scripts/smoke_runner_20260518.py conv-008

# 完整跑（真下 CC + RE 实验，~50 min lab 时间）
python3 scripts/smoke_runner_20260518.py conv-008 --allow-dispatch

# 想要 JSON 诊断快照（不指定就只打 stdout）
python3 scripts/smoke_runner_20260518.py conv-008 --allow-dispatch --out /tmp/smoke.json
```

CLI args:

| flag | 默认 | 含义 |
|---|---|---|
| `conv_ids` | 必填 | 一个或多个 conv id（如 `conv-008`） |
| `--allow-dispatch` | off | **不带就不真下发**；带了 CC、RE 都真跑 |
| `--headed` | off | playwright 显示窗口（这台 ubuntu 没显示器） |
| `--rf` | `0.35` | TLC Rf 默认填值 |
| `--slot-id` | `bic_09B_l4_002` | CC 用的样品柱位 |
| `--slot-label` | `备料架L4层样品柱002位` | 同上的中文 label |
| `--out` | 无 | 不指定就**不写文件**；指定路径会同时写 JSON 诊断 |
| `--talos-base` | `$TALOS_BASE` 或 fallback | TALOS API base URL；覆盖环境变量。relay IP 变了用这个传 |
| `--phoenix-base` | `$PHOENIX_BASE` 或 fallback | Phoenix base URL；覆盖环境变量 |
| `--no-user-sim` | 默认开 | 关掉 LLM 用户模拟器，逐字回放脚本 turn（旧行为） |
| `--user-sim-engine` | `$USER_SIM_ENGINE` 或 `codex` | 模拟器引擎：`codex`（默认，走 OpenAI 额度，无需 Anthropic key）/ `claude` / `api` |
| `--user-sim-model` | `$USER_SIM_MODEL` 或 `claude-opus-4-7` | 仅 `api` 引擎用的模型；CLI 引擎忽略 |

## 用户模拟器（user-sim）

driver 默认**不逐字念稿**：信息类聊天 turn 会先读 TALOS 的真实回复，再用 Claude 生成贴合上下文的用户回复，brief 里那条 turn 只当「这步用户想表达啥 / 手上有哪些信息」的参考。解决的问题：以前 agent 问 A，脚本下一句是 B，driver 照样发 B（"agent 问天气，脚本写公交站，就发公交站"）。

- **引擎可切换**（`scripts/user_sim.py`，`--user-sim-engine` / `$USER_SIM_ENGINE`，默认 `codex`）：
  - `codex`（默认）：`codex exec --output-last-message` 取干净输出。走 codex CLI 自己的登录额度（OpenAI 系），**不需要 Anthropic key、不吃 Anthropic 限流**。官方非交互模式，最省事。
  - `claude`：`claude -p`（Claude Code print 模式），走 Claude 订阅。
  - `api`：anthropic SDK 直连 Messages API。key 从 `WWY_ANTHROPIC_API_KEY`（项目专用，优先）或 `ANTHROPIC_API_KEY` 读（`pip install anthropic`）。`--user-sim-model` 只对这个引擎生效。
  - **引擎不可用（CLI 不在 PATH / 无 key / 调用失败）→ 自动回落脚本原文**，确定性路径照常跑。
  - 调质量先跑离线单测（不碰 relay，只要引擎）：`python3 scripts/user_sim_offline_test.py --engine codex`。
- **只改聊天文案，不碰 stage 机**：面板动作（批准/确认/下发）和 stage 流转**仍然用脚本里的 `ut` 驱动**，不是用模拟器生成的文本。所以面板确定性逻辑一点没动。开场第一句（k==0）和 `dispatch_intent` 命中的 turn（含 `可以`/`下发`/`提交` 等）保持脚本原文。
- **配置**：`export WWY_ANTHROPIC_API_KEY=...`（或 `ANTHROPIC_API_KEY`）；模型默认 `claude-opus-4-7`，要省钱用 `--user-sim-model claude-haiku-4-5` 或 `export USER_SIM_MODEL=claude-sonnet-4-6`。
- **关掉**：`--no-user-sim` 回到逐字回放。
- **输出**：被改写的 turn 在 JSON 里多 `scripted_text` / `sim_text` / `sim_agent_reply_seen` 三个字段，stdout 打 `[user-sim] scripted=... → sent=...`，便于核对模拟器有没有跑偏。
- agent 最新回复靠 `.chat-col` innerText 的逐 turn 增量 diff 抽（`_new_agent_text`），selector 失效时回落到整页 body text。

## 输出

- 主信号：**stdout SUMMARY 行**，包含 `final_stage` / `submitted_tasks` / `re_phases` / Phoenix span 数
- Phoenix（http://100.84.102.34:6006/projects/UHJvamVjdDoy）：每轮 `LangGraph` root span，含 input/output/admittance/intention
- workflow-state API：`/api/sessions/<sid>/workflow-state`，权威状态

## 设计核心（改脚本前必读）

1. **权威成功信号 = workflow-state API 的 `phase` 字段**，不是 DOM 关键字。`talos_re_frontend_runner.confirm_re_spec` 轮询 `re_agent.phase == 'collecting_params'`。改成纯 DOM 检查会回到 1120 那种假阳性陷阱。
2. **brief 里的"热稳定性正常"/"下发"等短句驱不动 RE**——admittance 把它们当面板确认意图拒掉。Post-loop `_drive_re_after_cc` 自己合成完整 prompt 推 agent。
3. **`re_agent.spec` 任一必填字段为 null → 前端 confirm 静默被拒**（panel 点确认不报错但 backend 不推进到 `collecting_params`）。已知触发：
   - `solvent_ratio=null`（brief 没给比例）
   - `volume_ml=null`（brief 没给体积，2026-05-21 / 2026-05-25 conv-008 案例）
   - `solvents=null/empty`（同类）

   `RE_REQUIRED_SPEC_FIELDS = ("solvents", "solvent_ratio", "volume_ml")`（见 `smoke_runner_20260518.py`）。再发现新的 null-字段静默拒 bug，扩这个常量 + `fill_re_spec_via_ui` 的 JS 字段映射即可。
4. **关键陷阱：UI 直填不够，必须 nudge agent 同步 spec**。2026-05-25 conv-008（HEAD `d8de433`）实测：UI 直填后 backend `spec.volume_ml=192.0` 真的更新了，但 5 次 `确认` 点击 + 一次 chat fallback 全部失败，phase 始终停在 `collecting_spec`。
   - **根因**：backend 推进 phase 的语义不是"DOM 有完整 spec 就行"——它要 **agent 内部记住的 spec recommendation** 也完整，user 在确认 **agent 的推荐**。UI 直填走 frontend onChange → backend 字段更新通道，**根本没经过 agent**，agent 的"心智模型"里那个字段还是 null。点 `确认` 时 backend 双校验失败（spec 完整 ✓，但不是 agent 认可的最新 recommendation ✗），静默拒。
   - **修复**（HEAD ≥ d8de433+1）：`_drive_re_after_cc` 现在 **无条件** 在 spec 不完整时送 nudge，**不只 `not_started`**。Nudge 是 chemistry-rich 自然语言（"合并液约 192 ml，体系是 PE 和 EA，PE:EA = 1:1"），admittance 当 chemistry intent 放行 → agent 重新生成完整 spec recommendation → 后续 UI 填值变成 idempotent 兜底 → `确认` 能推进。
5. **`_drive_re_after_cc` 现在的执行序**：
   - **A.** 读 phase + spec。**`needs_nudge = phase==not_started OR (phase==collecting_spec AND missing 非空)`**。`needs_nudge=True` 就发一次 `_compose_re_nudge`（体积 100-300 mL 随机），等到 phase 进 collecting_params **或** spec 在 collecting_spec 下变完整（180s 预算）。Budget 耗尽不 bail，落到 C 兜底。
   - **B.** 短 grace（15s）让 agent 处理完最后的 chat 上下文。
   - **C.** 仍有 missing 字段时调 `talos_panel_ui.fill_re_spec_via_ui`，**用 Playwright `.type()` 模拟真键盘输入（不是 JS 直接 `el.value = ...`）**往面板 input 写值；溶剂表用 `select_option` + 真键盘。轮询 15s 看 backend spec 是否同步。
   - **D.** 点 `确认` 推进到 `collecting_params`（`confirm_re_spec_via_ui`，180s 预算）。
   - **为什么 nudge 优先于 UI 直填**：让 agent "知道" 完整 spec 是 backend 推进 phase 的必要条件；UI 直填只能改 backend 字段值，改不了 agent 的内部 recommendation。UI 直填只在 agent 怎么 nudge 都不肯填的边缘情况兜底（少见）。
   - **为什么 chat supplement "补充 spec: xxx" 不行**：admittance 把这种 UI-指令-味的短消息过滤掉；只有 chemistry-rich 上下文才放行。
   - **为什么用 `.type()` 而不是 JS 直接设 `value`**：React 控制的 input 有内部 `_valueTracker` 记录"上次提交的值"。JS 直接 `setter.call(el, x)` 改了 DOM `value`，再 dispatch input 事件——React 比较 `el.value === tracker.value` 发现"看起来没变"（因为 tracker 才是 React 的真值），**跳过 onChange，前端不发 API 给 backend**。2026-05-21 conv-008 retry 实测：`volume_value='288'` 写到 DOM 里了，但 `spec.volume_ml` 一直 null。Playwright `.type()` 模拟真键盘事件，React SyntheticEvent 层正常拿到，onChange 触发，backend 真更新。同理 `.select_option()` 也走真选择路径。
6. **in-loop RE confirm 触发不能只看 body text**——CC 总结卡片里有"溶剂体系"会假阳性。现在加了 `_re_task_phase` API gate **+ spec 完整性 gate**：spec 不完整时 in-loop 跳过 confirm（`panel_action="confirm_re_spec_DEFERRED"`），交给 post-loop 走 nudge+UI-fill 流程，省下白点 180s。
7. **CC spec 同款保护**（2026-05-25 加，预防性）：CC spec 推进 phase 的双校验机制和 RE 一致——backend 要求 agent's recommendation 完整。conv-008 没踩，是因为 brief u04 把 SMILES + 上样量 + Rf + 体系都给齐了。如果某个 conv brief 漏一个（最脆的是 `sample_amount_g`，只能从 chat 来，TLC 图识别覆盖不到），会跟 RE volume_ml 一样静默拒。
   - **`CC_REQUIRED_SPEC_FIELDS = ("solvents", "rf_values", "solvent_ratio", "sample_amount_g", "tlc_image_url")`**。`tlc_image_url` 由 TLC 模态上传补，drive 函数在判断"是否需要 nudge"时把它排除掉。
   - **`_compose_cc_nudge(brief)`** 用正则从 brief user_turns 抽 SMILES / 上样量 / Rf / 体系（默认值 200 mg / Rf 0.35 / PE/EA 1:1），组装 chemistry-rich 句子。`_extract_cc_hints` 单独可测。
   - **`_drive_cc_spec_if_incomplete`** 统一入口：spec 不完整就先 nudge → 等 180s 让 agent 补 → 然后跑 `confirm_cc_spec_via_ui`（TLC 上传 + 面板确认 + state polling）。主循环 in-loop CC confirm trigger 现在直接调它，不再绕 `confirm_cc_spec_via_ui`。
8. **面板 DOM 是已知量**：`scripts/dump_panel_doms.py` 跑一遍能扒出 cc_spec / cc_params / re_spec / re_params 四个面板的全部可编辑控件（label / cssPath / value / readonly）。再加新的 UI 直填字段前先用它复核当前 DOM；产物在 `eval_outputs/panel_doms_*/summary.md`。这条脚本不消耗 lab。

## 已知失败模式 / 怎么判断

| stage 最终值 | 含义 | 是产品 bug 还是脚本 bug |
|---|---|---|
| `done` | RE 跑到 terminal | 成功 |
| `re_collecting_params_no_dispatch` | 没 `--allow-dispatch`，停在确认完 spec 那一步 | 设计就是这样，不是 bug |
| `re_spec_failed` + `submitted=['cc']` + RE 全程 `not_started` | **可能不是 RE 的问题，是 CC 没真跑起来**——先看 CC 那段日志有没有 `slot ... install NOT confirmed` / `Could not select cartridge`。若有 → 插槽安装确认时序竞态：点"安装"后后端落库要 ~6-8s，慢 relay 上更久；脚本若等不及判假失败，柱子没选上 CC 仍下发 → CC 跑不起来 → RE 不流转。已用轮询修（`_SLOT_INSTALL_TIMEOUT_S=30` / `_SLOT_SELECT_TIMEOUT_S=20`，见 `talos_panel_ui.py`）。还复发就调大这俩常量 |
| `re_spec_failed`（spec 层面） | spec 没在预算内完整化、或 confirm 拒绝推进 | 看 `re_finalize.ui_fill`：(1) `errors` 数组有 `volume input not found` → 看同一个对象里的 `diag.number_inputs`，里面有 active 卡片里所有 number input 的 min/max/value/placeholder/label，对照定位真实控件；(2) `errors` 为空但 backend `missing` 仍非空 → 该字段不是通过 `input[type=number]` 写入的，扩 `RE_REQUIRED_SPEC_FIELDS` + `fill_re_spec_via_ui` 的 JS 映射；(3) `diag.has_step_card_active=false` → 面板还没渲到 active 状态，pre-wait 不够或者根本没到 RE spec phase。如果 missing 一直空但 confirm 不推进，看 Phoenix admittance reason |
| `re_submit_failed` | submit_re_params 内部抛异常 | 多半是 `瓶 1` locator 找不到——面板没渲到 add-flask 步，回头查为什么 confirm 没真推进 backend |
| `blocked` + `dispatch_gated=True` | 没 `--allow-dispatch` | 正常 |
| `cc_spec_violation` 非 None | `column_type` 在 CC 终态不是 `silica_12g` | 产品 bug（guide §4.3 硬规） |
| `planner_mismatch=True` | plan 缺 cc 或 re executor | 产品 bug |
| `ERROR new_session: could not confirm a NEW session ...` | 点 `新对话` 后没拿到新 session id（按钮没渲染出来 / SPA 没建会话） | 脚本**故意报错不静默**。先看是不是 `新对话` 按钮在 `click_timeout`（默认 15s）内没出来——高延迟就调大；如果按钮点了但 id 不变，是 SPA 建会话不可靠，归产品 |

Relay 偶尔会瞬时 read timeout（~每小时 1-2 次）。如果脚本运行中卡 15s 又恢复，那是 relay；不要把这类报错算到产品头上。

`new_session` 现在会**校验拿到的是新 session**（记点击前 id → 点 → 断言 id 变了 / 空→有），拿不到就重试 `attempts`（默认 3）次再 raise，绝不静默沿用旧 id。可视窗口从 2s 提到 15s（`click_timeout`）。历史上"整个 conv 跑在旧 session 上、结果全错却无报错"就是这个洞。

## 文件依赖

```
scripts/
  smoke_runner_20260518.py        ← 主入口
  user_sim.py                     ← LLM 用户模拟器（默认 codex CLI 引擎；被主入口 import，引擎缺失自动回落脚本）
  talos_panel_ui.py               ← 面板 UI helpers（被主入口 import）
  talos_re_frontend_runner.py     ← state-driven RE 驱动（被 panel_ui 委托）
  talos_cc_frontend_runner.py     ← CC 驱动 + 通用 helpers
  judge_20260518.py               ← Phoenix 评分（独立流程，可选）

agent_eval_dataset.json           ← brief 数据源
demo.jpeg                         ← CC TLC 占位图
```

不要碰：`talos_re_frontend_runner.py`、`talos_cc_frontend_runner.py`——它们是 proven baseline。`talos_panel_ui.py` 的 RE 部分委托给它们。

**唯一例外：纯 timeout 数值的放宽**。把 `is_visible(timeout=...)` / `wait_for(timeout=...)` 这类等待窗口**调大**（只改数字、不动 selector / 逻辑 / 顺序）是允许的——它单调更宽容：原来窗口内能命中的仍命中，原来错过的现在能接住，不可能让能跑的路径变坏。改的时候在 commit 里点名是 baseline 文件 + 纯 timeout bump 即可。**调小 timeout 或任何 selector/逻辑改动仍然禁止。**（2026-05-25 `talos_cc_frontend_runner.py` 12g 下拉 2s→8s 就是这个例外。）
