---
title: 'Lesson 6: Agent Architectures — Building autonomous agents in Go'
author: Atharva Pandey
series: "Go and AI/LLM Integration"
lesson: 6
keywords:
  - Go
  - golang
  - AI agents
  - LLM
  - autonomous agents
  - ReAct
  - multi-agent
  - agentic systems
tags:
  - Go tutorial
  - golang
  - AI
  - LLM
  - MCP
date: '2025-07-10T00:00:00.000Z'
---

An agent is a loop: observe, think, act, repeat. The LLM is the "think" step — it decides what to do next given the current state. Your Go code handles "observe" (gathering context), "act" (executing tool calls), and the loop control that keeps everything running. I've built agents that write and execute code, agents that browse the web, and agents that orchestrate multi-step data pipelines. The underlying architecture is always the same few patterns, and Go's concurrency makes the execution layer clean and fast.

## The Problem

A single-step LLM call is limited to what the model knows from its training data and the context you provide. Complex tasks — "research this company and write a competitive analysis" or "find the bug in this test suite and fix it" — require multiple rounds of gathering information, forming hypotheses, taking actions, and updating based on results. Without an agent architecture, you either cram everything into one prompt (which fails on complex tasks) or write brittle multi-step code that doesn't adapt to unexpected intermediate results.

```go
// WRONG — rigid sequential pipeline, no adaptation
func analyzeBug(report string) string {
    // Step 1: Ask model what code to look at
    files := llm.Ask("Given bug report, what files are relevant? " + report)
    // Step 2: Always read exactly those files
    contents := readFiles(parseFileList(files))
    // Step 3: Always ask for analysis
    analysis := llm.Ask("Analyze this code: " + contents)
    // Step 4: Always return analysis
    return analysis
}
// If step 1 suggests running a test, this code can't do that.
// If the analysis reveals another file is needed, this code can't fetch it.
```

## The Idiomatic Way

The ReAct (Reason + Act) pattern is the foundation of most practical agent architectures. The model is prompted to reason about what it needs, decide on an action, the action is executed, the result is added to context, and the cycle repeats.

**A production-ready agent executor:**

```go
// agent/executor.go
package agent

import (
    "context"
    "fmt"
    "log/slog"
)

type Executor struct {
    llm     LLMClient
    tools   ToolRegistry
    memory  Memory
    maxSteps int
}

type Step struct {
    Thought string
    Action  *ToolCall
    Result  string
}

type ToolCall struct {
    Name  string
    Input json.RawMessage
}

func NewExecutor(llm LLMClient, tools ToolRegistry, mem Memory) *Executor {
    return &Executor{
        llm:     llm,
        tools:   tools,
        memory:  mem,
        maxSteps: 20, // prevent runaway agents
    }
}

func (e *Executor) Run(ctx context.Context, task string) (string, error) {
    // Initialize conversation with the task
    messages := []Message{
        {Role: "user", Content: task},
    }

    var steps []Step

    for i := 0; i < e.maxSteps; i++ {
        // Check if context is cancelled
        if err := ctx.Err(); err != nil {
            return "", fmt.Errorf("agent cancelled after %d steps: %w", i, err)
        }

        // Get the model's next action
        resp, err := e.llm.MessagesWithTools(ctx, MessagesRequest{
            System:    e.buildSystemPrompt(),
            Messages:  messages,
            Tools:     e.tools.Definitions(),
            MaxTokens: 4096,
        })
        if err != nil {
            return "", fmt.Errorf("step %d: llm error: %w", i, err)
        }

        // Add assistant message to history
        messages = append(messages, Message{
            Role:    "assistant",
            Content: resp.Content,
        })

        // If no tool use, the agent is done
        if resp.StopReason != "tool_use" {
            for _, block := range resp.Content {
                if block.Type == "text" {
                    slog.Info("agent complete", "steps", i, "task", task[:min(50, len(task))])
                    return block.Text, nil
                }
            }
            return "", fmt.Errorf("agent finished with no text response")
        }

        // Execute tool calls (potentially concurrent for independent tools)
        toolResults, err := e.executeTools(ctx, resp.Content)
        if err != nil {
            return "", fmt.Errorf("step %d: tool execution: %w", i, err)
        }

        // Add tool results to conversation
        messages = append(messages, Message{
            Role:    "user",
            Content: toolResults,
        })

        // Log the step for observability
        steps = append(steps, Step{Result: fmt.Sprintf("Completed %d tool calls", len(toolResults))})
    }

    return "", fmt.Errorf("agent exceeded max steps (%d)", e.maxSteps)
}
```

**Concurrent tool execution for independent calls:**

```go
// Execute multiple tool calls concurrently when they don't depend on each other
func (e *Executor) executeTools(ctx context.Context, blocks []ContentBlock) ([]ToolResult, error) {
    var toolUseBlocks []ContentBlock
    for _, b := range blocks {
        if b.Type == "tool_use" {
            toolUseBlocks = append(toolUseBlocks, b)
        }
    }

    type result struct {
        idx    int
        result ToolResult
        err    error
    }

    results := make(chan result, len(toolUseBlocks))

    for i, block := range toolUseBlocks {
        i, block := i, block
        go func() {
            output, err := e.tools.Execute(ctx, block.Name, block.Input)
            if err != nil {
                output = fmt.Sprintf("Tool error: %s", err.Error())
                slog.Warn("tool failed", "tool", block.Name, "err", err)
            }
            results <- result{
                idx: i,
                result: ToolResult{
                    Type:      "tool_result",
                    ToolUseID: block.ID,
                    Content:   output,
                },
            }
        }()
    }

    toolResults := make([]ToolResult, len(toolUseBlocks))
    for range toolUseBlocks {
        r := <-results
        toolResults[r.idx] = r.result
    }

    return toolResults, nil
}
```

**System prompt for effective ReAct behavior:**

```go
func (e *Executor) buildSystemPrompt() string {
    return `You are an autonomous agent. For each task:
1. Think about what information you need
2. Use tools to gather that information
3. Continue reasoning and using tools until you have a complete answer
4. Provide a clear, concise final response

Guidelines:
- Use tools when you need current information, not just your training knowledge
- When multiple tools can be called independently, call them in the same response
- If a tool returns an error, try to recover or explain what went wrong
- Always provide a final text response when you're done working`
}
```

## In The Wild

I built a code review agent that could analyze a pull request, run the test suite, check for race conditions, and generate a review summary. The multi-step nature was essential: the agent would read the diff, decide which files needed deeper examination, fetch those files, sometimes run specific tests to understand behavior, and then synthesize a review.

The key design decision was the tool set: `read_file`, `list_directory`, `run_tests`, `search_code`, and `get_git_diff`. With these five tools, the agent could navigate an entire codebase autonomously. The `run_tests` tool was sandboxed in a Docker container with a 60-second timeout — critical for safety.

On a test corpus of 50 PRs that human engineers had reviewed, the agent identified 73% of the bugs the humans caught, and flagged 12 issues the humans missed (8 of which were confirmed as real bugs on closer inspection).

## The Gotchas

**Agents must be sandboxed when executing code.** If your agent can run code, it can run arbitrary code. Always execute in isolated environments — Docker containers, restricted VMs, or subprocess sandboxes with resource limits. Never run agent-generated code directly on your host.

**Conversation history grows with every step.** Each tool call and result adds to the message history. For long-running agents, you'll hit the context window limit. Implement message summarization: after every N steps, ask the model to summarize the work done so far and replace the detailed history with the summary.

**Agents can loop on failure.** If a tool consistently returns errors, the agent may retry indefinitely or spiral into confusion. Implement per-tool retry limits and add explicit failure detection: if the same tool fails 3 times in a row, abort the agent with a clear error.

**Observability is critical.** An agent that ran for 20 steps and produced a wrong answer is nearly impossible to debug without step-by-step logging. Log every tool call, its inputs, and its outputs. Use distributed tracing to capture the full execution tree.

## Key Takeaway

🎓 **Course Complete!** You've finished "Go and AI/LLM Integration."

Agents in Go are a tool execution loop around an LLM. The ReAct pattern — reason, act, observe, repeat — maps directly to a for-loop that calls the LLM, executes tool_use blocks, and feeds results back. Use Go's concurrency to execute independent tool calls in parallel. Cap the step count to prevent runaway agents. Sandbox any code execution. Log every step for observability. The combination of Go's performance and type safety with the reasoning capabilities of modern LLMs produces agents that are both capable and maintainable — and unlike most Python-based agent frameworks, they're genuinely easy to deploy and operate.

---

[← Lesson 5: Embedding and Vector Search](/post/go/go-ai-embeddings) | [Course Index](/series/go-and-ai-llm-integration)
