# 闸门与停止条件

## 工作模式

| 模式 | 使用时机 | 必须输出 |
|---|---|---|
| 记录模式 | 正常、已获批的单项实现 | Base、Head、提交、变更文件、测试、CI、Guard、Live、阻塞项 |
| 故障模式 | CI、并发、数据库、静态架构或安全扫描失败 | 精确 Head、失败 Job/测试、异常类型、关键 traceback、最小修复边界 |
| 闸门模式 | 合并前或阶段切换前 | 范围、Head、CI、tree parity、Guard、Live、结论 PASS / NEEDS CHANGES |

## 全项目停止条件

出现任何一项时，停止当前实现，保留现场并报告；不得用测试桩、宽泛 allowlist 或重跑掩盖问题。

1. 需要 fake legacy fact，或需要随机 UUID 作为业务幂等身份。
2. `correlation_id` 进入经济 fingerprint。
3. Gateway / Repository 的 caller-owned 方法自行 commit 或 rollback。
4. CANCEL 被迫进入 Hard Risk；DENY 创建 Reservation 或 Outbox；降低风险动作创建 Reservation。
5. Projection、Shadow Diff 或 Reconciliation 产生交易决策。
6. 新增 direct Executor / Exchange bypass，或 Guard / Entry-Point baseline 增加。
7. Live 被启用，或出现未获批的真实账户、凭证或交易调用。
8. 需要 destructive migration，或 Schema 前置不足却试图用测试替代权威合同。
9. raw driver exception 泄漏、死锁未类型化、连接在 rollback 后不可复用。
10. 需要修改或提交 `docs/codex/`。
11. CI 结果不属于批准 Head、PR base 变化、tree parity 不成立，或发现未获批的文件范围。

## 精确 Head 与 PR 闸门

每个 Draft PR 在 Ready 前必须证明：

- PR 为 Open / Draft，base 与批准基线一致，Head 精确一致且 mergeable/CLEAN。
- Backend、PostgreSQL（适用时）、CodeQL、依赖/源码审计和 Secret Scan 均针对精确 Head 成功。
- `git diff --check`、相关定向测试、完整相关回归、Architecture Guard 和 Live OFF 检查成功。
- 合并后验证 local main = origin/main；Squash PR 需验证 main 与原 PR 分支 tree parity。
- 仅在上述全部成立后清理远程分支、本地分支和 worktree。

## 当前与未来闸门

| Gate | 状态 | 必要证据 | 禁止推断 |
|---|---|---|---|
| G4-A Admission Write Chain | DONE | PR #21、`b111fae`、精确 Head CI、Guard 46、baseline 44、Live OFF | 不把它写成 Projection Consumer 已完成 |
| G4-B Read Cutover | DEFERRED | PR-14 的 Consumer、Candidate Projection、Shadow、Reconciliation、Derived Health 和故障演练 | 不因 Outbox 已存在而默认通过 |
| Entry-Point Convergence Gate | NOT_STARTED | 七类入口收口、baseline 只减不增、无直接执行调用 | 不因 Adapter 存在而默认完成迁移 |
| Read Cutover / G4-B Gate | DEFERRED | generation/watermark/replay、Shadow、Reconciliation、Derived Health、只读 API | 不让读模型参与交易决策 |
| Legacy Retirement and Failure Drill Gate | NOT_STARTED | 无遗留权威事实、无孤儿、升级/重启/重复/网络/DB 故障演练 | 不把 16/16 解释为自动 Live |
