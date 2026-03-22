---
title: "Lesson 9: Greedy Algorithms — When being selfish is optimal"
author: "Atharva Pandey"
keywords:
  - greedy algorithms
  - interval scheduling
  - huffman coding
  - job scheduling
  - optimization
  - production engineering
tags:
  - fundamentals
  - algorithms
series: "Algorithms That Matter in Production"
lesson: 9
date: "2024-08-17T00:00:00.000Z"
---

Greedy algorithms have a simple idea at their core: at each step, make the locally optimal choice. No looking ahead, no considering alternatives, no backtracking. Just take the best available option right now and trust that it leads to a globally optimal result.

The catch is that this only works for specific problem structures. Use greedy when the problem has the **greedy choice property** — the locally optimal choice is always part of a globally optimal solution. When this holds, greedy is elegant and fast. When it does not, greedy gives you a wrong answer with no warning.

I use greedy thinking regularly in production code — for scheduling tasks, compressing logs, assigning work to workers, and packing data into batches. Knowing when greedy is provably correct versus when it just seems correct is the skill.

## How It Works

The greedy template is always the same:

1. Sort or prioritize the options by some criterion
2. Iterate, greedily selecting the best available option at each step
3. Return the accumulated result

The hardest part is choosing the right criterion for step 1. The wrong criterion produces wrong answers. The right one produces provably optimal results.

```go
// Interval scheduling maximization
// Given a set of tasks with start and end times, schedule the maximum number
// of non-overlapping tasks.
// Greedy criterion: always pick the task that ends earliest.
type Task struct {
    ID    string
    Start int
    End   int
}

func scheduleMaxTasks(tasks []Task) []Task {
    // Sort by end time — the greedy criterion
    sort.Slice(tasks, func(i, j int) bool {
        return tasks[i].End < tasks[j].End
    })

    var scheduled []Task
    lastEnd := math.MinInt64

    for _, task := range tasks {
        if task.Start >= lastEnd {
            scheduled = append(scheduled, task)
            lastEnd = task.End
        }
    }
    return scheduled
}
```

Why does sorting by end time work? Because any task that ends later gives fewer opportunities for subsequent tasks. By always choosing the earliest-ending task, we leave maximum room for future tasks. This is provably optimal — it is one of the cleanest examples of the greedy choice property.

Sorting by duration (shortest first) does not work. Sorting by start time does not work. Ending time is the only criterion that is provably optimal.

## When You Need It

Greedy works for:

- **Interval scheduling**: maximizing concurrent task execution
- **Fractional knapsack**: pack items by value/weight ratio (not the 0-1 variant — that requires DP)
- **Minimum spanning tree**: Kruskal's and Prim's are greedy
- **Huffman coding**: building optimal prefix-free codes (used in gzip and zstd)
- **Coin change with standard denominations**: for standard currency (1, 5, 10, 25 cents), always use the largest coin ≤ remaining amount

It does not work for:
- **0-1 Knapsack** (use DP)
- **Coin change with arbitrary denominations** (use DP)
- **Shortest path with negative weights** (use Bellman-Ford)

```go
// Fractional knapsack — greedy works here because items are divisible
type Item struct {
    Name   string
    Value  float64
    Weight float64
}

func fractionalKnapsack(items []Item, capacity float64) float64 {
    // Sort by value/weight ratio descending — take the most valuable per unit first
    sort.Slice(items, func(i, j int) bool {
        return items[i].Value/items[i].Weight > items[j].Value/items[j].Weight
    })

    totalValue := 0.0
    remaining := capacity
    for _, item := range items {
        if remaining <= 0 {
            break
        }
        take := math.Min(item.Weight, remaining)
        totalValue += take * (item.Value / item.Weight)
        remaining -= take
    }
    return totalValue
}
```

## Production Example

A batch processing system needed to assign incoming jobs to worker slots. Each job had an estimated duration. Workers had a maximum shift length. The goal was to pack as many jobs as possible into shifts without splitting jobs across shifts and without exceeding shift length.

This is the bin-packing problem — NP-hard in general. But with a greedy approximation (first-fit decreasing), it gets close to optimal in practice and runs in O(n log n).

```go
type Job struct {
    ID       string
    Duration int // minutes
}

type Shift struct {
    MaxDuration int
    Jobs        []Job
    UsedMinutes int
}

// First Fit Decreasing bin packing — greedy approximation
// Pack largest jobs first; each job goes into the first shift that fits
func packJobsIntoShifts(jobs []Job, shiftLength int) []Shift {
    // Sort jobs by duration descending — biggest first
    sort.Slice(jobs, func(i, j int) bool {
        return jobs[i].Duration > jobs[j].Duration
    })

    var shifts []Shift

    for _, job := range jobs {
        placed := false
        // Try to fit into an existing shift
        for i := range shifts {
            if shifts[i].UsedMinutes+job.Duration <= shifts[i].MaxDuration {
                shifts[i].Jobs = append(shifts[i].Jobs, job)
                shifts[i].UsedMinutes += job.Duration
                placed = true
                break
            }
        }
        // No existing shift fits; open a new one
        if !placed {
            shifts = append(shifts, Shift{
                MaxDuration: shiftLength,
                Jobs:        []Job{job},
                UsedMinutes: job.Duration,
            })
        }
    }
    return shifts
}

// Utilization report
func reportUtilization(shifts []Shift) {
    for i, s := range shifts {
        util := float64(s.UsedMinutes) / float64(s.MaxDuration) * 100
        fmt.Printf("Shift %d: %d/%d min (%.1f%%), %d jobs\n",
            i+1, s.UsedMinutes, s.MaxDuration, util, len(s.Jobs))
    }
}
```

FFD (First Fit Decreasing) is guaranteed to use no more than 11/9 * OPT + 6/9 shifts (OPT being the theoretical minimum). In practice, on realistic job size distributions, it typically achieves 95%+ utilization. The alternative — finding the true optimum — requires exponential time.

Another greedy application I use regularly is Huffman coding for log compression. The algorithm builds a prefix-free binary code that minimizes total encoded length given character frequencies.

```go
// Simplified Huffman code length estimation
// (Full implementation would build the tree and produce codes)
type FreqNode struct {
    Symbol rune
    Freq   float64
    Left   *FreqNode
    Right  *FreqNode
}

type FreqHeap []*FreqNode

func (h FreqHeap) Len() int            { return len(h) }
func (h FreqHeap) Less(i, j int) bool  { return h[i].Freq < h[j].Freq }
func (h FreqHeap) Swap(i, j int)       { h[i], h[j] = h[j], h[i] }
func (h *FreqHeap) Push(x any)         { *h = append(*h, x.(*FreqNode)) }
func (h *FreqHeap) Pop() any           { old := *h; n := len(old); x := old[n-1]; *h = old[:n-1]; return x }

func buildHuffmanTree(freqs map[rune]float64) *FreqNode {
    h := &FreqHeap{}
    for sym, f := range freqs {
        heap.Push(h, &FreqNode{Symbol: sym, Freq: f})
    }
    heap.Init(h)
    for h.Len() > 1 {
        left := heap.Pop(h).(*FreqNode)
        right := heap.Pop(h).(*FreqNode)
        heap.Push(h, &FreqNode{
            Freq:  left.Freq + right.Freq,
            Left:  left,
            Right: right,
        })
    }
    return heap.Pop(h).(*FreqNode)
}
```

The greedy criterion: always merge the two least-frequent symbols. This produces the optimal prefix-free code. gzip, zstd, and deflate all use variants of Huffman coding as their entropy encoding stage.

## The Tradeoffs

The biggest danger with greedy is applying it to problems where it is not provably correct. The coin change problem is the canonical trap: greedy works for US coins (25, 10, 5, 1 cents) but fails for denominations like (1, 3, 4) cents where making change for 6 cents greedily gives 4+1+1 (3 coins) instead of the optimal 3+3 (2 coins).

Always ask: is there a proof that the greedy choice is always part of an optimal solution? For interval scheduling by end time — yes, the exchange argument proves it. For arbitrary scheduling problems — usually no.

Greedy approximation algorithms are a legitimate tool for NP-hard problems. When exact optimization is intractable, a greedy approximation that is within a known factor of optimal is often good enough. FFD bin packing, nearest-neighbor TSP heuristic, and greedy set cover all fall into this category. The key is knowing the approximation ratio.

## Key Takeaway

Greedy algorithms make locally optimal choices at each step and work when those choices are provably part of a globally optimal solution. In production, greedy powers scheduling, bin packing, and compression. The skill is not just implementing the algorithm — it is recognizing which problems have the greedy choice property and which ones do not. When in doubt, test greedy against known-optimal cases for your specific problem before relying on it in production.

---

← [Lesson 8: Dynamic Programming Intuition](/post/fundamentals/algo-dynamic-programming/) | [Course Index](/post/fundamentals/) | Next: [Lesson 10: Backtracking](/post/fundamentals/algo-backtracking/) →
