---
title: "Lesson 10: Math Patterns — When the Answer Is Math, Not Code"
author: "Atharva Pandey"
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 10
keywords:
  - interviews
  - algorithms
  - leetcode
  - FAANG
  - pattern
  - math
  - number theory
  - fast exponentiation
tags:
  - fundamentals
  - interviews
date: "2024-11-25T00:00:00.000Z"
---

Some interview problems look like they need a data structure or a clever algorithm, but the answer is actually just math. I've watched candidates build hash maps and simulate processes for problems that have elegant O(1) or O(log n) solutions grounded in number theory or geometric reasoning. When I encountered Happy Number for the first time, I tried to detect cycles with a hash set — which works, but the Floyd's cycle detection approach (same one as linked list cycle detection) is what separates a good answer from a great one.

Math-based problems show up in rounds where interviewers want to test whether you can think beyond "write a loop." They're also surprisingly common as warm-ups or phone screen questions — Pow(x, n) appears in almost every Google phone screen I've heard about. This lesson covers three problems with distinct mathematical structures: cycle detection in numerical sequences, fast exponentiation, and geometric reasoning with hash maps.

## The Pattern

Math problems reward recognizing structure in the problem domain rather than simulating. Cycles appear in number sequences just as they appear in linked lists — use the same detection technique. Exponentiation has a divide-and-conquer structure that reduces O(n) multiplications to O(log n). Geometric problems that ask about collinear or coincident points can often be solved by hashing derived properties (slopes, offsets). When simulation is obvious but slow, ask: "Is there a mathematical property I can exploit?"

---

## Problem 1: Happy Number

**The problem.** A happy number is defined by the following process: starting with any positive integer, replace the number with the sum of the squares of its digits. Repeat until the number equals 1 (happy) or it loops endlessly in a cycle (not happy). Return `true` if the number is happy, `false` otherwise.

Input: `19` → `true` (1² + 9² = 82 → 8² + 2² = 68 → ... → 1)
Input: `2` → `false` (cycles: 4 → 16 → 37 → 58 → 89 → 145 → 42 → 20 → 4)

**Brute force.** Simulate and detect cycles with a hash set.

```go
func isHappyBrute(n int) bool {
    seen := make(map[int]bool)
    for n != 1 {
        if seen[n] {
            return false // cycle detected
        }
        seen[n] = true
        n = sumOfSquaredDigits(n)
    }
    return true
}

func sumOfSquaredDigits(n int) int {
    sum := 0
    for n > 0 {
        d := n % 10
        sum += d * d
        n /= 10
    }
    return sum
}
```

Time: O(log n) per step, and the sequence length before cycling is bounded (numbers with up to d digits produce at most a 3-digit result after one step, so the sequence can't grow). Space: O(log n) for the hash set.

**Building toward O(1) space: Floyd's cycle detection.** The sequence of values produced by `sumOfSquaredDigits` is a functional graph — each number has exactly one successor. Either the sequence reaches 1 (terminates) or it enters a cycle. This is structurally identical to a linked list with a potential cycle.

Use Floyd's slow/fast pointer algorithm. The "slow" pointer advances one step at a time. The "fast" pointer advances two steps. If they ever meet at a value other than 1, there's a cycle (the number is not happy). If fast reaches 1, the number is happy.

```go
func isHappy(n int) bool {
    slow := n
    fast := sumOfSquaredDigits(n)

    for fast != 1 && slow != fast {
        slow = sumOfSquaredDigits(slow)
        fast = sumOfSquaredDigits(sumOfSquaredDigits(fast))
    }
    return fast == 1
}

func sumOfSquaredDigits(n int) int {
    sum := 0
    for n > 0 {
        d := n % 10
        sum += d * d
        n /= 10
    }
    return sum
}
```

Time: O(log n). Space: O(1).

**The mathematical insight.** Any non-happy number eventually enters the cycle `4 → 16 → 37 → 58 → 89 → 145 → 42 → 20 → 4`. You could hardcode `89` as the sentinel (if you ever reach 89, it's not happy) and skip Floyd's entirely. But Floyd's is the general approach and shows interviewers you understand cycle detection as a pattern, not just a happy-number hack.

---

## Problem 2: Pow(x, n)

**The problem.** Implement `pow(x, n)`, which calculates x raised to the power n. `n` is an integer (possibly negative). `x` is a floating-point number.

Input: `x = 2.0, n = 10` → `1024.0`
Input: `x = 2.1, n = 3` → `9.261...`
Input: `x = 2.0, n = -2` → `0.25`

**Brute force.** Multiply x by itself n times.

```go
func myPowBrute(x float64, n int) float64 {
    if n < 0 {
        x = 1 / x
        n = -n
    }
    result := 1.0
    for i := 0; i < n; i++ {
        result *= x
    }
    return result
}
```

Time: O(n). For n = 2³¹ - 1, this is over 2 billion multiplications. Unusable.

**Building toward O(log n): Fast Exponentiation (Exponentiation by Squaring).** The recurrence:

- If n is even: `x^n = (x²)^(n/2)` — square x, halve n
- If n is odd: `x^n = x · (x²)^((n-1)/2)` — pull one x out, then halve

Each step halves n, giving O(log n) multiplications.

```go
func myPow(x float64, n int) float64 {
    if n < 0 {
        x = 1 / x
        n = -n
    }
    return fastPow(x, n)
}

func fastPow(x float64, n int) float64 {
    if n == 0 {
        return 1
    }
    if n%2 == 0 {
        half := fastPow(x, n/2)
        return half * half // square the half-power
    }
    return x * fastPow(x, n-1) // pull out one x, recurse on even n-1
}
```

Time: O(log n). Space: O(log n) recursion depth.

**Iterative version** (avoids recursion stack):

```go
func myPowIterative(x float64, n int) float64 {
    if n < 0 {
        x = 1 / x
        n = -n
    }

    result := 1.0
    current := x

    for n > 0 {
        if n%2 == 1 {
            result *= current // if bit is set, multiply into result
        }
        current *= current // square the base
        n /= 2             // shift to next bit
    }
    return result
}
```

**The negative n edge case.** When n is negative, `x^n = (1/x)^(-n)`. In Go, `int` can be negative down to about -2^63. If you naively do `n = -n` for the most negative int, you get integer overflow. A robust implementation either works with `int64` explicitly or uses `uint(n)` after the sign check. For interviews, mention this edge case even if you simplify to `n = -n` in your solution.

**Where fast exponentiation appears in production.** RSA encryption relies on modular exponentiation (`x^n mod m`) computed with this same algorithm. Matrix exponentiation (raising a matrix to the nth power) uses the same recurrence to compute Fibonacci numbers in O(log n) — a classic hard interview problem.

---

## Problem 3: Detect Squares

**The problem.** You are given a stream of points on a 2D plane. Implement a data structure that supports:
- `add(point)`: adds a new point to the data structure
- `count(point)`: given a query point, return the number of axis-aligned squares that can be formed using the query point as one of the four corners.

An axis-aligned square has all sides parallel to the coordinate axes.

**Brute force.** On each `count` query, scan all pairs of stored points and check if they form a valid square with the query point.

```go
type DetectSquaresBrute struct {
    points [][]int
}

func (d *DetectSquaresBrute) Add(point []int) {
    d.points = append(d.points, point)
}

func (d *DetectSquaresBrute) Count(point []int) int {
    count := 0
    px, py := point[0], point[1]
    for i := 0; i < len(d.points); i++ {
        for j := i + 1; j < len(d.points); j++ {
            // check if point, points[i], points[j] form three corners of an axis-aligned square
            ax, ay := d.points[i][0], d.points[i][1]
            bx, by := d.points[j][0], d.points[j][1]
            // ... many conditions
            _ = ax; _ = ay; _ = bx; _ = by; _ = px; _ = py
        }
    }
    return count
}
```

Time: O(n²) per query. Space: O(n). Unacceptable for a stream.

**Building toward optimal.** For an axis-aligned square with one corner at `p = (px, py)`, any diagonal corner `d = (dx, dy)` determines the square uniquely: the other two corners must be `(px, dy)` and `(dx, py)`. The side length is `|dx - px|` and must equal `|dy - py|`.

So: enumerate all points `d` in the structure as potential diagonal corners. For each `d` where `|dx - px| == |dy - py|` and `dx != px` (non-degenerate), check whether both `(px, dy)` and `(dx, py)` exist in the structure. Multiply counts (each combination of those two points forms a distinct square). Use a hash map of point counts for O(1) lookups.

```go
type DetectSquares struct {
    pointCount map[[2]int]int  // point -> how many times added
    xToYs      map[int][]int   // x -> list of distinct y-values at this x
}

func Constructor() DetectSquares {
    return DetectSquares{
        pointCount: make(map[[2]int]int),
        xToYs:      make(map[int][]int),
    }
}

func (d *DetectSquares) Add(point []int) {
    p := [2]int{point[0], point[1]}
    if d.pointCount[p] == 0 {
        d.xToYs[point[0]] = append(d.xToYs[point[0]], point[1])
    }
    d.pointCount[p]++
}

func (d *DetectSquares) Count(point []int) int {
    px, py := point[0], point[1]
    total := 0

    // enumerate all points with the same x as p — these are potential same-column corners
    for _, dy := range d.xToYs[px] {
        if dy == py {
            continue // same point as query, skip
        }
        sideLen := dy - py // signed side length (can be negative)

        // the diagonal corners are at (px + sideLen, py) and (px + sideLen, dy)
        corner1 := [2]int{px + sideLen, py}
        corner2 := [2]int{px + sideLen, dy}

        total += d.pointCount[corner1] * d.pointCount[corner2]
    }
    return total
}
```

Time: `add` is O(1). `count` is O(n) where n is the number of distinct points with the same x-coordinate as the query point — at most O(total points). Space: O(n).

**Why this works.** An axis-aligned square is determined by two opposite diagonal corners. We fix the query point as one corner and enumerate all points in the same column as potential second corners on the same side (not diagonal). This pair `(px, py)` and `(px, dy)` defines a side. The other two corners are forced: `(px + sideLen, py)` and `(px + sideLen, dy)`. We just check if they exist, multiplied by their counts to handle duplicate points.

The `xToYs` map lets us enumerate only points in the same column — a key optimization over scanning all points. The `pointCount` map handles duplicates correctly: if `corner1` appears 3 times and `corner2` appears 2 times, there are `3 * 2 = 6` distinct squares using that diagonal.

---

## How to Recognize This Pattern

- "Is this sequence periodic?" + space constraint → Floyd's cycle detection (two pointer on the sequence)
- "Compute x^n efficiently" → exponentiation by squaring, O(log n)
- "Count geometric shapes with axis-aligned constraints" → fix one element, derive the others, use hash map
- "Sum of digit operations, check for property" → simulate but detect cycles
- "Does this involve repeated multiplication/operations?" → probably divide-and-conquer halving available

Math problems reward asking: "Is there a closed form?" and "Is there a mathematical property of this sequence?" before reaching for simulation.

## Key Takeaway

Mathematical intuition in interviews comes from recognizing structure. Happy Number is a cycle detection problem wearing a number theory disguise — the moment you see "loop endlessly," think Floyd's. Pow(x, n) is divide-and-conquer: halving n at each step is O(log n) regardless of what operation you're doing. Detect Squares is a geometry problem reducible to hash map lookups once you identify the constraints that fix the other corners.

In all three cases, the brute force simulate-everything approach works in small inputs. The insight that makes it elegant is always: "this problem has more structure than it looks — what mathematical property can I exploit?"

---

*[← Lesson 9: Bit Manipulation](/post/fundamentals/interview-bit-manipulation/) | [Course Index](/series/interview-patterns-cracking-faang-in-the-ai-era/) | End of Phase 1 →*

---

**This completes Phase 1: Foundations of the Interview Patterns series.** You now have the core building blocks: hashing, two pointers, sliding window, stacks, binary search, linked lists, recursion, sorting, bit manipulation, and math. The next phase covers trees, graphs, dynamic programming, and the system design patterns that separate L5 from L6 candidates.
