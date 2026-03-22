---
title: "Lesson 9: Tries — Prefix matching, autocomplete, routing tables"
author: Atharva Pandey
series: "Data Structures for Production Engineers"
lesson: 9
keywords: [data structures, computer science, trie, prefix tree, autocomplete, routing table, IP routing]
tags: [fundamentals, data structures]
date: '2024-08-06T00:00:00.000Z'
---

When you type "ath" into a search box and it suggests "atharva," "athens," and "athletics," that's a trie. When an IP packet arrives at a router and the router decides which interface to forward it to, that's a trie (specifically a Patricia trie). When your web framework matches `/api/users/:id` against an incoming URL, the fast implementations use a trie.

Tries (pronounced "try," from "re*trie*val") are specialized trees for string keys. They trade memory for speed in prefix-matching scenarios, and they make certain string operations fundamentally faster than any other structure.

## How It Actually Works

A trie stores strings character by character. Each node represents a prefix, and the path from the root to any node spells out that prefix. Nodes are marked when they represent the end of a complete word.

```go
package main

import "fmt"

type TrieNode struct {
    children map[rune]*TrieNode
    isEnd    bool
    value    interface{} // optional: store data at end nodes
}

type Trie struct {
    root *TrieNode
}

func NewTrie() *Trie {
    return &Trie{root: &TrieNode{children: make(map[rune]*TrieNode)}}
}

func (t *Trie) Insert(word string, val interface{}) {
    node := t.root
    for _, ch := range word {
        if _, ok := node.children[ch]; !ok {
            node.children[ch] = &TrieNode{children: make(map[rune]*TrieNode)}
        }
        node = node.children[ch]
    }
    node.isEnd = true
    node.value = val
}

func (t *Trie) Search(word string) (interface{}, bool) {
    node := t.root
    for _, ch := range word {
        if _, ok := node.children[ch]; !ok {
            return nil, false
        }
        node = node.children[ch]
    }
    return node.value, node.isEnd
}

func (t *Trie) StartsWith(prefix string) bool {
    node := t.root
    for _, ch := range prefix {
        if _, ok := node.children[ch]; !ok {
            return false
        }
        node = node.children[ch]
    }
    return true
}

func main() {
    t := NewTrie()
    t.Insert("atharva", "author")
    t.Insert("athens", "city")
    t.Insert("athletics", "sport")
    t.Insert("atomic", "element")

    if v, ok := t.Search("atharva"); ok {
        fmt.Println("Found:", v)
    }

    fmt.Println("Has prefix 'ath':", t.StartsWith("ath")) // true
    fmt.Println("Has prefix 'xyz':", t.StartsWith("xyz")) // false
}
```

Lookup is O(m) where m is the length of the search string — independent of how many strings are in the trie. Inserting 10 strings or 10 million strings doesn't change the lookup time for "atharva." Compare this to a hash map: still O(m) for the hash computation, but with better prefix sharing and without hash collisions.

## When to Use It

Use a trie when:
- You need prefix matching or autocomplete
- You're building a router that matches URL patterns
- You need to check if any string in a set is a prefix of an input
- You're implementing spell checking or suggestions
- You need to efficiently store and query IP address prefixes (routing tables)

Don't use a trie when:
- Keys are not string-like (use a hash map)
- Memory is very constrained (tries can use significantly more memory than a hash map for the same data, depending on prefix overlap)
- You only need exact-match lookups with no prefix operations (hash map is simpler and faster)

## Production Example

Let's build an autocomplete engine — the kind you'd use to power search-box suggestions:

```go
package main

import (
    "fmt"
    "sort"
)

type AutocompleteNode struct {
    children  map[rune]*AutocompleteNode
    isEnd     bool
    frequency int // how often this term has been searched
}

type AutocompleteEngine struct {
    root *AutocompleteNode
}

func NewAutocompleteEngine() *AutocompleteEngine {
    return &AutocompleteEngine{
        root: &AutocompleteNode{children: make(map[rune]*AutocompleteNode)},
    }
}

func (ae *AutocompleteEngine) AddTerm(term string, frequency int) {
    node := ae.root
    for _, ch := range term {
        if _, ok := node.children[ch]; !ok {
            node.children[ch] = &AutocompleteNode{children: make(map[rune]*AutocompleteNode)}
        }
        node = node.children[ch]
    }
    node.isEnd = true
    node.frequency = frequency
}

type Suggestion struct {
    Term      string
    Frequency int
}

func (ae *AutocompleteEngine) Suggest(prefix string, limit int) []Suggestion {
    node := ae.root
    for _, ch := range prefix {
        if _, ok := node.children[ch]; !ok {
            return nil
        }
        node = node.children[ch]
    }

    var results []Suggestion
    var dfs func(n *AutocompleteNode, current string)
    dfs = func(n *AutocompleteNode, current string) {
        if n.isEnd {
            results = append(results, Suggestion{current, n.frequency})
        }
        for ch, child := range n.children {
            dfs(child, current+string(ch))
        }
    }
    dfs(node, prefix)

    sort.Slice(results, func(i, j int) bool {
        return results[i].Frequency > results[j].Frequency
    })
    if len(results) > limit {
        results = results[:limit]
    }
    return results
}

func main() {
    engine := NewAutocompleteEngine()
    terms := []struct {
        term string
        freq int
    }{
        {"golang", 9500}, {"google", 50000}, {"goroutine", 7200},
        {"git", 30000}, {"github", 25000}, {"graphql", 8000},
        {"grpc", 11000}, {"gradle", 5000},
    }
    for _, t := range terms {
        engine.AddTerm(t.term, t.freq)
    }

    fmt.Println("Suggestions for 'go':")
    for _, s := range engine.Suggest("go", 3) {
        fmt.Printf("  %s (%d searches)\n", s.Term, s.Frequency)
    }

    fmt.Println("\nSuggestions for 'gr':")
    for _, s := range engine.Suggest("gr", 5) {
        fmt.Printf("  %s (%d searches)\n", s.Term, s.Frequency)
    }
}
```

Now let's look at IP routing — this is what network routers do with every packet:

```go
package main

import (
    "fmt"
    "net"
)

// Simplified longest-prefix-match routing table using a trie over IP bits
type RoutingNode struct {
    children  [2]*RoutingNode
    iface     string // network interface name, if this is a terminal node
    isPrefix  bool
}

type RoutingTable struct {
    root *RoutingNode
}

func NewRoutingTable() *RoutingTable {
    return &RoutingTable{root: &RoutingNode{}}
}

func ipToBits(ip net.IP) []int {
    ip = ip.To4()
    bits := make([]int, 32)
    for i, b := range ip {
        for j := 7; j >= 0; j-- {
            bits[i*8+(7-j)] = int((b >> j) & 1)
        }
    }
    return bits
}

func (rt *RoutingTable) AddRoute(cidr, iface string) {
    _, network, _ := net.ParseCIDR(cidr)
    ones, _ := network.Mask.Size()
    bits := ipToBits(network.IP)

    node := rt.root
    for i := 0; i < ones; i++ {
        b := bits[i]
        if node.children[b] == nil {
            node.children[b] = &RoutingNode{}
        }
        node = node.children[b]
    }
    node.isPrefix = true
    node.iface = iface
}

// LongestPrefixMatch — the core of IP routing
func (rt *RoutingTable) Lookup(ipStr string) string {
    ip := net.ParseIP(ipStr)
    bits := ipToBits(ip)

    node := rt.root
    bestMatch := ""

    for _, b := range bits {
        if node.children[b] == nil {
            break
        }
        node = node.children[b]
        if node.isPrefix {
            bestMatch = node.iface
        }
    }
    return bestMatch
}

func main() {
    rt := NewRoutingTable()
    rt.AddRoute("10.0.0.0/8", "eth0")
    rt.AddRoute("10.0.1.0/24", "eth1") // more specific — takes priority
    rt.AddRoute("192.168.0.0/16", "eth2")

    fmt.Println("10.0.1.55 ->", rt.Lookup("10.0.1.55"))   // eth1 (more specific)
    fmt.Println("10.0.2.1  ->", rt.Lookup("10.0.2.1"))    // eth0 (less specific)
    fmt.Println("192.168.1.1 ->", rt.Lookup("192.168.1.1")) // eth2
}
```

This is longest-prefix-match routing. The trie walk automatically returns the most specific matching prefix because you keep walking until the path ends — the last valid match wins. This is exactly what BGP routers do, operating on tries with hundreds of thousands of prefixes.

## The Tradeoffs

**Memory usage can be high.** A naive trie with one node per character can use far more memory than a hash map for the same keys. For a trie over 26 characters, each node might allocate space for 26 children pointers even if only 2-3 are used. Compressed tries (Patricia tries, radix trees) collapse single-child chains into edge labels, dramatically reducing memory. NGINX uses a radix tree for its routing table.

**Cache behavior is mixed.** The character-by-character descent involves pointer chasing, similar to linked lists. For short strings (URLs, IPv4 addresses), the number of pointer hops is bounded and small enough that cache misses are acceptable. For very long strings over large tries, cache performance degrades.

**The alphabet size matters.** A trie over lowercase ASCII (26 characters) is efficient. A trie over Unicode codepoints (100K+ possible values) would need a hash map at each node, adding overhead. The implementation above uses `map[rune]` for this reason — it handles arbitrary Unicode but with hash map overhead at each level.

## Key Takeaway

Tries solve a class of problems that no other data structure handles as cleanly: prefix-based operations on strings. If you need autocomplete, URL routing, IP longest-prefix matching, or spell-check suggestions, a trie gives you O(m) operations independent of dictionary size. The memory overhead is real, which is why production implementations use compressed variants (Patricia tries, radix trees) — but the conceptual model is the same.

The next time you see a web framework boast about "radix tree routing," you'll know exactly what they mean and why it's faster than iterating over a list of regex patterns.

---

*Previous: [Lesson 8: Graphs — You're solving graph problems without knowing it](/post/fundamentals/ds-graphs/)*

*Next: [Lesson 10: Bloom Filters — Probably yes, definitely no](/post/fundamentals/ds-bloom-filters/)*
