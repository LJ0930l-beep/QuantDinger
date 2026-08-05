# Test and Release Matrix

| 层级 | 必测内容 |
| --- | --- |
| Unit | Decimal、canonicalization、action/risk matrix、scope、fingerprint、策略规则、数量和冷静期 |
| PostgreSQL | FK/CHECK/append-only、caller-owned transaction、replay/conflict、unique race、projection generation、ledger balance |
| Integration | Admission、Outbox、Consumer、Projection、Shadow、Reconciliation、Gate read contracts |
| Replay | 相同事实相同结果；不同 scope/规则/参数 typed conflict；重启可重建 |
| Concurrency | 两连接、锁序、幂等、reservation、offset/event、Gate sync |
| Failure | DB/网络/容器重启、rollback、timeout、stale/missing/conflict、孤儿事实 |
| Security | Secret scan、CodeQL、dependency/source audit、权限、restricted source、Live OFF |
| Guard | Architecture Guard=46 或下降；Entry baseline ≤31；AI boundary 不增加 |

## 发布闸门

精确 Head 的 Backend CI 与 Security CI 全绿后，完成 Diff 自审、树一致性、Live/凭证检查和 PR 描述更新，再 Ready/Squash Merge。任何旧 Head CI 不作为当前验收依据；失败必须定位具体 Job/traceback，不得以重跑成功掩盖。
