# GOV-00：纯量化产品重置

## 决定

项目从 “AI Quant” 产品方向正式重置为**确定性加密量化交易系统**。安全、审计、回测和重放优先于产品扩展；所有交易决定必须由版本化数据、确定性策略、Decimal、权威账户/仓位事实、Hard Risk、Reservation 和 Admission 共同决定。

## 永久边界

AI / LLM / Agent 的交易权限为 0%。它们不得决定交易与否、方向、action、risk_effect、数量、仓位目标、reduce-only、保护价、杠杆、策略切换、风险预算、时机或订单修改/撤销。

历史范围不是被静默删除：

> AI / LLM / Agent trading scope removed by product decision. Direct AI trading authority is permanently zero under the current charter.

## 迁移含义

- AGENT 与 MCP 是遗留入口清理对象，不再作为产品功能继续开发。
- GRID 在确定性策略平台重新实现之前保持 DISABLED。
- Candidate Projection 仅是确定性读模型候选；Candidate Trade Plan 仅是确定性策略输出；两者均非 AI 产品能力。
- Controlled Live Ready 的依赖不包含 AI，而包含 Safety Core、市场数据、回测、Paper/Shadow、策略平台、确定性策略、组合风险、前端只读集成和故障演练。

详细盘点见 [AI Boundary Inventory](AI_BOUNDARY_INVENTORY.md)。
