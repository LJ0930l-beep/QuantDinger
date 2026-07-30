# AI Boundary Inventory

本清单是 GOV-00 的可达性与退役审计起点。分类：A 名称/UI；B 非交易分析；C 遗留交易入口；D 模型供应商调用；E 未使用代码；F 待确认。未列出的匹配项不等于安全；新增匹配必须经 AI Boundary Guard 审查。

| 分类 | 证据位置 | 当前结论 | 处理 |
| --- | --- | --- | --- |
| C | `backend_api_python/app/routes/agent_v1/quick_trade.py` | 遗留 Agent quick-trade 可直达历史执行路径 | 维持 fail-closed，SC-13/SC-15 退役 |
| C | `mcp_server/src/quantdinger_mcp/server.py` | 遗留 MCP quick-trade HTTP 路径 | 维持 fail-closed，SC-13/SC-15 退役 |
| C | `backend_api_python/architecture/entrypoint_convergence_manifest.json` | 对 Agent/MCP/Grid 旁路有逐项证据 | baseline 仅减不增 |
| D | `backend_api_python/app/services/llm.py`、`backend_api_python/app/config/api_keys.py` | LLM provider 和配置代码 | 不进入新量化命名空间；留待 PR-15 安全退役 |
| D | `backend_api_python/app/services/strategy_review.py` | 遗留 LLM 策略复盘导入 | 已登记为 AI Boundary Guard baseline，不能扩展 |
| D | `backend_api_python/app/services/strategy_authoring.py`、`app/services/ai_generation_contracts.py` | 遗留 AI 策略生成提示词/辅助 | 已登记 baseline；不得进入确定性策略链 |
| B | `backend_api_python/app/routes/fast_analysis.py` | 非交易分析 API | 不得连接 Canonical Entry / Risk Facts；后续单独退役评估 |
| B | `backend_api_python/app/routes/indicator.py`、`app/routes/strategy.py` | LLM 代码生成/指标辅助 | 非交易分析；不得作为策略或交易权威 |
| A | `docs/project-plan/*`、前端原型文案 | 历史 AI 产品名称 | 已改为 Quant Trading Dashboard / 纯量化路线 |
| F | `ai_radar`、`ai_analysis`、`ai_trade`、自动参数调整、自动策略选择 | 需在 SC-15 前按可达性逐项验证 | 不得假定已退役；交易可达路径必须 fail-closed |

## 当前保证

- 确定性交易核心受 `ai_boundary_guard.py` 保护：新 provider import 或模型输出流向交易事实即失败。
- Guard 只为现有两处遗留策略服务导入保留精确 baseline；baseline 只能减少。
- 当前活动 Admission、Hard Risk、Runtime Entry Ingress 与 Durable Entry 路径没有 AI 调用目标。
