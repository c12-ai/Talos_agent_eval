# Talos Agent Eval · 使用说明

本仓库做两件事：
1. **生产**：人工/半人工地生成 Talos agent（化学实验室助手）评测对话数据集，规则见 [`CLAUDE.md`](CLAUDE.md)。
2. **验证**：把 dataset 里的 conv 通过 live TALOS 前端跑一遍，产 Phoenix 追踪数据供后续标注/打分。

USAGE.md 主要讲第 2 件（验证）怎么用；第 1 件的"对话长什么样、字段怎么写"看 CLAUDE.md。

## 1. 一次性环境准备

```bash
git clone https://github.com/wuwenyan302/Talos_agent_eval
cd Talos_agent_eval
pip install -r requirements.txt
playwright install chromium
```

Python ≥ 3.10。

## 2. 网络前置（卡死率最高）

脚本访问 live TALOS：

| 服务 | 默认地址（写死在 `scripts/smoke_runner_20260518.py`） |
|---|---|
| TALOS API | `http://100.84.102.34:8080` |
| Phoenix | `http://100.84.102.34:6006` |

`100.84.102.34` 是当时 Mac 的 Tailscale IP，由 Mac 上 ssh 端口转发到 lab。**IP 会变**——Tailscale 重登/重装就换。出错先：

```bash
# 在 Mac 上看当前 IP
tailscale ip -4
```

IP 变了就同步改：
- `scripts/smoke_runner_20260518.py` 顶部的 `TALOS_BASE` / `PHOENIX_BASE`
- `scripts/judge_20260518.py` 的 `PHOENIX_BASE`
- 重启 Mac 上的 `ssh -L 100.x.x.x:6006:localhost:6006 -L 100.x.x.x:8080:localhost:8080 newbox`

不在能 Tailscale 直连这台 Mac 的网络下跑，就自己起一个等价隧道，再改上述常量。

## 3. Lab 物理前置

要跑 `--allow-dispatch`（真在 lab 上下发实验）前必须确认：

- 样品柱插槽 `bic_09B_l4_002`（"备料架L4层样品柱002位"）装着空闲的 **12g 硅胶柱**
- PE、EA 溶剂量足
- 旋蒸瓶（茄形瓶）就位
- lab 当前没人并行用

每条 CC+RE conv 跑完全程 ≈ 50 min lab 时间。

## 4. 三种典型工作流

### 4.1 跑冒烟（验证 agent 链路 + 抓 Phoenix）

```bash
# 安全干跑，不真下发，~5 min（CC 在 submit 那一步停住）
python3 scripts/smoke_runner_20260518.py conv-008

# 完整跑（CC + RE 真下发到 lab），~50 min
python3 scripts/smoke_runner_20260518.py conv-008 --allow-dispatch

# 想拿 JSON 诊断快照
python3 scripts/smoke_runner_20260518.py conv-008 --allow-dispatch --out /tmp/smoke.json
```

CLI 完整说明见 [`scripts/SMOKE_README.md`](scripts/SMOKE_README.md)。

**stdout SUMMARY 行**就是主信号，里面有：
- `final_stage` — 最终走到哪个阶段（`done` / `re_spec_failed` / `re_submit_failed` 等）
- `submitted_tasks` — 真下发过哪些（`['cc']` / `['cc', 're']` / `[]`）
- `re_phases` — RE 状态机走过的 phase 序列
- `cc_spec_violation` — CC 柱规格守卫（应该是 `None`，非 None 就是产品 bug）
- `spans` — Phoenix 抓到的 root span 数

### 4.2 打分（judge）

```bash
# DRY-RUN：打印每轮判定 + 会写入 Phoenix 的 annotation，但不真写
python3 scripts/judge_20260518.py eval_outputs/<run.json>

# 真写 Phoenix（共享 dev instance，对外可见）
python3 scripts/judge_20260518.py <run.json> --write
```

注意：`judge` 要的是 JSON 输入，所以跑 `smoke_runner` 时记得带 `--out`。

### 4.3 生成新的 conv brief

不跑代码——人编/Claude 协同编。规则强约束在 [`CLAUDE.md`](CLAUDE.md)。dataset 文件是 `agent_eval_dataset.json`。

## 5. 文件地图

```
Talos_agent_eval/
├── CLAUDE.md                   ← 数据集生成强规则（必读）
├── USAGE.md                    ← 本文件
├── project_background.md       ← 产品/技术背景
├── agent_eval.md               ← 评测设计 + 用例说明
├── eval_criteria.md            ← 评分标准
├── mcp-tools.md                ← agent 可用工具的清单
├── phoenix_annotation_criteria.md
├── phoenix_api_config.md
├── phoenix_issues_report.md
├── talos_operation_guide.md
├── claude_MVP.md
├── robot_log.md
├── agent_eval_dataset.json     ← brief 数据源
├── demo.jpeg                   ← TLC 占位图（CC spec 上传用）
├── query.png                   ← UI 示意图
├── 会话骨架.png                 ← 会话结构示意图
├── requirements.txt
├── scripts/
│   ├── SMOKE_README.md            ← smoke 详细说明
│   ├── smoke_runner_20260518.py   ★ 主入口（数据集 → live TALOS）
│   ├── talos_panel_ui.py          ← 面板 UI helpers
│   ├── talos_re_frontend_runner.py← state-driven RE 驱动（proven baseline，别改）
│   ├── talos_cc_frontend_runner.py← CC 驱动 + 通用 helpers（别改）
│   ├── talos_cc_re_frontend_runner.py
│   ├── talos_chat_frontend_runner.py
│   ├── talos_panel.py
│   ├── judge_20260518.py          ★ Phoenix 打分（独立流程）
│   ├── continue_cc_existing_session.py    ← 在已存在会话上单步驱 CC
│   ├── continue_re_params_existing_session.py
│   ├── retry_re_spec_existing_session.py
│   ├── send_existing_session_message.py
│   ├── approve_existing_session.py
│   ├── debug_talos_session_dom.py
│   ├── capture_slot_dom.py
│   ├── validate_slot_fix.py
│   ├── phoenix_annotate_*.py     ← Phoenix annotation 写入辅助
│   ├── run_conv_eval.py          ← 旧版本 runner（被 smoke_runner 取代）
│   └── run_conv_smoke.py         ← 旧版本 smoke（同上）
├── eval_inputs/
└── eval_outputs/               ← Phoenix annotation md、judge 结果等
```

不要碰 `talos_re_frontend_runner.py` / `talos_cc_frontend_runner.py`——它们是已经过验证的 baseline，`smoke_runner_20260518` 委托给它们。

## 6. 常见故障 → 怎么定位

| 现象 | 多半是 |
|---|---|
| `fatal: could not read Username for 'https://github.com'` | gh 没把自己配成 git credential helper，跑一次 `gh auth setup-git` |
| `Connection to 100.84.102.34 timed out` | Mac Tailscale 掉了或 IP 变了，看 §2 |
| `Locator.click: Timeout 30000ms exceeded waiting for "瓶 1"` | RE add-flask 没渲到——往回查 confirm_re_spec 是不是真的把 backend 推到 collecting_params 了 |
| 反复 `[talos-re] RE spec scoped confirm button not found` | re_agent 还在 `not_started`，nudge 没生效。看 Phoenix 里 agent 最近的回复是不是 "请求已拒绝" 或者在追问体系/容器 |
| `final_stage=re_spec_failed` 但 Phoenix 显示 agent 给出了 spec | spec 里 `solvent_ratio=null`——nudge 缺比例，见 `scripts/SMOKE_README.md` 设计核心 §3 |
| `cc_spec_violation` 非 None | **产品 bug**：CC 终态 column_type 不是 `silica_12g`（违反 guide §4.3 硬规） |
| `planner_mismatch=True` | **产品 bug**：planner 没出 `cc_agent` + `re_agent` 两个 executor |

更细的失败模式分类表在 [`scripts/SMOKE_README.md`](scripts/SMOKE_README.md)。

## 7. 把这套交给别的 agent（如 Codex）跑

最小套件 = 本仓库当前 HEAD（已含 `requirements.txt` + `SMOKE_README.md`）。Codex 拿到后：

1. clone + `pip install -r requirements.txt && playwright install chromium`
2. 确认能 ping 通 `100.84.102.34:8080` 和 `:6006`（否则按 §2 重配）
3. 确认 lab 物理前置（§3）
4. 跑 `python3 scripts/smoke_runner_20260518.py conv-008 --allow-dispatch`
5. 看 stdout SUMMARY 行判定（§4.1）；失败照 §6 / SMOKE_README 排查

如果让 Codex 跑全集而不是单 conv-008，先用 `--allow-dispatch=False` 试一遍每个 conv 通不通 plan/spec，再决定哪些上 dispatch（lab 时间贵）。
