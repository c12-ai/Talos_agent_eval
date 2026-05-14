# 服务异常记录
生成时间：2026-05-13 17:33 Asia/Shanghai

| 序号 | 时间点 | 会话 | 操作 | 异常表现 | 证据 | 尝试过的解决方式 | 是否解决 | 后续处理 |
|---|---|---|---|---|---|---|---|---|
| 1 | 2026-05-13 17:29 Asia/Shanghai | 上一轮残留会话 `fad5d111-4bf1-4782-84ea-b6771d7d6c8a` | 新批次开始前检查 lab 串行锁 | 首次读取 Talos workflow-state 时出现 `curl: (7) Couldn't connect to server` | `GET /api/sessions/fad5d111-4bf1-4782-84ea-b6771d7d6c8a/workflow-state` 连接失败 | 10 秒间隔重试 6 次 | 已恢复 | 后续重试可读取 workflow-state |
| 2 | 2026-05-13 17:29-17:32 Asia/Shanghai | 上一轮残留会话 `fad5d111-4bf1-4782-84ea-b6771d7d6c8a` | 新批次下发 lab 任务前检查当前是否已有 lab run 未结束 | RE lab run 长时间停在 `conducting/waiting`；最后一步 `end_evaporation` 一直 `pending`，未进入 terminal 状态 | `lab_server_id=a06e7194-74ee-44b0-a64c-f23c7e0396e8`；`phase=conducting`；`latest_run.status=waiting`；steps: `0/1/2 completed, 3 pending` | 连续轮询约 3 分钟；确认没有残留自动化进程继续操作页面 | 未解决 | 按串行约束停止本批次新的 CC/RE lab 下发；建议人工确认或清理该 lab run 后再重新启动批量测试 |
