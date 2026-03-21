---
title: 'Lesson 4: Tool Calling Patterns — Letting the LLM invoke your Go functions'
author: Atharva Pandey
series: "Go and AI/LLM Integration"
lesson: 4
keywords:
  - Go
  - golang
  - LLM
  - tool calling
  - function calling
  - AI agents
  - Claude
tags:
  - Go tutorial
  - golang
  - AI
  - LLM
  - MCP
date: '2025-03-12T00:00:00.000Z'
---

Tool calling is where LLM integrations get genuinely powerful. Without tool calling, you're limited to asking the model to generate text. With tool calling, you can build a conversational interface where the model decides which of your functions to call, calls them, incorporates the results into its reasoning, and decides whether to call more tools or return a final answer. I've used this to build support assistants that query databases, coding assistants that run test suites, and research tools that fetch live web content — all driven by the model's judgment about which tools to invoke.

## The Problem

Without a structured tool-calling loop, the model can describe what it would do but can't actually do it.

```go
// WRONG — asking the model to pretend to execute code
resp, _ := llm.Messages(ctx, MessagesRequest{
    Messages: []Message{{
        Role: "user",
        Content: "What are the 5 most recent orders for user 482?",
    }},
})

// The model outputs something like:
// "To answer this, I would query: SELECT * FROM orders WHERE user_id = 482 ORDER BY created_at DESC LIMIT 5"
// But it hasn't actually queried anything. The answer is fabricated or generic.
```

The model's training data ends at a cutoff date — it has no access to your live database. Without real tool calls, you get plausible-sounding but potentially wrong answers about your production data.

## The Idiomatic Way

The tool calling loop has three phases: (1) send a request with available tools defined, (2) the model returns a tool_use block instead of text, (3) you execute the tool and send the result back, then repeat.

**Define tools using Anthropic's schema format:**

```go
// tools/registry.go
package tools

type Tool struct {
    Name        string    `json:"name"`
    Description string    `json:"description"`
    InputSchema Schema    `json:"input_schema"`
    Handler     func(ctx context.Context, input json.RawMessage) (string, error) `json:"-"`
}

type Schema struct {
    Type       string              `json:"type"`
    Properties map[string]Property `json:"properties"`
    Required   []string            `json:"required"`
}

type Property struct {
    Type        string   `json:"type"`
    Description string   `json:"description"`
    Enum        []string `json:"enum,omitempty"`
}

type Registry struct {
    tools map[string]*Tool
}

func NewRegistry() *Registry {
    return &Registry{tools: make(map[string]*Tool)}
}

func (r *Registry) Register(t *Tool) {
    r.tools[t.Name] = t
}

func (r *Registry) Execute(ctx context.Context, name string, input json.RawMessage) (string, error) {
    t, ok := r.tools[name]
    if !ok {
        return "", fmt.Errorf("unknown tool: %s", name)
    }
    return t.Handler(ctx, input)
}

func (r *Registry) Definitions() []Tool {
    defs := make([]Tool, 0, len(r.tools))
    for _, t := range r.tools {
        defs = append(defs, *t)
    }
    return defs
}
```

**Registering concrete tools:**

```go
// tools/order_tools.go
func RegisterOrderTools(reg *Registry, store *order.Store) {
    reg.Register(&Tool{
        Name:        "get_orders",
        Description: "Retrieve recent orders for a user. Returns order ID, status, total, and creation date.",
        InputSchema: Schema{
            Type: "object",
            Properties: map[string]Property{
                "user_id": {Type: "integer", Description: "The user's numeric ID"},
                "limit":   {Type: "integer", Description: "Max orders to return (default 10, max 50)"},
                "status":  {Type: "string", Description: "Filter by status", Enum: []string{"pending", "confirmed", "shipped", "delivered", "cancelled"}},
            },
            Required: []string{"user_id"},
        },
        Handler: func(ctx context.Context, input json.RawMessage) (string, error) {
            var args struct {
                UserID int64  `json:"user_id"`
                Limit  int    `json:"limit"`
                Status string `json:"status"`
            }
            if err := json.Unmarshal(input, &args); err != nil {
                return "", fmt.Errorf("invalid arguments: %w", err)
            }
            if args.Limit == 0 { args.Limit = 10 }
            if args.Limit > 50 { args.Limit = 50 }

            orders, err := store.FindByUser(ctx, args.UserID, args.Limit, args.Status)
            if err != nil {
                return "", fmt.Errorf("query orders: %w", err)
            }

            if len(orders) == 0 {
                return fmt.Sprintf("No orders found for user %d", args.UserID), nil
            }

            var sb strings.Builder
            fmt.Fprintf(&sb, "Found %d orders for user %d:\n", len(orders), args.UserID)
            for _, o := range orders {
                fmt.Fprintf(&sb, "- Order %d: %s, $%.2f, created %s\n",
                    o.ID, o.Status, o.Total, o.CreatedAt.Format("2006-01-02"))
            }
            return sb.String(), nil
        },
    })
}
```

**The agentic loop:**

```go
// agent/loop.go
func RunAgentLoop(ctx context.Context, llm *anthropic.Client, reg *tools.Registry, userMessage string) (string, error) {
    messages := []anthropic.Message{
        {Role: "user", Content: userMessage},
    }

    toolDefs := reg.Definitions()

    for {
        resp, err := llm.MessagesWithTools(ctx, anthropic.MessagesRequest{
            MaxTokens: 2048,
            Messages:  messages,
            Tools:     toolDefs,
        })
        if err != nil {
            return "", fmt.Errorf("llm request: %w", err)
        }

        // Collect the assistant's response (may include text and/or tool_use blocks)
        messages = append(messages, anthropic.Message{
            Role:    "assistant",
            Content: resp.Content, // structured content blocks
        })

        // Check if the model wants to use tools
        if resp.StopReason != "tool_use" {
            // Model is done — return the text response
            for _, block := range resp.Content {
                if block.Type == "text" {
                    return block.Text, nil
                }
            }
            return "", nil
        }

        // Execute all tool calls and collect results
        var toolResults []anthropic.ToolResult
        for _, block := range resp.Content {
            if block.Type != "tool_use" {
                continue
            }

            result, err := reg.Execute(ctx, block.Name, block.Input)
            if err != nil {
                result = fmt.Sprintf("Error: %s", err.Error())
            }

            toolResults = append(toolResults, anthropic.ToolResult{
                Type:      "tool_result",
                ToolUseID: block.ID,
                Content:   result,
            })
        }

        // Send tool results back to the model
        messages = append(messages, anthropic.Message{
            Role:    "user",
            Content: toolResults,
        })

        // Loop — the model will process the results and either call more tools or respond
    }
}
```

## In The Wild

I built a customer support assistant for an e-commerce platform with five tools: `get_orders`, `get_order_detail`, `search_customers`, `get_shipping_status`, and `issue_refund`. The `issue_refund` tool had a safeguard: it returned a confirmation prompt rather than immediately issuing the refund, and only executed the actual refund if the conversation continued with confirmation.

In the first month of production use, the assistant handled 2,300 support conversations. About 40% required tool calls (mostly order lookups). Of the tool-calling conversations, 78% were resolved without human escalation. The refund safeguard triggered 14 times — in 12 of those cases, the customer actually meant to ask about eligibility, not immediately receive a refund.

## The Gotchas

**Prevent infinite loops with a step limit.** The agentic loop can cycle indefinitely if the model keeps calling tools without reaching a conclusion. Add a max-steps counter and return an error or escalate when it's exceeded.

**Tool errors should be informative, not generic.** When a tool fails, return a message that helps the model self-correct: `"User 999 not found — valid user IDs are positive integers"` is far more useful than `"error"`. The model can often retry with corrected arguments.

**Parallel tool calls are supported.** Anthropic's API may return multiple `tool_use` blocks in a single response when tools are independent. Execute them concurrently with `errgroup` rather than sequentially.

**Guard destructive tools with explicit confirmation.** Any tool that modifies state — creates, updates, deletes, sends, pays — should require explicit confirmation in the conversation before executing. Use a two-step pattern: the first tool call returns what would happen, the second actually does it.

## Key Takeaway

Tool calling turns an LLM from a text generator into an agent that can interact with your systems. The loop is straightforward: define tools with JSON schema, send them in the request, execute tool_use blocks when the model requests them, send results back, and repeat until the model produces a final text response. Guard the loop against infinite iteration, make tool errors informative for model self-correction, and treat destructive operations with the same caution you'd give a production API.

---

[← Lesson 3: Streaming Responses](/post/go/go-ai-streaming) | [Course Index](/series/go-and-ai-llm-integration) | [Next → Lesson 5: Embedding and Vector Search](/post/go/go-ai-embeddings)
