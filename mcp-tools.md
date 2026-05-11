> [!SUCCESS]
> 本文档用于 Phoenix 标注员了解 BIC Lab Service 的 MCP 查询工具，辅助标注 correct\_tools 字段。
> Ref：https://github.com/c12-ai/BIC-lab-service app/mcp/tools/state\_tools.py

## 工具总览

BIC Lab Service 通过 MCP 协议暴露 10 个只读查询工具，供 AI Agent 查询实验室状态、任务进度和事件历史。按用途分为四类：

| 分类 | 工具 | 核心用途 |
| --- | --- | --- |
| 设备与机器人 | get\_robot\_status | 机器人当前状态、位置、正在执行的任务 |
| 设备与机器人 | get\_running\_experiments | 所有正在运行的实验快照 |
| 物料库存 | get\_material\_inventory | 按类型查询实验室整体物料库存 |
| 物料库存 | get\_involved\_materials | 查询某个具体任务涉及的物料与设备 |
| 任务编排 | get\_task\_status | 任务状态、参数、步骤概览 |
| 任务编排 | get\_task\_detail | 任务执行明细，含每步结果与返回码 |
| 任务编排 | list\_tasks | 浏览任务列表，支持状态过滤 |
| 任务编排 | get\_task\_entity\_events | 跟踪某任务执行期间实体的状态变化 |
| 事件审计 | get\_entity\_events | 查询某个具体实体的事件历史 |
| 事件审计 | get\_recent\_events | 时间窗口内所有实体的近期事件 |

---

## 一、设备与机器人状态

### get\_robot\_status

查询机器人当前状态，包括状态值（idle / working / charging / disconnected）、所在位置、正在执行的 skill。

**参数**

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| robot\_id | 否 | 机器人 UUID；不传则返回所有机器人 |

**典型问题**

- 机器人现在在做什么？

- 机器人空闲吗？

- 机器人在哪个工位？

### get\_running\_experiments

快照式查询当前所有正在运行的实验，包括运行中的 CC 系统、旋转蒸发仪，以及进行中的 skill。无参数。

**典型问题**

- 现在有哪些实验在跑？

- 实验室正在进行什么操作？

- CC 系统现在是否忙？

---

## 二、物料库存

> [!CAUTION]
> 注意区分：get\_material\_inventory 查**全局库存**；get\_involved\_materials 查**某个具体任务**涉及的物料。

### get\_material\_inventory

按类型查询实验室整体物料库存情况。

**参数**

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| material\_type | 否 | 物料类型：consumable（耗材，如 cartridge）、container\_rack（试管架）、container（容器，如圆底烧瓶）；不传返回汇总 |
| only\_available | 否 | 默认 false；为 true 时仅返回可用的物料 |

**典型问题**

- 还有多少 cartridge？

- 有哪些可用的试管架？

- 实验室还剩什么物料？

### get\_involved\_materials

查询**某个具体任务**涉及的物料与设备及其当前状态。

- CC 任务：sample cartridge、CC 设备、CC 辅助模块

- RE 任务：旋转蒸发设备

**参数**

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| task\_id | 是 | 任务 UUID |
| task\_type | 是 | column\_chromatography 或 rotary\_evaporation |

**典型问题**

- 这个任务用到了哪些物料？

- 这个 CC 任务用的 cartridge 状态如何？

- 当前任务涉及的设备状态？

---

## 三、任务编排

### get\_task\_status

根据任务 ID 获取任务的状态、参数以及步骤概览。

**参数**

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| task\_id | 是 | 任务 UUID |

**典型问题**

- 这个任务现在什么状态？

- 这个任务的参数是什么？

### get\_task\_detail

获取任务的紧凑执行明细，包含每个 step 的结果与对应 skill 的返回码。用于检查任务进度或排查失败步骤。

**参数**

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| task\_id | 是 | 任务 UUID |

**典型问题**

- 任务跑到第几步了？

- 哪一步失败了？为什么？

- 任务的每一步返回了什么？

### list\_tasks

浏览任务列表，支持按状态过滤并分页。

**参数**

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| status | 否 | 按任务状态过滤（pending / in\_progress / completed / failed 等） |
| skip | 否 | 分页偏移，默认 0 |
| limit | 否 | 最多返回条数，默认 100 |

**典型问题**

- 最近有哪些任务？

- 有哪些失败任务？

- 所有运行中的任务？

### get\_task\_entity\_events

跟踪**一个任务执行期间**某类实体的状态变化。遍历任务所有步骤、按 skill\_id 聚合事件，可按 entity\_type 过滤。

**参数**

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| task\_id | 是 | 任务 UUID |
| entity\_type | 否 | 实体类型过滤（如 consumable、device）；不传返回所有类型 |

**典型问题**

- 这个任务过程中 sample cartridge 经历了什么变化？

- 设备在这个任务中发生过什么事件？

---

## 四、事件审计

### get\_entity\_events

查询**某个具体实体**（robot、设备、物料等）的事件历史，返回审计轨迹，包含状态变更、关联的 skill、时间戳等。

**参数**

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| entity\_type | 是 | 实体类型 |
| entity\_id | 是 | 实体 UUID 或字符串 ID |
| limit | 否 | 最多返回事件数，默认 100 |

**典型问题**

- 这台 CC 设备最近发生过什么？

- 这个 cartridge 的历史记录？

- 某个机器人的操作轨迹？

### get\_recent\_events

在指定时间窗口内查询**所有实体**的近期事件，用于监控实验室活动与调试。

**参数**

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| hours | 否 | 时间窗口（小时），默认 24 |
| entity\_type | 否 | 按实体类型过滤 |
| limit | 否 | 最多返回事件数，默认 100 |

**典型问题**

- 过去一小时实验室发生了什么？

- 最近 24 小时有什么事件？

- 最近设备有什么变化？

---

## 标注决策提示

> [!TIP]
> 遇到多个候选工具时，按下列优先顺序判断：

| 用户意图信号 | 首选工具 |
| --- | --- |
| 问机器人状态、位置 | get\_robot\_status |
| 问现在有什么实验在跑 | get\_running\_experiments |
| 问整体库存、可用物料 | get\_material\_inventory |
| 提到具体 task\_id 且问涉及的物料或设备 | get\_involved\_materials |
| 提到具体 task\_id 且问状态或参数 | get\_task\_status |
| 提到具体 task\_id 且问步骤进度或失败原因 | get\_task\_detail |
| 问任务列表、多个任务 | list\_tasks |
| 提到具体 task\_id 且问实体在任务期间的变化 | get\_task\_entity\_events |
| 问某个具体实体的历史 | get\_entity\_events |
| 问最近一段时间全局事件 | get\_recent\_events |

**易混淆点**

- 全库存 vs 任务物料：get\_material\_inventory 是全局的；get\_involved\_materials 必须给定 task\_id。

- 任务概览 vs 任务明细：get\_task\_status 只给状态和参数；get\_task\_detail 给每步执行结果。

- 实体历史 vs 任务内实体事件：get\_entity\_events 跨任务查某实体全部历史；get\_task\_entity\_events 只看某个任务期间的变化。
