---
title: "Lesson 1: Building LLM API Clients in Rust — Type-safe AI calls"
author: Atharva Pandey
series: "Rust and AI/LLM Integration"
lesson: 1
keywords: [Rust, rust, AI, LLM, OpenAI, API client, type-safe, reqwest, serde]
tags: [Rust tutorial, rust, ai, llm]
date: '2025-08-12T09:14:00.000Z'
---

Last month I watched a coworker's Python script silently swallow a malformed response from the OpenAI API. The `choices` field came back empty, the code plowed ahead with `choices[0]`, and the whole pipeline crashed at 2 AM. Nobody got paged because the error handler was also broken. Classic.

That's the moment I decided to rebuild our LLM integration layer in Rust. Not because I'm some Rust evangelist who thinks Python is evil — I use Python daily. But when you're making API calls that cost real money and feed into production systems, maybe you want a type system that actually catches things before runtime.

## Why Rust for LLM API Clients?

Here's the thing — most LLM API wrappers are thin HTTP layers. You serialize some JSON, fire off a POST, deserialize the response. Easy. But the devil's in the details:

- API responses have nested, polymorphic structures (tool calls, function results, content blocks)
- Token counting matters when you're paying per token
- Rate limits require retry logic that doesn't double-spend
- Streaming responses need careful buffer management

Rust's type system catches entire categories of bugs that dynamic languages let slip through. And `serde` makes JSON handling genuinely pleasant — sometimes more pleasant than Python's `dict` wrangling, honestly.

## Setting Up

Let's build a proper OpenAI-compatible client. This works with OpenAI, Azure OpenAI, Anthropic-compatible endpoints, and any provider that follows the chat completions spec.

```toml
[package]
name = "llm-client"
version = "0.1.0"
edition = "2021"

[dependencies]
reqwest = { version = "0.12", features = ["json", "rustls-tls"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
tokio = { version = "1", features = ["full"] }
secrecy = "0.10"
thiserror = "2"
```

I'm using `secrecy` here because API keys should never accidentally show up in logs. You'd be shocked how often I've seen keys dumped to stdout in debug output.

## Modeling the API Types

This is where Rust really shines. Let's define the request and response types properly:

```rust
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize)]
pub struct ChatRequest {
    pub model: String,
    pub messages: Vec<Message>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub temperature: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_tokens: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tools: Option<Vec<Tool>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Message {
    pub role: Role,
    pub content: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_calls: Option<Vec<ToolCall>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_call_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Role {
    System,
    User,
    Assistant,
    Tool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCall {
    pub id: String,
    #[serde(rename = "type")]
    pub call_type: String,
    pub function: FunctionCall,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FunctionCall {
    pub name: String,
    pub arguments: String, // JSON string — yes, it's a string within JSON
}

#[derive(Debug, Clone, Serialize)]
pub struct Tool {
    #[serde(rename = "type")]
    pub tool_type: String,
    pub function: FunctionDef,
}

#[derive(Debug, Clone, Serialize)]
pub struct FunctionDef {
    pub name: String,
    pub description: String,
    pub parameters: serde_json::Value,
}
```

Notice `arguments` in `FunctionCall` is a `String`, not a `serde_json::Value`. That's because the API literally sends a JSON string inside JSON. It's ugly, but modeling it accurately means we handle it correctly instead of pretending it's something it's not.

## The Response Types

```rust
#[derive(Debug, Deserialize)]
pub struct ChatResponse {
    pub id: String,
    pub choices: Vec<Choice>,
    pub usage: Usage,
    pub model: String,
}

#[derive(Debug, Deserialize)]
pub struct Choice {
    pub index: u32,
    pub message: Message,
    pub finish_reason: Option<FinishReason>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FinishReason {
    Stop,
    Length,
    ToolCalls,
    ContentFilter,
}

#[derive(Debug, Deserialize)]
pub struct Usage {
    pub prompt_tokens: u32,
    pub completion_tokens: u32,
    pub total_tokens: u32,
}
```

`FinishReason` as an enum is a small thing, but it means I can `match` on it exhaustively. If OpenAI adds a new finish reason tomorrow, my code won't silently ignore it — the compiler will tell me about the unhandled variant. That's the kind of safety net you don't get in Python.

## Building the Client

```rust
use reqwest::Client;
use secrecy::{ExposeSecret, SecretString};
use thiserror::Error;
use std::time::Duration;

#[derive(Error, Debug)]
pub enum LlmError {
    #[error("HTTP error: {0}")]
    Http(#[from] reqwest::Error),

    #[error("API error ({status}): {message}")]
    Api { status: u16, message: String },

    #[error("Rate limited — retry after {retry_after_ms}ms")]
    RateLimited { retry_after_ms: u64 },

    #[error("Empty response — no choices returned")]
    EmptyResponse,

    #[error("Deserialization failed: {0}")]
    Deserialize(String),
}

pub struct LlmClient {
    http: Client,
    api_key: SecretString,
    base_url: String,
    default_model: String,
}

impl LlmClient {
    pub fn new(
        api_key: impl Into<String>,
        base_url: impl Into<String>,
        default_model: impl Into<String>,
    ) -> Self {
        let http = Client::builder()
            .timeout(Duration::from_secs(120))
            .build()
            .expect("Failed to build HTTP client");

        Self {
            http,
            api_key: SecretString::from(api_key.into()),
            base_url: base_url.into(),
            default_model: default_model.into(),
        }
    }

    pub fn openai(api_key: impl Into<String>) -> Self {
        Self::new(
            api_key,
            "https://api.openai.com/v1",
            "gpt-4o",
        )
    }

    pub async fn chat(&self, request: &ChatRequest) -> Result<ChatResponse, LlmError> {
        let url = format!("{}/chat/completions", self.base_url);

        let response = self
            .http
            .post(&url)
            .header("Authorization", format!("Bearer {}", self.api_key.expose_secret()))
            .json(request)
            .send()
            .await?;

        let status = response.status();

        if status == reqwest::StatusCode::TOO_MANY_REQUESTS {
            let retry_after = response
                .headers()
                .get("retry-after")
                .and_then(|v| v.to_str().ok())
                .and_then(|v| v.parse::<u64>().ok())
                .unwrap_or(1000);

            return Err(LlmError::RateLimited {
                retry_after_ms: retry_after * 1000,
            });
        }

        if !status.is_success() {
            let body = response.text().await.unwrap_or_default();
            return Err(LlmError::Api {
                status: status.as_u16(),
                message: body,
            });
        }

        let body = response.text().await?;
        let parsed: ChatResponse = serde_json::from_str(&body)
            .map_err(|e| LlmError::Deserialize(format!("{e}: {body}")))?;

        if parsed.choices.is_empty() {
            return Err(LlmError::EmptyResponse);
        }

        Ok(parsed)
    }
}
```

A few things worth calling out here. First, I'm deserializing through `text()` instead of using reqwest's built-in `.json()` on the response. Why? Because when deserialization fails, I want to see the raw body in the error message. The number of hours I've wasted debugging "expected value at line 1 column 1" with no idea what the actual response was... never again.

Second, rate limiting is handled as a typed error. The caller decides what to do — maybe they retry, maybe they queue, maybe they switch to a different API key. The client doesn't make that decision.

## Adding Retry Logic

Speaking of retries, let's add a wrapper with exponential backoff:

```rust
impl LlmClient {
    pub async fn chat_with_retry(
        &self,
        request: &ChatRequest,
        max_retries: u32,
    ) -> Result<ChatResponse, LlmError> {
        let mut last_error = None;

        for attempt in 0..=max_retries {
            match self.chat(request).await {
                Ok(response) => return Ok(response),
                Err(LlmError::RateLimited { retry_after_ms }) => {
                    if attempt == max_retries {
                        return Err(LlmError::RateLimited { retry_after_ms });
                    }
                    let wait = std::cmp::max(retry_after_ms, 500);
                    eprintln!("Rate limited, waiting {wait}ms (attempt {attempt}/{max_retries})");
                    tokio::time::sleep(Duration::from_millis(wait)).await;
                }
                Err(LlmError::Http(ref e)) if e.is_timeout() || e.is_connect() => {
                    if attempt == max_retries {
                        return Err(LlmError::Http(
                            last_error.take().unwrap_or_else(|| {
                                reqwest::Error::from(e.clone())
                            })
                        ));
                    }
                    let backoff = 500 * 2u64.pow(attempt);
                    eprintln!("Connection error, retrying in {backoff}ms");
                    tokio::time::sleep(Duration::from_millis(backoff)).await;
                    last_error = Some(e.clone());
                }
                Err(e) => return Err(e),
            }
        }

        unreachable!()
    }
}
```

We only retry on rate limits and transient connection errors. API errors (400, 401, 422) fail immediately — retrying a bad request is just wasting money.

## A Convenient Builder

Nobody wants to construct `ChatRequest` structs by hand every time. Let's make it ergonomic:

```rust
pub struct ChatBuilder<'a> {
    client: &'a LlmClient,
    messages: Vec<Message>,
    model: Option<String>,
    temperature: Option<f32>,
    max_tokens: Option<u32>,
    tools: Option<Vec<Tool>>,
}

impl LlmClient {
    pub fn builder(&self) -> ChatBuilder<'_> {
        ChatBuilder {
            client: self,
            messages: Vec::new(),
            model: None,
            temperature: None,
            max_tokens: None,
            tools: None,
        }
    }
}

impl<'a> ChatBuilder<'a> {
    pub fn system(mut self, content: impl Into<String>) -> Self {
        self.messages.push(Message {
            role: Role::System,
            content: Some(content.into()),
            tool_calls: None,
            tool_call_id: None,
        });
        self
    }

    pub fn user(mut self, content: impl Into<String>) -> Self {
        self.messages.push(Message {
            role: Role::User,
            content: Some(content.into()),
            tool_calls: None,
            tool_call_id: None,
        });
        self
    }

    pub fn model(mut self, model: impl Into<String>) -> Self {
        self.model = Some(model.into());
        self
    }

    pub fn temperature(mut self, temp: f32) -> Self {
        self.temperature = Some(temp);
        self
    }

    pub fn max_tokens(mut self, tokens: u32) -> Self {
        self.max_tokens = Some(tokens);
        self
    }

    pub async fn send(self) -> Result<ChatResponse, LlmError> {
        let request = ChatRequest {
            model: self.model.unwrap_or_else(|| self.client.default_model.clone()),
            messages: self.messages,
            temperature: self.temperature,
            max_tokens: self.max_tokens,
            tools: self.tools,
        };
        self.client.chat(&request).await
    }
}
```

Now calling the API looks like this:

```rust
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let api_key = std::env::var("OPENAI_API_KEY")?;
    let client = LlmClient::openai(api_key);

    let response = client
        .builder()
        .system("You are a helpful assistant that speaks like a pirate.")
        .user("What's the capital of France?")
        .temperature(0.7)
        .max_tokens(200)
        .send()
        .await?;

    let reply = &response.choices[0].message.content;
    println!("Reply: {}", reply.as_deref().unwrap_or("(no content)"));
    println!(
        "Tokens used: {} prompt + {} completion = {} total",
        response.usage.prompt_tokens,
        response.usage.completion_tokens,
        response.usage.total_tokens
    );

    Ok(())
}
```

Clean, type-safe, and impossible to accidentally pass a number where a string should go.

## Tracking Token Usage

When you're making thousands of API calls a day, tracking costs matters. Here's a simple usage tracker:

```rust
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

#[derive(Debug, Clone)]
pub struct UsageTracker {
    prompt_tokens: Arc<AtomicU64>,
    completion_tokens: Arc<AtomicU64>,
    request_count: Arc<AtomicU64>,
}

impl UsageTracker {
    pub fn new() -> Self {
        Self {
            prompt_tokens: Arc::new(AtomicU64::new(0)),
            completion_tokens: Arc::new(AtomicU64::new(0)),
            request_count: Arc::new(AtomicU64::new(0)),
        }
    }

    pub fn record(&self, usage: &Usage) {
        self.prompt_tokens
            .fetch_add(usage.prompt_tokens as u64, Ordering::Relaxed);
        self.completion_tokens
            .fetch_add(usage.completion_tokens as u64, Ordering::Relaxed);
        self.request_count.fetch_add(1, Ordering::Relaxed);
    }

    pub fn summary(&self) -> UsageSummary {
        UsageSummary {
            prompt_tokens: self.prompt_tokens.load(Ordering::Relaxed),
            completion_tokens: self.completion_tokens.load(Ordering::Relaxed),
            request_count: self.request_count.load(Ordering::Relaxed),
        }
    }
}

#[derive(Debug)]
pub struct UsageSummary {
    pub prompt_tokens: u64,
    pub completion_tokens: u64,
    pub request_count: u64,
}

impl UsageSummary {
    pub fn estimated_cost_usd(&self, prompt_price_per_m: f64, completion_price_per_m: f64) -> f64 {
        (self.prompt_tokens as f64 / 1_000_000.0) * prompt_price_per_m
            + (self.completion_tokens as f64 / 1_000_000.0) * completion_price_per_m
    }
}
```

Using atomics instead of a mutex because we're just incrementing counters — no need for heavier synchronization. This tracker is `Clone` and thread-safe, so you can share it across concurrent tasks without any ceremony.

## Multi-Provider Support

Real systems rarely stick to one LLM provider. Let's make the client work with different backends:

```rust
pub enum Provider {
    OpenAI,
    AzureOpenAI { deployment: String, api_version: String },
    Custom { base_url: String },
}

impl LlmClient {
    pub fn from_provider(api_key: impl Into<String>, provider: Provider) -> Self {
        match provider {
            Provider::OpenAI => Self::openai(api_key),
            Provider::AzureOpenAI { deployment, api_version } => {
                // Azure uses a different URL pattern
                Self::new(
                    api_key,
                    format!(
                        "https://{deployment}.openai.azure.com/openai/deployments/{deployment}"
                    ),
                    format!("?api-version={api_version}"),
                )
            }
            Provider::Custom { base_url } => {
                Self::new(api_key, base_url, "default")
            }
        }
    }
}
```

This is deliberately simple. You could build an elaborate trait-based abstraction with `dyn Provider` and registration and all that — but honestly, the differences between providers are mostly just URL patterns and header names. A simple enum handles 90% of cases without the complexity tax.

## What's Next

We've built a solid foundation — type-safe request/response models, proper error handling, retry logic, usage tracking, and multi-provider support. This is the kind of client you can actually deploy to production without crossing your fingers.

In the next lesson, we'll tackle streaming responses. Because waiting for a 2000-token response to complete before showing anything to the user? That's a terrible experience. We'll handle SSE parsing, chunked responses, and backpressure — all the fun parts of working with streams in async Rust.
