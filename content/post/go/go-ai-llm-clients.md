---
title: 'Lesson 2: LLM API Clients — Calling Claude, GPT, and Groq from Go'
author: Atharva Pandey
series: "Go and AI/LLM Integration"
lesson: 2
keywords:
  - Go
  - golang
  - LLM
  - Claude
  - OpenAI
  - GPT
  - Groq
  - API client
  - AI integration
tags:
  - Go tutorial
  - golang
  - AI
  - LLM
  - MCP
date: '2024-10-06T00:00:00.000Z'
---

Most Go developers approach LLM APIs the same way they approach any REST API — write an HTTP client, handle errors, parse JSON. That instinct is correct, but LLM APIs have a few characteristics that require specific handling: they're slow (seconds, not milliseconds), they have complex nested response structures, they support streaming, and the model selection and token management have real cost implications. This lesson is about building Go clients that handle all of this properly.

## The Problem

A naive LLM API client has a few failure modes that only show up in production.

```go
// WRONG — no timeout, no retry logic, no streaming, brittle error handling
func askClaude(question string) (string, error) {
    body, _ := json.Marshal(map[string]any{
        "model": "claude-opus-4-5",
        "max_tokens": 1024,
        "messages": []map[string]string{
            {"role": "user", "content": question},
        },
    })

    // No timeout — a slow response blocks this goroutine indefinitely
    resp, err := http.Post(
        "https://api.anthropic.com/v1/messages",
        "application/json",
        bytes.NewReader(body),
    )
    if err != nil {
        return "", err
    }
    defer resp.Body.Close()

    // No error status check — a 429 or 500 silently returns garbage
    var result map[string]any
    json.NewDecoder(resp.Body).Decode(&result)

    // Brittle path traversal — panics if structure changes
    return result["content"].([]any)[0].(map[string]any)["text"].(string), nil
}
```

Three problems: no request timeout, no HTTP error handling, and brittle type assertions on the response.

## The Idiomatic Way

Build a typed client with proper request/response structs, timeout handling, and retry logic for rate limits.

**Anthropic Claude client:**

```go
// llm/anthropic/client.go
package anthropic

import (
    "bytes"
    "context"
    "encoding/json"
    "fmt"
    "net/http"
    "time"
)

const baseURL = "https://api.anthropic.com/v1"

type Client struct {
    apiKey  string
    model   string
    http    *http.Client
}

func NewClient(apiKey, model string) *Client {
    return &Client{
        apiKey: apiKey,
        model:  model,
        http: &http.Client{
            Timeout: 120 * time.Second, // LLM responses can be slow
        },
    }
}

type Message struct {
    Role    string `json:"role"`
    Content string `json:"content"`
}

type MessagesRequest struct {
    Model     string    `json:"model"`
    MaxTokens int       `json:"max_tokens"`
    System    string    `json:"system,omitempty"`
    Messages  []Message `json:"messages"`
}

type MessagesResponse struct {
    ID           string         `json:"id"`
    Type         string         `json:"type"`
    Role         string         `json:"role"`
    Content      []ContentBlock `json:"content"`
    Model        string         `json:"model"`
    StopReason   string         `json:"stop_reason"`
    Usage        Usage          `json:"usage"`
}

type ContentBlock struct {
    Type string `json:"type"`
    Text string `json:"text"`
}

type Usage struct {
    InputTokens  int `json:"input_tokens"`
    OutputTokens int `json:"output_tokens"`
}

type APIError struct {
    StatusCode int
    Type       string `json:"type"`
    Error      struct {
        Type    string `json:"type"`
        Message string `json:"message"`
    } `json:"error"`
}

func (e *APIError) Error() string {
    return fmt.Sprintf("anthropic API error %d: %s", e.StatusCode, e.Error.Message)
}

func (c *Client) Messages(ctx context.Context, req MessagesRequest) (*MessagesResponse, error) {
    req.Model = c.model

    body, err := json.Marshal(req)
    if err != nil {
        return nil, fmt.Errorf("marshal request: %w", err)
    }

    httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost,
        baseURL+"/messages", bytes.NewReader(body))
    if err != nil {
        return nil, fmt.Errorf("create request: %w", err)
    }

    httpReq.Header.Set("Content-Type", "application/json")
    httpReq.Header.Set("x-api-key", c.apiKey)
    httpReq.Header.Set("anthropic-version", "2023-06-01")

    resp, err := c.http.Do(httpReq)
    if err != nil {
        return nil, fmt.Errorf("send request: %w", err)
    }
    defer resp.Body.Close()

    if resp.StatusCode != http.StatusOK {
        var apiErr APIError
        apiErr.StatusCode = resp.StatusCode
        json.NewDecoder(resp.Body).Decode(&apiErr)
        return nil, &apiErr
    }

    var result MessagesResponse
    if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
        return nil, fmt.Errorf("decode response: %w", err)
    }

    return &result, nil
}
```

**Wrapping both Claude and OpenAI behind a common interface:**

```go
// llm/provider.go — provider-agnostic interface
package llm

type Provider interface {
    Complete(ctx context.Context, req CompletionRequest) (*CompletionResponse, error)
}

type CompletionRequest struct {
    System   string
    Messages []Message
    MaxTokens int
}

type CompletionResponse struct {
    Content      string
    InputTokens  int
    OutputTokens int
    Model        string
}

// anthropicProvider adapts the Anthropic client to the Provider interface
type anthropicProvider struct {
    client *anthropic.Client
}

func (p *anthropicProvider) Complete(ctx context.Context, req CompletionRequest) (*CompletionResponse, error) {
    msgs := make([]anthropic.Message, len(req.Messages))
    for i, m := range req.Messages {
        msgs[i] = anthropic.Message{Role: m.Role, Content: m.Content}
    }

    resp, err := p.client.Messages(ctx, anthropic.MessagesRequest{
        System:    req.System,
        Messages:  msgs,
        MaxTokens: req.MaxTokens,
    })
    if err != nil {
        return nil, err
    }

    text := ""
    if len(resp.Content) > 0 {
        text = resp.Content[0].Text
    }

    return &CompletionResponse{
        Content:      text,
        InputTokens:  resp.Usage.InputTokens,
        OutputTokens: resp.Usage.OutputTokens,
        Model:        resp.Model,
    }, nil
}
```

## In The Wild

I built a document processing service that needed to classify and extract data from uploaded PDFs. The first version used a single provider directly. When Anthropic had a brief API outage, the service went down completely.

We refactored to use the `Provider` interface with a fallback chain: try Claude first, fall back to GPT-4 on any 5xx error. The failover was transparent to the rest of the system.

```go
type FallbackProvider struct {
    primary   Provider
    secondary Provider
}

func (f *FallbackProvider) Complete(ctx context.Context, req CompletionRequest) (*CompletionResponse, error) {
    resp, err := f.primary.Complete(ctx, req)
    if err != nil {
        var apiErr *anthropic.APIError
        if errors.As(err, &apiErr) && apiErr.StatusCode >= 500 {
            return f.secondary.Complete(ctx, req)
        }
        return nil, err
    }
    return resp, nil
}
```

During the three Anthropic maintenance windows over the following six months, the service automatically served all traffic from OpenAI. Zero downtime, zero intervention.

## The Gotchas

**Rate limit errors (429) need exponential backoff, not immediate retry.** When the API returns 429, the `retry-after` header tells you how long to wait. Respect it. Hammering the API on 429 makes the problem worse and burns through your quota faster.

**Token limits are per-request, not per-session.** There's no session state in LLM APIs. Every request sends the full conversation history. As conversations get longer, you approach the model's context window limit. Track `input_tokens` in every response and truncate old messages when you're approaching the limit.

**API keys should be loaded from environment, never hardcoded.** Use `os.Getenv` or a secrets manager. Log the first and last 4 characters of the key on startup for debugging (`key[:4] + "..." + key[len(key)-4:]`) but never log the full key.

**The `context.Context` timeout applies to the whole response.** LLM API calls can take 30–60 seconds for long completions. Set your HTTP client timeout generously (90–120 seconds). Use context deadlines for the overall request pipeline budget, not for individual LLM calls.

## Key Takeaway

LLM API clients in Go need typed request/response structs (no `map[string]any` in the hot path), proper timeout configuration (90–120 seconds for non-streaming), explicit error type checking on non-200 status codes, and retry logic with backoff for rate limits. Abstract the specific provider behind an interface so you can swap providers or add fallback behavior without changing call sites. Track token usage from the start — it's your primary cost signal.

---

[← Lesson 1: Building MCP Servers in Go](/post/go/go-ai-mcp-servers) | [Course Index](/series/go-and-ai-llm-integration) | [Next → Lesson 3: Streaming Responses](/post/go/go-ai-streaming)
