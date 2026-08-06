# QuantDinger Deployment Guide

## Architecture

```
                   ┌─────────────┐
                   │   Nginx     │ :443 (TLS)
                   └──────┬──────┘
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
   ┌──────────┐   ┌──────────┐   ┌──────────┐
   │ API      │   │ Worker   │   │ Frontend │
   │ :5000    │   │ :5001    │   │ :8000    │
   └────┬─────┘   └────┬─────┘   └──────────┘
        │              │
        ▼              ▼
   ┌──────────────────────────┐
   │      PostgreSQL :5432    │
   └──────────────────────────┘
        │
        ▼
   ┌──────────────────────────┐
   │      Redis :6379         │
   └──────────────────────────┘
```

## Quick Start (Local)

```bash
# Start all services
docker-compose up -d

# Or use one-click script
powershell -File start-quantdinger.ps1
```

## Blue-Green Deployment

### Initial Setup

1. Deploy "blue" (current) to production domain
2. Deploy "green" (new version) to staging domain
3. Run smoke tests on green
4. Swap Nginx upstream to green
5. Monitor for 5 minutes
6. If healthy: keep green, retire blue
7. If unhealthy: swap back to blue immediately

### Rollback Procedure

```bash
# 1. Swap Nginx back to previous upstream
nginx -s reload -c /etc/nginx/conf.d/quantdinger-blue.conf

# 2. Verify health
curl -f https://api.quantdinger.example.com/health

# 3. Investigate green logs
docker-compose -f docker-compose.production.yml logs --tail=200 green-api
```

## Environment Variables

| Variable | Purpose | Required |
|---|---|---|
| `DATABASE_URL` | PostgreSQL connection string | Yes |
| `REDIS_HOST` | Redis hostname | Yes |
| `SECRET_KEY` | Flask secret key (min 32 bytes) | Yes |
| `AGENT_LIVE_TRADING_ENABLED` | Must be `0` (Live OFF) | Yes |
| `GATE_TESTNET_WRITE_ENABLED` | Must be `0` (Write OFF) | Yes |
| `CACHE_ENABLED` | Redis cache toggle | No |
| `SKIP_STARTUP_HOOKS` | Skip DB init on startup | No |

## Health Checks

| Endpoint | Expected | Interval |
|---|---|---|
| `GET /api/health` | 200 `{"status":"ok"}` | 10s |
| `GET /api/quant/readonly` | 200 or 503 | 30s |
| `GET /metrics` (Prometheus) | 200 | 15s |

## Backup Schedule

- **Full backup**: Daily at 03:00 UTC via cron
- **Retention**: 7 daily, 4 weekly
- **WAL archiving**: Continuous (PITR enabled)
- **Restore test**: Weekly (Sunday) to staging DB

## Monitoring

Key metrics in Prometheus / Grafana (`ops/grafana/dashboards/runtime-overview.json`):

- `quantdinger_order_age_seconds` — alert if > 300
- `quantdinger_unknown_order_count` — alert if > 0
- `quantdinger_outbox_lag_seconds` — alert if > 60
- `quantdinger_reconciliation_mismatch_count` — alert if > 0
- `quantdinger_projection_lag_seconds` — alert if > 120

## Security Checklist

- [ ] `.env` and `*.pem` in `.gitignore`
- [ ] No hardcoded credentials in source code
- [ ] TLS enabled on all external endpoints
- [ ] Database accessible only from internal network
- [ ] Redis password set (if exposed)
- [ ] CI secret scanning enabled (`.github/workflows/security-ci.yml`)
- [ ] `AGENT_LIVE_TRADING_ENABLED=0` in production
- [ ] `GATE_TESTNET_WRITE_ENABLED=0` in production
