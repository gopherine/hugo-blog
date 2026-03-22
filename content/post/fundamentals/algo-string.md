---
title: "Lesson 11: String Algorithms — KMP, Rabin-Karp, and why regex can be slow"
author: "Atharva Pandey"
keywords:
  - KMP algorithm
  - Rabin-Karp
  - string matching
  - regex performance
  - pattern search
  - production engineering
tags:
  - fundamentals
  - algorithms
series: "Algorithms That Matter in Production"
lesson: 11
date: "2024-09-17T00:00:00.000Z"
---

String processing sits at the foundation of almost every production system. Log parsing, protocol parsing, search, validation, templating — it all comes down to finding and transforming patterns in text. Most of the time, strings.Contains or a simple loop is fast enough. But when you are processing millions of log lines per second, or running user-supplied patterns against untrusted input, or implementing a search feature that needs to handle long documents, naive string matching becomes a bottleneck or a security hole.

I ran into this the hard way when a user-facing search box was DoS-able with a crafted regex input. Understanding why regex can be slow — and what alternatives exist — is something every production engineer should know.

## How It Works

**Naive string search** compares the pattern against every position in the text. O(n*m) in the worst case, where n is the text length and m is the pattern length.

**KMP (Knuth-Morris-Pratt)** preprocesses the pattern to build a "failure function" that tells you how far to jump back when a mismatch occurs. This allows the search to never revisit characters in the text. O(n + m) total.

**Rabin-Karp** uses a rolling hash: compute the hash of the pattern and the hash of each window in the text. When hashes match, verify character by character. Average O(n + m), useful for multi-pattern search.

```go
// KMP — linear time exact string matching
func kmpSearch(text, pattern string) []int {
    if len(pattern) == 0 {
        return []int{}
    }
    // Build failure function
    fail := buildFailure(pattern)
    var matches []int
    j := 0 // index in pattern
    for i := 0; i < len(text); i++ {
        for j > 0 && text[i] != pattern[j] {
            j = fail[j-1] // use failure function to skip
        }
        if text[i] == pattern[j] {
            j++
        }
        if j == len(pattern) {
            matches = append(matches, i-len(pattern)+1)
            j = fail[j-1]
        }
    }
    return matches
}

// failure[i] = length of longest proper prefix of pattern[:i+1] that is also a suffix
func buildFailure(pattern string) []int {
    fail := make([]int, len(pattern))
    fail[0] = 0
    k := 0
    for i := 1; i < len(pattern); i++ {
        for k > 0 && pattern[i] != pattern[k] {
            k = fail[k-1]
        }
        if pattern[i] == pattern[k] {
            k++
        }
        fail[i] = k
    }
    return fail
}
```

**Rabin-Karp** shines when you need to search for multiple patterns simultaneously — compute all pattern hashes up front and check each text window against all of them.

```go
// Rabin-Karp with polynomial rolling hash
const base = 31
const mod = 1_000_000_007

func rabinKarpSearch(text, pattern string) []int {
    n, m := len(text), len(pattern)
    if m > n {
        return nil
    }

    patHash := computeHash(pattern)
    windowHash := computeHash(text[:m])

    // Precompute base^(m-1) % mod
    power := 1
    for i := 0; i < m-1; i++ {
        power = (power * base) % mod
    }

    var matches []int
    if windowHash == patHash && text[:m] == pattern {
        matches = append(matches, 0)
    }

    for i := 1; i <= n-m; i++ {
        // Roll the hash: remove leftmost char, add new rightmost char
        windowHash = (windowHash - int(text[i-1]-'a'+1)*power%mod + mod) % mod
        windowHash = (windowHash*base + int(text[i+m-1]-'a'+1)) % mod
        if windowHash == patHash && text[i:i+m] == pattern {
            matches = append(matches, i)
        }
    }
    return matches
}

func computeHash(s string) int {
    h := 0
    p := 1
    for _, c := range s {
        h = (h + int(c-'a'+1)*p) % mod
        p = (p * base) % mod
    }
    return h
}
```

## When You Need It

**Know why regex can be catastrophically slow.** Most regex engines use backtracking to implement features like `.*` and `(.+)+`. Certain patterns cause the engine to explore exponentially many states. The classic attack pattern:

```
Pattern: (a+)+
Input: "aaaaaaaaaaaaaaaaaab"
```

This causes the backtracking engine to try every possible grouping of 'a's before concluding the match fails. With 20 'a's, this is 2^20 attempts. With 30, it is 1 billion. This is **ReDoS** (Regular Expression Denial of Service).

Go's regexp package uses a RE2-based engine that runs in O(n) for any pattern because it does not use backtracking. This is why Go regex is safe from ReDoS. The tradeoff: it does not support backreferences or lookaheads, which require backtracking.

```go
// Go's regexp is safe — O(n) via RE2
import "regexp"

// This is safe in Go even for adversarial input
re := regexp.MustCompile(`(a+)+`)
// Will complete in linear time regardless of input

// Check if a library's regex is safe before accepting user-supplied patterns
func safeRegexMatch(pattern, input string) (bool, error) {
    re, err := regexp.Compile(pattern) // Go's regexp uses RE2 — safe
    if err != nil {
        return false, fmt.Errorf("invalid pattern: %w", err)
    }
    return re.MatchString(input), nil
}
```

For Python, Java, or PCRE-based systems, do not accept arbitrary user regex patterns without sandboxing or validation. Use a RE2 library or reject patterns with known explosive constructs.

## Production Example

A log aggregation pipeline needed to extract structured fields from raw log lines. The logs came from ~50 different services, each with a different format. The naive implementation ran `regexp.FindStringSubmatch` on every line against every pattern — O(patterns * line_length) per line.

At 500,000 lines/second (moderate log volume), with 50 patterns averaging 50 characters each, this was 1.25 billion character comparisons per second. The regex was the bottleneck.

The optimization was a two-stage approach: use a fast prefix check to narrow down which patterns could possibly match before running the regex.

```go
type LogParser struct {
    patterns []CompiledPattern
}

type CompiledPattern struct {
    Prefix  string // fast pre-filter
    Regex   *regexp.Regexp
    Fields  []string // named capture group order
}

func (p *LogParser) Parse(line string) (map[string]string, bool) {
    for _, pat := range p.patterns {
        // Fast prefix check before expensive regex
        if pat.Prefix != "" && !strings.HasPrefix(line, pat.Prefix) {
            continue
        }
        matches := pat.Regex.FindStringSubmatch(line)
        if matches == nil {
            continue
        }
        result := make(map[string]string, len(pat.Fields))
        for i, name := range pat.Fields {
            if i+1 < len(matches) {
                result[name] = matches[i+1]
            }
        }
        return result, true
    }
    return nil, false
}

// Aho-Corasick for multi-pattern search — find all patterns simultaneously in one pass
// (Implementation omitted for brevity; cloudflare/ahocorasick is a good Go library)
// Aho-Corasick runs in O(n + total_pattern_length + number_of_matches)
// vastly outperforms running each regex separately for large pattern sets
```

For log parsing at high volume, the Aho-Corasick algorithm (a generalization of KMP for multiple patterns) can identify which log format matches a line in a single O(n) pass, then apply only the matching pattern's regex for field extraction.

```go
// Efficient multi-keyword search using Go's built-in strings.Index
// For simple keyword search without regex, this is faster than regexp
func findAllKeywords(text string, keywords []string) map[string][]int {
    positions := make(map[string][]int)
    for _, kw := range keywords {
        start := 0
        for {
            idx := strings.Index(text[start:], kw)
            if idx == -1 {
                break
            }
            abs := start + idx
            positions[kw] = append(positions[kw], abs)
            start = abs + 1
        }
    }
    return positions
}
```

Go's `strings.Index` uses an optimized Raime-Sunday or Boyer-Moore-Horspool variant internally. For fixed-string search, it is faster than compiling and running a regex.

## The Tradeoffs

KMP has O(m) preprocessing cost. For a pattern searched against a single text, this overhead might not be worth it — use `strings.Index` for simple cases. KMP shines when the same pattern is searched against many texts (precompute the failure function once).

Rabin-Karp has hash collision risk. Two different strings can have the same hash (collision), causing a false positive that the character-by-character verification step catches. With a good hash function, collisions are rare, but they are possible. The verification step makes the worst case O(n*m) (if every window hash matches and collides), though this is astronomically unlikely with a good hash.

Boyer-Moore is generally the fastest algorithm for single-pattern search in long texts (O(n/m) average case), but has complex preprocessing. `strings.Index` in Go uses a variant of Boyer-Moore-Horspool.

## Key Takeaway

For simple fixed-string search, use `strings.Index` — it is optimized and fast. For multi-pattern search at high volume, consider Aho-Corasick. For user-supplied patterns, use Go's RE2-based regexp which is safe from ReDoS. Know that backtracking regex engines (Python, Java, PCRE) are vulnerable to adversarial inputs and treat user-supplied patterns as untrusted input. The underlying algorithms — KMP, Rabin-Karp, Aho-Corasick — matter when string matching is on the critical path.

---

← [Lesson 10: Backtracking](/post/fundamentals/algo-backtracking/) | [Course Index](/post/fundamentals/) | Next: [Lesson 12: Compression Basics](/post/fundamentals/algo-compression/) →
