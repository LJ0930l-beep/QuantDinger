# Unified-order architecture guard

PR-00 adds a pure AST check. It parses protected Python source without importing
or executing routes, workers, exchange clients, or trading services.

Run from the repository root:

```powershell
cd backend_api_python
python scripts/check_order_architecture.py --repo-root ..
```

`order_side_effect_baseline.json` records each existing direct order side effect
by repository-relative path, enclosing symbol, resolved call pattern, semantic
AST fingerprint, and informational line number. A whole file or directory is
never allowlisted. A new call in a file that already contains legacy debt gets a
new fingerprint and fails the guard. Removing a legacy call also fails until the
baseline is deliberately reduced in the same reviewed change.

Protected production scopes currently cover Human/Agent routes, MCP, Grid,
Quick Trade helpers, and native protection. The future gateway is not created by
PR-00; compliant callers use a gateway-level method such as `gateway.submit`,
which is intentionally distinct from exchange `submit_order`/`place_order` APIs.

## Known static-analysis limits

- Non-constant reflection such as `getattr(client, method_name)(...)`, dynamic
  module loading, `eval`/`exec`, and dictionary-based callable dispatch cannot be
  proven safe by this AST guard.
- Constant `getattr(client, "place_order")`, direct attribute chains, imported
  helper aliases, and local aliases of forbidden methods are detected.
- Raw private exchange calls currently used by native protection
  (`_signed_request`/`_swap_private_request_raw`) are treated as order side
  effects throughout the protected scopes; this intentionally prefers a false
  positive over an unguarded private POST path.
- The guard is a regression boundary, not evidence that baselined legacy calls
  are safe. Those calls remain technical debt for later gateway-convergence PRs.
- Generated/build/virtual-environment trees are excluded. Tests use temporary
  fixtures outside protected production paths and do not add a production
  gateway stub.
