---
title: "Lesson 4: Stack — Last In, First Out Solves More Than You Think"
author: "Atharva Pandey"
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 4
keywords:
  - interviews
  - algorithms
  - leetcode
  - FAANG
  - pattern
  - stack
  - monotonic stack
tags:
  - fundamentals
  - interviews
date: "2024-04-13T00:00:00.000Z"
---

The stack is one of those data structures that seems too simple to be interesting — push, pop, peek, done. Then you sit in an interview, stare at a problem about brackets or expression evaluation, and realize you're reaching for exactly this tool. I've seen candidates overcomplicate parenthesis validation with counters and flags when a stack makes it four lines of logic. I've also seen the reverse: candidates who knew the stack solution for valid parentheses but couldn't extend the thinking to a harder variant.

This lesson teaches the stack as a thinking tool, not just a data structure. Meta, Google, and Amazon all love expression-based problems and constraint-based sequence problems. Stacks appear constantly in compiler work, calculator design, and undo/redo systems — so interviewers at systems-focused companies use them to probe whether you understand recursive structure.

## The Pattern

A stack is the right tool when the problem has a "most recent unresolved item" structure. Opening brackets need their matching close. Operators in an expression need their operands. Previous elements need to be revisited in reverse order of appearance. If you find yourself needing to "remember the last thing and act on it when a trigger arrives," reach for a stack.

---

## Problem 1: Valid Parentheses

**The problem.** Given a string `s` containing only the characters `(`, `)`, `{`, `}`, `[`, and `]`, determine if the input string is valid. A string is valid if every open bracket is closed by the same type of bracket and in the correct order.

Input: `"()[]{}"` → `true`
Input: `"(]"` → `false`
Input: `"([)]"` → `false`

**Brute force.** Repeatedly scan the string and remove adjacent matched pairs until no more matches are possible or you're stuck.

```go
func isValidBrute(s string) bool {
    pairs := map[string]bool{"()": true, "[]": true, "{}": true}
    for {
        prev := s
        for pair := range pairs {
            s = strings.ReplaceAll(s, pair, "")
        }
        if s == prev {
            break
        }
    }
    return s == ""
}
```

Time: O(n²) — each pass removes pairs, up to n/2 passes. Space: O(n).

**Building toward optimal.** The core insight: when you see an open bracket, you don't know yet whether it's valid. You have to wait for its matching close bracket. But you always match the most recently opened bracket first — that's LIFO. Use a stack. Push open brackets. When you see a close bracket, check if the top of the stack is the matching open bracket. If it is, pop and continue. If not, the string is invalid.

```go
func isValid(s string) bool {
    stack := []rune{}
    matching := map[rune]rune{
        ')': '(',
        ']': '[',
        '}': '{',
    }

    for _, c := range s {
        if c == '(' || c == '[' || c == '{' {
            stack = append(stack, c)
        } else {
            // close bracket: check if top matches
            if len(stack) == 0 || stack[len(stack)-1] != matching[c] {
                return false
            }
            stack = stack[:len(stack)-1] // pop
        }
    }
    return len(stack) == 0
}
```

Time: O(n). Space: O(n) — in the worst case (all open brackets) the stack holds every character.

**Why the stack is not optional here.** A simple counter works for a single bracket type: increment on `(`, decrement on `)`, valid if counter ends at zero. But with three types, the ordering matters. `([)]` has balanced counts but mismatched nesting. The stack encodes the nesting structure — it remembers not just that an open bracket is waiting, but which one and in what order. That's something no counter can do.

---

## Problem 2: Min Stack

**The problem.** Design a stack that supports push, pop, top, and retrieving the minimum element, all in O(1) time.

Implement: `push(val)`, `pop()`, `top()`, `getMin()`.

**Brute force.** A regular stack plus a scan for minimum.

```go
type MinStackBrute struct {
    data []int
}

func (s *MinStackBrute) Push(val int)  { s.data = append(s.data, val) }
func (s *MinStackBrute) Pop()          { s.data = s.data[:len(s.data)-1] }
func (s *MinStackBrute) Top() int      { return s.data[len(s.data)-1] }
func (s *MinStackBrute) GetMin() int {
    m := s.data[0]
    for _, v := range s.data {
        if v < m {
            m = v
        }
    }
    return m
}
```

`GetMin` is O(n). Unacceptable per the problem constraints.

**Building toward optimal.** The challenge: the minimum can change on every push and pop. When you pop the current minimum, you need to know the previous minimum instantly. A second stack — a "min history stack" — tracks the minimum at every state. When you push, push to the min stack only if the new value is ≤ the current minimum (so the min stack contains each successive minimum, with duplicates when needed). When you pop the main stack, check if the popped value equals the top of the min stack — if so, pop the min stack too.

```go
type MinStack struct {
    data    []int
    minData []int // tracks minimum at each depth
}

func Constructor() MinStack {
    return MinStack{}
}

func (s *MinStack) Push(val int) {
    s.data = append(s.data, val)
    if len(s.minData) == 0 || val <= s.minData[len(s.minData)-1] {
        s.minData = append(s.minData, val)
    }
}

func (s *MinStack) Pop() {
    top := s.data[len(s.data)-1]
    s.data = s.data[:len(s.data)-1]
    if top == s.minData[len(s.minData)-1] {
        s.minData = s.minData[:len(s.minData)-1]
    }
}

func (s *MinStack) Top() int {
    return s.data[len(s.data)-1]
}

func (s *MinStack) GetMin() int {
    return s.minData[len(s.minData)-1]
}
```

Time: O(1) for all operations. Space: O(n) — the min stack holds at most n entries.

**Why this works.** The min stack is a monotonically non-increasing sequence. Every time the true minimum decreases, we record it. Every time the true minimum is removed from the main stack, we remove it from the min stack. The top of the min stack always equals the minimum of the current main stack. The ≤ condition (not just <) handles duplicate minimums correctly — if you push 3, 1, 1 and then pop twice, you still correctly return 3 as the minimum on the third pop.

This problem teaches a transferable idea: when a derived property (like minimum) is expensive to recompute, maintain it incrementally using a parallel auxiliary structure that mirrors the main structure's state changes.

---

## Problem 3: Evaluate Reverse Polish Notation

**The problem.** Evaluate an arithmetic expression in Reverse Polish Notation (postfix). Valid operators are `+`, `-`, `*`, `/`. Each operand may be an integer. Division truncates toward zero.

Input: `["2","1","+","3","*"]` → `9` (((2 + 1) * 3) = 9)
Input: `["4","13","5","/","+"]` → `6` ((4 + (13 / 5)) = 6)

**Brute force.** There's no simpler approach than the stack-based one for this problem — RPN was literally designed for stack evaluation. But a naive version without understanding the structure might try to parse an expression tree first.

**Building toward optimal.** RPN eliminates the need for parentheses or operator precedence. The rule: when you see a number, push it. When you see an operator, pop two numbers, apply the operator, push the result. The operand ordering matters: the first popped value is the right operand, the second popped is the left operand (this affects subtraction and division).

```go
func evalRPN(tokens []string) int {
    stack := []int{}

    for _, token := range tokens {
        switch token {
        case "+", "-", "*", "/":
            // pop two operands — right first, then left
            right := stack[len(stack)-1]
            left := stack[len(stack)-2]
            stack = stack[:len(stack)-2]

            var result int
            switch token {
            case "+":
                result = left + right
            case "-":
                result = left - right
            case "*":
                result = left * right
            case "/":
                result = left / right // Go truncates toward zero
            }
            stack = append(stack, result)
        default:
            // it's a number — convert and push
            num, _ := strconv.Atoi(token)
            stack = append(stack, num)
        }
    }

    return stack[0]
}
```

Time: O(n) — each token processed once. Space: O(n) — the stack holds at most (n+1)/2 numbers.

**The connection to a compiler.** RPN evaluation is how compilers execute expressions after parsing. The parser converts infix notation (`2 + 3 * 4`) to postfix (`2 3 4 * +`) using the shunting-yard algorithm — itself stack-based. Then the VM evaluates the postfix stack. Understanding this problem means understanding a fundamental piece of how programming languages work, which is why systems-focused interviewers love it.

**Building on Valid Parentheses.** Valid Parentheses taught you: "push when you open, act when you close." RPN is the same shape: "push operands, act when you see an operator." The trigger-action pattern is identical. This generalization — push items onto a stack until a trigger arrives that consumes them — appears in monotonic stack problems, calculator problems, and expression parsing of all kinds.

---

## How to Recognize This Pattern

- "Matching pairs" with nesting (brackets, tags, calls) → push opens, match closes
- "Retrieve minimum/maximum in O(1) after arbitrary push/pop" → parallel auxiliary stack
- "Evaluate an expression with operators" → operand stack, pop on operator
- "Next greater element," "daily temperatures," "largest rectangle" → monotonic stack (the advanced form of this pattern)
- The brute force rescans backward or reprocesses previous elements → a stack probably caches what you keep recomputing

The common thread: the most recently seen unresolved item needs to be acted on before anything older. That's LIFO, and LIFO is a stack.

## Key Takeaway

The stack shines when problems have recursive or nested structure. Brackets nest. Expressions have sub-expressions. Minimums change and must be un-changed when pops happen. In all these cases, the stack is not just convenient — it is the natural model of the problem's structure.

When you spot "most recent unresolved item" in a problem, stop thinking about arrays and hash maps and draw a stack. The code often follows immediately.

---

*[← Lesson 3: Sliding Window](/post/fundamentals/interview-sliding-window/) | [Course Index](/series/interview-patterns-cracking-faang-in-the-ai-era/) | [Lesson 5: Binary Search →](/post/fundamentals/interview-binary-search/)*
