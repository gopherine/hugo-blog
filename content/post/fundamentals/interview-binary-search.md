---
title: "Lesson 5: Binary Search — When the Search Space Is Sorted or Monotonic"
author: "Atharva Pandey"
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 5
keywords:
  - interviews
  - algorithms
  - leetcode
  - FAANG
  - pattern
  - binary search
  - rotated array
  - monotonic
tags:
  - fundamentals
  - interviews
date: "2024-05-09T00:00:00.000Z"
---

Binary search has a reputation as the thing you learned in your first CS course and never thought about deeply again. Then you encounter a rotated array or a capacity-minimization problem in an interview, and suddenly it is not obvious at all. I've seen engineers who could recite the textbook implementation fail completely on problems that are, at their core, binary search — because the search space isn't an array of values, it's something more abstract.

This lesson is about recognizing binary search in disguise. The classic form searches for a value in a sorted array. The interview form searches for a threshold in any monotonic space — which turns out to be a vast generalization. Google and Meta love the rotated array variant. Amazon and Uber love the capacity/feasibility variant (Koko Eating Bananas is a famous example). Both are binary search.

## The Pattern

Binary search works on any space where you can answer the question: "Is the answer at least X?" with a yes/no, and where the answers are monotonic — all the yes answers are on one side, all the no answers are on the other. Find the boundary. You need three things: a `left` and `right` bound, a `mid` calculation that avoids overflow, and a condition that tells you which half to discard.

---

## Problem 1: Search in Rotated Sorted Array

**The problem.** An integer array was sorted in ascending order and then rotated at some unknown pivot. Given the array and a target, return the index of the target or -1 if it is not present.

Input: `nums = [4,5,6,7,0,1,2]`, `target = 0` → `4`
Input: `nums = [4,5,6,7,0,1,2]`, `target = 3` → `-1`

**Brute force.** Linear scan.

```go
func searchBrute(nums []int, target int) int {
    for i, n := range nums {
        if n == target {
            return i
        }
    }
    return -1
}
```

Time: O(n). Space: O(1). Works, but doesn't use the sorted structure at all.

**Building toward optimal.** Despite the rotation, each half of the array (split at `mid`) is always sorted. That's the key observation. When you split at `mid`, either `[left, mid]` is sorted or `[mid, right]` is sorted — at least one of them must be. Determine which half is sorted, check if the target falls within that sorted half's range, and if so, search there. Otherwise, search the other half.

```go
func search(nums []int, target int) int {
    left, right := 0, len(nums)-1

    for left <= right {
        mid := left + (right-left)/2

        if nums[mid] == target {
            return mid
        }

        // determine which half is sorted
        if nums[left] <= nums[mid] {
            // left half [left..mid] is sorted
            if nums[left] <= target && target < nums[mid] {
                right = mid - 1 // target is in the sorted left half
            } else {
                left = mid + 1 // target must be in the right half
            }
        } else {
            // right half [mid..right] is sorted
            if nums[mid] < target && target <= nums[right] {
                left = mid + 1 // target is in the sorted right half
            } else {
                right = mid - 1 // target must be in the left half
            }
        }
    }
    return -1
}
```

Time: O(log n). Space: O(1).

**The invariant that makes this work.** `nums[left] <= nums[mid]` tells you the left half has no rotation discontinuity — the values increase monotonically from `left` to `mid`. The right half may or may not contain the pivot. By checking whether `target` falls in the range of the sorted half, you make a definitive decision about which half to pursue. You never discard a half without being certain the target isn't in it.

---

## Problem 2: Find Minimum in Rotated Sorted Array

**The problem.** Given a sorted rotated array of unique elements, find the minimum element.

Input: `[3,4,5,1,2]` → `1`
Input: `[4,5,6,7,0,1,2]` → `0`
Input: `[11,13,15,17]` → `11` (not rotated)

**Brute force.** Linear scan for minimum.

```go
func findMinBrute(nums []int) int {
    m := nums[0]
    for _, n := range nums {
        if n < m {
            m = n
        }
    }
    return m
}
```

Time: O(n). Again, ignores structure.

**Building toward optimal.** The minimum element is at the rotation point — the one place where a value is smaller than its predecessor. Binary search can find this. At any `mid`, if `nums[mid] > nums[right]`, the minimum is in the right half (the rotation is there). Otherwise, the minimum is in the left half including `mid` itself.

```go
func findMin(nums []int) int {
    left, right := 0, len(nums)-1

    for left < right {
        mid := left + (right-left)/2

        if nums[mid] > nums[right] {
            // rotation point (minimum) is in the right half
            left = mid + 1
        } else {
            // minimum is in the left half, possibly at mid
            right = mid
        }
    }
    return nums[left]
}
```

Time: O(log n). Space: O(1).

**Why `right = mid` and not `right = mid - 1`.** When `nums[mid] <= nums[right]`, `mid` could itself be the minimum. We can't exclude it, so we keep it by setting `right = mid`. The loop invariant is: the minimum is always in `[left, right]`. The loop terminates when `left == right`, and that element is the answer. Getting this boundary condition right — when to use `<=` vs `<`, when to subtract 1 and when not to — is where most binary search bugs live.

**How it builds on Problem 1.** Problem 1 needed to identify which half was sorted in order to chase the target. Problem 2 identifies which half contains the discontinuity (the rotation point) in order to chase the minimum. The structure is identical — you're always comparing `nums[mid]` to a boundary value and deciding which half to eliminate.

---

## Problem 3: Koko Eating Bananas

**The problem.** Koko has `piles` of bananas. She can eat at most `k` bananas per hour. The guards will be back in `h` hours. Find the minimum integer eating speed `k` such that she can eat all bananas in `h` hours.

Input: `piles = [3,6,7,11]`, `h = 8` → `4`

If `k = 4`: pile 3 needs 1h, pile 6 needs 2h, pile 7 needs 2h, pile 11 needs 3h. Total: 8h. That's exactly `h`, so 4 works. Can 3 work? pile 11 alone needs 4h, rest need 5h total = 9h > 8. So 3 doesn't work. Minimum is 4.

**Brute force.** Try every speed from 1 to max(piles).

```go
func minEatingSpeedBrute(piles []int, h int) int {
    maxPile := 0
    for _, p := range piles {
        if p > maxPile {
            maxPile = p
        }
    }

    for k := 1; k <= maxPile; k++ {
        if canFinish(piles, k, h) {
            return k
        }
    }
    return maxPile
}

func canFinish(piles []int, k, h int) bool {
    hours := 0
    for _, p := range piles {
        hours += (p + k - 1) / k // ceiling division
    }
    return hours <= h
}
```

Time: O(max(piles) · n). If piles go up to 10⁹, this is completely infeasible.

**Building toward optimal.** The speed `k` ranges from 1 to `max(piles)`. For small k, Koko can't finish in time. For large k (at least max(piles)), she always can. The feasibility function is monotonic: if speed `k` works, then `k+1` also works. If `k` doesn't work, neither does `k-1`. This monotonic structure is exactly what binary search requires.

Binary search over the speed, not over the array. The search space is `[1, max(piles)]`. At each midpoint, ask: "Can Koko finish at speed `mid`?" If yes, we might be able to do better — try slower (search left). If no, we need to go faster (search right). We want the leftmost speed that works.

```go
func minEatingSpeed(piles []int, h int) int {
    left, right := 1, 0
    for _, p := range piles {
        if p > right {
            right = p
        }
    }

    for left < right {
        mid := left + (right-left)/2
        if canFinish(piles, mid, h) {
            right = mid // mid works, but maybe something smaller works too
        } else {
            left = mid + 1 // mid doesn't work, need at least mid+1
        }
    }
    return left
}

func canFinish(piles []int, k, h int) bool {
    total := 0
    for _, p := range piles {
        total += (p + k - 1) / k
    }
    return total <= h
}
```

Time: O(n · log(max(piles))). Space: O(1).

**This is binary search on the answer.** The search space is not the input array — it's the range of possible answers. Whenever you need to minimize a value subject to a feasibility condition, and feasibility is monotonic in that value, you can binary search on it. Same pattern: ship capacity problems, painter's partition, maximum books per shelf. Recognize the shape: "minimum X such that condition(X) is true and condition is monotonically true-then-false or false-then-true."

---

## How to Recognize This Pattern

- Array is sorted or rotated → binary search on the array
- "Find the minimum/maximum value that satisfies condition C" where C is monotonic → binary search on the answer space
- Brute force tries every value in a range → binary search over that range
- "Can we do X in Y time/cost/capacity?" → likely a feasibility function ready for binary search
- O(n) scan of a sorted or monotonic space → O(log n) is possible

The key question to ask yourself: "If I could magically answer the feasibility question, is there a sorted or monotonic structure I could binary search over?" If yes, binary search applies.

## Key Takeaway

Binary search is not about sorted arrays. It's about eliminating half the search space at each step. Sorted arrays let you do this because you can determine which half contains the target. Monotonic feasibility functions let you do this because you can determine which half contains the threshold.

Once you internalize "binary search on the answer," a huge class of optimization problems becomes tractable. Whenever you are minimizing or maximizing a value and the feasibility is monotonic, you're looking at O(log n) regardless of whether there's an array involved at all.

---

*[← Lesson 4: Stack](/post/fundamentals/interview-stack/) | [Course Index](/series/interview-patterns-cracking-faang-in-the-ai-era/) | [Lesson 6: Linked Lists →](/post/fundamentals/interview-linked-lists/)*
