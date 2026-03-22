---
title: "Lesson 6: Feature Flags — Progressive rollout and kill switches"
author: Atharva Pandey
keywords:
  - feature flags
  - feature toggles
  - progressive rollout
  - kill switch
  - LaunchDarkly
  - engineering practices
tags:
  - fundamentals
  - engineering practices
series: "Engineering Practices That Scale"
lesson: 6
date: '2024-07-28T00:00:00.000Z'
---

We deployed a new checkout flow on a Friday afternoon. It had passed code review, passed testing, passed staging load tests. By Friday evening, the error rate was climbing. The new flow had a race condition that only manifested under specific mobile browser timing that we hadn't tested. Without a feature flag, the fix would have required an emergency deployment — 25 minutes of build time, deployment, and validation. With a feature flag, the rollback was turning off a switch. Thirty seconds.

Feature flags are a safety mechanism, not just a release tool. I've changed my opinion on them: they're now mandatory for any user-facing change in production.

## How It Works

A feature flag is a conditional in your code that evaluates at runtime:

```go
if flags.IsEnabled(ctx, "new_checkout_flow") {
    return newCheckoutHandler(w, r)
}
return legacyCheckoutHandler(w, r)
```

The flag value comes from a flag service — not from a config file that requires a deployment to change. The flag service evaluates the flag at runtime, usually with:

- **Boolean**: On or off for everyone
- **Percentage rollout**: On for X% of users (consistent per user)
- **User targeting**: On for specific user IDs, emails, or attributes
- **Rule-based**: On for users matching certain criteria (country, plan tier, beta users)

**Flag Lifecycle**

Flags have types that determine their lifecycle:

```
Release flag      → Short-lived. Used to gate a new feature during rollout.
                    Remove within 1-2 sprints of reaching 100%.

Ops (kill switch) → Long-lived. Used to disable a feature in emergencies.
                    Keep indefinitely. The value is the ability to turn it off.

Experiment flag   → A/B test. Run for the duration of the experiment.
                    Analyze, decide, remove.

Permission flag   → Permanent. Controls access by user tier or org.
                    These never go away.
```

The most common mistake: treating all flags as release flags and never removing them. Code with 30 unremoved flags is a maintenance nightmare — every code path branches, tests must cover all combinations, and no one remembers what each flag does.

**Consistent Assignment**

For a percentage rollout, the user should get the same experience on every request. You don't want a user who got the new checkout flow on page load to get the old one when they submit the form. Consistent hashing by user ID achieves this:

```go
func (f *Flags) rolloutPercentage(flagName, userID string) bool {
    h := fnv.New32a()
    h.Write([]byte(flagName + ":" + userID))
    // Hash to 0-99 range
    bucket := h.Sum32() % 100
    return int(bucket) < f.config[flagName].Rollout
}
```

Same flag name + user ID always produces the same bucket, so the same decision.

**OpenFeature Standard**

OpenFeature is an emerging standard for feature flag APIs, similar to how OpenTelemetry standardized observability. It lets you swap flag providers (LaunchDarkly, Unleash, Flagsmith, custom) without changing application code.

```go
import (
    "github.com/open-feature/go-sdk/openfeature"
    ldprovider "github.com/launchdarkly/openfeature-go-provider"
)

// Initialize once
provider := ldprovider.NewProvider(ldClient)
openfeature.SetProvider(provider)

// Use the standard API anywhere
client := openfeature.NewClient("my-app")
enabled, _ := client.BooleanValue(ctx, "new_checkout_flow", false,
    openfeature.NewEvaluationContext("user-123", map[string]interface{}{
        "email": "user@example.com",
        "plan":  "pro",
    }))
```

If you later switch from LaunchDarkly to Unleash, you change one line (the provider initialization) and nothing else.

## Why It Matters

Feature flags decouple deployment from release. This changes the release cycle fundamentally:

- Code can be merged and deployed while incomplete (behind an off flag)
- Releases can be rolled out to 1% → 10% → 50% → 100%, with validation at each step
- Rollbacks are instant (turn off the flag) instead of slow (deploy old code)
- A/B tests are first-class, not hacks

For teams practicing continuous deployment — deploying multiple times per day — feature flags are the mechanism that makes this safe for users. You deploy often; you release on your terms.

## Production Example

A complete feature flag setup in Go with a local fallback cache:

```go
package flags

import (
    "context"
    "encoding/json"
    "hash/fnv"
    "log/slog"
    "sync"
    "time"

    "github.com/redis/go-redis/v9"
)

type FlagConfig struct {
    Enabled       bool     `json:"enabled"`
    Rollout       int      `json:"rollout"`        // 0-100, percentage
    AllowList     []string `json:"allow_list"`     // specific user IDs
    DenyList      []string `json:"deny_list"`
    Description   string   `json:"description"`
    Owner         string   `json:"owner"`
    ExpiresAt     string   `json:"expires_at"`     // for release flags
}

type Service struct {
    redis  *redis.Client
    cache  sync.Map
    cacheTTL time.Duration
}

func (s *Service) IsEnabledForUser(ctx context.Context, flagName, userID string) bool {
    config, err := s.getConfig(ctx, flagName)
    if err != nil {
        slog.WarnContext(ctx, "flag lookup failed, defaulting to false",
            "flag", flagName, "error", err)
        return false // fail closed — safe default
    }

    // Explicit deny list check first
    for _, id := range config.DenyList {
        if id == userID {
            return false
        }
    }

    // Explicit allow list
    for _, id := range config.AllowList {
        if id == userID {
            return true
        }
    }

    // Global on/off
    if !config.Enabled {
        return false
    }

    // Percentage rollout
    if config.Rollout >= 100 {
        return true
    }
    if config.Rollout <= 0 {
        return false
    }

    h := fnv.New32a()
    h.Write([]byte(flagName + ":" + userID))
    return int(h.Sum32()%100) < config.Rollout
}

func (s *Service) getConfig(ctx context.Context, flagName string) (FlagConfig, error) {
    // Check local cache
    if cached, ok := s.cache.Load(flagName); ok {
        entry := cached.(cacheEntry)
        if time.Now().Before(entry.expiresAt) {
            return entry.config, nil
        }
    }

    // Fetch from Redis
    data, err := s.redis.Get(ctx, "flag:"+flagName).Bytes()
    if err == redis.Nil {
        return FlagConfig{}, nil // Flag doesn't exist → disabled
    }
    if err != nil {
        return FlagConfig{}, err
    }

    var config FlagConfig
    if err := json.Unmarshal(data, &config); err != nil {
        return FlagConfig{}, err
    }

    s.cache.Store(flagName, cacheEntry{
        config:    config,
        expiresAt: time.Now().Add(s.cacheTTL),
    })
    return config, nil
}
```

Progressive rollout in practice:

```go
// Week 1: Internal testing only
// redis-cli SET flag:new_checkout_flow '{"enabled":true,"rollout":0,"allow_list":["user-atharva","user-priya"]}'

// Week 2: 5% of users
// redis-cli SET flag:new_checkout_flow '{"enabled":true,"rollout":5}'

// Week 3: 25%
// redis-cli SET flag:new_checkout_flow '{"enabled":true,"rollout":25}'

// Week 4: 100%
// redis-cli SET flag:new_checkout_flow '{"enabled":true,"rollout":100}'

// After stable for 1 sprint: remove flag from code, remove from Redis
```

Ops kill switch for an external dependency:

```go
func (h *PaymentHandler) Authorize(w http.ResponseWriter, r *http.Request) {
    // Kill switch: if Stripe is having issues, fallback to backup processor
    useStripe := h.flags.IsEnabled(r.Context(), "payment_processor_stripe")

    if useStripe {
        result, err := h.stripe.Authorize(r.Context(), req)
        if err != nil {
            // Can turn off this flag immediately from ops console
            // without any deployment
            writeError(w, err)
            return
        }
        writeJSON(w, result)
        return
    }

    // Fallback to backup processor
    result, err := h.backup.Authorize(r.Context(), req)
    writeJSON(w, result)
}
```

Adding flag observability — knowing which flag variant users see:

```go
// Log flag evaluations for analysis
slog.InfoContext(ctx, "flag evaluated",
    "flag", "new_checkout_flow",
    "user_id", userID,
    "result", enabled,
    "reason", reason, // "allow_list", "percentage", "disabled"
)

// Track in metrics
flagEvaluations.WithLabelValues(flagName, strconv.FormatBool(enabled)).Inc()
```

## The Tradeoffs

**Flag evaluation latency**: Every flagged code path makes a flag service call (or cache read). With a local cache, this is sub-millisecond. Without caching, a Redis round trip adds ~1ms per flag check. For high-frequency code paths (every HTTP request), cache aggressively. For low-frequency code (batch jobs), a direct lookup is fine.

**Technical debt from flag accumulation**: Every active flag doubles the number of code paths to test. 10 flags = up to 1,024 combinations. In practice, not all flags interact, but the complexity is real. Enforce a flag lifecycle policy: every release flag has an expiry date, and clearing old flags is part of sprint planning.

**Testing with flags**: Your CI should test with flag combinations, not just the default state. At minimum: test the flag-off path (legacy behavior) and the flag-on path (new behavior). For flags that interact, test the critical combinations.

**Flag state in tests**: Tests that depend on flag state need flag state to be deterministic. Inject a test flag provider that returns predetermined values:

```go
// In tests:
testFlags := flags.NewStatic(map[string]bool{
    "new_checkout_flow": true,
    "legacy_payment_path": false,
})
handler := NewCheckoutHandler(testFlags, ...)
```

**Multivariate flags**: Boolean flags are simple. Multivariate flags (string or numeric values — "which algorithm version?", "what timeout value?") are more powerful but harder to reason about. Start with boolean flags. Reach for multivariate only when you have a clear need.

## Key Takeaway

Feature flags decouple deployment from release, making continuous deployment safe and rollbacks instant. The core pattern is simple: a runtime boolean per feature, evaluated against user context, with consistent assignment for percentage rollouts. Maintain a flag lifecycle policy — release flags expire and must be removed. Use kill switches for any integration with external services or high-risk code paths. The investment in a flag system pays back on the first Friday deployment you don't have to roll back with a 25-minute build cycle.

---

**Previous:** [Lesson 5: Load Testing](/post/fundamentals/eng-load-testing/)
**Next:** [Lesson 7: On-Call Engineering — Reducing toil, improving reliability](/post/fundamentals/eng-oncall/)
