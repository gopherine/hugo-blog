---
title: 'Lesson 3: Streaming Responses — Token-by-token output without buffering the whole response'
author: Atharva Pandey
series: "Go and AI/LLM Integration"
lesson: 3
keywords:
  - Go
  - golang
  - LLM
  - streaming
  - Server-Sent Events
  - SSE
  - AI integration
tags:
  - Go tutorial
  - golang
  - AI
  - LLM
  - MCP
date: '2024-12-18T00:00:00.000Z'
---

If you've ever used a non-streaming LLM endpoint in a user-facing feature, you know the experience it creates: the user submits a question, watches a spinner for 8 seconds, then suddenly gets a wall of text. Streaming changes this completely — the user sees output appearing word by word, which feels responsive and alive even when the total time-to-complete is identical. In Go, streaming LLM responses means reading Server-Sent Events (SSE) from the API and piping them to the client in real time. It's genuinely one of the nicer concurrency patterns I've implemented.

## The Problem

A non-streaming implementation buffers the entire response before the user sees anything.

```go
// WRONG — blocks for 10+ seconds, then delivers all at once
func handleAsk(w http.ResponseWriter, r *http.Request) {
    question := r.URL.Query().Get("q")

    // This call blocks until the ENTIRE response is generated
    resp, err := llmClient.Messages(r.Context(), MessagesRequest{
        Messages:  []Message{{Role: "user", Content: question}},
        MaxTokens: 2048,
    })
    if err != nil {
        http.Error(w, err.Error(), 500)
        return
    }

    // User waited 10 seconds to see this
    fmt.Fprint(w, resp.Content[0].Text)
}
```

For a 2000-token response at roughly 50 tokens/second, that's 40 seconds of spinner. Streaming delivers the first tokens in under a second.

## The Idiomatic Way

LLM streaming APIs use Server-Sent Events — a simple HTTP format where the server sends a stream of `data: <json>\n\n` lines. In Go, this maps naturally to reading the response body line by line.

**Reading SSE from the Anthropic API:**

```go
// llm/anthropic/stream.go
package anthropic

import (
    "bufio"
    "context"
    "encoding/json"
    "fmt"
    "strings"
)

type StreamEvent struct {
    Type  string `json:"type"`
    Index int    `json:"index"`
    Delta *struct {
        Type string `json:"type"`
        Text string `json:"text"`
    } `json:"delta"`
    Usage *Usage `json:"usage"`
}

// Stream sends a streaming request and calls onToken for each text delta.
// It returns the final usage stats when the stream ends.
func (c *Client) Stream(ctx context.Context, req MessagesRequest, onToken func(string)) (*Usage, error) {
    req.Model = c.model

    body, _ := json.Marshal(map[string]any{
        "model":      req.Model,
        "max_tokens": req.MaxTokens,
        "system":     req.System,
        "messages":   req.Messages,
        "stream":     true, // this is the key flag
    })

    httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost,
        baseURL+"/messages", bytes.NewReader(body))
    if err != nil {
        return nil, err
    }
    httpReq.Header.Set("Content-Type", "application/json")
    httpReq.Header.Set("x-api-key", c.apiKey)
    httpReq.Header.Set("anthropic-version", "2023-06-01")

    // Use a client WITHOUT a short timeout — stream can take minutes
    httpClient := &http.Client{} // no timeout — stream is long-lived
    resp, err := httpClient.Do(httpReq)
    if err != nil {
        return nil, fmt.Errorf("stream request: %w", err)
    }
    defer resp.Body.Close()

    if resp.StatusCode != http.StatusOK {
        return nil, fmt.Errorf("API error: %d", resp.StatusCode)
    }

    var finalUsage *Usage
    scanner := bufio.NewScanner(resp.Body)

    for scanner.Scan() {
        line := scanner.Text()

        // SSE format: "data: <json>" lines, blank lines between events
        if !strings.HasPrefix(line, "data: ") {
            continue
        }

        data := strings.TrimPrefix(line, "data: ")
        if data == "[DONE]" {
            break
        }

        var event StreamEvent
        if err := json.Unmarshal([]byte(data), &event); err != nil {
            continue
        }

        switch event.Type {
        case "content_block_delta":
            if event.Delta != nil && event.Delta.Type == "text_delta" {
                onToken(event.Delta.Text)
            }
        case "message_delta":
            if event.Usage != nil {
                finalUsage = event.Usage
            }
        }
    }

    return finalUsage, scanner.Err()
}
```

**HTTP handler that proxies the stream to the browser via SSE:**

```go
// api/handlers/stream.go
func (h *Handler) StreamAsk(w http.ResponseWriter, r *http.Request) {
    question := r.URL.Query().Get("q")
    if question == "" {
        http.Error(w, "q parameter required", http.StatusBadRequest)
        return
    }

    // Set SSE headers before writing anything
    w.Header().Set("Content-Type", "text/event-stream")
    w.Header().Set("Cache-Control", "no-cache")
    w.Header().Set("Connection", "keep-alive")
    w.Header().Set("Access-Control-Allow-Origin", "*")

    flusher, ok := w.(http.Flusher)
    if !ok {
        http.Error(w, "streaming not supported", http.StatusInternalServerError)
        return
    }

    usage, err := h.llm.Stream(r.Context(),
        anthropic.MessagesRequest{
            MaxTokens: 1024,
            Messages:  []anthropic.Message{{Role: "user", Content: question}},
        },
        func(token string) {
            // Write each token as an SSE event
            fmt.Fprintf(w, "data: %s\n\n", jsonEscape(token))
            flusher.Flush() // critical — sends the data to the client immediately
        },
    )

    if err != nil {
        fmt.Fprintf(w, "event: error\ndata: %s\n\n", err.Error())
        flusher.Flush()
        return
    }

    // Send usage stats as a final event before closing
    usageJSON, _ := json.Marshal(usage)
    fmt.Fprintf(w, "event: done\ndata: %s\n\n", usageJSON)
    flusher.Flush()
}

func jsonEscape(s string) string {
    b, _ := json.Marshal(s)
    return string(b[1 : len(b)-1]) // strip surrounding quotes
}
```

## In The Wild

I built a Go service that let users query a large technical documentation corpus via natural language. Without streaming, the 90th percentile response time was 12 seconds — long enough that 15% of users abandoned the query before seeing the result (we tracked this with a JavaScript timeout counter).

After adding streaming, the first token appeared in under 800ms. The abandonment rate dropped to under 3%. The actual time to full completion barely changed — but perceived responsiveness is what users care about.

The interesting engineering detail: the streaming handler needed to handle client disconnects gracefully. When a user navigated away mid-stream, the context was cancelled, `scanner.Scan()` returned false, and the goroutine exited cleanly. The LLM API connection was also terminated because the context was threaded through the HTTP request. No goroutine leaks, no wasted API calls.

## The Gotchas

**`http.Flusher` must be checked, not assumed.** Not all `http.ResponseWriter` implementations support flushing (e.g., certain middleware wrappers). If `w.(http.Flusher)` fails, you can't stream — fall back to a non-streaming request.

**Never set a timeout on the HTTP client used for streaming.** The stream can last minutes. Use the context passed to the request to control the overall budget from the caller side.

**Context cancellation must propagate through the stream reader.** If you're using `bufio.Scanner` and the context is cancelled, the scanner doesn't automatically stop. You need to either check `ctx.Done()` in the scan loop or ensure the underlying HTTP body read respects context cancellation (it does when using `http.NewRequestWithContext`).

**SSE reconnection is the browser's problem, not yours.** The browser's EventSource API automatically reconnects on connection drop using the `Last-Event-ID` header. If you need reliable streaming with resumption, implement event IDs. For most LLM use cases, reconnection means restarting the generation — handle that in the frontend with a "Regenerate" button.

## Key Takeaway

Streaming LLM responses in Go is a matter of setting `"stream": true` in the API request, reading SSE events line by line with `bufio.Scanner`, and flushing each token to the HTTP response immediately. The user experience improvement is dramatic — first-token latency under a second versus waiting for full generation. Use `http.Flusher`, thread context through the stream for cancellation, and avoid timeouts on the HTTP client used for streaming calls.

---

[← Lesson 2: LLM API Clients](/post/go/go-ai-llm-clients) | [Course Index](/series/go-and-ai-llm-integration) | [Next → Lesson 4: Tool Calling Patterns](/post/go/go-ai-tool-calling)
