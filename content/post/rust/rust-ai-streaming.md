---
title: "Lesson 2: Streaming LLM Responses — SSE and WebSockets"
author: Atharva Pandey
series: "Rust and AI/LLM Integration"
lesson: 2
keywords: [Rust, rust, AI, LLM, streaming, SSE, WebSocket, async, tokio, eventsource]
tags: [Rust tutorial, rust, ai, llm]
date: '2025-08-14T11:37:00.000Z'
---

The first time I demoed an LLM-powered feature to stakeholders, I made the rookie mistake of using non-streaming responses. The CEO asked a question, hit enter, and stared at a blank screen for eight seconds. "Is it broken?" No — it was thinking. But by the time the response appeared, she'd already mentally moved on to the next agenda item.

Streaming changes everything. Users see tokens appearing in real-time, which feels responsive even when the total generation time is identical. But implementing streaming in Rust? It's one of those things that's surprisingly nuanced once you get past the happy path.

## How LLM Streaming Actually Works

Most LLM APIs use Server-Sent Events (SSE) for streaming. It's an HTTP/1.1 standard where the server keeps the connection open and sends events line by line. Each event looks like this:

```
data: {"id":"chatcmpl-abc","choices":[{"delta":{"content":"Hello"}}]}

data: {"id":"chatcmpl-abc","choices":[{"delta":{"content":" world"}}]}

data: [DONE]
```

Key things to notice: each chunk uses `delta` instead of `message` (it's an incremental update, not the full response), content arrives token by token, and the stream ends with `data: [DONE]`.

## The Streaming Response Types

Let's model these delta types. They're subtly different from the non-streaming types we built in Lesson 1:

```rust
use serde::Deserialize;

#[derive(Debug, Deserialize)]
pub struct StreamChunk {
    pub id: String,
    pub choices: Vec<StreamChoice>,
    #[serde(default)]
    pub usage: Option<Usage>,
    pub model: String,
}

#[derive(Debug, Deserialize)]
pub struct StreamChoice {
    pub index: u32,
    pub delta: Delta,
    pub finish_reason: Option<FinishReason>,
}

#[derive(Debug, Deserialize)]
pub struct Delta {
    #[serde(default)]
    pub role: Option<Role>,
    #[serde(default)]
    pub content: Option<String>,
    #[serde(default)]
    pub tool_calls: Option<Vec<ToolCallDelta>>,
}

#[derive(Debug, Deserialize)]
pub struct ToolCallDelta {
    pub index: u32,
    #[serde(default)]
    pub id: Option<String>,
    #[serde(default)]
    pub function: Option<FunctionCallDelta>,
}

#[derive(Debug, Deserialize)]
pub struct FunctionCallDelta {
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub arguments: Option<String>,
}
```

Everything is optional in deltas. The first chunk might only have `role: "assistant"`. Subsequent chunks have `content`. Tool call arguments arrive in tiny fragments. You have to accumulate them yourself.

## Parsing the SSE Stream

Here's where it gets interesting. We need to parse the raw SSE format from the HTTP response body:

```rust
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio_stream::{Stream, StreamExt};
use futures::stream;
use std::pin::Pin;

impl LlmClient {
    pub async fn chat_stream(
        &self,
        request: &ChatRequest,
    ) -> Result<Pin<Box<dyn Stream<Item = Result<StreamChunk, LlmError>> + Send>>, LlmError> {
        let mut req = request.clone();
        // The API requires this flag for streaming
        let url = format!("{}/chat/completions", self.base_url);

        let response = self
            .http
            .post(&url)
            .header(
                "Authorization",
                format!("Bearer {}", self.api_key.expose_secret()),
            )
            .json(&serde_json::json!({
                "model": req.model,
                "messages": req.messages,
                "temperature": req.temperature,
                "max_tokens": req.max_tokens,
                "tools": req.tools,
                "stream": true,
            }))
            .send()
            .await?;

        if !response.status().is_success() {
            let body = response.text().await.unwrap_or_default();
            return Err(LlmError::Api {
                status: response.status().as_u16(),
                message: body,
            });
        }

        let byte_stream = response.bytes_stream();
        let stream = parse_sse_stream(byte_stream);

        Ok(Box::pin(stream))
    }
}

fn parse_sse_stream<S>(byte_stream: S) -> impl Stream<Item = Result<StreamChunk, LlmError>>
where
    S: Stream<Item = Result<bytes::Bytes, reqwest::Error>> + Send + 'static,
{
    async_stream::stream! {
        let mut buffer = String::new();

        tokio::pin!(byte_stream);

        while let Some(chunk_result) = byte_stream.next().await {
            let chunk = match chunk_result {
                Ok(c) => c,
                Err(e) => {
                    yield Err(LlmError::Http(e));
                    return;
                }
            };

            buffer.push_str(&String::from_utf8_lossy(&chunk));

            // Process complete lines
            while let Some(newline_pos) = buffer.find('\n') {
                let line = buffer[..newline_pos].trim().to_string();
                buffer = buffer[newline_pos + 1..].to_string();

                if line.is_empty() {
                    continue;
                }

                if let Some(data) = line.strip_prefix("data: ") {
                    if data.trim() == "[DONE]" {
                        return;
                    }

                    match serde_json::from_str::<StreamChunk>(data) {
                        Ok(chunk) => yield Ok(chunk),
                        Err(e) => {
                            yield Err(LlmError::Deserialize(
                                format!("Failed to parse chunk: {e}\nRaw: {data}")
                            ));
                            return;
                        }
                    }
                }
            }
        }
    }
}
```

Add `async-stream` and `futures` to your dependencies:

```toml
[dependencies]
async-stream = "0.3"
futures = "0.3"
tokio-stream = "0.1"
bytes = "1"
```

The buffer management here is important. Network chunks don't align with SSE event boundaries — you might get half a line in one chunk and the rest in the next. The buffer accumulates bytes until we find complete newline-delimited lines.

## Accumulating the Full Response

Streaming is great for display, but you usually also need the complete response for downstream processing. Here's an accumulator that collects stream chunks into a final message:

```rust
#[derive(Debug, Default)]
pub struct StreamAccumulator {
    pub content: String,
    pub tool_calls: Vec<AccumulatedToolCall>,
    pub finish_reason: Option<FinishReason>,
    pub model: String,
    total_chunks: u32,
}

#[derive(Debug, Default, Clone)]
pub struct AccumulatedToolCall {
    pub id: String,
    pub function_name: String,
    pub arguments: String,
}

impl StreamAccumulator {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn process_chunk(&mut self, chunk: &StreamChunk) {
        self.total_chunks += 1;
        if self.model.is_empty() {
            self.model = chunk.model.clone();
        }

        for choice in &chunk.choices {
            // Accumulate content
            if let Some(ref content) = choice.delta.content {
                self.content.push_str(content);
            }

            // Accumulate tool calls
            if let Some(ref tool_calls) = choice.delta.tool_calls {
                for tc_delta in tool_calls {
                    let idx = tc_delta.index as usize;

                    // Ensure we have enough slots
                    while self.tool_calls.len() <= idx {
                        self.tool_calls.push(AccumulatedToolCall::default());
                    }

                    if let Some(ref id) = tc_delta.id {
                        self.tool_calls[idx].id = id.clone();
                    }
                    if let Some(ref func) = tc_delta.function {
                        if let Some(ref name) = func.name {
                            self.tool_calls[idx].function_name = name.clone();
                        }
                        if let Some(ref args) = func.arguments {
                            self.tool_calls[idx].arguments.push_str(args);
                        }
                    }
                }
            }

            if let Some(ref reason) = choice.finish_reason {
                self.finish_reason = Some(reason.clone());
            }
        }
    }

    pub fn into_message(self) -> Message {
        let tool_calls = if self.tool_calls.is_empty() {
            None
        } else {
            Some(
                self.tool_calls
                    .into_iter()
                    .map(|tc| ToolCall {
                        id: tc.id,
                        call_type: "function".to_string(),
                        function: FunctionCall {
                            name: tc.function_name,
                            arguments: tc.arguments,
                        },
                    })
                    .collect(),
            )
        };

        Message {
            role: Role::Assistant,
            content: if self.content.is_empty() {
                None
            } else {
                Some(self.content)
            },
            tool_calls,
            tool_call_id: None,
        }
    }
}
```

The tool call accumulation is the tricky part. Arguments arrive in tiny fragments — sometimes just a single character — spread across many chunks. The `index` field on each delta tells you which tool call the fragment belongs to. Getting this wrong means your JSON arguments end up garbled.

## Using the Stream

Here's how to consume the stream with real-time display and accumulation:

```rust
use tokio_stream::StreamExt;

async fn stream_chat(client: &LlmClient) -> Result<Message, LlmError> {
    let request = ChatRequest {
        model: "gpt-4o".to_string(),
        messages: vec![
            Message {
                role: Role::System,
                content: Some("You are a concise technical writer.".into()),
                tool_calls: None,
                tool_call_id: None,
            },
            Message {
                role: Role::User,
                content: Some("Explain async/await in Rust in 3 paragraphs.".into()),
                tool_calls: None,
                tool_call_id: None,
            },
        ],
        temperature: Some(0.3),
        max_tokens: Some(500),
        tools: None,
    };

    let mut stream = client.chat_stream(&request).await?;
    let mut accumulator = StreamAccumulator::new();

    while let Some(result) = stream.next().await {
        let chunk = result?;
        accumulator.process_chunk(&chunk);

        // Print tokens as they arrive
        for choice in &chunk.choices {
            if let Some(ref content) = choice.delta.content {
                print!("{content}");
                // Flush stdout so tokens appear immediately
                use std::io::Write;
                std::io::stdout().flush().ok();
            }
        }
    }

    println!(); // Final newline
    Ok(accumulator.into_message())
}
```

That `stdout().flush()` call is crucial. Without it, tokens buffer up and appear in bursts instead of smoothly streaming. Small detail, big UX difference.

## Adding Timeout and Cancellation

Streams can hang. The server might stop sending chunks without properly closing the connection. You need timeouts:

```rust
use std::time::Duration;
use tokio::time::timeout;

async fn stream_with_timeout(
    client: &LlmClient,
    request: &ChatRequest,
    chunk_timeout: Duration,
) -> Result<Message, LlmError> {
    let mut stream = client.chat_stream(request).await?;
    let mut accumulator = StreamAccumulator::new();
    let mut last_content_len = 0;

    loop {
        match timeout(chunk_timeout, stream.next()).await {
            Ok(Some(Ok(chunk))) => {
                accumulator.process_chunk(&chunk);
            }
            Ok(Some(Err(e))) => return Err(e),
            Ok(None) => break, // Stream ended normally
            Err(_) => {
                // Timeout — but did we get any content?
                if accumulator.content.len() > last_content_len {
                    // Got new content since last check, keep going
                    last_content_len = accumulator.content.len();
                    continue;
                }
                eprintln!("Stream timed out after {:?} of inactivity", chunk_timeout);
                break;
            }
        }
    }

    Ok(accumulator.into_message())
}
```

I use a per-chunk timeout rather than a total timeout. A long response might legitimately take 30 seconds, but no single chunk should take more than 10 seconds. If nothing arrives for the timeout period and we haven't gotten any new content, something's wrong.

## WebSocket Streaming

Some providers (and self-hosted models) use WebSockets instead of SSE. Here's a basic WebSocket streaming client using `tokio-tungstenite`:

```toml
[dependencies]
tokio-tungstenite = { version = "0.24", features = ["rustls-tls-webpki-roots"] }
```

```rust
use tokio_tungstenite::{connect_async, tungstenite::Message as WsMessage};
use futures::{SinkExt, StreamExt};

pub async fn websocket_stream(
    url: &str,
    request: &ChatRequest,
) -> Result<impl Stream<Item = Result<StreamChunk, LlmError>>, LlmError> {
    let (mut ws_stream, _) = connect_async(url)
        .await
        .map_err(|e| LlmError::Deserialize(format!("WebSocket connect failed: {e}")))?;

    // Send the request
    let request_json = serde_json::to_string(request)
        .map_err(|e| LlmError::Deserialize(e.to_string()))?;
    ws_stream
        .send(WsMessage::Text(request_json))
        .await
        .map_err(|e| LlmError::Deserialize(format!("WebSocket send failed: {e}")))?;

    // Return a stream of chunks
    let stream = async_stream::stream! {
        while let Some(msg) = ws_stream.next().await {
            match msg {
                Ok(WsMessage::Text(text)) => {
                    if text.trim() == "[DONE]" {
                        break;
                    }
                    match serde_json::from_str::<StreamChunk>(&text) {
                        Ok(chunk) => yield Ok(chunk),
                        Err(e) => {
                            yield Err(LlmError::Deserialize(format!("{e}: {text}")));
                            break;
                        }
                    }
                }
                Ok(WsMessage::Close(_)) => break,
                Err(e) => {
                    yield Err(LlmError::Deserialize(format!("WebSocket error: {e}")));
                    break;
                }
                _ => {} // Ignore pings, binary frames, etc.
            }
        }
    };

    Ok(stream)
}
```

The nice thing about using Rust's `Stream` trait is that the consumer code doesn't care whether the stream comes from SSE or WebSocket. Same `StreamExt::next()` loop either way.

## Backpressure and Flow Control

Here's a scenario that bit me: streaming tokens into a database or message queue that's slower than the LLM. Tokens pile up in memory, and if the response is long enough, you're looking at real memory pressure.

The fix is backpressure — slow down consumption to match downstream capacity:

```rust
use tokio::sync::mpsc;

pub async fn stream_with_backpressure(
    client: &LlmClient,
    request: &ChatRequest,
    buffer_size: usize,
) -> (mpsc::Receiver<String>, tokio::task::JoinHandle<Result<Message, LlmError>>) {
    let (tx, rx) = mpsc::channel::<String>(buffer_size);

    let client_ref = client;
    let request = request.clone();

    let handle = tokio::spawn(async move {
        let mut stream = client_ref.chat_stream(&request).await?;
        let mut accumulator = StreamAccumulator::new();

        while let Some(result) = stream.next().await {
            let chunk = result?;
            accumulator.process_chunk(&chunk);

            for choice in &chunk.choices {
                if let Some(ref content) = choice.delta.content {
                    // This will wait if the channel is full — backpressure!
                    if tx.send(content.clone()).await.is_err() {
                        // Receiver dropped, stop streaming
                        return Ok(accumulator.into_message());
                    }
                }
            }
        }

        Ok(accumulator.into_message())
    });

    (rx, handle)
}
```

The bounded `mpsc` channel is the key. When the buffer fills up, `tx.send()` awaits — which means the stream consumption pauses — which means we stop reading from the network — which means TCP flow control kicks in. Beautiful cascading backpressure, no manual buffer management needed.

## What's Next

We can now call LLMs and stream their responses in real-time. But the real power of modern LLMs is tool calling — letting the model invoke functions in your codebase. That's lesson 3, where we'll build a type-safe tool calling framework that makes it genuinely hard to wire up tools incorrectly.
