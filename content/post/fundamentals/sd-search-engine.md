---
title: "Lesson 12: Design a Search Engine — Inverted Index and Ranking"
author: "Atharva Pandey"
series: "System Design from First Principles"
lesson: 12
tags:
  - fundamentals
  - system design
keywords:
  - search engine design
  - inverted index
  - TF-IDF
  - Elasticsearch
  - search ranking
  - full-text search
  - system design
date: "2024-10-07T00:00:00.000Z"
---

Search is the feature that separates usable products from unusable ones at scale. When your application has thousands of documents, a sequential scan works. At millions, it doesn't. The difference between a search box that works and one that times out is a data structure invented in the 1960s that every modern search engine still fundamentally relies on: the inverted index. Understanding how it works — and how to build a system around it — is what this lesson is about.

## The Core Concept

**The naive approach and why it fails**

The simplest search: `SELECT * FROM documents WHERE content LIKE '%search term%'`. This scans every row, every character. For 100 million documents, this is O(N) in the number of characters across all documents. It's unusably slow.

**The inverted index**

An inverted index flips the relationship: instead of "document → words it contains," you build "word → documents that contain it."

```
Forward index (document → terms):
  doc1: "Go is fast"    → {go, is, fast}
  doc2: "Go is compiled" → {go, is, compiled}
  doc3: "Rust is fast"  → {rust, is, fast}

Inverted index (term → document IDs):
  "go"       → [doc1, doc2]
  "is"       → [doc1, doc2, doc3]
  "fast"     → [doc1, doc3]
  "compiled" → [doc2]
  "rust"     → [doc3]
```

To answer "find documents containing 'go' AND 'fast'": look up both terms in the inverted index, get their posting lists, and intersect them. Result: [doc1]. This intersection is O(k log k) where k is the number of matching documents — far smaller than N.

**Posting lists**

Each entry in the inverted index maps a term to a posting list: the list of documents (and positions within them) where the term appears. Position information enables phrase queries ("exact phrase" search) and relevance scoring.

```
"go" → [(doc1, positions:[0]), (doc2, positions:[0])]
```

**Tokenization and normalization**

Before building the index, you process text:
- **Tokenization**: split "Go is fast" into ["Go", "is", "fast"]
- **Lowercasing**: "Go" → "go" (so a search for "go" matches "Go")
- **Stop word removal**: remove common words like "is", "the", "and" that appear in almost every document and add no search signal
- **Stemming/Lemmatization**: "running" → "run", "compiled" → "compil" (so "compile" matches "compiled")

Different tokenizers produce different results. A tokenizer that splits on whitespace handles English well but fails for languages without spaces (Chinese, Japanese). Full-featured search systems have language-specific analyzers.

**Relevance: TF-IDF**

Not all matches are equally relevant. A document that mentions "golang" once and is primarily about Java is less relevant than one entirely about Golang. TF-IDF (Term Frequency - Inverse Document Frequency) is the classical relevance signal:

- **TF (Term Frequency)**: how often the term appears in this document. More occurrences → more relevant (to a point).
- **IDF (Inverse Document Frequency)**: how rare the term is across all documents. A term that appears in every document is not informative. A term that appears in only 1% of documents is highly discriminating.

```
TF(t, d) = count of t in d / total terms in d
IDF(t)   = log(total documents / documents containing t)
TF-IDF   = TF × IDF
```

Documents are scored by sum of TF-IDF across all query terms. The highest scorers are the most relevant.

Modern systems like Elasticsearch use BM25, an improved variant that handles term saturation (a document mentioning the term 100 times isn't necessarily 100x more relevant than one mentioning it once) and document length normalization.

## How to Design It

**Indexing pipeline**

```
Documents → [Crawler/Ingestion] → [Parser/Tokenizer] → [Inverted Index Builder]
                                                               ↓
                                                        [Index Store]
                                                        (Elasticsearch, Lucene segment)
```

New documents are indexed asynchronously. For a web search engine, a crawler fetches pages. For a product search, a pipeline processes new product listings. For a codebase search, a watcher detects file changes.

The index is typically built in segments (Lucene's segment model): new documents go into an in-memory segment that's periodically flushed to disk. Segments are merged periodically to keep the index efficient.

**Query execution**

```go
func Search(query string, index InvertedIndex) []Result {
    // 1. Tokenize query
    tokens := tokenize(query) // ["go", "fast"]

    // 2. Look up posting lists for each term
    lists := make([][]Posting, len(tokens))
    for i, token := range tokens {
        lists[i] = index.Lookup(token)
    }

    // 3. Intersect posting lists (AND query)
    candidates := intersect(lists)

    // 4. Score each candidate
    results := make([]Result, len(candidates))
    for i, doc := range candidates {
        results[i] = Result{DocID: doc.ID, Score: score(tokens, doc, index)}
    }

    // 5. Sort by score, return top K
    sort.Slice(results, func(i, j int) bool {
        return results[i].Score > results[j].Score
    })
    return results[:min(len(results), 10)]
}
```

**Distributed indexing**

For web-scale search, a single machine can't hold the entire index. You shard the index: each shard holds a subset of documents. A query fan-outs to all shards, each shard returns its top K results, a coordinator merges and re-ranks.

```
Query → [Coordinator]
              ↓ (fan-out)
  [Shard 1]  [Shard 2]  [Shard 3]
     ↓           ↓           ↓
  top 10     top 10      top 10
              ↓ (merge)
  [Coordinator re-ranks and returns top 10]
```

Elasticsearch's architecture does exactly this: shards + replicas + a coordinating node that merges results.

**Index freshness**

How quickly do new documents appear in search results? This depends on your pipeline latency. For a product catalog, minutes of lag is acceptable. For a news search, seconds of lag matters. Near-real-time indexing (Elasticsearch supports this with in-memory segment refresh) gives sub-second freshness. Fully real-time is hard — Druid and Pinot support streaming ingestion into their indexes.

## Real-World Example

Elasticsearch (built on Lucene) is the most widely deployed full-text search system. It handles tokenization, BM25 scoring, distributed sharding, replica-based high availability, and near-real-time indexing. Wikipedia uses Elasticsearch for its search. GitHub uses it for code search. Shopify uses it for product catalog search.

Google's web search is orders of magnitude more complex: PageRank adds link graph signals, knowledge graph enhances entity recognition, BERT models understand query intent. But the foundational inverted index and TF-IDF concepts are still at the core.

Algolia, a search-as-a-service provider, differentiates on low latency (sub-10ms search) and typo tolerance. Their typo tolerance is built using a combination of prefix indexing and edit distance computation over a trie structure — a different approach than pure inverted indexes.

## Interview Tips

"How would you design the search for a platform with 500 million products?" The key points: inverted index in Elasticsearch sharded across multiple nodes. Separate write path (indexing pipeline) from read path (query execution). Near-real-time indexing for new product listings. Caching popular queries (Redis or in-process LRU cache). Relevance includes non-text signals: popularity (views, purchases), recency, seller rating.

"What about autocomplete / typeahead?" A different data structure: a trie or a prefix index. You need to return matches on prefixes, not just whole words. Elasticsearch's `edge_ngram` tokenizer builds a prefix index. For very fast autocomplete (< 10ms), an in-memory trie is more effective.

"How do you handle spelling correction?" Levenshtein distance (edit distance) between the query term and known terms in the vocabulary. Keeping a sorted list of all indexed terms plus a BK-tree enables efficient approximate matching.

"How do you keep the index up to date when documents change?" Incremental updates: delete the old version, index the new one. Elasticsearch supports document updates, but internally they're delete + reindex. Frequent updates create index fragmentation — periodic segment merges clean this up.

## Key Takeaway

Search is built on the inverted index: a mapping from terms to the documents containing them. Tokenization, normalization, and stop word removal determine what gets indexed. TF-IDF and BM25 provide relevance scoring by combining term frequency with the rarity of the term across the corpus. At scale, the index is sharded — queries fan out across shards and results are merged by a coordinator. Index freshness and write throughput are separate concerns from query latency — design the indexing pipeline as an async write path. Understanding these fundamentals lets you reason about any search problem, whether it's a full-text document search, product catalog search, or code search.

---

**Previous:** [Lesson 11: Design a Notification System](/post/fundamentals/sd-notifications/)
**Next:** [Lesson 13: Design a Payment System — Idempotency, Reconciliation, Double-Entry](/post/fundamentals/sd-payment-system/)
