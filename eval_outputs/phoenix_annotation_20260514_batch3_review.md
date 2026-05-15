# 2026-05-14 批量测试与 Phoenix 标注记录

## 本轮范围
- 用例：`conv-001`、`conv-023`、`conv-024`
- 类型：单过柱
- 测试方式：真实 TALOS 前端对话 + 右侧面板操作

## 批测结果汇总
| 用例 | 会话标题 | session_id | 对话 | 面板 | 下发 | 结果 | Phoenix |
|---|---|---|---|---|---|---|---|
| conv-001 | `[20260514-cc-conv001]` | `ebaf45c5-5fd8-4314-9cd0-cb37c1d95142` | 成功 | 成功 | 成功 | 已进入 `conducting`，`latest_run.status=in_progress`，已发 1 次进度追问 | 未写 |
| conv-023 | `[20260514-cc-conv023-rerun]` | `05773f58-d6a7-4b79-979f-68eba0599443` | 成功 | 部分成功 | 未下发 | spec 确认后未进入 `管理插槽/硅胶柱规格` 面板，卡在 runner 等待 params panel | 未写 |
| conv-024 | `[20260514-cc-conv024-rerun]` | `b7e75383-f712-4c96-b81c-b63708ea204e` | 成功 | 成功 | 已下发 | 已进入 `conducting`，但 step 0 返回 `No idle robots available`，后续步骤取消 | 未写 |

## 已确认异常
- 暂无已确认产品异常。
- 本轮没有出现明确的答非所问、编造数据、假提交、错工具这类 Phoenix 口径问题。

## 未写 Phoenix 的项
- `run_conv_eval.py` 首轮卡在“新对话”按钮识别，属于 runner 入口问题，未写 Phoenix。
- `run_conv_eval.py` 重跑时把 `user_turns` 当硬脚本连续发送，和右侧弹层时序冲突，`conv-001` 卡在 textarea 被面板遮挡，未写 Phoenix。
- `conv-023` rerun 在 spec 确认后未出现 `管理插槽/硅胶柱规格` 面板，当前更像前端面板流转或自动化等待问题，未写 Phoenix。
- `conv-024` rerun 虽已真实下发，但 lab 返回 `No idle robots available`，这是资源占用/调度状态，不是回答质量异常，未写 Phoenix。

## Phoenix 标注明细
| 用例 | span_id | annotation.name | label | explanation | annotation_id |
|---|---|---|---|---|---|
| 本轮暂无 | — | — | — | 未发现需要按产品异常口径落 Phoenix 的 turn | — |

## 结论
- `conv-001`：真实完成到 lab 下发并进入执行，是本轮最干净的成功样本。
- `conv-023`：对话和 spec 面板通过，但 params 面板没有稳定出现，当前更像前端流转或 runner 时序问题。
- `conv-024`：真实完成到 lab 下发，但执行入口被实验室资源状态拦住，报 `No idle robots available`。
- 本轮最终 **不写 Phoenix 标注**，因为没有确认到产品回答层异常。