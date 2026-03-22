---
title: "Lesson 7: Migration Strategies — Strangler fig and feature flags"
author: Atharva Pandey
keywords:
  - strangler fig pattern
  - feature flags
  - migration strategy
  - software architecture
  - legacy systems
  - backend engineering
tags:
  - fundamentals
  - architecture
series: "Software Architecture That Survives"
lesson: 7
date: '2024-08-07T00:00:00.000Z'
---

Rewrites are seductive. The existing system is messy, it's slow to change, and the new system in your head is clean and fast and well-designed. Then you start the rewrite. Six months in, you've rebuilt 40% of the functionality and the remaining 60% is more complex than you thought. Meanwhile, the old system keeps shipping features. The new system falls behind, gets cancelled, and you're back where you started, except now you've lost six months and the team is demoralized. I've seen this happen twice. The third time, we used the strangler fig pattern instead, and it worked.

## How It Works

**The Strangler Fig Pattern**

Named after the strangler fig tree that grows around a host tree until it eventually replaces it, this pattern migrates a legacy system incrementally. New functionality is built in the new system. Existing functionality is migrated piece by piece. The old system shrinks as the new one grows. At some point, the old system is empty and can be removed.

```
Phase 1: Facade added
[Client] ──────▶ [Facade/Proxy] ──────▶ [Legacy System]

Phase 2: Some routes migrated
[Client] ──────▶ [Facade/Proxy] ──────▶ [Legacy System]
                      └──── new routes ──▶ [New System]

Phase 3: Migration complete
[Client] ──────▶ [Facade/Proxy] ──────▶ [New System]
                                          (legacy retired)
```

The facade (proxy) is the key enabler. It sits in front of both systems and routes requests. Initially, everything goes to the legacy. As new implementations are ready and verified, the facade routes specific endpoints to the new system. The facade also handles translation between old and new data formats if needed.

**Feature Flags**

Feature flags (also called feature toggles) let you ship code without enabling it for everyone. They decouple deployment from release. You can merge and deploy code that's gated behind a flag that's off, verify it in production with a small percentage of traffic, and progressively roll it out.

Types of feature flags:

- **Release toggles**: Enable new features progressively. Short-lived — removed once 100% rolled out.
- **Ops toggles**: Kill switches. Disable a feature quickly without a deployment. Long-lived.
- **Experiment toggles**: A/B tests. Route different users to different code paths to measure outcomes.
- **Permission toggles**: Enable features for specific users, tiers, or organizations. Often permanent.

**Expand/Contract Pattern**

For database schema migrations specifically, the expand/contract pattern prevents downtime:

```
Step 1 — Expand: Add new column (nullable or with default)
  → Both old and new code can run simultaneously
  → Old code ignores the new column
  → New code writes to both old and new columns

Step 2 — Migrate: Backfill existing rows to populate new column
  → Can run in background during normal operation

Step 3 — Contract: Remove old column
  → Only after 100% of code writes to and reads from new column
  → Old column no longer needed
```

This requires at least two deployments per schema change, but it means zero-downtime database migrations even for destructive changes.

## Why It Matters

Big-bang rewrites fail because software complexity is nonlinear — the last 20% takes as long as the first 80%, new requirements arrive during the rewrite, and the new system never quite matches the behavior of the old system in edge cases. The strangler fig approach keeps the old system running and generating value while you migrate incrementally. Risk is bounded to the portion you've migrated.

Feature flags give you the ability to separate code deployment from feature release. This means you can deploy code continuously (even incomplete features) and control exposure separately. It's what makes continuous delivery practical — you don't need to branch for weeks or gate releases on coordinated deployments.

## Production Example

A strangler fig migration from a legacy Go monolith to a new Go service, using a proxy for routing:

```go
// The facade proxy — routes requests between legacy and new systems
package proxy

import (
    "net/http"
    "net/http/httputil"
    "net/url"
)

type Facade struct {
    legacy *httputil.ReverseProxy
    newSvc *httputil.ReverseProxy
    flags  FeatureFlags
}

func NewFacade(legacyAddr, newSvcAddr string, flags FeatureFlags) *Facade {
    legacyURL, _ := url.Parse(legacyAddr)
    newSvcURL, _ := url.Parse(newSvcAddr)
    return &Facade{
        legacy: httputil.NewSingleHostReverseProxy(legacyURL),
        newSvc: httputil.NewSingleHostReverseProxy(newSvcURL),
        flags:  flags,
    }
}

func (f *Facade) ServeHTTP(w http.ResponseWriter, r *http.Request) {
    switch {
    case strings.HasPrefix(r.URL.Path, "/v2/orders") &&
         f.flags.IsEnabled("orders_new_service"):
        // Migrated: route to new service
        f.newSvc.ServeHTTP(w, r)

    case strings.HasPrefix(r.URL.Path, "/v1/customers") &&
         f.flags.IsEnabled("customers_new_service"):
        // Migrated: route to new service
        f.newSvc.ServeHTTP(w, r)

    default:
        // Not yet migrated: route to legacy
        f.legacy.ServeHTTP(w, r)
    }
}
```

A simple feature flag implementation using LaunchDarkly or a Redis-backed store:

```go
package flags

import (
    "context"
    "encoding/json"
    "github.com/redis/go-redis/v9"
)

type RedisFlags struct {
    client *redis.Client
    cache  sync.Map // local cache to avoid Redis hit per request
}

func (f *RedisFlags) IsEnabled(ctx context.Context, flag string) bool {
    // Check local cache first
    if val, ok := f.cache.Load(flag); ok {
        return val.(bool)
    }
    // Fall back to Redis
    result, err := f.client.Get(ctx, "feature:"+flag).Result()
    if err != nil {
        return false // default off on error
    }
    enabled := result == "1"
    f.cache.Store(flag, enabled)
    return enabled
}

// Percentage rollout — enabled for X% of users
func (f *RedisFlags) IsEnabledForUser(ctx context.Context, flag, userID string) bool {
    var config FlagConfig
    data, _ := f.client.Get(ctx, "feature:"+flag).Bytes()
    json.Unmarshal(data, &config)

    if config.Rollout >= 100 {
        return true
    }
    if config.Rollout <= 0 {
        return false
    }
    // Stable hash of userID — same user always gets same result
    hash := fnv32(userID) % 100
    return int(hash) < config.Rollout
}
```

**Database migration with expand/contract:**

```sql
-- Step 1: Expand — add new column (nullable, backward-compatible)
ALTER TABLE orders ADD COLUMN total_cents BIGINT;

-- Step 2: Backfill — run as a background job, not a migration
UPDATE orders
SET total_cents = (total * 100)::BIGINT
WHERE total_cents IS NULL
LIMIT 10000;  -- Batch to avoid locking the entire table

-- Repeat until complete. Check: SELECT COUNT(*) FROM orders WHERE total_cents IS NULL;

-- Step 3: Contract — once all code is reading/writing total_cents
-- First: add NOT NULL constraint (after confirming zero NULLs)
ALTER TABLE orders ALTER COLUMN total_cents SET NOT NULL;
-- Then: in next deployment, remove old column
ALTER TABLE orders DROP COLUMN total;
```

The Go code during the migration period writes to both columns:

```go
// During migration: write to both old and new columns
func (r *OrderRepo) Update(ctx context.Context, order *Order) error {
    _, err := r.db.ExecContext(ctx, `
        UPDATE orders
        SET total = $2,
            total_cents = $3  -- new column
        WHERE id = $1
    `, order.ID, order.Total.AsDecimal(), order.Total.Cents)
    return err
}
```

**Shadow mode** — run the new system in parallel, compare results:

```go
func (f *Facade) ServeHTTP(w http.ResponseWriter, r *http.Request) {
    if f.flags.IsEnabled("orders_shadow_mode") {
        // Fire and forget — send to new service in background
        go func() {
            shadowReq := r.Clone(context.Background())
            resp, err := f.newSvc.Do(shadowReq)
            if err != nil {
                metrics.Increment("shadow_mode.error")
                return
            }
            // Compare status codes and response shapes
            if resp.StatusCode != /* original status */ {
                metrics.Increment("shadow_mode.mismatch")
                log.Warn("shadow mode mismatch", ...)
            }
        }()
    }
    // Serve from legacy as normal
    f.legacy.ServeHTTP(w, r)
}
```

Shadow mode lets you validate the new implementation against real production traffic before routing any real traffic to it.

## The Tradeoffs

**Maintenance cost of the dual-write period**: During a migration, you're maintaining two systems. Code complexity temporarily doubles. Teams need to understand both old and new behavior. Keep the migration window short by prioritizing migration sprints over new features.

**Feature flag sprawl**: Feature flags are technical debt if they're never cleaned up. Every flag adds a code path that needs to be tested. Use a flag lifecycle policy: every flag has an owner, a purpose, and an expiry date. Remove release flags within one sprint of reaching 100% rollout.

**Facade as a bottleneck**: The proxy adds a network hop and is a single point of failure. It needs to be as simple and reliable as possible. Keep it stateless, keep it simple, load balance it, and don't put business logic in it.

**Data consistency during migration**: When two systems write to the same database, they can conflict. When they write to separate databases (full migration), you need to keep them in sync during the transition. Event-driven synchronization (change data capture, dual writes) handles this but adds complexity. This is often the hardest part of a migration.

**Testing the migration**: Before routing production traffic to the new system, test it with shadow mode. Compare outputs. Build confidence that it behaves identically to the legacy for all the edge cases you've discovered over years of running the old system.

## Key Takeaway

Never do a big-bang rewrite. Migrate incrementally with the strangler fig pattern: add a facade, route traffic progressively, verify each migrated piece before moving on. Feature flags decouple deployment from release, giving you the ability to test new implementations in production without customer impact. The expand/contract pattern makes database schema migrations zero-downtime. Shadow mode validates new implementations against real traffic. The common thread: make each change small, reversible, and independently verifiable.

---

**Previous:** [Lesson 6: API Versioning](/post/fundamentals/arch-api-versioning/)
**Next:** [Lesson 8: Technical Debt — When to pay, when to live with it](/post/fundamentals/arch-tech-debt/)
