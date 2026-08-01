# Project Progress Model

## 两种进度

- **Official Progress**：任务已合并、Definition of Done 全部满足、精确 Head CI 通过并有 Gate 记录。
- **Candidate Progress**：Draft PR、未合并 Head 或本地候选实现；不计入正式 Safety Core。

当前：Safety Core Official 13/16；PR #33 已合并后只代表入口退役证据的一部分，SC-13 仍需完整收口。多资产 Gate First 产品进度在文档中单独记录，不与 Safety Core 混算。

## 任务状态

`NOT_STARTED → PLANNED → IN_PROGRESS → DRAFT → MERGE_CANDIDATE → DONE`。

失败进入 `BLOCKED`，必须记录根因、证据和解除条件；不得通过跳过测试或放宽 baseline 强行进入下一状态。

## 权重规则

权重只用于路线可视化，不替代 Gate。每个子任务只有在 DoD 完成后才计入该领域；依赖未完成时即使代码存在也计 0%。

## 必须记录

`task_id, base, head, commits, changed_files, dependencies, tests, PostgreSQL, replay, concurrency, failure, security, Guard, Live, stop_conditions, rollback`。
