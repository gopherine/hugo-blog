---
title: "Lesson 29: State Machine DP — Track what state you're in"
author: "Atharva Pandey"
keywords:
  - dynamic programming
  - state machine DP
  - best time to buy sell stock
  - stock trading
  - paint houses
  - cooldown
  - transaction limit
tags:
  - fundamentals
  - interviews
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 29
date: "2025-10-04T00:00:00.000Z"
---

Most DP problems have a clean one-dimensional state: position in an array, remaining capacity, current index. State machine DP adds another dimension that isn't just a number — it's a *mode* or *status* that your system is in. "Am I currently holding a stock? Am I in a cooldown period? Which color did I just paint the last house?"

The stock trading problems are the canonical example. LeetCode has an entire series of them (I, II, III, IV, with Cooldown, with Transaction Fee) that all share the same structure but add constraints one by one. Solving them all with the same mental framework is satisfying once the state machine clicks.

The key skill: identify the finite set of states, define a DP value for each state at each time step, and write transitions that respect the rules for moving between states.

## The Pattern

State machine DP follows this shape:

1. Define the states: what distinct "modes" can the system be in?
2. For each day/step, maintain a value for each state.
3. Transitions: how can you move from state A to state B on the current step? What cost/reward does that transition carry?
4. Initialize states at day 0 (or day 1) carefully — some states are impossible at the start.

---

## Problem 1: Best Time to Buy and Sell Stock — Variants II, III, IV, and Cooldown

Let me work through the variants systematically, because the state machine approach makes all of them fall from a single template.

### Variant II: Unlimited Transactions (LeetCode 122)

States: `held` (holding a stock), `free` (not holding, no restriction).

Transitions:
- `free → held`: buy on this day. `held[i] = max(held[i-1], free[i-1] - prices[i])`
- `held → free`: sell on this day. `free[i] = max(free[i-1], held[i-1] + prices[i])`
- Stay in same state (do nothing).

```go
func maxProfitII(prices []int) int {
    held := -prices[0] // bought on day 0
    free := 0          // didn't buy on day 0

    for i := 1; i < len(prices); i++ {
        newHeld := held
        if free-prices[i] > newHeld {
            newHeld = free - prices[i]
        }
        newFree := free
        if held+prices[i] > newFree {
            newFree = held + prices[i]
        }
        held = newHeld
        free = newFree
    }
    return free
}
```

O(n) time, O(1) space. The `newHeld`/`newFree` temporaries prevent using today's updated values in the same step.

### Variant with Cooldown (LeetCode 309)

After selling, you must wait one day before buying again. Add a `cooldown` state:

States: `held` (holding), `cooldown` (just sold, must wait), `free` (can buy freely).

Transitions:
- `free → held`: buy. `held[i] = max(held[i-1], free[i-1] - prices[i])`
- `held → cooldown`: sell. `cooldown[i] = held[i-1] + prices[i]`
- `cooldown → free`: cooldown expires. `free[i] = max(free[i-1], cooldown[i-1])`

```go
func maxProfitCooldown(prices []int) int {
    held := -prices[0]
    cooldown := 0
    free := 0

    for i := 1; i < len(prices); i++ {
        newHeld := held
        if free-prices[i] > newHeld {
            newHeld = free - prices[i]
        }
        newCooldown := held + prices[i]
        newFree := free
        if cooldown > newFree {
            newFree = cooldown
        }
        held = newHeld
        cooldown = newCooldown
        free = newFree
    }
    return free
}
```

The cooldown variant is where the state machine framing pays off. Without it, you'd try to handle the "skip next day" rule inline with awkward index arithmetic.

### Variant III: At Most 2 Transactions (LeetCode 123)

Now transactions are limited. Add a "transaction count" dimension to the state.

States: `hold1` (holding after first buy), `free1` (sold once, or never bought), `hold2` (holding after second buy), `free2` (sold twice — or reached after any second sale).

Transitions in order of state progression:
- `free1 → hold1`: first buy. `hold1 = max(hold1, free1 - prices[i])`
- `hold1 → free1': first sell. `free1 = max(free1, hold1 + prices[i])` ← this updates in-place because we use the sell state
- Actually cleaner to track: `hold1`, `sold1`, `hold2`, `sold2`.

```go
func maxProfitIII(prices []int) int {
    hold1 := -prices[0]
    sold1 := 0
    hold2 := -prices[0]
    sold2 := 0

    for i := 1; i < len(prices); i++ {
        p := prices[i]
        // Update in reverse order of state chain to avoid using today's values
        sold2 = max2(sold2, hold2+p)
        hold2 = max2(hold2, sold1-p)
        sold1 = max2(sold1, hold1+p)
        hold1 = max2(hold1, -p)
    }
    return sold2
}

func max2(a, b int) int {
    if a > b {
        return a
    }
    return b
}
```

**Why update in reverse order:** `sold2` uses `hold2` from yesterday. If we update `hold2` first, we'd be using today's `hold2` to update `sold2`. Updating in reverse (sold2, hold2, sold1, hold1) ensures each state uses yesterday's values for the previous state.

### Variant IV: At Most k Transactions (LeetCode 188)

Generalize: maintain arrays `hold[k]` and `sold[k]`.

```go
func maxProfitIV(k int, prices []int) int {
    n := len(prices)
    if n == 0 {
        return 0
    }
    // If k >= n/2, it's equivalent to unlimited transactions
    if k >= n/2 {
        return maxProfitII(prices)
    }

    hold := make([]int, k)
    sold := make([]int, k)
    for i := range hold {
        hold[i] = -prices[0]
    }

    for i := 1; i < n; i++ {
        p := prices[i]
        for j := k - 1; j >= 0; j-- {
            sold[j] = max2(sold[j], hold[j]+p)
            if j == 0 {
                hold[j] = max2(hold[j], -p)
            } else {
                hold[j] = max2(hold[j], sold[j-1]-p)
            }
        }
    }
    return sold[k-1]
}
```

The k >= n/2 short-circuit is important: with enough allowed transactions, you can capture every upward move, which equals the unlimited variant.

---

## Problem 2: Paint Houses

**LeetCode 256 / 265.** There are n houses to paint. Paint house i with one of k colors. Adjacent houses cannot be the same color. The cost of painting house i with color c is `costs[i][c]`. Minimize total cost.

### Define the subproblem

`dp[i][c]` = minimum cost to paint houses 0 through i such that house i is painted color c.

### Recurrence

```
dp[i][c] = costs[i][c] + min over all c' != c of dp[i-1][c']
```

For k = 3 colors, this is:

```
dp[i][0] = costs[i][0] + min(dp[i-1][1], dp[i-1][2])
dp[i][1] = costs[i][1] + min(dp[i-1][0], dp[i-1][2])
dp[i][2] = costs[i][2] + min(dp[i-1][0], dp[i-1][1])
```

### Build bottom-up (3 colors)

```go
func minCostPaint(costs [][]int) int {
    n := len(costs)
    if n == 0 {
        return 0
    }

    dp := make([][]int, n)
    for i := range dp {
        dp[i] = make([]int, 3)
    }
    dp[0][0] = costs[0][0]
    dp[0][1] = costs[0][1]
    dp[0][2] = costs[0][2]

    for i := 1; i < n; i++ {
        dp[i][0] = costs[i][0] + min2(dp[i-1][1], dp[i-1][2])
        dp[i][1] = costs[i][1] + min2(dp[i-1][0], dp[i-1][2])
        dp[i][2] = costs[i][2] + min2(dp[i-1][0], dp[i-1][1])
    }

    result := dp[n-1][0]
    if dp[n-1][1] < result {
        result = dp[n-1][1]
    }
    if dp[n-1][2] < result {
        result = dp[n-1][2]
    }
    return result
}

func min2(a, b int) int {
    if a < b {
        return a
    }
    return b
}
```

### Space optimization and scaling to k colors

For k colors (LeetCode 265), the naive transition is O(k) per state, giving O(nk²) total. But you only need the two smallest values from the previous row — not the full array — to compute each transition in O(1):

```go
func minCostII(costs [][]int) int {
    n, k := len(costs), len(costs[0])
    if n == 0 {
        return 0
    }

    prev := make([]int, k)
    copy(prev, costs[0])

    for i := 1; i < n; i++ {
        // Find two smallest values in prev
        min1, min2 := 1<<31-1, 1<<31-1
        min1Idx := -1
        for c := 0; c < k; c++ {
            if prev[c] < min1 {
                min2 = min1
                min1 = prev[c]
                min1Idx = c
            } else if prev[c] < min2 {
                min2 = prev[c]
            }
        }

        curr := make([]int, k)
        for c := 0; c < k; c++ {
            if c == min1Idx {
                curr[c] = costs[i][c] + min2
            } else {
                curr[c] = costs[i][c] + min1
            }
        }
        prev = curr
    }

    result := prev[0]
    for _, v := range prev[1:] {
        if v < result {
            result = v
        }
    }
    return result
}
```

O(nk) time. The two-minimum trick is broadly applicable: any time a DP transition says "minimum of all neighbors except self," precompute the global min and second min, and handle the "except self" case by using the second min only when the current index matches the global minimum's index.

---

## How to Recognize This Pattern

You're looking at state machine DP when:

- The problem has a finite set of modes the system can be in at each time step.
- The mode affects what transitions are legal (can't buy while holding, can't buy during cooldown).
- The state isn't just "which index am I at" but also "what status am I currently in."

Watch for: transactions with limits, cooldown periods, consecutive constraints (can't do X twice in a row), color/category constraints on adjacent items.

The number of states is usually small (2-5). If you find yourself needing a large number of states, the state probably encodes too much — look for a simpler reformulation.

---

## Key Takeaway

State machine DP names the modes explicitly and writes one transition equation per valid state change. The stock problems are all the same framework: states represent (holding/not holding) with added constraints (cooldown, transaction count) expanding the state space.

The practical rule: write the state transition diagram first (on paper or mentally). Each arrow is one line of your recurrence. Then implement.

Update states in reverse dependency order to avoid using today's computed values instead of yesterday's.

---

*Previous: [Lesson 28: Interval DP](/post/fundamentals/interview-dp-intervals/) · Next: [Lesson 30: Bitmask DP](/post/fundamentals/interview-dp-bitmask/)*
