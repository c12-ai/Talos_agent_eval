# Phoenix 标注记录
生成时间：2026-05-13 18:51 Asia/Shanghai

## 标注汇总
| 会话 | span_id | annotation.name | label | explanation |
|---|---|---|---|---|
| `[20260513-1727-conv002]` | `1d88f59c33ab4d7c` | `Overview` | `Bad` | CC spec 确认后未生成执行参数；workflow-state 仍为 `cc_agent.phase=collecting_spec`，`params=null`，无法进入样品柱/12g 参数确认和 lab 下发。 |
| `[20260513-1727-conv002]` | `1d88f59c33ab4d7c` | `AnswerQuality-Correction` | CC spec 确认后应进入 `collecting_params` 并生成可提交的 CC 参数；若参数服务超时，应给出明确失败状态并允许可靠重试。 | 人工修正建议。 |
| `[20260513-1727-conv004]` | `6f593e2e344e6b77` | `Overview` | `Bad` | CC spec 确认后参数获取服务超时，前端显示 `COLUMN CHROMATOGRAPHY 参数获取失败 / MCP 服务响应时间过长`，workflow-state 回退并停留在 `collecting_spec`。 |
| `[20260513-1727-conv004]` | `6f593e2e344e6b77` | `AnswerQuality-Correction` | 参数服务超时时应清晰保留可重试状态；测试侧不能把该会话算作已完成，也不能继续下发 lab。 | 人工修正建议。 |
| `[20260513-1727-conv012]` | `e84c072c3c8e4ccc` | `Overview` | `Bad` | CC+RE 会话中 RE 已下发并进入执行，但 run 长时间停在 `conducting/waiting`，前三步 completed，`end_evaporation` 持续 pending，未进入 terminal 状态。 |
| `[20260513-1727-conv012]` | `e84c072c3c8e4ccc` | `AnswerQuality-Correction` | RE lab run 完成前三步后应继续执行 `end_evaporation` 或返回可诊断失败；不能长期保持 waiting/pending 阻塞批量测试队列。 | 人工修正建议。 |

## 标注 ID
| 会话 | annotation.name | Phoenix annotation_id |
|---|---|---|
| `[20260513-1727-conv002]` | `Overview` | `U3BhbkFubm90YXRpb246MzI5` |
| `[20260513-1727-conv002]` | `AnswerQuality-Correction` | `U3BhbkFubm90YXRpb246MzMw` |
| `[20260513-1727-conv004]` | `Overview` | `U3BhbkFubm90YXRpb246MzMx` |
| `[20260513-1727-conv004]` | `AnswerQuality-Correction` | `U3BhbkFubm90YXRpb246MzMy` |
| `[20260513-1727-conv012]` | `Overview` | `U3BhbkFubm90YXRpb246MzMz` |
| `[20260513-1727-conv012]` | `AnswerQuality-Correction` | `U3BhbkFubm90YXRpb246MzM0` |
