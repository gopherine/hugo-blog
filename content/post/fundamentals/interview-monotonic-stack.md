---
title: "Lesson 31: Monotonic Stack — The next greater element trick"
author: "Atharva Pandey"
keywords:
  - monotonic stack
  - next greater element
  - daily temperatures
  - largest rectangle histogram
  - trapping rain water
  - interview patterns
tags:
  - fundamentals
  - interviews
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 31
date: "2025-10-30T00:00:00.000Z"
---

I used to brute-force "next greater element" problems. Nested loops, O(n²), and a silent prayer that the input was small. Then a senior engineer at a Google mock interview drew me a picture of a stack where elements got popped the moment something bigger walked in, and the pattern clicked instantly. The monotonic stack is one of those techniques that, once you see it, you wonder how you ever missed it.

The core idea: maintain a stack whose values are always increasing (or always decreasing) from bottom to top. Every time that invariant would be violated, you pop — and those pops are exactly the moments you can answer questions about relationships between elements.

## The Pattern

A **monotonic stack** is a stack that is kept in monotonically increasing or decreasing order. When you push a new element that would break the ordering, you pop elements until the ordering is restored. The critical insight is that each element is pushed once and popped once, giving you O(n) total across all pops regardless of the input.

Two flavors:
- **Monotonically decreasing stack**: useful for "next greater element" — pop when current element is larger than the top.
- **Monotonically increasing stack**: useful for "next smaller element" — pop when current element is smaller than the top.

The classic skeleton in Go:

```go
func nextGreaterElements(nums []int) []int {
    n := len(nums)
    result := make([]int, n)
    for i := range result {
        result[i] = -1
    }
    stack := []int{} // stores indices

    for i := 0; i < n; i++ {
        // pop while current element is greater than stack top
        for len(stack) > 0 && nums[i] > nums[stack[len(stack)-1]] {
            idx := stack[len(stack)-1]
            stack = stack[:len(stack)-1]
            result[idx] = nums[i]
        }
        stack = append(stack, i)
    }
    return result
}
```

Store indices, not values. You almost always need the index to write the result.

## Problem 1: Daily Temperatures

Given an array `temperatures`, return an array `answer` where `answer[i]` is the number of days you have to wait after day `i` to get a warmer temperature. If no future day is warmer, put `0`.

**Pattern recognition**: "how many days until something warmer" is exactly "next greater element" with a distance twist.

```go
func dailyTemperatures(temperatures []int) []int {
    n := len(temperatures)
    answer := make([]int, n)
    stack := []int{} // indices of days with unresolved next-warmer

    for i := 0; i < n; i++ {
        for len(stack) > 0 && temperatures[i] > temperatures[stack[len(stack)-1]] {
            prev := stack[len(stack)-1]
            stack = stack[:len(stack)-1]
            answer[prev] = i - prev // distance
        }
        stack = append(stack, i)
    }
    // elements remaining in stack have no warmer day; answer[i] is already 0
    return answer
}
```

Time: O(n). Space: O(n). Each index is pushed and popped exactly once.

The key move: when you pop index `prev` because `temperatures[i]` is larger, the answer for `prev` is `i - prev`. The stack naturally groups "pending" days waiting for resolution.

## Problem 2: Largest Rectangle in Histogram

Given an array `heights` representing a histogram where each bar has width 1, find the area of the largest rectangle.

This is the classic hard problem that trips people up. The trick: for each bar, the largest rectangle that uses that bar's full height extends left and right until it hits a shorter bar. A monotonically increasing stack tells you exactly where those boundaries are.

```go
func largestRectangleArea(heights []int) int {
    // sentinel 0 at end triggers cleanup of all remaining bars
    heights = append(heights, 0)
    stack := []int{} // indices, maintaining increasing heights
    maxArea := 0

    for i := 0; i < len(heights); i++ {
        for len(stack) > 0 && heights[i] < heights[stack[len(stack)-1]] {
            h := heights[stack[len(stack)-1]]
            stack = stack[:len(stack)-1]

            width := i
            if len(stack) > 0 {
                width = i - stack[len(stack)-1] - 1
            }
            area := h * width
            if area > maxArea {
                maxArea = area
            }
        }
        stack = append(stack, i)
    }
    return maxArea
}
```

Time: O(n). The sentinel `0` appended at the end ensures all bars still in the stack get processed.

When you pop index `j` with height `h`, the rectangle extends from `stack[top]+1` to `i-1`. If the stack is empty, it extends all the way to index `0`. Take a moment to trace this with `[2, 1, 5, 6, 2, 3]` — watch how the `1` pops everything taller to its left.

## Problem 3: Trapping Rain Water

Given `height`, an array of non-negative integers representing an elevation map with bar width 1, compute how much water it can trap after raining.

There are multiple approaches (two pointers, prefix max arrays), but the monotonic stack approach is the most instructive for interview day because it follows the exact same template.

```go
func trap(height []int) int {
    stack := []int{} // indices, maintaining decreasing heights
    water := 0

    for i := 0; i < len(height); i++ {
        for len(stack) > 0 && height[i] > height[stack[len(stack)-1]] {
            bottom := stack[len(stack)-1]
            stack = stack[:len(stack)-1]

            if len(stack) == 0 {
                break
            }

            left := stack[len(stack)-1]
            boundedHeight := min(height[left], height[i]) - height[bottom]
            width := i - left - 1
            water += boundedHeight * width
        }
        stack = append(stack, i)
    }
    return water
}

func min(a, b int) int {
    if a < b {
        return a
    }
    return b
}
```

Time: O(n). When we pop `bottom`, the water trapped in that horizontal layer is bounded by the shorter of the two walls (`height[left]` and `height[i]`) minus the bottom floor (`height[bottom]`), times the width.

The difference from the histogram problem: here you want a decreasing stack (looking for a taller bar on the right to form the right wall), and you compute horizontal water layers, not vertical rectangles.

## How to Recognize This Pattern

Ask yourself these questions:

1. **Does the problem ask about the "next" or "previous" element that is larger/smaller?** Monotonic stack is almost certainly the answer.
2. **Are you computing areas, distances, or trapped values that depend on the relationship between an element and its nearest smaller/larger neighbor?** Stack.
3. **Do you find yourself writing O(n²) nested loops to compare each element against all others?** There is probably a stack solution.
4. **Does the problem involve histograms, skylines, or elevation maps?** Stack.

The structural signal in code: you need to relate element `i` to the most recent element that satisfies some ordering constraint. That "most recent" is exactly what a stack gives you.

At interview time, the moment I hear "next greater" or "nearest smaller," I draw the stack template immediately. The two variants — popping when current is larger (decreasing stack) versus popping when current is smaller (increasing stack) — cover nearly everything.

One trap to avoid: make sure you store indices, not values. You almost always need to compute distances or widths, which requires knowing where the elements live.

## Key Takeaway

The monotonic stack turns O(n²) neighbor-comparison problems into O(n) by making a simple contract: each element is pushed once and popped exactly once. The pop event is the answer event — it is the moment you know the "next greater" or "previous smaller" for that element.

Learn the skeleton cold. Practice it on Daily Temperatures first because the logic is cleanest there. Then apply it to Trapping Rain Water and the Histogram problem, which require slightly more book-keeping around widths and heights but follow the same popping logic.

When you see a problem where elements are waiting for something to "resolve" them — a future element that is bigger, smaller, or satisfies some condition — think stack.

---

**Series**: [Interview Patterns: Cracking FAANG+ in the AI Era](/series/interview-patterns-cracking-faang-in-the-ai-era/)

**Previous**: [Lesson 30](/post/fundamentals/interview-sliding-window/) — Sliding Window

**Next**: [Lesson 32: Heap Patterns](/post/fundamentals/interview-heap/) — Keep the top K without sorting everything
