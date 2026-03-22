---
title: "Lesson 4: Embeddings and Vector Search — Semantic search in Rust"
author: Atharva Pandey
series: "Rust and AI/LLM Integration"
lesson: 4
keywords: [Rust, rust, AI, LLM, embeddings, vector search, semantic search, cosine similarity, HNSW]
tags: [Rust tutorial, rust, ai, llm]
date: '2025-08-18T08:55:00.000Z'
---

I spent a week building a keyword search system for internal documentation. Regex patterns, stemming, tf-idf scoring — the whole nine yards. Then someone searched "how do I deploy" and got zero results because every doc said "deployment process" instead of "deploy." That's when I switched to embeddings.

Embeddings map text into high-dimensional vectors where semantically similar content lives close together. "Deploy" and "deployment process" end up near each other in vector space even though they share almost no characters. It's a fundamentally different approach to search, and once you've used it, keyword search feels like the dark ages.

## What Embeddings Actually Are

An embedding model takes text and produces a fixed-length array of floats. OpenAI's `text-embedding-3-small` produces 1536-dimensional vectors. Each dimension captures some aspect of meaning — though individual dimensions aren't interpretable by humans.

The magic is in distance. Two texts about similar topics produce vectors that are close together (high cosine similarity). Two texts about different topics produce distant vectors. This means "search" becomes "find the nearest vectors to my query vector."

## Getting Embeddings From the API

Let's extend our client from Lesson 1:

```rust
use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize)]
pub struct EmbeddingRequest {
    pub model: String,
    pub input: Vec<String>,
}

#[derive(Debug, Deserialize)]
pub struct EmbeddingResponse {
    pub data: Vec<EmbeddingData>,
    pub usage: EmbeddingUsage,
}

#[derive(Debug, Deserialize)]
pub struct EmbeddingData {
    pub embedding: Vec<f32>,
    pub index: usize,
}

#[derive(Debug, Deserialize)]
pub struct EmbeddingUsage {
    pub prompt_tokens: u32,
    pub total_tokens: u32,
}

impl LlmClient {
    pub async fn embed(&self, texts: &[&str]) -> Result<Vec<Vec<f32>>, LlmError> {
        let url = format!("{}/embeddings", self.base_url);

        let request = EmbeddingRequest {
            model: "text-embedding-3-small".to_string(),
            input: texts.iter().map(|s| s.to_string()).collect(),
        };

        let response = self
            .http
            .post(&url)
            .header(
                "Authorization",
                format!("Bearer {}", self.api_key.expose_secret()),
            )
            .json(&request)
            .send()
            .await?;

        if !response.status().is_success() {
            let body = response.text().await.unwrap_or_default();
            return Err(LlmError::Api {
                status: response.status().as_u16(),
                message: body,
            });
        }

        let body: EmbeddingResponse = response.json().await?;
        let mut embeddings: Vec<_> = body.data.into_iter().collect();
        embeddings.sort_by_key(|d| d.index);

        Ok(embeddings.into_iter().map(|d| d.embedding).collect())
    }
}
```

I sort by `index` because the API doesn't guarantee ordering. Found that out the hard way when batch embeddings came back shuffled and my search results were nonsensically matched to the wrong documents.

## Cosine Similarity

The standard similarity metric for embeddings is cosine similarity. It measures the angle between two vectors, ignoring magnitude:

```rust
pub fn cosine_similarity(a: &[f32], b: &[f32]) -> f32 {
    assert_eq!(a.len(), b.len(), "Vectors must have same dimension");

    let mut dot = 0.0f32;
    let mut norm_a = 0.0f32;
    let mut norm_b = 0.0f32;

    for i in 0..a.len() {
        dot += a[i] * b[i];
        norm_a += a[i] * a[i];
        norm_b += b[i] * b[i];
    }

    let denom = norm_a.sqrt() * norm_b.sqrt();
    if denom == 0.0 {
        return 0.0;
    }

    dot / denom
}
```

This works, but it's slow for large datasets. Each comparison is O(d) where d is the embedding dimension. With 1536 dimensions and a million documents, brute-force search is painful.

Let's optimize with SIMD-friendly iteration:

```rust
pub fn cosine_similarity_fast(a: &[f32], b: &[f32]) -> f32 {
    debug_assert_eq!(a.len(), b.len());

    // Process in chunks of 8 for better auto-vectorization
    let chunks = a.len() / 8;
    let mut dot = [0.0f32; 8];
    let mut na = [0.0f32; 8];
    let mut nb = [0.0f32; 8];

    for i in 0..chunks {
        let base = i * 8;
        for j in 0..8 {
            let ai = a[base + j];
            let bi = b[base + j];
            dot[j] += ai * bi;
            na[j] += ai * ai;
            nb[j] += bi * bi;
        }
    }

    let mut total_dot: f32 = dot.iter().sum();
    let mut total_na: f32 = na.iter().sum();
    let mut total_nb: f32 = nb.iter().sum();

    // Handle remaining elements
    for i in (chunks * 8)..a.len() {
        total_dot += a[i] * b[i];
        total_na += a[i] * a[i];
        total_nb += b[i] * b[i];
    }

    let denom = total_na.sqrt() * total_nb.sqrt();
    if denom == 0.0 {
        0.0
    } else {
        total_dot / denom
    }
}
```

The chunk-of-8 trick helps the compiler auto-vectorize the inner loop into SIMD instructions. On my machine, this is about 3x faster for 1536-dimensional vectors.

## Building a Vector Store

For anything beyond a toy project, you need an actual index. Let's build one using HNSW (Hierarchical Navigable Small World) — the algorithm behind most production vector databases:

```toml
[dependencies]
instant-distance = "0.6"
uuid = { version = "1", features = ["v4"] }
parking_lot = "0.12"
```

```rust
use instant_distance::{Builder, HnswMap, Search};
use parking_lot::RwLock;
use std::sync::Arc;
use uuid::Uuid;

#[derive(Debug, Clone)]
pub struct Document {
    pub id: String,
    pub text: String,
    pub metadata: serde_json::Value,
    pub embedding: Vec<f32>,
}

/// A point wrapper for instant-distance
#[derive(Clone)]
struct EmbeddingPoint(Vec<f32>);

impl instant_distance::Point for EmbeddingPoint {
    fn distance(&self, other: &Self) -> f32 {
        // instant-distance expects distance, not similarity
        // cosine distance = 1 - cosine similarity
        1.0 - cosine_similarity_fast(&self.0, &other.0)
    }
}

pub struct VectorStore {
    documents: Arc<RwLock<Vec<Document>>>,
    index: Arc<RwLock<Option<HnswMap<EmbeddingPoint, usize>>>>,
    dimension: usize,
}

impl VectorStore {
    pub fn new(dimension: usize) -> Self {
        Self {
            documents: Arc::new(RwLock::new(Vec::new())),
            index: Arc::new(RwLock::new(None)),
            dimension,
        }
    }

    pub fn add_documents(&self, docs: Vec<Document>) {
        let mut documents = self.documents.write();
        documents.extend(docs);
        // Rebuild index
        self.rebuild_index(&documents);
    }

    fn rebuild_index(&self, documents: &[Document]) {
        if documents.is_empty() {
            *self.index.write() = None;
            return;
        }

        let points: Vec<EmbeddingPoint> = documents
            .iter()
            .map(|d| EmbeddingPoint(d.embedding.clone()))
            .collect();

        let values: Vec<usize> = (0..documents.len()).collect();

        let hnsw = Builder::default().build(points, values);
        *self.index.write() = Some(hnsw);
    }

    pub fn search(&self, query_embedding: &[f32], top_k: usize) -> Vec<SearchResult> {
        let index = self.index.read();
        let index = match index.as_ref() {
            Some(idx) => idx,
            None => return Vec::new(),
        };

        let documents = self.documents.read();
        let query_point = EmbeddingPoint(query_embedding.to_vec());
        let mut search = Search::default();

        let neighbors = index.search(&query_point, &mut search);

        neighbors
            .take(top_k)
            .map(|item| {
                let doc_idx = *item.value;
                let doc = &documents[doc_idx];
                SearchResult {
                    document: doc.clone(),
                    score: 1.0 - item.distance, // Convert back to similarity
                }
            })
            .collect()
    }
}

#[derive(Debug, Clone)]
pub struct SearchResult {
    pub document: Document,
    pub score: f32,
}
```

HNSW gives us approximate nearest neighbor search in O(log n) time instead of O(n). The trade-off is a small chance of missing the actual nearest neighbor — but for semantic search, "close enough" is literally the point.

## Chunking Text for Embeddings

You can't just embed an entire document. Embedding models have token limits (8191 for `text-embedding-3-small`), and longer texts produce diluted embeddings that don't match specific queries well. You need to chunk:

```rust
pub struct TextChunker {
    chunk_size: usize,    // in characters (rough proxy for tokens)
    chunk_overlap: usize, // overlap between chunks
}

impl TextChunker {
    pub fn new(chunk_size: usize, chunk_overlap: usize) -> Self {
        Self {
            chunk_size,
            chunk_overlap,
        }
    }

    pub fn chunk(&self, text: &str) -> Vec<TextChunk> {
        let mut chunks = Vec::new();
        let sentences = self.split_sentences(text);
        let mut current_chunk = String::new();
        let mut chunk_start = 0;

        for sentence in &sentences {
            if current_chunk.len() + sentence.len() > self.chunk_size
                && !current_chunk.is_empty()
            {
                chunks.push(TextChunk {
                    text: current_chunk.trim().to_string(),
                    start_char: chunk_start,
                    end_char: chunk_start + current_chunk.len(),
                });

                // Keep overlap
                let overlap_start = current_chunk
                    .len()
                    .saturating_sub(self.chunk_overlap);
                current_chunk = current_chunk[overlap_start..].to_string();
                chunk_start += overlap_start;
            }

            current_chunk.push_str(sentence);
        }

        if !current_chunk.trim().is_empty() {
            chunks.push(TextChunk {
                text: current_chunk.trim().to_string(),
                start_char: chunk_start,
                end_char: chunk_start + current_chunk.len(),
            });
        }

        chunks
    }

    fn split_sentences(&self, text: &str) -> Vec<String> {
        let mut sentences = Vec::new();
        let mut current = String::new();

        for ch in text.chars() {
            current.push(ch);
            if matches!(ch, '.' | '!' | '?') {
                sentences.push(std::mem::take(&mut current));
            }
        }

        if !current.is_empty() {
            sentences.push(current);
        }

        sentences
    }
}

#[derive(Debug, Clone)]
pub struct TextChunk {
    pub text: String,
    pub start_char: usize,
    pub end_char: usize,
}
```

The overlap is important. Without it, a query about a concept that spans a chunk boundary won't match either chunk well. 200 characters of overlap works well in practice.

## Building a RAG Pipeline

Now let's combine everything into a Retrieval-Augmented Generation pipeline:

```rust
pub struct RagPipeline {
    client: LlmClient,
    store: VectorStore,
    chunker: TextChunker,
}

impl RagPipeline {
    pub fn new(client: LlmClient) -> Self {
        Self {
            client,
            store: VectorStore::new(1536),
            chunker: TextChunker::new(1500, 200),
        }
    }

    /// Ingest a document into the vector store
    pub async fn ingest(
        &self,
        source_id: &str,
        text: &str,
        metadata: serde_json::Value,
    ) -> Result<usize, LlmError> {
        let chunks = self.chunker.chunk(text);
        let chunk_texts: Vec<&str> = chunks.iter().map(|c| c.text.as_str()).collect();

        // Batch embed — API handles up to ~2048 inputs
        let embeddings = self.client.embed(&chunk_texts).await?;

        let documents: Vec<Document> = chunks
            .into_iter()
            .zip(embeddings)
            .enumerate()
            .map(|(i, (chunk, embedding))| Document {
                id: format!("{source_id}#chunk{i}"),
                text: chunk.text,
                metadata: serde_json::json!({
                    "source": source_id,
                    "chunk_index": i,
                    "start_char": chunk.start_char,
                    "end_char": chunk.end_char,
                    "original_metadata": metadata.clone(),
                }),
                embedding,
            })
            .collect();

        let count = documents.len();
        self.store.add_documents(documents);
        Ok(count)
    }

    /// Query the RAG pipeline
    pub async fn query(
        &self,
        question: &str,
        top_k: usize,
    ) -> Result<RagResponse, LlmError> {
        // Embed the question
        let query_embeddings = self.client.embed(&[question]).await?;
        let query_embedding = &query_embeddings[0];

        // Search for relevant chunks
        let results = self.store.search(query_embedding, top_k);

        // Build context from retrieved chunks
        let context: String = results
            .iter()
            .enumerate()
            .map(|(i, r)| {
                format!(
                    "[Source {}] (relevance: {:.2})\n{}\n",
                    i + 1,
                    r.score,
                    r.document.text
                )
            })
            .collect::<Vec<_>>()
            .join("\n---\n\n");

        // Generate answer with context
        let system_prompt = format!(
            "Answer the user's question based on the following context. \
             If the context doesn't contain enough information, say so. \
             Cite sources using [Source N] notation.\n\n\
             Context:\n{context}"
        );

        let response = self
            .client
            .builder()
            .system(&system_prompt)
            .user(question)
            .temperature(0.1)
            .max_tokens(1000)
            .send()
            .await?;

        let answer = response.choices[0]
            .message
            .content
            .clone()
            .unwrap_or_default();

        Ok(RagResponse {
            answer,
            sources: results,
            tokens_used: response.usage.total_tokens,
        })
    }
}

#[derive(Debug)]
pub struct RagResponse {
    pub answer: String,
    pub sources: Vec<SearchResult>,
    pub tokens_used: u32,
}
```

## Using the RAG Pipeline

```rust
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let client = LlmClient::openai(std::env::var("OPENAI_API_KEY")?);
    let rag = RagPipeline::new(client);

    // Ingest some documents
    let docs = vec![
        ("doc1", "Rust's ownership system ensures memory safety without garbage collection. Each value has a single owner, and when that owner goes out of scope, the value is dropped."),
        ("doc2", "The borrow checker enforces rules at compile time: you can have either one mutable reference or any number of immutable references, but not both."),
        ("doc3", "Lifetimes in Rust are annotations that tell the compiler how long references are valid. They prevent dangling references at compile time."),
    ];

    for (id, text) in docs {
        let chunks = rag
            .ingest(id, text, serde_json::json!({"type": "tutorial"}))
            .await?;
        println!("Ingested {id}: {chunks} chunks");
    }

    // Query
    let response = rag
        .query("How does Rust prevent memory bugs?", 3)
        .await?;

    println!("\nAnswer: {}", response.answer);
    println!("\nSources used: {}", response.sources.len());
    for (i, source) in response.sources.iter().enumerate() {
        println!("  [{}] score={:.3}: {}...", i + 1, source.score, &source.document.text[..50]);
    }

    Ok(())
}
```

## Persistence

An in-memory vector store is fine for prototyping but useless in production. Here's a simple file-based persistence layer:

```rust
use std::fs;
use std::path::Path;

impl VectorStore {
    pub fn save(&self, path: &Path) -> Result<(), std::io::Error> {
        let documents = self.documents.read();
        let serialized = serde_json::to_vec(&*documents)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e))?;
        fs::write(path, serialized)?;
        Ok(())
    }

    pub fn load(path: &Path, dimension: usize) -> Result<Self, std::io::Error> {
        let data = fs::read(path)?;
        let documents: Vec<Document> = serde_json::from_slice(&data)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e))?;

        let store = Self::new(dimension);
        store.add_documents(documents);
        Ok(store)
    }
}
```

For production, you'd use something like Qdrant, Milvus, or pgvector. But having a pure-Rust solution that works offline with no external dependencies? That's surprisingly useful for CLI tools and embedded applications.

## What's Next

We've got embeddings, vector search, and a complete RAG pipeline. Next up: agent architectures. We'll build ReAct-style agents that can reason, plan, and execute multi-step tasks — combining the tool calling from Lesson 3 with the retrieval from this lesson into something that actually feels intelligent.
