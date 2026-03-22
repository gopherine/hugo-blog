---
title: "Lesson 3: Incident Response — Postmortems and blameless culture"
author: Atharva Pandey
keywords:
  - incident response
  - postmortem
  - blameless culture
  - SRE
  - reliability
  - engineering practices
tags:
  - fundamentals
  - engineering practices
series: "Engineering Practices That Scale"
lesson: 3
date: '2024-06-08T00:00:00.000Z'
---

The first major incident I was on-call for, I spent 90 minutes trying to fix the problem and 30 minutes frantically communicating to stakeholders in a panic. The second one, I followed a runbook and spent the 90 minutes coordinating, communicating clearly, and delegating diagnosis — while the problem was resolved in 40 minutes. The difference wasn't technical skill. It was process. Incident response is a skill you can learn and practice, and it makes a measurable difference in how quickly you restore service and how well the team learns from failures.

## How It Works

**Incident Severity Levels**

Not everything that goes wrong is an incident. Having clear severity definitions prevents alert fatigue and ensures the right level of response:

```
SEV1 — Critical (page immediately, wake people up)
  Complete service outage. Significant data loss. Security breach.
  Business-critical transactions failing for all users.
  Response: All hands, 24/7, until resolved.

SEV2 — Major (page immediately during business hours)
  Significant degradation. > 10% of users affected.
  Core functionality broken with no workaround.
  Response: On-call engineer + team lead within 15 minutes.

SEV3 — Minor (alert, next business day)
  Small subset of users affected. Degraded performance but functional.
  Response: Engineer assigned within hours, fix within 24h.

SEV4 — Informational (log and monitor)
  Non-impacting anomaly. Worth investigating but no user impact.
```

**Incident Response Roles**

Effective incident response separates responsibilities. One person managing everything poorly is worse than multiple people managing their lanes well:

- **Incident Commander (IC)**: Coordinates the response. Owns communication. Makes decisions. Does NOT do deep technical debugging.
- **Technical Lead**: Diagnoses the problem. Implements fixes. Reports status to IC.
- **Comms Lead**: Writes status page updates. Responds to stakeholder messages. Protects the technical team from interruptions.
- **Scribe**: Documents what's happening in the incident channel. Timeline of actions and findings.

For small teams, one person may cover IC + Comms. But the IC never digs into debugging — that splits attention at exactly the wrong time.

**The Response Flow**

```
1. DETECT: Alert fires or user reports problem
         ↓
2. TRIAGE: Severity? Scope? Is it real? 5 minutes max.
         ↓
3. DECLARE: Create incident channel, assign IC, notify stakeholders
         ↓
4. DIAGNOSE: What changed? What are the symptoms? What does the data show?
         ↓
5. MITIGATE: Restore service first, understand cause second.
             Roll back, disable a feature, redirect traffic — whatever works.
         ↓
6. RESOLVE: Problem fixed, service confirmed healthy.
         ↓
7. POSTMORTEM: What happened, why, what we're changing.
```

The key discipline: **mitigation before root cause**. Many incidents drag on because engineers refuse to roll back ("but I know what the fix is") or get deep into debugging when a quick mitigation would restore service. Restore service first. Understand why later.

**The Blameless Postmortem**

A postmortem's purpose is organizational learning, not accountability. If engineers fear punishment after incidents, they'll hide problems, avoid risky-but-necessary work, and be less forthcoming in postmortems. The blameless culture, pioneered by Google SRE, starts from the premise: engineers make reasonable decisions given the information available to them at the time. When an incident happens, the question isn't "who made the wrong call?" but "what made this outcome possible, and what can we change about our systems and processes to prevent it?"

This doesn't mean no accountability exists — it means accountability is at the system level, not the individual level.

A good postmortem structure:

```
1. Summary (2-3 sentences): What happened, for how long, what was the impact.

2. Timeline: Chronological events from first symptom to full resolution.
   Include times. Be specific.

3. Root Cause: The technical cause. Not "engineer made a mistake" but
   "a configuration change was possible without requiring review" or
   "the circuit breaker was not configured for this service."

4. Contributing Factors: What conditions made the root cause possible or worse?
   Multiple contributing factors are expected — incidents are rarely single-cause.

5. Impact: Quantified. How many users? How much revenue? What data?

6. Action Items: Specific, assigned, with due dates. Not "improve monitoring"
   but "Add alert for payment authorization error rate > 1% (owner: @atharva, due: 2024-06-22)"

7. What went well: Acknowledge what worked. Monitoring caught it quickly?
   The rollback was fast? Document this too.
```

## Why It Matters

Every team that runs production systems will have incidents. The question is whether each incident makes the system more reliable (through action items) or just gets forgotten. Blameless postmortems with tracked action items are the mechanism by which reliability improves over time. Without them, you fix the same class of problem repeatedly.

The blameless culture also affects who volunteers for on-call, who reports near-misses before they become incidents, and whether engineers feel safe raising concerns about risky changes.

## Production Example

An incident Slack bot for structure and communication:

```go
// Automated incident creation — removes friction from declaring incidents
package incident

import (
    "fmt"
    "time"
)

type Incident struct {
    ID            string
    Severity      Severity
    Title         string
    IC            string    // Slack user ID
    Channel       string    // Created incident channel
    StartTime     time.Time
    Status        Status
    StatusUpdates []StatusUpdate
}

type StatusUpdate struct {
    Time    time.Time
    Message string
    Author  string
}

// Post to status page
func (i *Incident) PostStatusUpdate(message string, author string) {
    update := StatusUpdate{
        Time:    time.Now().UTC(),
        Message: message,
        Author:  author,
    }
    i.StatusUpdates = append(i.StatusUpdates, update)
    // Post to status page API (Statuspage.io, etc.)
    // Post to internal comms channel
}
```

A postmortem template I actually use:

```markdown
# Incident Postmortem: [INC-2024-0608] Payment Authorization Timeouts

**Date:** 2024-06-08
**Duration:** 47 minutes (14:23 - 15:10 UTC)
**Severity:** SEV2
**IC:** @atharva | **Tech Lead:** @priya | **Scribe:** @james

## Summary
Payment authorization requests experienced >30 second timeouts for 23% of
users between 14:23 and 15:10 UTC on 2024-06-08. The root cause was a database
connection pool exhaustion in the payment service triggered by a slow query
introduced in the 14:00 UTC deployment.

## Timeline
- 14:00 UTC — Deployment of payment-service v2.3.1 completed
- 14:23 UTC — PagerDuty alert: p99 payment latency > 10s (threshold: 2s)
- 14:25 UTC — @atharva declares SEV2, creates #inc-2024-0608-payments
- 14:28 UTC — Status page updated: "Investigating payment slowness"
- 14:31 UTC — @priya identifies elevated query times in Datadog APM
- 14:38 UTC — Root cause identified: N+1 query in order history lookup
- 14:42 UTC — Decision: rollback payment-service to v2.3.0
- 14:47 UTC — Rollback deployed
- 14:52 UTC — Latency returning to normal
- 15:10 UTC — All metrics confirmed normal, incident resolved
- 15:12 UTC — Status page updated: "Resolved"

## Root Cause
A new order history lookup in v2.3.1 introduced an N+1 query: for each
payment authorization, it made 1 + N additional database queries (where N =
number of previous orders for that customer). For users with many orders,
this exhausted the 20-connection database pool within minutes of deployment.

## Contributing Factors
1. No staging environment database was seeded with users having > 100 orders
2. No connection pool exhaustion alert existed
3. Load testing was done with new users (0 order history), not existing users

## Impact
- 23% of payment authorization requests timed out or were slow
- Estimated 847 failed checkout attempts
- ~$42,000 in potential revenue at risk (most would retry successfully)
- No data loss

## Action Items
| Action | Owner | Due |
|--------|-------|-----|
| Add alert: DB connection pool > 80% utilization | @priya | 2024-06-15 |
| Add users with 500+ orders to staging seed data | @james | 2024-06-22 |
| Add query performance check to deployment checklist | @atharva | 2024-06-15 |
| Add N+1 query detection (sqlcheck) to CI | @priya | 2024-06-29 |

## What Went Well
- Alert fired within 3 minutes of the issue starting (new alert added last month)
- Rollback was executed and complete in 5 minutes
- Status page was updated within 5 minutes of incident declaration
- On-call had a runbook for connection pool issues
```

## The Tradeoffs

**Blameless vs accountability**: Blameless doesn't mean consequence-free. If an engineer repeatedly ignores deployment procedures, or makes the same class of mistake many times, that's a management conversation. Blameless means: the first time something goes wrong, the question is "what about our system allowed this?" not "who did this?" The system includes processes, tools, and culture — not just code.

**Postmortem depth vs time cost**: A thorough postmortem for a 5-minute minor incident wastes more time than the incident cost. Calibrate depth to severity. SEV1/SEV2: full postmortem with action items. SEV3: abbreviated postmortem or just action items. SEV4: ticket in the backlog.

**Action item follow-through**: Postmortems that generate action items that never get done are worse than no postmortem — they erode trust in the process. Action items must be tracked in your team's work management system (Jira, Linear, etc.), assigned, given due dates, and reviewed at the next team meeting. If action items consistently slip, that's a process problem to address explicitly.

**Status page transparency**: Public status pages build user trust when things go wrong. Users who can see "we know about the problem and are working on it" are less frustrated than users who get no information. Status pages should be updated within 5 minutes of an incident declaration, even if the update is just "investigating."

**Runbooks**: Runbooks are the leverage of incident response. A good runbook for a known failure mode lets an on-call engineer who's never seen the problem resolve it quickly. Keep runbooks short, actionable, and up to date. A runbook that's wrong is worse than no runbook.

## Key Takeaway

Incident response is a learnable process, not a heroic talent. Separate roles (IC, tech lead, comms) prevent the chaos of one person trying to fix, coordinate, and communicate simultaneously. Restore service first, understand root cause second. Blameless postmortems shift the question from "who broke it" to "what made this possible" — which is the question that leads to actual systemic improvements. Track action items rigorously. The test of a good postmortem is not the document itself — it's whether the same class of incident recurs.

---

**Previous:** [Lesson 2: Code Review That Works](/post/fundamentals/eng-code-review/)
**Next:** [Lesson 4: Monitoring and Alerting — SLOs and alert fatigue](/post/fundamentals/eng-monitoring/)
