---
title: "Lesson 1: Big-O Thinking — Will this scale to 1M records?"
author: "Atharva Pandey"
keywords:
  - big-o notation
  - time complexity
  - space complexity
  - algorithm analysis
  - scalability
  - production engineering
tags:
  - fundamentals
  - algorithms
series: "Algorithms That Matter in Production"
lesson: 1
date: "2024-04-18T00:00:00.000Z"
---

I spent two years writing Go services before I really internalized Big-O. Not because I didn't know the notation — every CS course teaches you to recite O(n log n) — but because I never tied it to a real decision I had to make in production. It clicked for me the day a coworker asked, "will this work when we have a million users?" and I had no honest answer.

Big-O is not about impressing interviewers. It is about predicting failure before it happens.

## How It Works

Big-O describes how a function's runtime or memory usage grows relative to its input size. The key word is "relative." You are not measuring seconds. You are measuring the shape of growth.

The common classes you will actually encounter in production:

- **O(1)** — constant. A hash map lookup. Does not matter if there are 10 or 10 million records.
- **O(log n)** — logarithmic. Binary search. Halves the problem every step.
- **O(n)** — linear. Scanning a slice. Work grows proportionally with input.
- **O(n log n)** — linearithmic. Sorting. Acceptable at scale, but noticeable.
- **O(n²)** — quadratic. Nested loops. Kills you at 100k records.
- **O(2^n)** — exponential. Brute-force combinatorics. Kills you at 30.

```go
// O(1) — lookup by key
func getUser(cache map[string]User, id string) (User, bool) {
    u, ok := cache[id]
    return u, ok
}

// O(n) — scan every record
func findByEmail(users []User, email string) (User, bool) {
    for _, u := range users {
        if u.Email == email {
            return u, true
        }
    }
    return User{}, false
}

// O(n²) — compare every pair
func findDuplicateEmails(users []User) []string {
    var dups []string
    for i := 0; i < len(users); i++ {
        for j := i + 1; j < len(users); j++ {
            if users[i].Email == users[j].Email {
                dups = append(dups, users[i].Email)
            }
        }
    }
    return dups
}
```

That last function looks innocent. At 1,000 users it does roughly 500,000 comparisons. At 100,000 users it does 5 billion. A query that takes 2ms in staging will timeout in production.

## When You Need It

You need to think in Big-O whenever:

- You write a loop inside a loop (usually a signal to pause)
- You are choosing between a slice and a map for lookups
- A dataset could grow unbounded
- You are building a feature that runs on every row in a database

The mental model I use: at 1M records, O(n) is probably fine if the constant is small. O(n log n) is fine for offline processing. O(n²) is a time bomb.

```go
// Thinking through the tradeoff at the call site
// Scenario: check which of 10,000 incoming IDs are already in a set of 500,000 known IDs

// WRONG approach — O(n*m) = 10,000 * 500,000 = 5 billion operations
func filterKnown(incoming []string, known []string) []string {
    var result []string
    for _, id := range incoming {
        for _, k := range known {
            if id == k {
                result = append(result, id)
                break
            }
        }
    }
    return result
}

// RIGHT approach — O(n + m) = build the set once, then look up each
func filterKnownFast(incoming []string, known []string) []string {
    knownSet := make(map[string]struct{}, len(known))
    for _, k := range known {
        knownSet[k] = struct{}{}
    }
    var result []string
    for _, id := range incoming {
        if _, ok := knownSet[id]; ok {
            result = append(result, id)
        }
    }
    return result
}
```

The second version builds a hash set in O(m), then does O(n) lookups. Total: O(n + m). At those input sizes, the difference between the two approaches is seconds versus microseconds.

## Production Example

A billing service at a previous job had to cross-reference invoice line items against a catalog of ~200,000 product SKUs to apply discount rules. The original code did a linear scan of a slice for every line item. Reports had 3,000 to 8,000 line items. Every report generation ran a few hundred million comparisons.

The fix was a single architectural change: load the discount catalog into a map once at startup, then do O(1) lookups per line item.

```go
type DiscountCatalog struct {
    rules map[string]DiscountRule // keyed by SKU
}

func NewDiscountCatalog(rules []DiscountRule) *DiscountCatalog {
    m := make(map[string]DiscountRule, len(rules))
    for _, r := range rules {
        m[r.SKU] = r
    }
    return &DiscountCatalog{rules: m}
}

// O(1) per lookup instead of O(n) per lookup
func (c *DiscountCatalog) Apply(sku string, price float64) float64 {
    rule, ok := c.rules[sku]
    if !ok {
        return price
    }
    return price * (1 - rule.Discount)
}

// Processing a report: O(items) instead of O(items * catalog_size)
func processReport(items []LineItem, catalog *DiscountCatalog) []LineItem {
    for i := range items {
        items[i].FinalPrice = catalog.Apply(items[i].SKU, items[i].Price)
    }
    return items
}
```

Report generation dropped from 8–12 seconds to under 50ms. The fix was not clever code — it was understanding the complexity class of the operation.

## The Tradeoffs

Big-O hides constants. O(n) with a constant of 1,000 is slower than O(n²) with a constant of 0.001 for small n. The notation tells you the asymptotic behavior, not the real-world performance for your specific input size.

Memory is a dimension too. Changing the billing code to use a map improved time complexity but increased memory usage. For a catalog that could theoretically be 50M records, the map approach is not viable. You would need a database with an index instead, which gives you O(log n) per lookup with bounded memory.

Also: premature optimization is real. Profile before you rewrite. But knowing Big-O lets you recognize the class of problem before writing the code, so you avoid building something you will have to tear down.

Space complexity follows the same notation. Recursive algorithms with deep call stacks use O(depth) stack space. Memoization tables trade time for O(n) memory. You always have a budget for both.

## Key Takeaway

Big-O is not academic. It is the mental model you use to answer "will this break at scale?" before you ship it. Learn to look at every loop and ask: what is the complexity class here, and what does that mean at 1M records? Most production bugs are not logic errors — they are quadratic loops hiding inside linear-looking code.

---

[Course Index](/post/fundamentals/) | Next: [Lesson 2: Sorting in Practice](/post/fundamentals/algo-sorting/) →
