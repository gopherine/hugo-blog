---
title: 'Lesson 1: Building MCP Servers in Go — Give AI agents tools with the Model Context Protocol'
author: Atharva Pandey
series: "Go and AI/LLM Integration"
lesson: 1
keywords:
  - Go
  - golang
  - MCP
  - Model Context Protocol
  - AI agents
  - LLM
  - tools
tags:
  - Go tutorial
  - golang
  - AI
  - LLM
  - MCP
date: '2024-07-30T00:00:00.000Z'
---

When Claude or another AI assistant needs to look up a database record, call an internal API, or read a file from your filesystem, it can't do that on its own — it needs tools. The Model Context Protocol (MCP) is Anthropic's open standard for giving AI agents exactly those tools. An MCP server is a small program you write that exposes tools via a JSON-RPC protocol; the AI client calls your server to invoke them. I find this genuinely exciting as a Go developer: Go's concurrency model and fast startup time make it a natural fit for MCP servers.

## The Problem

Without a standard protocol, every AI integration has its own ad-hoc tool invocation format. You end up with bespoke JSON schemas for each tool, custom parsing code for each LLM's function-calling format, and no interoperability — a tool you build for Claude doesn't work with GPT-4 without rewriting the integration.

```go
// WRONG — ad-hoc tool invocation, tightly coupled to one LLM
type ClaudeToolCall struct {
    Name  string          `json:"name"`
    Input json.RawMessage `json:"input"`
}

// This only works with Claude's specific format.
// If you switch to GPT-4, you rewrite this handler.
func handleToolCall(tc ClaudeToolCall) (string, error) {
    switch tc.Name {
    case "get_user":
        var args struct{ UserID int64 }
        json.Unmarshal(tc.Input, &args)
        return getUserFromDB(args.UserID)
    // ... more cases
    }
    return "", fmt.Errorf("unknown tool: %s", tc.Name)
}
```

## The Idiomatic Way

An MCP server exposes three capabilities: **tools** (functions the LLM can call), **resources** (data the LLM can read), and **prompts** (reusable prompt templates). For most use cases, tools are what you need.

The MCP wire protocol is JSON-RPC 2.0 over stdio (for local servers) or HTTP with Server-Sent Events (for remote servers). You implement the `initialize`, `tools/list`, and `tools/call` RPC methods.

```go
// mcp-server/main.go — a minimal but complete MCP server in Go
package main

import (
    "bufio"
    "encoding/json"
    "fmt"
    "os"
)

type JSONRPCRequest struct {
    JSONRPC string          `json:"jsonrpc"`
    ID      any             `json:"id"`
    Method  string          `json:"method"`
    Params  json.RawMessage `json:"params"`
}

type JSONRPCResponse struct {
    JSONRPC string `json:"jsonrpc"`
    ID      any    `json:"id"`
    Result  any    `json:"result,omitempty"`
    Error   *RPCError `json:"error,omitempty"`
}

type RPCError struct {
    Code    int    `json:"code"`
    Message string `json:"message"`
}

type Tool struct {
    Name        string     `json:"name"`
    Description string     `json:"description"`
    InputSchema JSONSchema `json:"inputSchema"`
}

type JSONSchema struct {
    Type       string              `json:"type"`
    Properties map[string]Property `json:"properties"`
    Required   []string            `json:"required"`
}

type Property struct {
    Type        string `json:"type"`
    Description string `json:"description"`
}
```

The tools the server exposes:

```go
var tools = []Tool{
    {
        Name:        "get_user",
        Description: "Retrieve a user record by ID",
        InputSchema: JSONSchema{
            Type: "object",
            Properties: map[string]Property{
                "user_id": {Type: "integer", Description: "The user's numeric ID"},
            },
            Required: []string{"user_id"},
        },
    },
    {
        Name:        "search_orders",
        Description: "Search orders by user ID and optional status filter",
        InputSchema: JSONSchema{
            Type: "object",
            Properties: map[string]Property{
                "user_id": {Type: "integer", Description: "User ID to filter by"},
                "status":  {Type: "string", Description: "Optional: pending, confirmed, shipped, delivered"},
            },
            Required: []string{"user_id"},
        },
    },
}
```

The main dispatch loop reads JSON-RPC from stdin and writes responses to stdout:

```go
func main() {
    db := connectDB()
    scanner := bufio.NewScanner(os.Stdin)
    encoder := json.NewEncoder(os.Stdout)

    for scanner.Scan() {
        var req JSONRPCRequest
        if err := json.Unmarshal(scanner.Bytes(), &req); err != nil {
            continue
        }

        var result any
        var rpcErr *RPCError

        switch req.Method {
        case "initialize":
            result = map[string]any{
                "protocolVersion": "2024-11-05",
                "capabilities":    map[string]any{"tools": map[string]any{}},
                "serverInfo":      map[string]string{"name": "my-go-mcp-server", "version": "1.0.0"},
            }

        case "tools/list":
            result = map[string]any{"tools": tools}

        case "tools/call":
            var params struct {
                Name      string          `json:"name"`
                Arguments json.RawMessage `json:"arguments"`
            }
            json.Unmarshal(req.Params, &params)
            content, err := dispatchTool(db, params.Name, params.Arguments)
            if err != nil {
                rpcErr = &RPCError{Code: -32000, Message: err.Error()}
            } else {
                result = map[string]any{
                    "content": []map[string]string{{"type": "text", "text": content}},
                }
            }

        default:
            rpcErr = &RPCError{Code: -32601, Message: "method not found"}
        }

        encoder.Encode(JSONRPCResponse{
            JSONRPC: "2.0",
            ID:      req.ID,
            Result:  result,
            Error:   rpcErr,
        })
    }
}

func dispatchTool(db *sql.DB, name string, args json.RawMessage) (string, error) {
    switch name {
    case "get_user":
        var a struct{ UserID int64 `json:"user_id"` }
        json.Unmarshal(args, &a)
        return getUser(db, a.UserID)
    case "search_orders":
        var a struct {
            UserID int64  `json:"user_id"`
            Status string `json:"status"`
        }
        json.Unmarshal(args, &a)
        return searchOrders(db, a.UserID, a.Status)
    }
    return "", fmt.Errorf("unknown tool: %s", name)
}
```

## In The Wild

I built an MCP server for an internal support tool that let Claude answer questions about customer orders without the support engineer having to leave their chat interface. The server exposed four tools: `get_order`, `get_customer`, `search_orders_by_date`, and `refund_order`. The `refund_order` tool required a confirmation step — it returned "confirm with CONFIRM to proceed" before actually issuing the refund, which gave the engineer one final chance to verify.

The support team's average resolution time for order-related tickets dropped by about 35% in the first month because engineers could ask "what happened with order 48291 and why was it delayed?" in plain English instead of running 3 separate SQL queries.

## The Gotchas

**Tool descriptions are part of your API.** The LLM reads your tool descriptions to decide when to call them. Vague descriptions lead to incorrect tool selection. Write descriptions as if explaining to a capable but context-free developer: what does this tool do, when should it be called, and what are the important constraints?

**Return structured, readable text.** Tool results are included in the LLM's context window. Return data in a format that's easy for the model to reason about — not raw JSON blobs, but human-readable summaries. `"Order 48291: status=delayed, reason=out_of_stock, ETA=2025-02-10"` is better than `{"status":"delayed","reason":"out_of_stock","eta":"2025-02-10"}`.

**Validate all tool inputs.** The LLM generates arguments based on the schema, but it's not guaranteed to produce valid values. Validate every argument and return a clear error message — the LLM can often self-correct if you tell it what was wrong.

**Never give the LLM destructive tools without confirmation.** Delete, refund, cancel, send — any tool with irreversible effects should have a confirmation step or a dry-run mode. MCP tools run with your credentials; treat them accordingly.

## Key Takeaway

MCP servers are the clean, standard way to give AI agents access to your data and systems. In Go, the protocol is straightforward to implement over stdio — a JSON-RPC dispatch loop with a tools registry. Write tool descriptions as carefully as you write API documentation. Validate inputs, return readable output, and treat destructive tools with the same care you'd give any production API. Once your server is running, any MCP-compatible client (Claude Desktop, Cursor, custom agents) can use your tools without any integration changes.

---

[Course Index](/series/go-and-ai-llm-integration) | [Next → Lesson 2: LLM API Clients](/post/go/go-ai-llm-clients)
