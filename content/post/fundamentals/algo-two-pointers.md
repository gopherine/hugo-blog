---
title: "Lesson 4: Two Pointers and Sliding Window — Stream processing in disguise"
author: "Atharva Pandey"
keywords:
  - two pointers
  - sliding window
  - stream processing
  - rate limiting
  - time series
  - production engineering
tags:
  - fundamentals
  - algorithms
series: "Algorithms That Matter in Production"
lesson: 4
date: "2024-06-01T00:00:00.000Z"
---

The first time I recognized the sliding window pattern outside a textbook, I was reading the source code for a rate limiter. There was no comment saying "sliding window algorithm here." There was just a loop, two indices into a circular buffer, and some simple arithmetic that maintained a count of events in the last N seconds. Once I saw it, I started seeing it everywhere — in network flow control, in moving average calculations, in deduplication logic.

Two pointers and sliding window are not exotic techniques. They are the pattern you reach for when you need to answer questions about a contiguous subsequence of data without recomputing from scratch on every step.

## How It Works

**Two pointers** involves maintaining two indices into a sorted collection and moving them toward each other (or in the same direction) based on a condition. The classic use case is finding pairs that sum to a target.

```go
// Two sum on sorted array — O(n) instead of O(n²)
func twoSum(sorted []int, target int) (int, int, bool) {
    lo, hi := 0, len(sorted)-1
    for lo < hi {
        sum := sorted[lo] + sorted[hi]
        switch {
        case sum == target:
            return sorted[lo], sorted[hi], true
        case sum < target:
            lo++ // need larger sum, move left pointer right
        default:
            hi-- // need smaller sum, move right pointer left
        }
    }
    return 0, 0, false
}
```

**Sliding window** maintains a window (contiguous subarray) of variable or fixed size and moves it forward, adding elements on the right and removing them on the left. The key insight: instead of recomputing the window's property from scratch each step, you update it incrementally.

```go
// Maximum sum of any subarray of length k — O(n) with sliding window
// Naive approach would be O(n*k)
func maxSumWindow(arr []int, k int) int {
    if len(arr) < k {
        return 0
    }
    // Build the first window
    windowSum := 0
    for i := 0; i < k; i++ {
        windowSum += arr[i]
    }
    maxSum := windowSum
    // Slide: add the new element, remove the old one
    for i := k; i < len(arr); i++ {
        windowSum += arr[i] - arr[i-k]
        if windowSum > maxSum {
            maxSum = windowSum
        }
    }
    return maxSum
}
```

## When You Need It

The signal that sliding window applies is: "I need to know something about a fixed-size or variable-size window of recent elements." Common production scenarios:

- **Rate limiting**: how many requests has this client made in the last 60 seconds?
- **Moving averages**: what is the average CPU usage over the last 5 minutes?
- **Deduplication**: have I seen this event within the last N events?
- **Anomaly detection**: does any 5-minute window in today's data have more than X errors?
- **Longest subarray satisfying a condition**: find the longest span of time where all requests succeeded.

The variable-size sliding window grows when adding an element still satisfies the constraint, and shrinks from the left when it does not.

```go
// Longest subarray with sum <= budget — variable sliding window
func longestAffordableRun(prices []int, budget int) int {
    lo := 0
    windowSum := 0
    best := 0
    for hi := 0; hi < len(prices); hi++ {
        windowSum += prices[hi]
        // Shrink from the left until we are back within budget
        for windowSum > budget && lo <= hi {
            windowSum -= prices[lo]
            lo++
        }
        if hi-lo+1 > best {
            best = hi - lo + 1
        }
    }
    return best
}
```

## Production Example

The most direct production application is a **sliding window rate limiter**. The token bucket and fixed window approaches are simpler to implement, but the sliding window gives smoother enforcement — it prevents bursts at window boundaries that fixed windows allow.

Here is a production-grade sliding window rate limiter in Go using a circular buffer to avoid allocations on the hot path:

```go
package ratelimit

import (
    "sync"
    "time"
)

// SlidingWindowLimiter tracks requests within a rolling time window.
// Thread-safe for concurrent use.
type SlidingWindowLimiter struct {
    mu       sync.Mutex
    window   time.Duration
    limit    int
    requests []time.Time // circular buffer
    head     int         // index of oldest entry
    count    int         // number of valid entries
}

func NewSlidingWindowLimiter(limit int, window time.Duration) *SlidingWindowLimiter {
    return &SlidingWindowLimiter{
        window:   window,
        limit:    limit,
        requests: make([]time.Time, limit+1), // +1 to distinguish full from empty
    }
}

// Allow returns true if the request is within the rate limit.
func (l *SlidingWindowLimiter) Allow() bool {
    l.mu.Lock()
    defer l.mu.Unlock()

    now := time.Now()
    cutoff := now.Add(-l.window)

    // Evict timestamps older than the window (advance head pointer)
    for l.count > 0 && l.requests[l.head].Before(cutoff) {
        l.head = (l.head + 1) % len(l.requests)
        l.count--
    }

    if l.count >= l.limit {
        return false
    }

    // Record this request at the tail
    tail := (l.head + l.count) % len(l.requests)
    l.requests[tail] = now
    l.count++
    return true
}

// Remaining returns how many more requests are allowed right now.
func (l *SlidingWindowLimiter) Remaining() int {
    l.mu.Lock()
    defer l.mu.Unlock()
    return l.limit - l.count
}
```

The head pointer advances (evicting old entries) and the count tracks how many valid entries are in the window. No reallocation, no GC pressure on the hot path, O(1) amortized per request.

A moving-average monitor follows the same shape — a fixed-size circular buffer of samples, a sum maintained incrementally:

```go
type MovingAverage struct {
    mu     sync.Mutex
    buf    []float64
    pos    int
    count  int
    sum    float64
}

func NewMovingAverage(size int) *MovingAverage {
    return &MovingAverage{buf: make([]float64, size)}
}

func (m *MovingAverage) Add(v float64) float64 {
    m.mu.Lock()
    defer m.mu.Unlock()
    if m.count == len(m.buf) {
        m.sum -= m.buf[m.pos] // remove the oldest
    } else {
        m.count++
    }
    m.buf[m.pos] = v
    m.sum += v
    m.pos = (m.pos + 1) % len(m.buf)
    return m.sum / float64(m.count)
}
```

## The Tradeoffs

The circular-buffer approach avoids allocation but requires pre-sizing the buffer. If your rate limit is 100,000 requests per minute, you are allocating a slice of 100,000 timestamps. That is about 800KB per limiter instance per client if each `time.Time` is 24 bytes. For per-user rate limiters with millions of users, this requires a different storage backend (Redis with a sorted set is the common choice).

The two-pointer technique requires sorted data or a specific monotonic property to work correctly. Applying it to unsorted data produces wrong answers, not panics — which makes the bug hard to catch.

Sliding window logic also assumes that the "window" is contiguous in time or index space. If events arrive out of order, you need to sort them first or use a different data structure (a sorted set or priority queue) for the window.

## Key Takeaway

Two pointers and sliding window are the same insight applied in two directions: instead of restarting computation on every step, maintain state incrementally as the window moves. In production, this pattern powers rate limiters, moving averages, anomaly detectors, and any system that needs to answer questions about "the last N seconds" or "the last N events" efficiently. If you find yourself summing or scanning a subarray in a loop, check whether a sliding window can reduce the complexity.

---

← [Lesson 3: Binary Search](/post/fundamentals/algo-binary-search/) | [Course Index](/post/fundamentals/) | Next: [Lesson 5: Hashing and Consistent Hashing](/post/fundamentals/algo-hashing/) →
