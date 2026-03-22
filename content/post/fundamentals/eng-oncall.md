---
title: "Lesson 7: On-Call Engineering — Reducing toil, improving reliability"
author: Atharva Pandey
keywords:
  - on-call
  - toil
  - SRE
  - reliability
  - incident response
  - engineering practices
tags:
  - fundamentals
  - engineering practices
series: "Engineering Practices That Scale"
lesson: 7
date: '2024-08-11T00:00:00.000Z'
---

I did 12 months of on-call on a team that hadn't invested in reliability. The rotation was weekly. In a bad week, I'd get 15-20 pages. A good week was 5. I was exhausted by the end of my shift, and the paging frequency had barely changed over those 12 months. We were fixing incidents, not fixing the causes. The next team I joined approached on-call differently: on-call was treated as a reliability sensor, not a firefighting rotation. Pages were tracked, patterns identified, and root causes fixed. By month 6 I was averaging 2 pages per week on-call. The work we did during on-call made future on-call better.

## How It Works

**What On-Call Is Actually For**

On-call serves two purposes:
1. **Reactive**: Respond to incidents that impact users. Restore service.
2. **Proactive**: Use the data from incidents to improve the system. Reduce future incidents.

Teams that only do #1 are running a hamster wheel. Every new product feature potentially introduces new failure modes, and the operational burden grows without bound. Teams that treat every repeated page as a systemic failure to fix eventually reach a steady state where on-call is quiet.

**Toil and Its Cost**

Toil is repetitive, automatable work that doesn't improve the system — it just maintains the status quo. Examples:
- Manually restarting a service when it runs out of memory
- Running a database cleanup script every Monday
- Escalating a ticket to another team because you don't have permissions to fix it yourself
- Investigating an alert that always turns out to be a false positive

Google SRE recommends that no more than 50% of on-call time should be spent on toil. When toil exceeds 50%, it crowds out the improvement work that would reduce toil over time. The team gets stuck.

**Tracking On-Call Load**

You can't manage what you don't measure. Track every page:

```
Per-page metrics:
- Timestamp
- Service that generated the alert
- Time to acknowledge (TTAck)
- Time to resolve (TTR)
- Was it actionable? (required a human response, or self-resolved?)
- Was it a recurring alert? (same alert fired this week before)
- Root cause category (deployment, dependency, capacity, bug)
- Action taken

Weekly metrics:
- Total pages
- Pages per service
- % actionable
- Mean TTAck, Mean TTR
- Recurring alerts (same root cause, not yet fixed)
```

This data is the input for reliability investment decisions. If the payments service generates 60% of pages, that's where to invest.

**The Runbook**

A runbook is step-by-step instructions for a specific alert. It's the difference between an on-call engineer spending 45 minutes investigating from scratch, and spending 5 minutes following a procedure. Every alert should have a runbook. Every runbook should be tested by someone who didn't write it.

Good runbook structure:
1. **What triggered this**: The specific alert condition and what it means.
2. **Immediate check**: The first thing to look at.
3. **Decision tree**: If X, do Y. If A, do B.
4. **Mitigation options**: Rollback, kill switch, scale up, restart — with specific commands.
5. **Escalation**: Who to wake up if the runbook doesn't resolve it.

**Toil Reduction Strategies**

The recurring page that wakes you up at 3am because "service X ran out of connections" is a fixable system problem. But it requires someone to write the ticket, prioritize it, and actually fix it. The mechanism is the on-call report:

```
Weekly on-call review (15 minutes):
1. Review page count for the week
2. Identify top 3 recurring alerts
3. Create reliability tickets for root causes
4. Prioritize 1-2 reliability tickets for next sprint
5. Did any runbooks fail? Update them.
```

The reliability tickets compete for sprint capacity alongside feature work. This is the organizational conversation: what's the right ratio of feature work to reliability investment? The error budget (from your SLOs) provides the data for this negotiation.

## Why It Matters

On-call that burns out engineers is a retention and recruitment problem, not just an operational one. Engineers leave teams where on-call is miserable. The ones who stay become less effective from cumulative sleep deprivation. High on-call load creates a vicious cycle: engineers are too tired for deep work, reliability improvements don't happen, on-call stays high.

The virtuous cycle: low on-call burden → engineers have capacity for reliability work → reliability improves → on-call burden decreases. Getting into this cycle requires an initial investment in reliability, which requires organizational commitment to treat reliability work as real work.

## Production Example

A practical on-call setup with tooling:

**PagerDuty schedule and escalation policy:**
```yaml
# Typical escalation policy:
# Level 1: Primary on-call engineer (15 minute response SLA)
# Level 2: Secondary on-call / team lead (if no ack after 15 min)
# Level 3: Engineering manager (if no ack after 30 min)

# Rotation:
# - Weekly shifts (Mon 9am → Mon 9am)
# - Two person overlap: primary + secondary
# - After 10pm and before 8am: SEV1/SEV2 only, no SEV3 pages
```

Runbook example (using Notion/Confluence for storage):

```markdown
# Runbook: PaymentService — DB Connection Pool Exhausted

**Alert:** payment_db_connections_in_use > 18 (pool max: 20)
**Severity:** SEV2

## 1. Immediate checks (2 minutes)
- Current connections: `kubectl exec -n prod payment-pod -- curl localhost:8080/debug/stats | jq .db`
- Slow queries: Check Datadog APM → payment-service → Database → Slowest queries
- Recent deployments: `kubectl rollout history deploy/payment-service -n prod`

## 2. Is there a recent deployment?
YES → Roll back immediately:
  `kubectl rollout undo deploy/payment-service -n prod`
  Wait 2 minutes, verify connection count drops.

NO → Continue to step 3.

## 3. Check for slow query spike
- Datadog APM → payment-service → slowest DB queries (last 30 minutes)
- If one query is taking > 10s repeatedly → it's likely blocking connections
- Check if a large batch job started: look for `job.type=bulk_import` in logs

## 4. Emergency relief (if service is failing requests)
- Temporarily increase connection pool:
  `kubectl set env deploy/payment-service DB_POOL_MAX=40 -n prod`
  Note: This is a band-aid. Follow up with ENG ticket.

## 5. Escalate if not resolved in 15 minutes
- Page: @priya (payments team lead)
- Slack: #incident channel with current status

## After mitigation
- Create ENG ticket: root cause + permanent fix
- Update this runbook if steps were wrong or missing
```

On-call metrics dashboard (weekly report, automated):

```python
# weekly_oncall_report.py
# Run every Monday, posts to #on-call-health Slack channel

import pagerduty
from datetime import datetime, timedelta

pd = pagerduty.Client(api_key=os.environ['PD_API_KEY'])

last_week = datetime.now() - timedelta(days=7)
incidents = pd.list_incidents(since=last_week.isoformat())

# Aggregate
by_service = defaultdict(list)
for inc in incidents:
    by_service[inc['service']['summary']].append(inc)

actionable = [i for i in incidents if i['urgency'] == 'high']
noise = [i for i in incidents if i['urgency'] == 'low']

report = f"""
*On-Call Health Report — Week of {last_week.strftime('%Y-%m-%d')}*

Total pages: {len(incidents)}
Actionable (SEV1/SEV2): {len(actionable)}
Noise (SEV3/auto-resolved): {len(noise)}
Noise ratio: {len(noise)/max(len(incidents),1)*100:.0f}%

*Top paging services:*
{format_top_services(by_service)}

*Recurring alerts (same root cause, 2+ times):*
{format_recurring(incidents)}

*Reliability tickets to create:* [see recurring alerts above]
*On-call next week:* @engineer-name
"""

post_to_slack('#on-call-health', report)
```

**Handoff checklist** (end of on-call shift):

```markdown
## On-Call Handoff — Week of 2024-08-11

### Active incidents
- None

### Ongoing issues to watch
- Payment service connection pool has been elevated (15-18) since Tue
  Ticket: ENG-4521 (assigned @priya, expected fix Wed)
  Watch: payment_db_connections_in_use alert

### Flaky alerts (acknowledge quickly, these are noise)
- `staging-worker memory` alert: known issue, ENG-4480
  Will fire ~3x/day. Staging only. Not production impact.

### Runbooks I updated this week
- PaymentService DB connections: added step for batch job detection

### What I'd do differently
- The Tue night payment incident would have been faster with DB connection
  pool metrics on the main dashboard. Raised this with @priya.
```

## The Tradeoffs

**On-call breadth vs depth**: Should engineers be on-call for systems they didn't build? Broad on-call means everyone shares the burden and knows the whole system. Narrow on-call means experts handle their services. The right answer depends on team size. Small teams can't afford specialists for everything. Large teams can. Regardless: runbooks bridge the gap for broad rotations.

**Compensation and acknowledgment**: On-call outside business hours is a real personal cost. It disrupts sleep, weekends, and personal time. This should be compensated (stipend, time off, or both) and acknowledged by leadership. Teams where on-call is "just part of the job" with no recognition have higher turnover.

**Alert threshold tuning**: Tuning thresholds to reduce noise requires accepting that some real problems might be missed. The tradeoff is between sensitivity (catch everything) and specificity (only page when action is needed). The goal is specificity above 80% — fewer than 20% of pages should be noise.

**On-call culture**: Some teams normalize suffering through high on-call load as a sign of dedication. This is a cultural problem, not just an operational one. Good engineering teams treat high on-call burden as a system quality problem and invest in fixing it. On-call should not be a test of individual stamina.

**When to wake someone up**: The decision to escalate and wake up the secondary on-call or a specialist is a judgment call. Rule of thumb: if you've been investigating for 15 minutes without progress and the impact is still active, escalate. Ego about solving it yourself is the enemy of fast resolution.

## Key Takeaway

On-call is a reliability sensor, not a firefighting role. The pages you get tell you where your system has unsolved reliability problems. Track every page, identify recurring patterns, and convert them into reliability tickets. Reserve sprint capacity for reliability work — error budgets and on-call metrics provide the data to justify this to product stakeholders. Reduce toil systematically: automate the manual restarts, fix the flaky alerts, build the runbooks. The measure of a good on-call culture is not how quickly engineers respond to incidents — it's how few incidents recur.

---

**Previous:** [Lesson 6: Feature Flags](/post/fundamentals/eng-feature-flags/)

---

🎓 **Course Complete!** You've finished "Engineering Practices That Scale." From Git power-user techniques through code review, incident response, SLO-based monitoring, load testing, feature flags, and on-call reliability — these are the practices that separate teams that scale from teams that struggle.
