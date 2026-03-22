---
title: "Lesson 3: Binary Search — The most useful algorithm you'll use weekly"
author: "Atharva Pandey"
keywords:
  - binary search
  - sorted data
  - search algorithms
  - lower bound
  - upper bound
  - production engineering
tags:
  - fundamentals
  - algorithms
series: "Algorithms That Matter in Production"
lesson: 3
date: "2024-05-18T00:00:00.000Z"
---

Binary search has a reputation as a simple algorithm — and it is, conceptually. Divide the search space in half, check the middle, repeat. Every programmer knows this. Yet I have seen engineers reach for a linear scan when binary search would have solved the problem in a fraction of the time, and I have also seen subtly buggy binary search implementations that work 99.9% of the time and silently fail on edge cases.

Binary search appears constantly in production code, often disguised. It shows up in database index lookups, in config range lookups, in version comparison logic, and in any scenario where you need to answer "what is the first thing greater than X?" over a sorted collection.

## How It Works

The invariant is simple: the array is sorted. The algorithm maintains a left and right boundary and repeatedly checks the midpoint until it finds the target or exhausts the search space.

```go
// Classic binary search — find exact value, return index or -1
func binarySearch(sorted []int, target int) int {
    lo, hi := 0, len(sorted)-1
    for lo <= hi {
        mid := lo + (hi-lo)/2 // avoid integer overflow; never write (lo+hi)/2
        switch {
        case sorted[mid] == target:
            return mid
        case sorted[mid] < target:
            lo = mid + 1
        default:
            hi = mid - 1
        }
    }
    return -1
}
```

The `lo + (hi-lo)/2` formula is worth memorizing. `(lo + hi) / 2` overflows when both values are large int32 or int64 numbers. This is a real bug found in production Java code at Google (documented publicly in 2006). Go's int is 64-bit on 64-bit systems so overflow is less likely, but the habit is good.

More useful than exact search are the lower-bound and upper-bound variants. Lower bound finds the first index where `sorted[i] >= target`. Upper bound finds the first index where `sorted[i] > target`.

```go
// Lower bound: first index where arr[i] >= target
// Returns len(arr) if all elements are < target
func lowerBound(arr []int, target int) int {
    lo, hi := 0, len(arr)
    for lo < hi {
        mid := lo + (hi-lo)/2
        if arr[mid] < target {
            lo = mid + 1
        } else {
            hi = mid
        }
    }
    return lo
}

// Upper bound: first index where arr[i] > target
// Returns len(arr) if all elements are <= target
func upperBound(arr []int, target int) int {
    lo, hi := 0, len(arr)
    for lo < hi {
        mid := lo + (hi-lo)/2
        if arr[mid] <= target {
            lo = mid + 1
        } else {
            hi = mid
        }
    }
    return lo
}
```

Go's standard library provides `sort.Search(n, f)` which is the generalized form: it finds the smallest index i in [0, n) for which `f(i)` returns true. The function `f` must be monotonically false then true.

## When You Need It

Any time you have a sorted slice and need to find an element, or find the nearest element, or count how many elements fall in a range — binary search is the answer.

Concrete production scenarios:

- **Rate limit tier lookup**: given a user's request count, find which pricing tier applies.
- **Log timestamp search**: given a timestamp, find the first log entry after that time in a sorted log file.
- **Version range checks**: given a semver, find the first compatible config in a sorted list of versioned configs.
- **IP range lookup**: given an IP address (as an integer), find which subnet range it belongs to.

```go
// Rate limit tier lookup — which tier does this request count fall into?
type Tier struct {
    MinRequests int
    Name        string
    PricePerReq float64
}

// tiers must be sorted by MinRequests ascending
var tiers = []Tier{
    {0, "free", 0.0},
    {1000, "starter", 0.001},
    {10000, "pro", 0.0008},
    {100000, "enterprise", 0.0005},
}

func getTier(requestCount int) Tier {
    // Find the last tier whose MinRequests <= requestCount
    i := sort.Search(len(tiers), func(i int) bool {
        return tiers[i].MinRequests > requestCount
    })
    if i == 0 {
        return tiers[0]
    }
    return tiers[i-1]
}
```

## Production Example

A feature flag service I worked on needed to evaluate which variant of an experiment a user should see. The system used a deterministic hash of the user ID to produce a number between 0 and 10,000 (representing a percentage with two decimal places). Variants were defined as cumulative weight ranges: variant A gets 0–3000, variant B gets 3001–7000, variant C gets 7001–10000.

Linear scan over the variants was fine at 3–5 variants. But as the system grew to support 50+ concurrent experiments with many variants each, the linear scan added measurable latency at high call rates (this function was called on every page load).

```go
type Variant struct {
    Name            string
    CumulativeWeight int // upper bound of this variant's range [0, 10000]
}

// variants must be sorted by CumulativeWeight ascending
type Experiment struct {
    Name     string
    Variants []Variant
}

func (e *Experiment) Assign(userHash int) string {
    // userHash is in [0, 10000]
    // Find the first variant whose CumulativeWeight >= userHash
    i := sort.Search(len(e.Variants), func(i int) bool {
        return e.Variants[i].CumulativeWeight >= userHash
    })
    if i >= len(e.Variants) {
        return e.Variants[len(e.Variants)-1].Name
    }
    return e.Variants[i].Name
}

func main() {
    exp := &Experiment{
        Name: "checkout_flow",
        Variants: []Variant{
            {"control", 5000},  // 50%
            {"variant_a", 8000}, // 30%
            {"variant_b", 10000}, // 20%
        },
    }

    // User with hash 6500 gets variant_a
    fmt.Println(exp.Assign(6500)) // variant_a
    // User with hash 2000 gets control
    fmt.Println(exp.Assign(2000)) // control
}
```

The binary search reduces per-assignment work from O(variants) to O(log variants). For 50 variants that is the difference between 50 comparisons and 6. Multiply by millions of page loads per day and the CPU savings are real.

## The Tradeoffs

Binary search only works on sorted data. If your data is unsorted, you pay the sort cost upfront (O(n log n)) and then get O(log n) lookups. This is a good trade if you do many lookups, poor trade if you do one.

It also requires random access. Binary search on a linked list is O(n) because you cannot jump to the midpoint. It works on slices and arrays where index access is O(1).

The off-by-one errors in binary search implementations are notoriously easy to make. The variants (exact search, lower bound, upper bound, search with predicate) each have slightly different loop invariants. Using `sort.Search` from Go's standard library eliminates this class of bug for most production use cases — write the predicate clearly and let the library handle the loop.

One more gotcha: binary search assumes the sorted order does not change during the search. If you are searching a slice that another goroutine is modifying, you need synchronization. I have seen races where a concurrent append caused the slice header to be replaced mid-search, producing reads from freed memory.

## Key Takeaway

Binary search is O(log n) search over sorted data. In practice this means 20 comparisons to search through a million records. Any time you find yourself scanning a sorted collection from the start looking for the first element that satisfies a condition, binary search is the right tool. Learn the lower-bound and upper-bound variants — they are more useful than exact match in most production scenarios.

---

← [Lesson 2: Sorting in Practice](/post/fundamentals/algo-sorting/) | [Course Index](/post/fundamentals/) | Next: [Lesson 4: Two Pointers and Sliding Window](/post/fundamentals/algo-two-pointers/) →
