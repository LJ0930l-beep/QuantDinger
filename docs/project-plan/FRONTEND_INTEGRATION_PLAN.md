# 前端集成计划

## 当前前端参考

| 项目 | 当前事实 |
|---|---|
| 仓库 | `LJ0930l-beep/QuantDinger-Vue` |
| Draft PR | #1 `Add AI quant dashboard prototype` |
| Head | `a72a8c80491da5057c50ab6408c5effd2451c8d1` |
| 分支 | `feature/frontend-quant-dashboard-prototype` |
| 路由 | `/#/quant-dashboard` |
| 数据 | 独立 Mock / PAPER / SHADOW，只读 |
| 安全边界 | 无真实 API、Executor、Exchange、下单、撤单、仓位修改或 Live 调用 |

后端总控文档仅在本仓库维护；前端仓库不创建重复总控来源。

| Task ID | 状态 | 目标 | 依赖 | 允许范围 | 禁止范围 | 交付物 | 验证证据 | 闸门 | 停止条件 | 仓库 | 分支 / PR | 最后批准 Head |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F-01 | IN_PROGRESS | 合并只读 Mock Prototype | 前端 Draft PR #1 的独立审查 | 视觉、Mock、静态测试、构建 | 真实 API/交易接入 | `/quant-dashboard` Mock 页面 | Mock 测试、Vite build、截图、Draft PR | Frontend Mock Gate | 任何真实调用或 Live 语义 | 前端 | `feature/frontend-quant-dashboard-prototype` / #1 | `a72a8c8` |
| F-02 | NOT_STARTED | 将公开 Prototype 路由迁入鉴权工作区 | F-01 DONE；产品鉴权需求明确 | 路由、权限 UX | 通过前端掩盖后端权限缺失 | 鉴权路由方案 | 访问控制与回归测试 | Frontend Auth Gate | 未定义用户/租户权限模型 | 前端 | TBD | TBD |
| F-03 | BLOCKED | 定义只读 Dashboard API 合同 | PR-14 R14-10 DONE | 类型、状态、错误/陈旧语义 | 在前端推断风险或账户真相 | Read-only API Contract | 后端合同测试、前端 fixture | Dashboard API Contract Gate | 后端无权威 read model | 前后端 | TBD | TBD |
| F-04 | BLOCKED | 接入账户、持仓、风险、Admission、Shadow、Reconciliation、Health 的只读数据 | F-03 DONE | authenticated read rendering | 写入接口、交易按钮语义 | 只读 Dashboard 集成 | API contract、stale/unavailable、权限测试 | Dashboard Integration Gate | 数据源非服务端权威 | 前端 | TBD | TBD |
| F-05 | NOT_STARTED | 补齐 loading / stale / unavailable / unauthorized 状态 | F-03 READY | 状态组件与可访问性 | 静默展示陈旧数据为实时 | 状态 UX | 可访问性和状态矩阵测试 | Dashboard Integration Gate | 状态含义不明确 | 前端 | TBD | TBD |
| F-06 | BLOCKED | 在引入 WebSocket/事件流前锁定重连、游标、重复事件语义 | F-03 DONE；事件合同稳定 | 只读流合同与原型 | 先连流后定义幂等语义 | Event Stream Contract | 断线、重连、重复消息测试 | Event Stream Gate | 无 cursor/replay 合同 | 前后端 | TBD | TBD |
| F-07 | NOT_STARTED | 为未来交易操作设计独立体验，而非从 Prototype 按钮演化 | 独立批准；P5-LIVE READY | 设计文档、无副作用原型 | 真实交易操作、默认 Live | 交易 UX 设计 | 人审、权限和安全评审 | Live UX Gate | 按钮语义被视为下单成功 | 前端 | TBD | TBD |

## 前端不变量

- Mock 数据必须与真实数据明显隔离。
- Live 状态必须由服务端权威提供，前端不能自行判断或打开。
- 前端不得自行计算风控结论，不得将按钮点击等同于交易成功。
- 金额和数量以字符串/Decimal 语义呈现，binary float 不能成为权威事实。
- 真实交易操作仅能在独立批准的设计和 API 合同完成后讨论。
