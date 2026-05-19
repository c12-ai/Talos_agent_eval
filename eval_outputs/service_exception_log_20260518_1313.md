# 2026-05-18 冒烟批次 服务异常记录

> 批次：`[20260518-*]` 链路验证冒烟（D：每类 1 条）。范围 conv-018/017/001/005；conv-008（CC+RE）当时被异常 1（CC 腿）+ 异常 2（RE 腿占用 lab）阻塞未跑，**经用户 2026-05-19 决策本批跳过 conv-008，留到下批**。
> 运行主机经 Mac Tailscale 中转访问 `100.118.16.118`（TALOS 8080 / Phoenix 6006）。
> **2026-05-19 复核更新**：异常 2 的 hung run 已自行进终态 completed；异常 5/conv-017 经用户确认后端无对应历史，归因落定。详见各节与文末决策结论。

## 结论速览

| # | 异常 | 类型 | 状态 | 阻塞 |
|---|---|---|---|---|
| 1 | 过柱「管理插槽」自动化驱动不稳定（**非产品缺陷**：人工点击稳定，已由用户确认） | 测试脚手架/自动化 | **已解决并验证**（PASS，未触发 lab） | 解除 |
| 2 | RE lab run 在 `end_evaporation` 停滞 >20min（pending / waiting） | 产品/lab service（**长停滞，非永久死锁**） | **2026-05-19 复核：已自行进终态 completed**（4 步全 completed 无报错） | 当时按串行约束临时阻塞；现已自愈，lab 空闲 |
| 3 | 测试脚手架未强制 12g 硅胶柱规格，首个 CC 下发以 `silica_24g` 提交并被 lab 拒绝 | 脚手架/操作（违反 guide §4.3） | 已修复并加硬校验 | 首个 conv-001 下发结果作废 |
| 4 | RE 参数面板试管按钮点击不可靠（5 选 2） | 脚手架/UX | 绕过（任务仍下发） | 否（collect_config 偏差） |
| 5 | conv-017 查询历史数据在后端不存在 | 数据态/评测归因 | **2026-05-19 用户确认后端无此历史 → 归因落定：非 agent 缺陷，维持不标注** | 解除 |

---

## 异常 1：过柱「管理插槽」自动化驱动不稳定（非产品缺陷）

- **归因更正**：用户反馈人工点击该面板稳定可用。故此条为**测试自动化（Playwright）脆弱**，**不是产品/UX 缺陷**。先前误记为「产品/UX」，已更正。
- 时间点：2026-05-18 11:01–12:50 CST（UTC 03:01–04:50），conv-001 共 5 次尝试
- 批次 / 会话：`[20260518-*-conv-001]`；最后一次 session `2e3285c6-f145-4719-bc7f-fbe647ff1669` 等
- 操作：过柱 spec 确认后，点击「管理插槽」安装备料架 L4 层样品柱 002 位
- 异常表现（均为自动化与该弹窗交互问题，人工操作不受影响）：
  - (a) TLC 识别弹窗遮罩 `fixed inset-0 z-50` 未关闭前，Playwright actionability 判定「被遮挡」→ 后续聊天输入 30s 超时（人工点击不受 actionability 判定影响）
  - (b) `get_by_text(槽位标签)` 命中文字节点而非外层可点击卡片 → `click` 30s 超时
  - (c) 降级用 force-click 跳过 actionability，但未触发 React onClick，状态未翻转 → 「保存」按钮不出现
  - (d) 插槽列表异步渲染，固定 12s 等待偶尔不足 → 槽位文本「未找到」；叠加前几次重试可能反复 toggle 同一槽位（guide §4.2）扰乱状态
  - 链路其余环节（建会话、批准方案、spec 确认、12g 强制、lab 下发机制、agent 文本）均正常
- 证据：`/tmp/smoke_conv001{b..e}.log`；`/tmp/slot_dialog_dom.html`（已接入导出，部分轮在槽位文本未现时未捕获）
- 尝试过的解决方式：模态遮罩自动消除；spec 提前确认门控（已生效）；样品柱点击 normal→force→js 三级降级 + 点击后状态校验；幂等检测「已安装则跳过」避免反复点击卸柱（guide §4.2）；提高等待超时
- **真实根因（用真实 DOM 定位，2026-05-18 捕获）**：
  1. 插槽是 `<button>`，已安装态 `border-emerald-400 bg-emerald-50`；旧代码盲点击插槽——弹窗副标题明确「点击插槽切换安装/移除状态」，对已安装的 002 再点一下就是**卸载**，导致 5 次尝试状态非确定
  2. `保存` 按钮在无改动时为**禁用态**（class `cursor-not-allowed`，无 `disabled` 属性），旧代码无条件等/点「保存」→ 超时
  3. 旧 emerald 检测落在文本节点祖先链，判定不准
  4. 早期误判「管理插槽」非按钮（实为蓝边 `<button>管理插槽</button>`）；样品柱与硅胶柱规格均为原生 `<select>`（`bic_09B_l4_002` / `silica_12g`）
- 修复（`talos_panel_ui.py`）：`_slot_installed` 改为只认 `border-emerald/bg-emerald`；已安装则**不点击**（toggle-safe）；新增 `_close_slot_dialog`——`保存` 可用才点，否则点「取消」；`submit_cc_via_ui` 用 `get_by_role(button,管理插槽)` + `_close_slot_dialog`；样品柱/12g 走原生 select
- 验证：`scripts/validate_slot_fix.py` 跑通，`select values=['PE','EA','bic_09B_l4_002','silica_12g','EA']`，三项断言全 PASS，**未点最终确认、未产生 lab run**
- 是否解决：**已解决并验证**（自动化侧）。印证用户判断：人工稳定是因为人不会重复点已装插槽、不会等灰掉的保存
- 后续处理：不写 Phoenix；conv-001 自动化阻塞已解除，可在授权后重跑端到端真机下发

## 异常 2：RE lab run 卡在 end_evaporation

- 时间点：2026-05-18 13:0x CST（UTC 05:0x），conv-005 第 2 次（修复 RE-spec 门控后）
- 批次 / 会话：`[20260518-*-conv-005]`，session `5a3da0eb-0008-4ede-85de-bfafcc27adae`
- 操作：RE spec 确认 → RE 参数提交 → 真机下发 → 等待 lab terminal
- 异常表现：lab run 成功创建（`lab_server_id=e3d3de8f-5556-433d-811b-c6059d88abf9`），step0 `collect_column_chromatography_fractions`、step1 `start_evaporation`、step2 `dispose_cc_bins` 均 completed，**step3 `end_evaporation` 持续 `pending`，`latest_run.status=waiting`**，>20 min 未进入 terminal。`re_agent.phase=conducting` 一直保持
- 证据：`eval_outputs/smoke_conv005_20260518.json`（观测窗内 workflow_state.tasks[].latest_run.steps）；停滞模式与 05-13 `[20260513-1727-conv012]`（span `e84c072c3c8e4ccc`，`end_evaporation` 持续 pending）一致
- **2026-05-19 复核（实时 workflow-state）**：run `e3d3de8f-5556-433d-811b-c6059d88abf9` 已自行进终态——`latest_run.status=completed`，step0-3（含 `end_evaporation`）全 `completed`、`error_message=None`，`tasks[0].phase=done`、`plan.status=completed`。即该 run 在 05-18 观测窗（>20min stall）之后最终完成，**非永久死锁，是长停滞后自愈**。lab 当前空闲（最近会话活动停在 05-18，无 05-19 活跃/卡死 run）。
- 尝试过的解决方式：非阻塞轮询 + 1200s 终态等待；执行中追问确认 agent 文本（agent 回答正确，见下）
- 是否解决：**已自愈**（lab service 长停滞复现，但最终成功完成，非永久 hung）
- 后续处理：
  - 用户 2026-05-19 一度授权脚本取消该 run；复核发现其已 `completed`，**无需也未执行任何取消操作**（取消已完成 run 无意义且错误）。
  - conv-008 当时因 lab 占用未跑；用户决策**本批跳过 conv-008**，lab 现已空闲，留到下批。
  - Phoenix 标注：agent 执行中回答真实、无编造、无「假提交」，按口径不在 agent turn 打 Overview=Bad；**用户 2026-05-19 决定：本条 RE 长停滞不按 05-13 先例打 Phoenix，维持本批「不写」**（理由更充分：run 最终成功、agent 全程如实，仅 lab service 侧偶发长停滞，已记录在本服务异常文档）。

## 异常 3：脚手架未强制 12g，首个 CC 以 silica_24g 提交被 lab 拒绝

- 时间点：2026-05-18 ~11:35 CST，conv-001 attempt-3
- 异常表现：agent（按化学合理性）推荐 24g 柱；旧 `_set_column_type_12g` 因「页面任意位置出现 12g 即提前 return」从未真正操作 `硅胶柱规格` 控件，`silica_24g` 进入 lab，step1 `setup_cartridges` failed：`No unused silica cartridge available for spec 'silica_24g'`（与 guide §4.3 预期完全一致）
- 证据：conv-001 attempt-3 workflow_state（已被后续尝试覆盖，判定见对话记录与 review 文档）
- 尝试过的解决方式：重写 `_set_column_type_12g`（native select → 作用域 popover → typed option，带日志）；新增**提交后硬校验** `column_type==silica_12g`，否则标 `cc_spec_violation` 且不计通过
- 是否解决：已解决（attempt-4/5 日志 `[UI] column spec -> 12g (native select)`，被异常 1 阻断在更后步骤）
- 后续处理：首个 conv-001 下发结果作废，不计入评分；非 agent 缺陷，不打 Phoenix

## 异常 4：RE 参数面板试管按钮点击不可靠

- 异常表现：`submit_re_via_ui` 点击试管 1-5，仅 2 个 className 变化（实际 collect_config=`[0,0,0,1,1]`，落在 4/5 管）。与异常 1 同属点击不触发状态翻转的前端不稳定族
- 影响：任务仍成功下发；collect_config 与预期 `[1,1,1,1,1]` 偏差；agent 如实复述了真实配置（第 4、5 管入瓶）
- 是否解决：绕过（非致命）；后续可加点击后状态校验 + 重试
- 后续处理：记录，不单独打 Phoenix

## 异常 5：conv-017 查询历史在后端可能不存在（评测归因）

- 表现：conv-017 查询「上周嘧啶类回收率」「RXN091」等历史数据。agent 未调 `list_tasks`/`get_task_detail` 而要求澄清
- **2026-05-19 用户确认：后端确无此历史数据**。故 agent 要求澄清是合理降级，**不是 agent 漏调工具缺陷**
- 处理：**归因落定——conv-017 维持不写任何 Phoenix**（自动判官 C2/C3 系关键词误报，详见 review 文档）；本条结案

---

## 决策结论（2026-05-19 用户拍板，全部结案）

1. **取消 hung RE run** `e3d3de8f-5556-433d-811b-c6059d88abf9`：用户授权脚本取消；复核发现该 run 已自行 `completed`，**无需取消，未执行任何操作**，lab 已空闲。结案。
2. **conv-017 是否写 Phoenix**：用户确认**后端无对应历史数据** → agent 要求澄清为合理降级，非缺陷 → **维持不写**。结案。
3. **RE `end_evaporation` 长停滞是否按 05-13 先例打 Phoenix**：用户决定**不打，维持本批「不写」**（run 最终成功 + agent 全程如实，仅记本服务异常文档）。结案。
4. **conv-008 是否补跑**：用户决定**本批跳过 conv-008**，留到下批。结案。
5. ~~CC 插槽面板~~：**已解决**（自动化侧修复并验证 PASS，非产品缺陷）。是否授权重跑 conv-001/conv-008 端到端真机下发——按 #4，本批不重跑，留下批。
