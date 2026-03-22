---
title: "Lesson 2: Two Pointers — When One Pointer Isn't Enough"
author: "Atharva Pandey"
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 2
keywords:
  - interviews
  - algorithms
  - leetcode
  - FAANG
  - pattern
  - two pointers
  - array
tags:
  - fundamentals
  - interviews
date: "2024-03-14T00:00:00.000Z"
---

One of my early interview mistakes was throwing a hash map at every array problem. It works a surprising amount of the time, but there's a whole class of problems where you don't need extra space at all — where the structure of the input lets two indices do the work together. Two pointers is that technique, and once you see it, you cannot unsee it.

The pattern shows up at every company. Amazon loves it for stream-processing problems. Google uses it for geometry and partition questions. Meta favors it in string validation and deduplication. Three Sum alone has appeared in more on-site rounds than I can count.

## The Pattern

Place two indices into an array — usually one at each end, or one slow and one fast — and move them toward each other (or forward at different speeds) based on a condition. Because the array is often sorted, or because the two pointers together encode a state you would otherwise need extra memory to track, you get O(n) time and O(1) space. The key is knowing which pointer to move and when.

---

## Problem 1: Valid Palindrome

**The problem.** A phrase is a palindrome if, after converting all uppercase letters to lowercase and removing all non-alphanumeric characters, it reads the same forward and backward. Given a string `s`, return true if it is a palindrome.

Input: `"A man, a plan, a canal: Panama"` → `true`
Input: `"race a car"` → `false`

**Brute force.** Clean the string first — filter to alphanumeric and lowercase — then compare it to its reverse.

```go
func isPalindromeBrute(s string) bool {
    clean := []byte{}
    for i := 0; i < len(s); i++ {
        c := s[i]
        if c >= 'A' && c <= 'Z' {
            clean = append(clean, c+32) // to lowercase
        } else if (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') {
            clean = append(clean, c)
        }
    }
    for i, j := 0, len(clean)-1; i < j; i, j = i+1, j-1 {
        if clean[i] != clean[j] {
            return false
        }
    }
    return true
}
```

Time: O(n). Space: O(n) for the cleaned copy. Good time complexity, but we're allocating.

**Building toward optimal.** The cleaned copy is just scaffolding — we built it so we could compare characters from both ends. But we can compare characters from both ends of the original string directly, skipping non-alphanumeric characters as we go. Left pointer starts at 0, right pointer starts at the end. Advance each pointer past invalid characters, then compare.

```go
func isPalindrome(s string) bool {
    left, right := 0, len(s)-1

    for left < right {
        // skip non-alphanumeric from the left
        for left < right && !isAlphanumeric(s[left]) {
            left++
        }
        // skip non-alphanumeric from the right
        for left < right && !isAlphanumeric(s[right]) {
            right--
        }

        if toLower(s[left]) != toLower(s[right]) {
            return false
        }
        left++
        right--
    }
    return true
}

func isAlphanumeric(c byte) bool {
    return (c >= 'a' && c <= 'z') ||
        (c >= 'A' && c <= 'Z') ||
        (c >= '0' && c <= '9')
}

func toLower(c byte) byte {
    if c >= 'A' && c <= 'Z' {
        return c + 32
    }
    return c
}
```

Time: O(n). Space: O(1). Each character is visited at most twice.

**Why this works.** The two pointers move toward each other, so together they cover the full string exactly once. The inner skip loops never cause a pointer to go backward — left only moves right, right only moves left — so the total work is linear. This "converging pointers" shape is the most common two-pointer setup.

---

## Problem 2: Three Sum

**The problem.** Given an integer array `nums`, return all triplets `[nums[i], nums[j], nums[k]]` such that `i != j`, `i != k`, `j != k`, and `nums[i] + nums[j] + nums[k] == 0`. The solution set must not contain duplicate triplets.

Input: `[-1, 0, 1, 2, -1, -4]`
Output: `[[-1,-1,2],[-1,0,1]]`

**Brute force.** Three nested loops. Check every triplet.

```go
func threeSumBrute(nums []int) [][]int {
    sort.Ints(nums)
    seen := make(map[[3]int]bool)
    result := [][]int{}

    for i := 0; i < len(nums)-2; i++ {
        for j := i + 1; j < len(nums)-1; j++ {
            for k := j + 1; k < len(nums); k++ {
                if nums[i]+nums[j]+nums[k] == 0 {
                    triple := [3]int{nums[i], nums[j], nums[k]}
                    if !seen[triple] {
                        seen[triple] = true
                        result = append(result, []int{nums[i], nums[j], nums[k]})
                    }
                }
            }
        }
    }
    return result
}
```

Time: O(n³). Completely unacceptable for large inputs.

**Building toward optimal.** Sort the array. Now fix the first element `nums[i]` and reduce the problem to Two Sum on the remaining subarray — find two numbers in `nums[i+1..n-1]` that sum to `-nums[i]`. Since the subarray is sorted, we can use converging two pointers: left starts just after `i`, right starts at the end.

If the sum of the two pointers is too small, move left right. If too large, move right left. If equal, record the triplet and advance both, skipping duplicates.

```go
func threeSum(nums []int) [][]int {
    sort.Ints(nums)
    result := [][]int{}

    for i := 0; i < len(nums)-2; i++ {
        // skip duplicate values for the first element
        if i > 0 && nums[i] == nums[i-1] {
            continue
        }
        // early exit: smallest possible sum is already positive
        if nums[i] > 0 {
            break
        }

        left, right := i+1, len(nums)-1
        for left < right {
            sum := nums[i] + nums[left] + nums[right]
            if sum == 0 {
                result = append(result, []int{nums[i], nums[left], nums[right]})
                // skip duplicates for second and third elements
                for left < right && nums[left] == nums[left+1] {
                    left++
                }
                for left < right && nums[right] == nums[right-1] {
                    right--
                }
                left++
                right--
            } else if sum < 0 {
                left++
            } else {
                right--
            }
        }
    }
    return result
}
```

Time: O(n²) — O(n log n) to sort plus O(n) for each of n outer iterations. Space: O(1) excluding the output.

**The jump from Two Sum to Three Sum.** In Two Sum we used a hash map because there was no sorted structure to exploit. Here, sorting costs O(n log n) but buys us the two-pointer inner loop, which is O(n) instead of O(n) with a map. More importantly, the sorted order makes duplicate skipping trivial — you just check adjacent elements. This "fix one, two-pointer the rest" technique extends to Four Sum (fix two, two-pointer the rest) and beyond.

---

## Problem 3: Container With Most Water

**The problem.** You are given an integer array `height` of length `n`. There are `n` vertical lines drawn at positions `i` and `i+1`. Find two lines that together with the x-axis form a container that holds the most water.

Input: `height = [1,8,6,2,5,4,8,3,7]`
Output: `49` (lines at index 1 and 8, width 7, min height 7)

**Brute force.** Check every pair.

```go
func maxAreaBrute(height []int) int {
    maxWater := 0
    for i := 0; i < len(height)-1; i++ {
        for j := i + 1; j < len(height); j++ {
            width := j - i
            h := min(height[i], height[j])
            if area := width * h; area > maxWater {
                maxWater = area
            }
        }
    }
    return maxWater
}

func min(a, b int) int {
    if a < b {
        return a
    }
    return b
}
```

Time: O(n²). Space: O(1). On n=100,000 lines, this is 5 billion comparisons.

**Building toward optimal.** Start with the widest possible container: left at index 0, right at the last index. The area is `(right - left) * min(height[left], height[right])`. To potentially improve, we must shrink the width — there's no way around that. The only hope is to find a taller line. The shorter of the two current lines is the bottleneck — the taller line is already as useful as it can be at this width. So move the pointer pointing to the shorter line inward and hope to find something taller.

```go
func maxArea(height []int) int {
    left, right := 0, len(height)-1
    maxWater := 0

    for left < right {
        width := right - left
        h := height[left]
        if height[right] < h {
            h = height[right]
        }
        if area := width * h; area > maxWater {
            maxWater = area
        }

        // move the pointer at the shorter line
        if height[left] <= height[right] {
            left++
        } else {
            right--
        }
    }
    return maxWater
}
```

Time: O(n). Space: O(1).

**Why is this correct?** When we move the left pointer inward (because `height[left] <= height[right]`), we are discarding all pairs `(left, j)` for `j < right`. Are any of those pairs the answer? No: the width for any such pair is smaller than the current width, and the height is bounded by `height[left]` (since we'd still pick the minimum). The current pair `(left, right)` already achieved the best possible area with `height[left]` as the constraining height. Moving right inward instead would be wrong — it could discard the actual optimum.

This greedy argument — "the shorter side can never do better at a narrower width, so move it" — is what makes the algorithm correct. Proving greedy correctness in an interview earns significant signal.

---

## How to Recognize This Pattern

- The array is sorted (or you can sort it) and you need pairs or triplets → converging pointers
- You need to find two elements satisfying a condition with minimum space → start from both ends
- Nested loops in brute force, but one loop's behavior depends on the other in a monotonic way → two pointers collapse it to O(n)
- The word "palindrome," "container," "two numbers summing to," or "remove duplicates in-place" → two pointers almost certainly

The giveaway is monotonicity: if moving a pointer in one direction always makes things better (or at least not worse) and moving it the other way always makes things worse, two pointers work.

## Key Takeaway

Two pointers is not a trick — it's a formalization of the observation that sorted structure lets two indices encode a 2D search in O(n) time. Brute force explores an n×n grid of pairs. Two pointers traces a single path through that grid, guided by the sorted order.

When you see a sorted array and a pair-based condition, your first instinct should be two pointers. When the array is not sorted, ask whether sorting it (at O(n log n) cost) enables two pointers — that trade is almost always worth making.

---

*[← Lesson 1: Arrays and Hashing](/post/fundamentals/interview-arrays-hashing/) | [Course Index](/series/interview-patterns-cracking-faang-in-the-ai-era/) | [Lesson 3: Sliding Window →](/post/fundamentals/interview-sliding-window/)*
