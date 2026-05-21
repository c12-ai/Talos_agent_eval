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

## 网络前置

脚本访问两个 IP/端口：

| 服务 | URL（写死在 `smoke_runner_20260518.py`） |
|---|---|
| TALOS API | `http://100.84.102.34:8080` |
| Phoenix | `http://100.84.102.34:6006` |

`100.84.102.34` 是**当前 Mac 的 Tailscale IP**，由 Mac 上 `ssh -L 100.84.102.34:{8080,6006}:localhost:{8080,6006} newbox` 中转。如果 Codex 不在能直连这台 Mac 的网络下：

- 改 `TALOS_BASE` / `PHOENIX_BASE`（同时改 `scripts/judge_20260518.py` 里的 `PHOENIX_BASE`）
- 或在 Codex host 上建等价隧道
- IP 还会变（Tailscale 重登就换）；变了先 `tailscale ip -4` 重读

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

## 输出

- 主信号：**stdout SUMMARY 行**，包含 `final_stage` / `submitted_tasks` / `re_phases` / Phoenix span 数
- Phoenix（http://100.84.102.34:6006/projects/UHJvamVjdDoy）：每轮 `LangGraph` root span，含 input/output/admittance/intention
- workflow-state API：`/api/sessions/<sid>/workflow-state`，权威状态

## 设计核心（改脚本前必读）

1. **权威成功信号 = workflow-state API 的 `phase` 字段**，不是 DOM 关键字。`talos_re_frontend_runner.confirm_re_spec` 轮询 `re_agent.phase == 'collecting_params'`。改成纯 DOM 检查会回到 1120 那种假阳性陷阱。
2. **brief 里的"热稳定性正常"/"下发"等短句驱不动 RE**——admittance 把它们当面板确认意图拒掉。Post-loop `_drive_re_after_cc` 自己合成完整 prompt 推 agent。
3. **`re_agent.spec` 任一必填字段为 null → 前端 confirm 静默被拒**（panel 点确认不报错但 backend 不推进到 `collecting_params`）。已知触发：
   - `solvent_ratio=null`（brief 没给比例）
   - `volume_ml=null`（brief 没给体积，2026-05-21 conv-008 案例）
   - `solvents=null/empty`（同类）

   `RE_REQUIRED_SPEC_FIELDS = ("solvents", "solvent_ratio", "volume_ml")`（见 `smoke_runner_20260518.py`）。再发现新的 null-字段静默拒 bug，扩这个常量和 `_compose_re_supplement` 即可。
4. **`_drive_re_after_cc` 是 spec-driven 闭环**，不是「nudge 一次就完事」。每轮读 `(phase, spec)` → 算 `missing` → 缺什么发什么：
   - `phase=not_started` → 发完整 nudge（`_compose_re_nudge`）
   - `phase=collecting_spec` 且有 missing → 发 field-targeted supplement（`_compose_re_supplement`），最多 3 次
   - `phase=collecting_spec` 且 spec 完整 → 调 `confirm_re_spec_via_ui`

   旧版（pre-2026-05-21）只看 phase 当 gate，遇到「brief 把 phase 推到 collecting_spec 但 spec 里有 null」就跳过 nudge → confirm 永远不推进 → `re_spec_failed`。
5. **in-loop RE confirm 触发不能只看 body text**——CC 总结卡片里有"溶剂体系"会假阳性。现在加了 `_re_task_phase` API gate **+ spec 完整性 gate**：spec 不完整时 in-loop 跳过 confirm（`panel_action="confirm_re_spec_DEFERRED"`），交给 post-loop 补字段后再确认，省下白点 180s。

## 已知失败模式 / 怎么判断

| stage 最终值 | 含义 | 是产品 bug 还是脚本 bug |
|---|---|---|
| `done` | RE 跑到 terminal | 成功 |
| `re_collecting_params_no_dispatch` | 没 `--allow-dispatch`，停在确认完 spec 那一步 | 设计就是这样，不是 bug |
| `re_spec_failed` | spec 没在预算内完整化、或 confirm 拒绝推进 | 看 `re_finalize.phase_seq` 里每条的 `missing`：长期非空说明 supplement 没把字段喂进 spec——查 `_compose_re_supplement` 对该字段的措辞是否被 agent 接受，或扩 `RE_REQUIRED_SPEC_FIELDS`。如果 missing 一直空但 confirm 仍不推进，看 Phoenix admittance reason |
| `re_submit_failed` | submit_re_params 内部抛异常 | 多半是 `瓶 1` locator 找不到——面板没渲到 add-flask 步，回头查为什么 confirm 没真推进 backend |
| `blocked` + `dispatch_gated=True` | 没 `--allow-dispatch` | 正常 |
| `cc_spec_violation` 非 None | `column_type` 在 CC 终态不是 `silica_12g` | 产品 bug（guide §4.3 硬规） |
| `planner_mismatch=True` | plan 缺 cc 或 re executor | 产品 bug |

Relay 偶尔会瞬时 read timeout（~每小时 1-2 次）。如果脚本运行中卡 15s 又恢复，那是 relay；不要把这类报错算到产品头上。

## 文件依赖

```
scripts/
  smoke_runner_20260518.py        ← 主入口
  talos_panel_ui.py               ← 面板 UI helpers（被主入口 import）
  talos_re_frontend_runner.py     ← state-driven RE 驱动（被 panel_ui 委托）
  talos_cc_frontend_runner.py     ← CC 驱动 + 通用 helpers
  judge_20260518.py               ← Phoenix 评分（独立流程，可选）

agent_eval_dataset.json           ← brief 数据源
demo.jpeg                         ← CC TLC 占位图
```

不要碰：`talos_re_frontend_runner.py`、`talos_cc_frontend_runner.py`——它们是 proven baseline。`talos_panel_ui.py` 的 RE 部分委托给它们。
