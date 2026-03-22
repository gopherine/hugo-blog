---
title: "Lesson 3: Sliding Window — Fixed or Variable, the Window Always Moves Right"
author: "Atharva Pandey"
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 3
keywords:
  - interviews
  - algorithms
  - leetcode
  - FAANG
  - pattern
  - sliding window
  - subarray
  - substring
tags:
  - fundamentals
  - interviews
date: "2024-03-28T00:00:00.000Z"
---

Sliding window problems have a tell: the problem asks about a contiguous subarray or substring, and there's a constraint that makes the brute force obvious but slow. When I was interviewing at a mid-size fintech that fancied itself FAANG-adjacent, I got a stock price problem in my first round. I almost panicked — "is this dynamic programming?" It wasn't. It was a sliding window in disguise, and once I saw it, the solution wrote itself in about four minutes.

The pattern shows up everywhere: substring problems at Google and Meta, subarray maximums at Amazon, window-based constraints at almost every company doing systems-adjacent roles. If you can write a clean sliding window from scratch, you're ahead of most candidates.

## The Pattern

Maintain a window defined by two indices, `left` and `right`, over a sequence. Always expand the window by moving `right`. Shrink it by moving `left` when the window violates a constraint. Because both pointers only ever move rightward, the total work is O(n) — each element enters and exits the window at most once. Fixed-size windows move `left` in lockstep with `right`. Variable-size windows move `left` only when needed.

---

## Problem 1: Best Time to Buy and Sell Stock

**The problem.** You are given an array `prices` where `prices[i]` is the price of a stock on day `i`. Return the maximum profit you can achieve from one buy and one sell. If no profit is possible, return 0.

Input: `[7, 1, 5, 3, 6, 4]`
Output: `5` (buy on day 2 at price 1, sell on day 5 at price 6)

**Brute force.** Try every buy-sell pair.

```go
func maxProfitBrute(prices []int) int {
    best := 0
    for buy := 0; buy < len(prices); buy++ {
        for sell := buy + 1; sell < len(prices); sell++ {
            if profit := prices[sell] - prices[buy]; profit > best {
                best = profit
            }
        }
    }
    return best
}
```

Time: O(n²). Space: O(1). Fine for small inputs, unusable at scale.

**Building toward optimal.** You can only profit if you sell after you buy. So as you move through the prices left to right, you want to know: "what's the cheapest price I've seen so far?" That's your potential buy point. The current price is your potential sell point. Track both in a single pass.

```go
func maxProfit(prices []int) int {
    if len(prices) == 0 {
        return 0
    }

    minPrice := prices[0]
    maxProfitVal := 0

    for _, price := range prices {
        if price < minPrice {
            minPrice = price // new cheaper buy point found
        } else if profit := price - minPrice; profit > maxProfitVal {
            maxProfitVal = profit // new best profit found
        }
    }
    return maxProfitVal
}
```

Time: O(n). Space: O(1).

**The sliding window framing.** Think of `minPrice` as the `left` pointer (the buy day) and the current iteration as the `right` pointer (the sell day). Whenever you find a cheaper price, you slide `left` forward to that position — you'd never buy on a more expensive day before a cheaper one. The window is always `[minPrice_day, current_day]` and it only grows or resets to a cheaper start. This is the simplest form of the pattern: a variable window where `left` jumps forward to the best known position.

---

## Problem 2: Longest Substring Without Repeating Characters

**The problem.** Given a string `s`, return the length of the longest substring that contains no repeating characters.

Input: `"abcabcbb"` → `3` (the substring `"abc"`)
Input: `"pwwkew"` → `3` (the substring `"wke"`)

**Brute force.** Check every substring.

```go
func lengthOfLongestSubstringBrute(s string) int {
    best := 0
    for i := 0; i < len(s); i++ {
        seen := make(map[byte]bool)
        for j := i; j < len(s); j++ {
            if seen[s[j]] {
                break
            }
            seen[s[j]] = true
            if length := j - i + 1; length > best {
                best = length
            }
        }
    }
    return best
}
```

Time: O(n²). Space: O(min(n, charset)). Every substring is visited.

**Building toward optimal.** Start with `left = 0`. Expand `right` one character at a time. Track characters in the current window with a hash map (character → most recent index). When you hit a repeated character, don't restart from scratch — slide `left` forward to just past the previous occurrence of that character. The key insight: you never need to move `left` backward.

```go
func lengthOfLongestSubstring(s string) int {
    lastSeen := make(map[byte]int) // char -> last index seen
    best := 0
    left := 0

    for right := 0; right < len(s); right++ {
        c := s[right]
        // if c was seen and its last occurrence is inside our window
        if idx, ok := lastSeen[c]; ok && idx >= left {
            left = idx + 1 // shrink window to exclude the duplicate
        }
        lastSeen[c] = right
        if length := right - left + 1; length > best {
            best = length
        }
    }
    return best
}
```

Time: O(n). Space: O(min(n, charset)).

**The crucial detail.** The condition `idx >= left` is important. Without it, you might move `left` backward if the character was last seen before the window started. The `left` pointer only moves right — never left. This invariant is what makes the algorithm O(n).

Also note: when a duplicate is found, we jump `left` directly to `idx + 1`, skipping all the characters in between. We don't need to revisit them — the window from `left` to `right - 1` was already valid. This jump is only possible because the map tells us exactly where to go.

---

## Problem 3: Minimum Window Substring

**The problem.** Given two strings `s` and `t`, return the minimum window substring of `s` that contains every character in `t` (including duplicates). If no such window exists, return an empty string.

Input: `s = "ADOBECODEBANC"`, `t = "ABC"`
Output: `"BANC"`

This is a hard problem. It's appeared in Google, Meta, and Amazon on-sites. The brute force is obvious, the optimal is a textbook sliding window, and the implementation has enough moving parts to trip you up under pressure.

**Brute force.** Check every substring of `s` and test if it contains all characters of `t`.

```go
func minWindowBrute(s string, t string) string {
    need := make(map[byte]int)
    for i := 0; i < len(t); i++ {
        need[t[i]]++
    }

    best := ""
    for i := 0; i < len(s); i++ {
        for j := i + len(t); j <= len(s); j++ {
            sub := s[i:j]
            if containsAll(sub, need) {
                if best == "" || len(sub) < len(best) {
                    best = sub
                }
            }
        }
    }
    return best
}

func containsAll(sub string, need map[byte]int) bool {
    have := make(map[byte]int)
    for i := 0; i < len(sub); i++ {
        have[sub[i]]++
    }
    for c, count := range need {
        if have[c] < count {
            return false
        }
    }
    return true
}
```

Time: O(n² · m) where m = |t|. Space: O(m).

**Building toward optimal.** This is the canonical variable sliding window. We want the smallest window that satisfies a constraint (contains all of `t`). Strategy: expand `right` until the window is valid (contains all characters of `t`). Once valid, try to shrink by moving `left` right. When shrinking breaks validity, expand again. Track "how many distinct required characters are currently satisfied" with a counter.

```go
func minWindow(s string, t string) string {
    if len(s) == 0 || len(t) == 0 {
        return ""
    }

    // characters we need and their required counts
    need := make(map[byte]int)
    for i := 0; i < len(t); i++ {
        need[t[i]]++
    }

    // characters we currently have in the window
    have := make(map[byte]int)

    // how many distinct characters from t are "satisfied" (have >= need)
    satisfied := 0
    required := len(need)

    left := 0
    bestLeft, bestLen := 0, len(s)+1

    for right := 0; right < len(s); right++ {
        // expand: add s[right] to window
        c := s[right]
        have[c]++
        if need[c] > 0 && have[c] == need[c] {
            satisfied++ // this character's requirement just met
        }

        // shrink: try to move left while window is still valid
        for satisfied == required {
            // update best window
            if windowLen := right - left + 1; windowLen < bestLen {
                bestLen = windowLen
                bestLeft = left
            }

            // remove s[left] from window
            leftChar := s[left]
            have[leftChar]--
            if need[leftChar] > 0 && have[leftChar] < need[leftChar] {
                satisfied-- // requirement no longer met
            }
            left++
        }
    }

    if bestLen == len(s)+1 {
        return ""
    }
    return s[bestLeft : bestLeft+bestLen]
}
```

Time: O(|s| + |t|). Space: O(|s| + |t|) for the maps.

**The `satisfied` counter is the key.** Without it, you'd have to scan the entire `need` map on every step to check if the window is valid — O(m) per step, making the algorithm O(n·m). By maintaining `satisfied` incrementally, the validity check is O(1). Only increment `satisfied` when a character transitions from under-satisfied to exactly satisfied; only decrement when it transitions back. This incremental bookkeeping is the technique that separates O(n) from O(n·m).

---

## How to Recognize This Pattern

- "Longest/shortest subarray or substring satisfying condition X" → variable sliding window
- "Maximum/minimum of all subarrays of size k" → fixed sliding window
- "At most k distinct," "no repeating," "contains all of t" → sliding window with a frequency map
- Brute force has two nested loops where the inner loop always starts at the outer index → classic sliding window setup

The dead giveaway is contiguity. If the problem requires contiguous elements and involves an optimization over a length or count, it's almost always sliding window.

## Key Takeaway

The sliding window pattern exists because both pointers only ever move right. That one invariant is what makes O(n) possible: you visit each element at most twice (once as `right` enters, once as `left` exits). Anytime you catch yourself restarting a scan from the beginning, ask whether you can instead just advance `left` to skip the offending element. The answer is usually yes.

Stock prices: `left` is the cheapest day seen; move it when you find cheaper. Longest unique substring: `left` jumps past the previous duplicate. Minimum window: `left` advances to shrink once the window is valid. Three different constraints, one mechanism.

---

*[← Lesson 2: Two Pointers](/post/fundamentals/interview-two-pointers/) | [Course Index](/series/interview-patterns-cracking-faang-in-the-ai-era/) | [Lesson 4: Stack →](/post/fundamentals/interview-stack/)*
