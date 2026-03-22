---
title: "Lesson 9: Bit Manipulation — The Trick Questions That Test Fundamentals"
author: "Atharva Pandey"
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 9
keywords:
  - interviews
  - algorithms
  - leetcode
  - FAANG
  - pattern
  - bit manipulation
  - XOR
  - bitwise
tags:
  - fundamentals
  - interviews
date: "2024-11-07T00:00:00.000Z"
---

Bit manipulation problems have a reputation for being tricks — the kind of problem where you either know the one-liner or you don't, and if you don't, no amount of reasoning will get you there. That's mostly false. The problems in this lesson have elegant solutions, but those solutions come from understanding a small set of bitwise properties and applying them deliberately. I've seen candidates ace these problems in interviews not because they memorized the answer, but because they reasoned through the bit properties in real time.

These problems show up at every level — the XOR single number question is a common warm-up at Google. Counting bits appears in embedded systems roles at any company doing hardware work. Reverse bits is a classic systems interview question at Amazon and Meta for infrastructure roles. None of them are unfair if you understand what XOR, AND, and shifts actually do.

## The Pattern

Bit manipulation works because integers are, fundamentally, arrays of bits. Certain operations that are expensive on integer arrays are free on the bit representation: XOR finds differences in O(1), AND isolates specific bits, right shift halves a number, left shift doubles it. When a problem involves unique elements, parity, powers of two, or low-level numeric operations, think bits first.

---

## Problem 1: Single Number

**The problem.** Given a non-empty array of integers where every element appears exactly twice except for one element, find that single element. Your solution must run in O(n) time and O(1) space.

Input: `[4, 1, 2, 1, 2]` → `4`

**Brute force.** Hash map to count frequencies.

```go
func singleNumberBrute(nums []int) int {
    count := make(map[int]int)
    for _, n := range nums {
        count[n]++
    }
    for n, c := range count {
        if c == 1 {
            return n
        }
    }
    return -1
}
```

Time: O(n). Space: O(n). Correct, but violates the O(1) space constraint.

**Building toward optimal.** The constraint "O(1) space" combined with "every element appears twice except one" is a strong hint toward XOR. Why? XOR has two properties that matter here:

1. `a XOR a = 0` (a number XOR'd with itself is zero)
2. `a XOR 0 = a` (a number XOR'd with zero is itself)
3. XOR is commutative and associative (order doesn't matter)

If you XOR all elements together, the pairs cancel out (become 0), and the lone element remains.

```go
func singleNumber(nums []int) int {
    result := 0
    for _, n := range nums {
        result ^= n
    }
    return result
}
```

Time: O(n). Space: O(1).

**Walk through the example.** `4 ^ 1 ^ 2 ^ 1 ^ 2`. Reorder: `(1 ^ 1) ^ (2 ^ 2) ^ 4 = 0 ^ 0 ^ 4 = 4`. The XOR of every element is just the single element.

**Why this is elegant and generalizable.** XOR-based deduplication shows up in other problems: find the two non-duplicate elements (XOR all, split by a set bit into two groups, XOR each group). Find a missing number in range [0, n] (XOR with expected range). Wherever you need to "cancel out" equal elements without storing them, XOR is the tool.

---

## Problem 2: Counting Bits

**The problem.** Given an integer `n`, return an array `ans` of length `n + 1` such that for each `i` in `[0, n]`, `ans[i]` is the number of 1s in the binary representation of `i`.

Input: `n = 5` → `[0,1,1,2,1,2]`

**Brute force.** For each number, count its set bits.

```go
func countBitsBrute(n int) []int {
    ans := make([]int, n+1)
    for i := 0; i <= n; i++ {
        ans[i] = countOnes(i)
    }
    return ans
}

func countOnes(n int) int {
    count := 0
    for n > 0 {
        count += n & 1 // check lowest bit
        n >>= 1        // shift right
    }
    return count
}
```

Time: O(n log n) — O(log i) per number. Space: O(1) extra.

**Building toward O(n).** Notice the pattern in the popcount values:

```
0: 0  → 0 ones
1: 1  → 1 one
2: 10 → 1 one
3: 11 → 2 ones
4: 100 → 1 one
5: 101 → 2 ones
6: 110 → 2 ones
7: 111 → 3 ones
```

The relationship: `popcount(i) = popcount(i >> 1) + (i & 1)`. Strip the lowest bit (`i >> 1` right-shifts, discarding it), count the ones in the rest, then add whether the bit you stripped was a 1.

```go
func countBits(n int) []int {
    ans := make([]int, n+1)
    for i := 1; i <= n; i++ {
        ans[i] = ans[i>>1] + (i & 1)
    }
    return ans
}
```

Time: O(n). Space: O(1) extra (the output array is required).

**Alternative DP relation.** `popcount(i) = popcount(i & (i-1)) + 1`. The expression `i & (i-1)` clears the lowest set bit. So the count for `i` is one more than the count for `i` with its lowest set bit removed — which we've already computed.

```go
func countBitsAlt(n int) []int {
    ans := make([]int, n+1)
    for i := 1; i <= n; i++ {
        ans[i] = ans[i&(i-1)] + 1
    }
    return ans
}
```

**The `i & (i-1)` trick.** This clears the lowest set bit of `i` and appears in many bit problems: check if a number is a power of two (`n & (n-1) == 0` for n > 0), count set bits iteratively (Kernighan's algorithm), find if two numbers differ by exactly one bit. Memorize this operation and what it does.

---

## Problem 3: Reverse Bits

**The problem.** Reverse the bits of a given 32-bit unsigned integer.

Input: `n = 00000010100101000001111010011100` (binary) → `964176192` (`00111001011110000010100101000000` binary)

**Brute force.** Extract bits one at a time into a string, reverse it, parse it back.

```go
func reverseBitsBrute(num uint32) uint32 {
    bits := fmt.Sprintf("%032b", num)
    reversed := []byte(bits)
    for i, j := 0, len(reversed)-1; i < j; i, j = i+1, j-1 {
        reversed[i], reversed[j] = reversed[j], reversed[i]
    }
    result, _ := strconv.ParseUint(string(reversed), 2, 32)
    return uint32(result)
}
```

Time: O(32) = O(1). Space: O(32) = O(1). But string manipulation for bit operations is poor form.

**Building toward optimal.** Build the result bit by bit. Extract the lowest bit of `n` with `n & 1`, place it in the correct position of the result with a left shift, then right-shift `n` to process the next bit.

```go
func reverseBits(num uint32) uint32 {
    var result uint32
    for i := 0; i < 32; i++ {
        result = (result << 1) | (num & 1) // shift result left, OR in lowest bit of num
        num >>= 1                           // advance to next bit of num
    }
    return result
}
```

Time: O(32) = O(1). Space: O(1).

**Walk through the logic.** Each iteration: left-shift `result` to make room for the next bit, OR in the current lowest bit of `num`, right-shift `num` to expose the next bit. After 32 iterations, the bits are reversed. The key operation is `(result << 1) | (num & 1)` — shift the accumulated result one position and append the extracted bit.

**Divide and conquer bit reversal.** For repeated calls (follow-up: "optimize for a function that's called many times"), you can reverse in O(1) using a sequence of mask-and-swap operations:

```go
func reverseBitsDivide(num uint32) uint32 {
    // swap odd and even bits
    num = ((num & 0xAAAAAAAA) >> 1) | ((num & 0x55555555) << 1)
    // swap consecutive pairs
    num = ((num & 0xCCCCCCCC) >> 2) | ((num & 0x33333333) << 2)
    // swap nibbles
    num = ((num & 0xF0F0F0F0) >> 4) | ((num & 0x0F0F0F0F) << 4)
    // swap bytes
    num = ((num & 0xFF00FF00) >> 8) | ((num & 0x00FF00FF) << 8)
    // swap 16-bit halves
    num = (num >> 16) | (num << 16)
    return num
}
```

Each step swaps bits at different granularities. This is useful when you'd otherwise compute this millions of times — you can also precompute a lookup table for 8-bit or 16-bit chunks and combine them.

---

## How to Recognize This Pattern

- "O(1) space" + "elements appear exactly twice except one" → XOR cancellation
- "Count set bits for all numbers 0..n" → DP on bit structure
- "Is this a power of two?" → `n & (n-1) == 0`
- "Clear the lowest set bit" → `n & (n-1)`
- "Extract the lowest set bit" → `n & (-n)` (two's complement trick)
- "Parity of a number" → XOR all bits together
- Low-level numeric problems, encoding, masks → think about the bit representation first

The signals for bit manipulation: the problem constrains space heavily, involves powers of two, mentions "exactly once/twice," or involves hardware-level operations like byte manipulation.

## Key Takeaway

Bit manipulation problems are not tricks — they are applications of a small set of identities. XOR cancels equal values. `n & (n-1)` clears the lowest set bit. `n & 1` extracts the lowest bit. Shifts multiply and divide by powers of two. AND masks isolate bits; OR sets them; XOR flips them.

When a problem seems to demand extra space but has a "can you do O(1)?" constraint, and the data is numeric, think about what the bit representation reveals. More often than you'd expect, the answer is already encoded in the bits.

---

*[← Lesson 8: Sorting](/post/fundamentals/interview-sorting/) | [Course Index](/series/interview-patterns-cracking-faang-in-the-ai-era/) | [Lesson 10: Math Patterns →](/post/fundamentals/interview-math/)*
