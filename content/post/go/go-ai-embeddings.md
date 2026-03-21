---
title: 'Lesson 5: Embedding and Vector Search — Semantic search in Go without Python'
author: Atharva Pandey
series: "Go and AI/LLM Integration"
lesson: 5
keywords:
  - Go
  - golang
  - embeddings
  - vector search
  - semantic search
  - RAG
  - pgvector
  - AI
tags:
  - Go tutorial
  - golang
  - AI
  - LLM
  - MCP
date: '2025-05-18T00:00:00.000Z'
---

For a long time, embedding-based semantic search felt like Python territory. The tutorials all pointed to LangChain, FAISS, and numpy. But the actual operations — generate an embedding vector, store it in a database, query for nearest neighbors — map directly onto Go's strengths: clean HTTP client code for the embedding API, `pgx` for PostgreSQL with pgvector, and fast concurrent query pipelines. I've built production semantic search systems entirely in Go and they're fast, maintainable, and don't require a Python sidecar.

## The Problem

Keyword search fails on semantic similarity. A user searching for "I can't log in" won't match a support article titled "Authentication troubleshooting guide" because the words don't overlap. Embedding-based search finds semantically similar content regardless of exact word match.

```go
// WRONG — keyword search misses semantically related content
func searchDocs(db *sql.DB, query string) ([]Doc, error) {
    // This matches "login" and "log in" but not "authentication" or "sign in"
    rows, err := db.Query(`
        SELECT id, title, content
        FROM docs
        WHERE to_tsvector('english', content) @@ plainto_tsquery('english', $1)
        LIMIT 10
    `, query)
    // ... scan rows
}
```

The query "password reset not working" won't match an article about "credential recovery" even though they describe the same thing.

## The Idiomatic Way

The pipeline has three phases: (1) embed documents and store vectors on ingest, (2) embed the query at search time, (3) find nearest neighbors by cosine similarity.

**Generating embeddings via the OpenAI API:**

```go
// embed/openai.go
package embed

import (
    "bytes"
    "context"
    "encoding/json"
    "fmt"
    "net/http"
)

const embeddingModel = "text-embedding-3-small" // 1536 dimensions, fast and cheap

type Client struct {
    apiKey string
    http   *http.Client
}

func NewClient(apiKey string) *Client {
    return &Client{
        apiKey: apiKey,
        http:   &http.Client{Timeout: 30 * time.Second},
    }
}

type EmbedRequest struct {
    Input []string `json:"input"`
    Model string   `json:"model"`
}

type EmbedResponse struct {
    Data []struct {
        Embedding []float32 `json:"embedding"`
        Index     int       `json:"index"`
    } `json:"data"`
    Usage struct {
        TotalTokens int `json:"total_tokens"`
    } `json:"usage"`
}

func (c *Client) Embed(ctx context.Context, texts []string) ([][]float32, error) {
    body, _ := json.Marshal(EmbedRequest{
        Input: texts,
        Model: embeddingModel,
    })

    req, err := http.NewRequestWithContext(ctx, http.MethodPost,
        "https://api.openai.com/v1/embeddings", bytes.NewReader(body))
    if err != nil {
        return nil, err
    }
    req.Header.Set("Content-Type", "application/json")
    req.Header.Set("Authorization", "Bearer "+c.apiKey)

    resp, err := c.http.Do(req)
    if err != nil {
        return nil, fmt.Errorf("embed request: %w", err)
    }
    defer resp.Body.Close()

    if resp.StatusCode != http.StatusOK {
        return nil, fmt.Errorf("embed API error: %d", resp.StatusCode)
    }

    var result EmbedResponse
    if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
        return nil, fmt.Errorf("decode embed response: %w", err)
    }

    // Return embeddings in input order
    embeddings := make([][]float32, len(result.Data))
    for _, d := range result.Data {
        embeddings[d.Index] = d.Embedding
    }
    return embeddings, nil
}
```

**Storing and searching with PostgreSQL + pgvector:**

```sql
-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Documents table with embedding column
CREATE TABLE docs (
    id          BIGSERIAL PRIMARY KEY,
    title       TEXT NOT NULL,
    content     TEXT NOT NULL,
    embedding   vector(1536),  -- matches text-embedding-3-small dimensions
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- IVFFlat index for fast approximate nearest-neighbor search
-- lists = sqrt(number of rows) is a good starting point
CREATE INDEX docs_embedding_idx ON docs USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
```

```go
// store/doc_store.go
package store

import (
    "context"
    "github.com/jackc/pgx/v5"
    "github.com/pgvector/pgvector-go"
)

type DocStore struct {
    pool *pgxpool.Pool
}

func (s *DocStore) Insert(ctx context.Context, title, content string, embedding []float32) (int64, error) {
    var id int64
    err := s.pool.QueryRow(ctx, `
        INSERT INTO docs (title, content, embedding)
        VALUES ($1, $2, $3)
        RETURNING id
    `, title, content, pgvector.NewVector(embedding)).Scan(&id)
    return id, err
}

type SearchResult struct {
    ID         int64
    Title      string
    Content    string
    Similarity float64
}

func (s *DocStore) Search(ctx context.Context, queryEmbedding []float32, limit int) ([]SearchResult, error) {
    rows, err := s.pool.Query(ctx, `
        SELECT id, title, content,
               1 - (embedding <=> $1) AS similarity
        FROM docs
        ORDER BY embedding <=> $1  -- cosine distance operator
        LIMIT $2
    `, pgvector.NewVector(queryEmbedding), limit)
    if err != nil {
        return nil, fmt.Errorf("vector search: %w", err)
    }
    defer rows.Close()

    var results []SearchResult
    for rows.Next() {
        var r SearchResult
        if err := rows.Scan(&r.ID, &r.Title, &r.Content, &r.Similarity); err != nil {
            return nil, err
        }
        results = append(results, r)
    }
    return results, rows.Err()
}
```

**Putting it together in a search handler:**

```go
// Semantic search handler — embed the query, find nearest documents
func (h *Handler) Search(w http.ResponseWriter, r *http.Request) {
    query := r.URL.Query().Get("q")
    if query == "" {
        http.Error(w, "q required", http.StatusBadRequest)
        return
    }

    // Embed the query using the same model as the documents
    embeddings, err := h.embedder.Embed(r.Context(), []string{query})
    if err != nil {
        http.Error(w, "embed query failed", http.StatusInternalServerError)
        return
    }

    results, err := h.store.Search(r.Context(), embeddings[0], 10)
    if err != nil {
        http.Error(w, "search failed", http.StatusInternalServerError)
        return
    }

    // Filter by similarity threshold — results below 0.7 are usually not relevant
    var relevant []SearchResult
    for _, r := range results {
        if r.Similarity >= 0.70 {
            relevant = append(relevant, r)
        }
    }

    json.NewEncoder(w).Encode(relevant)
}
```

## In The Wild

I built a semantic search system for a 50,000-article technical knowledge base. The previous keyword search handled about 60% of user queries successfully (the user found what they were looking for). After adding embedding-based search, that rose to 82%.

The performance concern was real: embedding the query adds a round trip to the OpenAI API before every search. We addressed this with a two-level cache: Redis for exact query text matches (many users ask identical questions), and an in-memory LRU for the current process instance. P99 latency including the cache miss path was 180ms — acceptable for a search interface.

## The Gotchas

**Embed with the same model you indexed with.** Embeddings from different models live in different vector spaces. If you switch embedding models, you must re-embed all documents — they can't be mixed. Store the model name alongside each embedding in your database.

**Batch your embedding calls.** Most embedding APIs support batching multiple texts in a single request. Embedding 100 documents one at a time costs 100 API calls and 100x the latency. Batch them — OpenAI allows up to 2048 inputs per request.

**pgvector's IVFFlat index needs `SET ivfflat.probes`** for recall tuning. Higher probe count means better recall but slower search. Start with `probes = 10` (the default is 1), measure recall against exact search, and tune from there.

**Similarity scores are not probabilities.** A cosine similarity of 0.72 doesn't mean "72% relevant." The right threshold varies by embedding model and domain. Measure it empirically using a labeled evaluation set.

## Key Takeaway

Semantic search in Go requires an embedding API client, a vector database (pgvector on PostgreSQL works beautifully), and a search handler that embeds the query and finds nearest neighbors. Use the same embedding model for indexing and querying. Batch your embedding calls. Add a similarity threshold to filter low-confidence results. Cache embeddings of common queries. The whole stack runs in Go without Python, without a separate vector database service, and performs well under production load.

---

[← Lesson 4: Tool Calling Patterns](/post/go/go-ai-tool-calling) | [Course Index](/series/go-and-ai-llm-integration) | [Next → Lesson 6: Agent Architectures](/post/go/go-ai-agent-architectures)
