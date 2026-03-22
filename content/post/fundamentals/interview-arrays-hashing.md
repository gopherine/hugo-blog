---
title: "Lesson 1: Arrays and Hashing — The Pattern Behind 30% of All Interview Questions"
author: "Atharva Pandey"
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 1
keywords:
  - interviews
  - algorithms
  - leetcode
  - FAANG
  - pattern
  - arrays
  - hashing
  - hash map
tags:
  - fundamentals
  - interviews
date: "2024-03-03T00:00:00.000Z"
---

When I was preparing for my first round of FAANG interviews, I was overwhelmed. Hundreds of LeetCode problems, dozens of patterns, no clear starting point. Then I noticed something: roughly a third of the medium-difficulty problems I encountered could be cracked with one insight — trading time for space using a hash map. Once I internalized that, arrays and hashing stopped feeling like a category and started feeling like a reflex.

This lesson teaches that reflex. We will work through three canonical problems, each one building on the last, and by the end you will have a mental model you can apply on the spot.

## The Pattern

When a problem asks you to find, count, or group elements in an array, and brute force requires nested loops, ask yourself: "Can I remember what I've seen before in O(1)?" A hash map lets you answer membership and frequency questions in constant time. The tradeoff is O(n) extra space — almost always worth it.

---

## Problem 1: Two Sum

**The problem.** Given an integer array `nums` and a target integer, return the indices of the two numbers that add up to the target. You may assume exactly one solution exists, and you cannot use the same element twice.

**Brute force.** Check every pair. For each element `nums[i]`, scan every element `nums[j]` where `j > i` and check if they sum to target.

```go
func twoSumBrute(nums []int, target int) []int {
    for i := 0; i < len(nums); i++ {
        for j := i + 1; j < len(nums); j++ {
            if nums[i]+nums[j] == target {
                return []int{i, j}
            }
        }
    }
    return nil
}
```

Time: O(n²). Space: O(1). This works but dies on large inputs — an array of a million elements means up to 500 billion comparisons.

**Building toward optimal.** The key question is: when I'm standing at index `i` looking at `nums[i]`, what do I need? I need to know if `target - nums[i]` exists somewhere in the array. Instead of scanning forward every time, what if I'd been keeping a record of everything I've already passed?

That's the hash map insight. As you walk the array left to right, store each number you've seen along with its index. Before you store the current number, check if its complement already lives in the map.

**Optimal solution.**

```go
func twoSum(nums []int, target int) []int {
    seen := make(map[int]int) // value -> index

    for i, n := range nums {
        complement := target - n
        if j, ok := seen[complement]; ok {
            return []int{j, i}
        }
        seen[n] = i
    }
    return nil
}
```

Time: O(n) — single pass. Space: O(n) — the map stores at most n entries.

**Why this works.** By the time you reach index `i`, the map contains every element from index 0 to i-1. If the complement exists anywhere to the left, you'll find it. If it exists to the right, you'll find it when you get there. The order of the check-then-store matters: checking first prevents using the same element twice.

This single-pass, complement-lookup pattern appears everywhere: target sum variants, pair counting, difference problems. Recognize the shape.

---

## Problem 2: Group Anagrams

**The problem.** Given an array of strings, group the anagrams together. Return a list of groups in any order.

Input: `["eat","tea","tan","ate","nat","bat"]`
Output: `[["bat"],["nat","tan"],["ate","eat","tea"]]`

**Brute force.** For every pair of strings, check if they are anagrams by sorting both and comparing. Group them accordingly.

```go
func groupAnagramsBrute(strs []string) [][]string {
    used := make([]bool, len(strs))
    result := [][]string{}

    for i := 0; i < len(strs); i++ {
        if used[i] {
            continue
        }
        group := []string{strs[i]}
        sortedI := sortString(strs[i])
        for j := i + 1; j < len(strs); j++ {
            if !used[j] && sortString(strs[j]) == sortedI {
                group = append(group, strs[j])
                used[j] = true
            }
        }
        result = append(result, group)
    }
    return result
}

func sortString(s string) string {
    r := []byte(s)
    sort.Slice(r, func(i, j int) bool { return r[i] < r[j] })
    return string(r)
}
```

Time: O(n² · k log k) where k is the average string length. Ugly.

**Building toward optimal.** Anagrams share one property: when you sort their characters, you get the same string. "eat", "tea", "ate" all become "aet". That sorted string is a canonical key — a fingerprint. If we use this fingerprint as a hash map key, we can group anagrams in a single pass.

**Optimal solution.**

```go
func groupAnagrams(strs []string) [][]string {
    groups := make(map[string][]string)

    for _, s := range strs {
        key := sortString(s)
        groups[key] = append(groups[key], s)
    }

    result := make([][]string, 0, len(groups))
    for _, group := range groups {
        result = append(result, group)
    }
    return result
}

func sortString(s string) string {
    b := []byte(s)
    sort.Slice(b, func(i, j int) bool { return b[i] < b[j] })
    return string(b)
}
```

Time: O(n · k log k) — n strings, each sorted in k log k. Space: O(n · k) for the map. One pass, clean and fast.

**The deeper idea.** The pattern here is "canonical form as key." When you need to group things that are equivalent under some transformation, find the transformation, apply it, and use the result as a hash map key. You'll see this again with isomorphic strings, valid anagram checks, and word pattern matching.

---

## Problem 3: Top K Frequent Elements

**The problem.** Given an integer array `nums` and an integer `k`, return the `k` most frequent elements. You may return the answer in any order.

Input: `nums = [1,1,1,2,2,3]`, `k = 2`
Output: `[1,2]`

**Brute force.** Count frequencies, sort all unique elements by frequency descending, take the top k.

```go
func topKFrequentBrute(nums []int, k int) []int {
    count := make(map[int]int)
    for _, n := range nums {
        count[n]++
    }

    unique := make([]int, 0, len(count))
    for n := range count {
        unique = append(unique, n)
    }
    sort.Slice(unique, func(i, j int) bool {
        return count[unique[i]] > count[unique[j]]
    })
    return unique[:k]
}
```

Time: O(n log n). Space: O(n). This is decent, but the follow-up in an interview is always: "Can you do better than O(n log n)?"

**Building toward optimal.** The constraint is n total elements, so frequencies range from 1 to n. That's a bounded range — ideal for bucket sort. Create an array of n+1 buckets where index `i` holds all numbers that appear exactly `i` times. Then walk the buckets from high to low, collecting elements until you have k of them.

```go
func topKFrequent(nums []int, k int) []int {
    // Step 1: count frequencies
    count := make(map[int]int)
    for _, n := range nums {
        count[n]++
    }

    // Step 2: bucket by frequency
    // bucket[i] = list of numbers that appear i times
    buckets := make([][]int, len(nums)+1)
    for num, freq := range count {
        buckets[freq] = append(buckets[freq], num)
    }

    // Step 3: collect top k from high-frequency buckets
    result := make([]int, 0, k)
    for i := len(buckets) - 1; i >= 0 && len(result) < k; i-- {
        result = append(result, buckets[i]...)
    }
    return result[:k]
}
```

Time: O(n). Space: O(n). We beat O(n log n) by exploiting the bounded frequency range.

**Why this pattern completes the trio.** Two Sum taught you to look up complements in O(1). Group Anagrams taught you to use canonical keys to group equivalent items. Top K Frequent teaches you to invert a map — use values as keys — and to reach for linear time when the value domain is bounded.

---

## How to Recognize This Pattern

When you see these signals, think arrays and hashing:

- "Find two elements that sum to X" → complement lookup in a map
- "Group elements that share a property" → canonical key in a map
- "Find the most/least frequent k elements" → frequency map, then bucket or heap
- Nested loops in your brute force → almost always replaceable with a map
- "O(1) lookup" is needed for correctness or performance → hash map

If the problem asks about membership, frequency, or grouping, and the brute force is quadratic, you are almost certainly looking at a hashing problem.

## Key Takeaway

The hash map is the workhorse of interview problems because it converts O(n) lookups into O(1) lookups at the cost of O(n) space. The art is recognizing when you have been doing unnecessary repeated work (nested loops, re-scanning) and asking: what if I just remembered this?

Two Sum: remember complements. Group Anagrams: remember canonical forms. Top K Frequent: remember frequencies, then invert. Same idea, three different shapes.

When you sit down in an interview and see an array problem with no obvious structure, reach for the hash map before anything else. It won't always be the answer, but it's the right first question.

---

*← Start of Series | [Course Index](/series/interview-patterns-cracking-faang-in-the-ai-era/) | [Lesson 2: Two Pointers →](/post/fundamentals/interview-two-pointers/)*
