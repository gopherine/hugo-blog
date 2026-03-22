---
title: "Lesson 2: Code Review That Works — What to look for"
author: Atharva Pandey
keywords:
  - code review
  - pull request
  - engineering practices
  - software quality
  - team collaboration
tags:
  - fundamentals
  - engineering practices
series: "Engineering Practices That Scale"
lesson: 2
date: '2024-05-22T00:00:00.000Z'
---

I've been on the wrong end of bad code reviews in both directions. Reviews that were nitpick sessions about variable naming while missing a race condition. Reviews that rubber-stamped everything because the reviewer was busy. And I've given both kinds myself. It took a few years, a few incidents, and a few honest retrospectives to develop a framework for reviews that actually improve code quality without burning out reviewers or demoralizing authors.

## How It Works

Code review serves two purposes that are often conflated: **quality assurance** (finding bugs, logic errors, security issues) and **knowledge sharing** (spreading understanding across the team). A good review process serves both. A bad one serves neither.

**The Priority Hierarchy**

When reviewing code, I work from highest to lowest severity. Most review comments should be in the top tiers, not the bottom:

```
P1 — Bugs and correctness issues
     Race conditions, off-by-one errors, null pointer dereferences,
     incorrect logic, missing error handling for critical paths

P2 — Security issues
     SQL injection, authorization bypass, secrets in code,
     unsafe deserialization, input not validated

P3 — Design issues
     This approach will cause problems later. Wrong abstraction.
     This shouldn't live in this package. Wrong layer.

P4 — Readability and maintainability
     Confusing variable names, missing comments on complex logic,
     duplicated code that should be extracted

P5 — Style and formatting
     (Should be automated. If your CI doesn't run a formatter,
      add one before reviewing style manually.)
```

If I'm spending 70% of review time on P4/P5 issues, something is wrong. Either the formatter isn't running, or the author needs coaching on the basics, or I'm not prioritizing correctly.

**The Review Process**

I review code in this order:

1. **Read the PR description first.** What problem is this solving? If there's no description, ask for one before reviewing. You can't evaluate a solution without understanding the problem.

2. **Look at the tests.** What did the author test? Are the happy paths covered? Are the error paths covered? Tests tell you what the author understood about the behavior. Missing tests often indicate missing understanding.

3. **Read the diff top to bottom.** Look for bugs and logic errors. Don't get distracted by style.

4. **Consider the broader context.** Does this interact with any concurrent code? Does this change any behavior that other systems depend on? Is there a migration needed?

5. **Check for security issues.** SQL queries built with string concatenation. API endpoints without authentication. Sensitive data logged.

6. **Add readability comments last.** After the important stuff.

**How to Write Review Comments**

There's a spectrum from blocking to non-blocking. Being explicit about which your comment is saves a lot of back-and-forth:

- **Blocking ("must fix")**: This is a bug. This is a security issue. This will break in production.
- **Suggestion ("could fix")**: I think there's a better approach, but your approach works.
- **Nit ("optional")**: Minor style or naming preference. Prefix with "nit:" so the author can decide.
- **Question ("explain")**: I don't understand why. Not necessarily wrong — might just need context.

```
// BLOCKING:
This will panic if `user` is nil. The database query can return nil
when the user doesn't exist. Add a nil check.

// SUGGESTION:
This could be simplified using `errors.As` instead of a type assertion.
Not a blocker, but would be more idiomatic Go.

// NIT:
nit: `processOrder` might be clearer as `confirmOrder` since it only
runs during the confirmation step.

// QUESTION:
Why are we skipping validation for admin users here? Is this intentional?
```

Framing matters. "This is wrong" triggers defensiveness. "This will panic when..." focuses on the consequence, which is collaborative rather than accusatory.

## Why It Matters

Code review is the last line of defense before code reaches production — but it shouldn't be the only one. If your CI doesn't run tests, if you don't have automated formatters, if you don't have linters catching obvious issues — your reviewers spend time on things that should be automated, leaving less attention for the bugs that matter.

At the team level, code review is how knowledge propagates. The junior engineer who sees a senior reviewer explain a concurrency issue learns from it. The senior engineer who sees a junior engineer using a new library learns too. The alternative — siloed ownership where only the original author understands their own code — creates fragility.

## Production Example

A PR description template that makes reviews easier:

```markdown
## What does this PR do?
Adds retry logic with exponential backoff to the payment authorization service
client. The current client fails immediately on transient network errors.

## Why?
We're seeing ~0.3% of payment authorization requests fail due to transient
network errors. These should succeed on retry. Ticket: ENG-2345.

## How?
- Added `RetryClient` wrapper in `internal/payments/client.go`
- Default: 3 retries with 100ms initial backoff, 2x multiplier, jitter
- Only retries on `codes.Unavailable` and connection errors (not on 4xx/logic errors)
- Added integration test against a mock that fails the first 2 requests

## What should reviewers focus on?
- The retry logic in `retry.go` — specifically the backoff calculation and the
  jitter implementation
- The error classification logic — are we retrying on the right error codes?

## Checklist
- [x] Unit tests added/updated
- [x] Integration test covers retry behavior
- [x] No secrets or hardcoded values
- [x] Backward compatible
```

A review comment that teaches rather than just flags:

```go
// Code being reviewed:
func processOrders(orders []Order) {
    for _, o := range orders {
        go processOrder(o)  // <- reviewer sees this
    }
}

// Review comment (blocking):
//
// This has a goroutine leak. If `processOrder` blocks or the program exits,
// there's no way to cancel these goroutines or wait for them to finish.
//
// Consider using errgroup or a worker pool with a WaitGroup:
//
//   var wg sync.WaitGroup
//   for _, o := range orders {
//       wg.Add(1)
//       go func(order Order) {
//           defer wg.Done()
//           processOrder(order)
//       }(o)
//   }
//   wg.Wait()
//
// Or for bounded concurrency:
//   g, ctx := errgroup.WithContext(ctx)
//   for _, o := range orders {
//       o := o
//       g.Go(func() error { return processOrder(ctx, o) })
//   }
//   return g.Wait()
```

An automated checklist in your PR template for security-sensitive code:

```yaml
# .github/pull_request_template.md
## Security checklist (for changes touching auth, payments, user data)
- [ ] No SQL queries built with string concatenation (use parameterized queries)
- [ ] All API endpoints authenticated where required
- [ ] User input validated before use
- [ ] No sensitive data in logs (passwords, tokens, PII)
- [ ] Dependent service calls use timeouts
```

## The Tradeoffs

**Review turnaround time**: A PR that sits for 48 hours is a context switch cost for the author when it finally gets reviewed. Set a team SLA — respond to review requests within 4 business hours. This doesn't mean a full review in 4 hours — it means at least acknowledging it and scheduling a time.

**Review size**: Large PRs are harder to review well. Over 400 lines changed, review quality drops — it's cognitively exhausting and things get missed. If a PR is too large, ask for it to be split. Not every change can be split, but most can. The author benefits too: smaller PRs merge faster.

**Author autonomy**: Code review can become a vehicle for imposing your preferences on other people's code. If the code is correct, readable, and tested, and the reviewer's suggestion is a different-but-equivalent approach — it's a suggestion, not a requirement. Save blocking comments for issues that actually matter.

**The "LGTM" culture**: Rubber-stamp approvals are worse than useful — they give false confidence while providing no value. If you don't have time to review properly, say so and schedule a time when you do. An LGTM without reading the code is actively harmful.

**Review vs pairing**: For complex changes or when an engineer is learning a new area, pairing is often faster and better than async review. The back-and-forth of async comments is replaced by a real-time conversation. Use pairing for high-complexity, high-stakes changes; async review for routine changes.

## Key Takeaway

Code review is quality assurance and knowledge sharing — not nitpicking. Work through the priority hierarchy: bugs and security issues first, design issues second, style last. Be explicit about whether comments are blocking or suggestions. Write comments that explain the consequence, not just the judgment. Automate what can be automated (formatting, linting) so human attention focuses on what machines can't do. And review the PR description before the code — you can't evaluate a solution without understanding the problem it's solving.

---

**Previous:** [Lesson 1: Git Beyond Basics](/post/fundamentals/eng-git/)
**Next:** [Lesson 3: Incident Response — Postmortems and blameless culture](/post/fundamentals/eng-incidents/)
