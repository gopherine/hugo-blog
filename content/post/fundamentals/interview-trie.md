---
title: "Lesson 33: Trie — When you need prefix matching"
author: "Atharva Pandey"
keywords:
  - trie
  - prefix tree
  - autocomplete
  - word search
  - interview patterns
tags:
  - fundamentals
  - interviews
series: "Interview Patterns: Cracking FAANG+ in the AI Era"
lesson: 33
date: "2025-11-26T00:00:00.000Z"
---

Tries showed up in a Google interview I did early in my career. The problem was autocomplete. I started sketching a hash map of prefixes to word lists and immediately knew something was wrong — the interviewer was watching too patiently. The correct structure, the one that makes the solution feel inevitable, is a trie. It organizes words so that every prefix lookup is just a traversal of shared nodes, and I had been fighting to reconstruct that structure from scratch.

A trie — short for retrieval tree, sometimes called a prefix tree — is purpose-built for prefix queries. If you are working with string sets and need "does this prefix exist," "find all words with this prefix," or "match this pattern," the trie is usually the answer.

## The Pattern

A trie stores characters at the edges (or equivalently at the nodes). Each node represents a prefix. A word is complete when you reach a node flagged as a terminal. The alphabet is typically 26 lowercase English letters, but the structure generalizes.

```go
type TrieNode struct {
    children [26]*TrieNode
    isEnd    bool
}

type Trie struct {
    root *TrieNode
}

func Constructor() Trie {
    return Trie{root: &TrieNode{}}
}

func (t *Trie) Insert(word string) {
    node := t.root
    for _, ch := range word {
        idx := ch - 'a'
        if node.children[idx] == nil {
            node.children[idx] = &TrieNode{}
        }
        node = node.children[idx]
    }
    node.isEnd = true
}

func (t *Trie) Search(word string) bool {
    node := t.root
    for _, ch := range word {
        idx := ch - 'a'
        if node.children[idx] == nil {
            return false
        }
        node = node.children[idx]
    }
    return node.isEnd
}

func (t *Trie) StartsWith(prefix string) bool {
    node := t.root
    for _, ch := range prefix {
        idx := ch - 'a'
        if node.children[idx] == nil {
            return false
        }
        node = node.children[idx]
    }
    return true
}
```

Time: O(m) for all operations where m is the length of the word or prefix. Space: O(total characters across all inserted words) in the worst case, but shared prefixes are stored once — which is the whole point.

## Problem 1: Implement Trie

The problem above is the implementation itself. Every interview that involves a trie starts here. A few things worth emphasizing when you code this under pressure:

- The `root` node holds no character itself — it is just an entry point.
- `children[26]` is cleaner than a map when you know the alphabet is lowercase English. A map trades some speed for flexibility.
- `isEnd` is on the node, not the edge. A node can be `isEnd = true` and still have children (think "app" and "apple" both inserted).

When the interviewer asks "what if we need to delete a word?", the answer involves walking down, unsetting `isEnd`, and recursively cleaning up childless nodes on the way back up. Rarely asked but good to mention.

## Problem 2: Word Search II

Given a 2D board of characters and a list of words, find all words that can be formed by sequentially adjacent cells (horizontally or vertically, no reuse).

This is a DFS on the board combined with a trie to prune dead-end paths early. Without the trie you would run a separate DFS for each word — O(words × 4^L) where L is word length. With the trie you run one DFS that follows trie paths, pruning whenever the board cell does not match the next trie node.

```go
func findWords(board [][]byte, words []string) []string {
    root := &TrieNode{}

    // Build trie from word list
    insertWord := func(word string) {
        node := root
        for _, ch := range word {
            idx := ch - 'a'
            if node.children[idx] == nil {
                node.children[idx] = &TrieNode{}
            }
            node = node.children[idx]
        }
        node.isEnd = true
    }
    for _, w := range words {
        insertWord(w)
    }

    rows, cols := len(board), len(board[0])
    found := map[string]bool{}
    path := []byte{}

    var dfs func(r, c int, node *TrieNode)
    dfs = func(r, c int, node *TrieNode) {
        if r < 0 || r >= rows || c < 0 || c >= cols || board[r][c] == '#' {
            return
        }
        ch := board[r][c]
        next := node.children[ch-'a']
        if next == nil {
            return // no word in trie starts with this prefix — prune
        }
        path = append(path, ch)
        board[r][c] = '#' // mark visited

        if next.isEnd {
            found[string(path)] = true
        }

        dirs := [][2]int{{0, 1}, {0, -1}, {1, 0}, {-1, 0}}
        for _, d := range dirs {
            dfs(r+d[0], c+d[1], next)
        }

        board[r][c] = ch // restore
        path = path[:len(path)-1]
    }

    for r := 0; r < rows; r++ {
        for c := 0; c < cols; c++ {
            dfs(r, c, root)
        }
    }

    result := make([]string, 0, len(found))
    for w := range found {
        result = append(result, w)
    }
    return result
}
```

The trie pruning is the engine here. When `next == nil`, we know no word in our dictionary can extend from the current path — stop immediately. Without this, the DFS explores every possible string in the board. With it, we cut branches that will never produce a match.

## Problem 3: Design Search Autocomplete System

Design a system that, given a sequence of typed characters, returns the top 3 historical queries with that prefix (ranked by frequency, then lexicographically).

This is the interview's favorite "trie + extras" problem. The trie gives you prefix navigation; each node also stores the top-K sentences at that prefix so you do not have to re-traverse on every keystroke.

```go
type AutocompleteNode struct {
    children [27]*AutocompleteNode // 26 letters + space (index 26)
    counts   map[string]int        // sentence -> frequency at this node
}

type AutocompleteSystem struct {
    root    *AutocompleteNode
    cur     *AutocompleteNode
    input   []byte
    history map[string]int
}

func NewAutocompleteSystem(sentences []string, times []int) AutocompleteSystem {
    root := &AutocompleteNode{counts: map[string]int{}}
    history := map[string]int{}

    sys := AutocompleteSystem{root: root, cur: root, history: history}
    for i, s := range sentences {
        sys.addSentence(s, times[i])
        history[s] = times[i]
    }
    return sys
}

func (sys *AutocompleteSystem) addSentence(sentence string, freq int) {
    node := sys.root
    for _, ch := range sentence {
        var idx int
        if ch == ' ' {
            idx = 26
        } else {
            idx = int(ch - 'a')
        }
        if node.children[idx] == nil {
            node.children[idx] = &AutocompleteNode{counts: map[string]int{}}
        }
        node = node.children[idx]
        node.counts[sentence] += freq
    }
}

func (sys *AutocompleteSystem) Input(c byte) []string {
    if c == '#' {
        sentence := string(sys.input)
        sys.history[sentence]++
        sys.addSentence(sentence, 1)
        sys.input = sys.input[:0]
        sys.cur = sys.root
        return nil
    }

    sys.input = append(sys.input, c)
    var idx int
    if c == ' ' {
        idx = 26
    } else {
        idx = int(c - 'a')
    }

    if sys.cur == nil || sys.cur.children[idx] == nil {
        sys.cur = nil
        return nil
    }
    sys.cur = sys.cur.children[idx]

    // Return top 3 by frequency, then lexicographic
    type item struct {
        sentence string
        freq     int
    }
    items := make([]item, 0, len(sys.cur.counts))
    for s, f := range sys.cur.counts {
        items = append(items, item{s, f})
    }
    sort.Slice(items, func(i, j int) bool {
        if items[i].freq != items[j].freq {
            return items[i].freq > items[j].freq
        }
        return items[i].sentence < items[j].sentence
    })

    result := []string{}
    for i := 0; i < 3 && i < len(items); i++ {
        result = append(result, items[i].sentence)
    }
    return result
}
```

The trade-off: storing counts at every node costs more memory but makes prefix lookups O(m + k log k) where m is prefix length and k is number of matching sentences. An alternative is to only store sentences at the terminal nodes and DFS from the prefix node to collect all completions — simpler to implement, slower for large dictionaries.

## How to Recognize This Pattern

Trie problems announce themselves clearly:

1. **"Prefix"** appears in the problem statement.
2. **"Autocomplete," "search suggestions," "word matching"** — trie.
3. **You are searching for multiple patterns simultaneously in a string or grid** — build a trie of all patterns, traverse the input once.
4. **Wildcard matching where `.` matches any character** — trie with DFS that tries all children at `.` nodes.
5. **You have a set of strings and need to answer prefix queries repeatedly** — trie outperforms rehashing every time.

The trie vs. hash map decision: if you need exact lookups only, a hash set is simpler. The trie earns its complexity when you need prefix operations, common prefix extraction, or simultaneous multi-pattern matching.

## Key Takeaway

A trie is a tree where shared prefixes share nodes. That simple structural fact makes every prefix query proportional to the prefix length — not the number of words. When a problem involves strings and prefixes, reach for the trie first.

The implementation is mechanical enough to write quickly under pressure. Internalize the `Insert` and `Search` template. From there, every trie problem is just deciding what extra data to hang off each node.

---

**Series**: [Interview Patterns: Cracking FAANG+ in the AI Era](/series/interview-patterns-cracking-faang-in-the-ai-era/)

**Previous**: [Lesson 32: Heap Patterns](/post/fundamentals/interview-heap/) — Keep the top K without sorting everything

**Next**: [Lesson 34: Backtracking](/post/fundamentals/interview-backtracking/) — Try everything, undo what doesn't work
