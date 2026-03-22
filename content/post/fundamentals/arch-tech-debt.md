---
title: "Lesson 8: Technical Debt — When to pay, when to live with it"
author: Atharva Pandey
keywords:
  - technical debt
  - software architecture
  - engineering management
  - code quality
  - backend engineering
tags:
  - fundamentals
  - architecture
series: "Software Architecture That Survives"
lesson: 8
date: '2024-08-23T00:00:00.000Z'
---

The term "technical debt" gets used to mean everything from "this code is a mess" to "we made a pragmatic shortcut we need to revisit" to "this system has grown organically and nobody understands it anymore." These are different problems requiring different responses. Treating all technical debt as something that must be paid now, or all of it as something acceptable to defer indefinitely — both lead to bad outcomes. The skill is knowing which debt to address, when, and how.

## How It Works

Ward Cunningham, who coined the term, described technical debt specifically as writing code that doesn't fully reflect your current understanding, with the intention of going back to refactor it later. The "debt" metaphor comes from financial debt: you borrow productivity now and pay interest later in the form of slower development.

The metaphor has limits. Cunningham's original meaning has been stretched to cover every type of code quality issue. A useful taxonomy:

**Intentional vs Unintentional Debt**

- **Intentional, prudent**: "We know a better design, but we're shipping this to meet the deadline and will refactor next sprint." This is manageable if you actually go back.
- **Intentional, reckless**: "We don't have time for tests, ship it." The "we'll add tests later" promise that never gets kept.
- **Unintentional, prudent**: You wrote the best code you could, but you've since learned a better approach. The old code isn't wrong — it's just not optimal given what you know now.
- **Unintentional, reckless**: The team didn't know about good practices when they wrote this. No one noticed. Now it's load-bearing production code.

**Interest Rate**

Technical debt has an interest rate — how much it slows you down per unit time. High-interest debt is in areas you change frequently. If a poorly-designed module is never touched, its interest rate is effectively zero. You can live with it forever. If a poorly-structured module blocks every new feature, its interest rate is enormous and paying it down becomes urgent.

**Debt Quadrant**

```
                HIGH RECKLESSNESS
                       ↑
       Reckless + Intentional  │  Reckless + Unintentional
       "Ship it, fix later"    │  "Spaghetti that grew"
                               │
LOW ──────────────────────────────────────────── HIGH
DELIBERATENESS                 │                DELIBERATENESS
                               │
       Prudent + Intentional   │  Prudent + Unintentional
       "Shortcut with a plan"  │  "Best at the time, now better
                               │   practices exist"
                       ↓
                LOW RECKLESSNESS
```

Only the top-left quadrant is what Cunningham intended. Most real-world debt is in the other three.

**When to Pay**

Pay debt when:
- Its interest rate is high (it's blocking work in a frequently-changed area)
- You're about to change the surrounding code anyway (opportunistic refactoring)
- It poses a security risk or reliability risk
- It's preventing you from hiring because the codebase is too difficult to onboard

Defer debt when:
- The code is stable and rarely changed
- The refactoring cost exceeds the accumulated interest before the system is retired
- You don't have a clear picture of the correct design yet (refactoring to the wrong design is worse than not refactoring)
- There's higher-value work competing for the same time

## Why It Matters

Technical debt management is fundamentally a capacity planning problem. Unchecked debt accumulation leads to the classic symptom: "we spend 80% of our time on maintenance and only 20% shipping new features." The flip side — stopping all feature work to pay down debt — is also dangerous: you lose momentum, business outcomes suffer, and the team loses context on what matters.

The sustainable approach is continuous debt management: paying down debt incrementally as part of normal development, not as a separate "debt sprint" that never gets prioritized.

## Production Example

Identifying and prioritizing debt — a framework I actually use:

```go
// Technical debt inventory — I keep this as a simple file
// debt-registry.md or a label in your issue tracker

type DebtItem struct {
    ID          string
    Description string
    Area        string    // which part of the codebase
    Type        DebtType  // design, test coverage, documentation, dependencies
    InterestRate string   // high/medium/low — how much it slows us down per week
    PayoffCost  string    // small/medium/large — relative effort to fix
    Owner       string    // team or engineer who knows this area
    CreatedAt   time.Time
    Notes       string
}
```

The interest rate × payoff cost matrix helps prioritize:

```
               | LOW COST    | MEDIUM COST | HIGH COST
HIGH INTEREST  | Do now      | Do this quarter | Plan carefully
MEDIUM INTEREST| Do soon     | Next quarter    | Probably defer
LOW INTEREST   | Whenever    | Probably defer  | Likely never
```

Opportunistic refactoring — paying debt as part of related work:

```go
// You're adding a new feature to the user authentication module.
// You notice the password hashing is using MD5 (high-interest, high-risk debt).
// The rule: if you're touching a file, leave it better than you found it.
// You're already here — pay the debt while the context is loaded.

// Before (bad debt discovered):
func hashPassword(password string) string {
    h := md5.Sum([]byte(password))
    return hex.EncodeToString(h[:])
}

// After (debt paid while adding the new feature):
func hashPassword(password string) (string, error) {
    hash, err := bcrypt.GenerateFromPassword([]byte(password), bcrypt.DefaultCost)
    if err != nil {
        return "", fmt.Errorf("hash password: %w", err)
    }
    return string(hash), nil
}
```

Making debt visible in code:

```go
// Use TODO/FIXME comments with tickets for any intentional debt:
// TODO(atharva): This session store uses in-memory storage and loses sessions
// on restart. Replace with Redis before horizontal scaling.
// Ticket: https://linear.app/example/issue/ENG-1234
// Impact: If we restart, users are logged out. Low risk while single-node.

// FIXME: This query does a full table scan on large datasets.
// Acceptable for < 100K rows. Will need an index or pagination strategy
// before reaching 500K rows. Current count: 82K (as of 2024-06).
// Ticket: ENG-5678
```

A team-level debt review process that actually works:

```
Monthly technical debt review (30 minutes):
1. Walk through the debt registry
2. Update interest rates — have any items gotten worse?
3. Promote any "defer" items to "do this quarter" based on current pain
4. For each item being worked this sprint: 10% capacity reserved for debt
5. Close items that are no longer relevant (code was deleted, system retired)
```

The 10% rule: reserve 10% of engineering capacity every sprint for technical debt. Not negotiable, not "if we have time." This sounds small but compounds — it's the difference between a codebase that degrades slowly and one that improves slowly.

**Recognizing when to rewrite a module:**

```
Rewrite signals for a specific module (not the whole system):
- Change failure rate > 30% of changes in this module cause bugs
- Average time to implement a feature in this module > 3x other modules
- Onboarding new engineers takes > 2 weeks just for this module
- Test coverage is < 20% and the module handles critical business logic

If these are true, the interest rate is very high.
Calculate: (cost of rewrite) vs (accumulated interest over next 12 months).
If interest > rewrite cost, rewrite the module (not the whole system).
Use the strangler fig pattern to do it incrementally.
```

## The Tradeoffs

**"Boy Scout Rule" vs focus**: Leaving code better than you found it sounds good in principle. In practice, an undisciplined interpretation leads to sprawling PRs where "added feature X" also includes "refactored 15 files I touched along the way." Scope refactoring to: the function you're changing, the immediate callers you're modifying, and the tests for both. No wider.

**Debt sprints**: Some teams dedicate an entire sprint to paying down debt. This rarely works in practice — product stakeholders view debt sprints as "the team isn't shipping." The sustainable model is ongoing 10-20% capacity allocation, not discrete debt sprints.

**Test coverage as debt metric**: Low test coverage is a form of debt, but coverage percentage is a poor metric. 90% coverage on trivial getters and setters is worse than 60% coverage focused on business logic and edge cases. Measure coverage in the areas where bugs are expensive, not globally.

**Rewrites are debt too**: Every rewrite generates new debt in the form of feature gaps (the new system doesn't have all the edge cases the old one accumulated), migration costs, and the knowledge lost in the people who leave before the rewrite completes. Factor this into the rewrite vs incrementally-improve decision.

**Documentation debt**: Under-documented systems are a form of debt that's easy to ignore and expensive to pay. The interest accumulates every time someone new joins the team and spends days reverse-engineering how something works. Treat documentation as first-class work, not a "nice to have."

## Key Takeaway

Not all technical debt is equal, and not all of it should be paid down. Prioritize by interest rate — debt in high-change areas costs more each week it goes unpaid. Use the opportunistic refactoring rule: improve code you're already touching. Reserve 10% of sprint capacity for debt repayment and treat it as non-negotiable. Make debt visible with comments and a registry. The goal isn't zero debt — it's managing debt so it doesn't accumulate to the point of paralysis. A codebase can be imperfect and productive; the failure mode is a codebase that's so encrusted with high-interest debt that no one wants to touch it.

---

**Previous:** [Lesson 7: Migration Strategies](/post/fundamentals/arch-migrations/)

---

🎓 **Course Complete!** You've finished "Software Architecture That Survives." From monolith-first thinking through clean architecture, event-driven systems, CQRS, DDD, API versioning, migration strategies, and technical debt management — these are the patterns that keep systems maintainable as they grow.
