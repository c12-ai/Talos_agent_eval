# Talos 评测文档入口

`eval_criteria.md` 已拆分为两个职责更清晰的文档：

| 文档 | 用途 |
|---|---|
| `talos_operation_guide.md` | 操作 TALOS 系统的说明：会话命名、user simulator、右侧面板、过柱/旋蒸下发、lab 串行约束、异常处理 |
| `phoenix_annotation_criteria.md` | Phoenix 标注要求：annotation name、标注落点、REST API、只标异常原则、Overview/工具选择/准入口径 |

## 使用建议

1. 跑测试前先读 `talos_operation_guide.md`，确认 TALOS 前端和 lab 任务下发流程。
2. 标注前读 `phoenix_annotation_criteria.md`，确认哪些问题需要落 Phoenix annotation。
3. 测试输入仍放在 `eval_inputs/`。
4. 测试产出、执行记录和标注 review 文档放在 `eval_outputs/`。

## 当前固定地址

- TALOS 前端：`http://192.168.12.239:8080/`
- Phoenix traces：`http://192.168.12.239:6006/projects/UHJvamVjdDoy/traces`
- CC/TLC 默认图片：`/Users/wuwenyan/Desktop/demo.jpeg`

## 关键口径摘要

- Phoenix 默认只标异常，不标正常。
- `expected_within_capacity=true` 表示准入节点对能力边界处理符合预期；不支持任务被正确拒绝也为 `true`。
- 过柱参数确认中硅胶柱规格只能选 `12g`。
- 使用样品柱 002 时，必须在“管理插槽”里安装 002 的柱子。
- 旋蒸最终下发必须通过右侧面板添加茄形瓶、选择“瓶 1”、配置试管并确认。
- lab 同一时刻只能有一个真实执行任务；批量测试可以并行开会话，但最终下发必须串行。
