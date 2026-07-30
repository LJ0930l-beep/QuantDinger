# 产品路线图

本路线图开始于 Safety Core Complete 之后。所有阶段的状态不替代独立授权；不承诺发布日期或上线时间。

## 依赖顺序

```text
Safety Core Complete
  -> P1 Paper / Shadow Runtime
  -> P2 Strategy Platform
  -> P3 SMC / ICT Strategy
  -> P4 AI Analysis Layer
  -> P5 Controlled Live Readiness
  -> 单独正式 Live 启用决定
```

## 阶段任务卡

| Task ID | 状态 | 目标 | 依赖 | 允许范围 | 禁止范围 | 交付物 | 验证证据 | 闸门 | 停止条件 | 仓库 | 分支 / PR | 最后批准 Head |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| P1-RUNTIME | BLOCKED | 建立长期可运行的 Paper / Shadow Runtime：只读行情、Paper Account/Fill、Fee/Slippage、Paper Position、Shadow Strategy Run、恢复和监控 | Safety Core Complete | Paper/Shadow、受控模拟、只读市场数据 | Live、真实下单、绕过 Admission | Paper / Shadow 运行闭环 | 批准观察周期内无身份漂移、无对账异常、重启恢复通过 | Paper / Shadow Runtime Gate | 市场输入不可信、账本或对账不健康 | 后端 + 前端 | TBD | TBD |
| P2-STRATEGY | BLOCKED | 建立策略版本、参数/数据快照、回测/Forward Test、生命周期、风险预算、Kill Switch 与归因 | P1-RUNTIME DONE | 策略平台、模拟执行、版本化数据 | 直接绕过风险、未审计的 Live | Strategy Platform | 数据/参数/策略版本可重放，Forward 与 Paper 一致性证据 | Strategy Platform Gate | 策略结果不可重放或无风险预算 | 后端 + 前端 | TBD | TBD |
| P3-SMCICT | BLOCKED | 将 SMC / ICT 编码为确定性可解释规则：Structure、BOS/CHoCH、OB、FVG、Sweep、PD、Session/Killzone、多周期一致性、Entry/Invalidation/Target | P2-STRATEGY DONE | 规则引擎、研究、Paper/Shadow 验证 | AI 直接产生订单、不可解释规则 | SMC / ICT Rule Engine | 单元、回测、Forward、反例与解释输出 | SMC / ICT Gate | 规则依赖未验证 AI 推断或无法解释 | 后端 + 前端 | TBD | TBD |
| P4-AI | BLOCKED | 建立 AI 分析层：市场摘要、策略/风险解释、异常诊断、复盘、参数建议、自然语言查询 | P2-STRATEGY DONE；P3 可并行研究但不放行交易 | 建议、解释、结构化 Candidate Plan、成本与权限控制 | 调用 Exchange、绕过 Admission、修改 Risk Facts、开启 Live | AI Analysis Layer | 输入/输出审计、预算、权限、失败降级与安全测试 | AI Analysis Gate | AI 获得交易或风险权威 | 后端 + 前端 | TBD | TBD |
| P5-LIVE | BLOCKED | 建立受控小额 Live 就绪能力：单交易所/账户/策略、独立凭证、限额、Kill Switch、恢复、对账、人审、紧急平仓和审计 | P1 至 P4 完成；独立审批 | 演练、隔离环境、受控发布设计 | 默认开启、无人审、扩大账户/策略 | Controlled Live Readiness 证据包 | 下列发布检查表全部通过，且单独正式批准 | Controlled Live Readiness Gate | 任一恢复、对账、凭证、告警或演练缺失 | 后端 + 前端 + 运维 | TBD | TBD |

## AI 的永久边界

AI 只能输出建议、解释和结构化 Candidate Plan。AI 不得直接调用 Exchange，不得绕过 Admission，不得修改 Risk Facts，不得开启 Live，也不得把模型输出视为确定性规则的替代品。

## Controlled Live 的最终决定

即使 P5-LIVE 的所有工程和演练证据达到门槛，Live 仍保持 OFF。任何启用必须有单独的正式决策、受限账户与额度、人审、可逆发布计划及持续监控。
