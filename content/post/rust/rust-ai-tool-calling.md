---
title: "Lesson 3: Tool Calling / Function Calling Patterns — Agents need tools"
author: Atharva Pandey
series: "Rust and AI/LLM Integration"
lesson: 3
keywords: [Rust, rust, AI, LLM, tool calling, function calling, agents, serde, JSON schema]
tags: [Rust tutorial, rust, ai, llm]
date: '2025-08-16T16:42:00.000Z'
---

Here's something that took me embarrassingly long to internalize: LLMs don't *do* things. They generate text that *describes* doing things. The tool calling protocol is just the model saying "hey, I'd like you to call this function with these arguments" — and then your code actually does it.

This distinction matters because the entire tool calling system is essentially a serialization contract. The model generates JSON conforming to a schema you provided, you execute the function, and you send the result back. Get the schema wrong, and the model hallucinates arguments. Get the execution wrong, and you've got a broken agent. Get the result format wrong, and the model can't make sense of what happened.

Rust is absurdly good at this. The type system *is* the contract.

## The Tool Calling Flow

Before we write code, let's be crystal clear about the protocol:

1. You send a request with a list of available tools (name, description, JSON schema for parameters)
2. The model responds with `finish_reason: "tool_calls"` and one or more tool call objects
3. You execute the requested functions locally
4. You append the results as `tool` messages and send them back
5. The model responds with a final answer (or more tool calls)

It's a loop. The model might chain multiple tool calls, and each round trip costs tokens and latency. Designing good tools that minimize round trips is half the battle.

## Defining Tools With Traits

Let's build a trait-based system where each tool is a struct:

```rust
use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[async_trait]
pub trait Tool: Send + Sync {
    /// The name the model uses to invoke this tool
    fn name(&self) -> &str;

    /// Human-readable description — this goes to the model
    fn description(&self) -> &str;

    /// JSON Schema for the tool's parameters
    fn parameters_schema(&self) -> Value;

    /// Execute the tool with the given arguments
    async fn execute(&self, arguments: &str) -> Result<String, ToolError>;
}

#[derive(Debug, thiserror::Error)]
pub enum ToolError {
    #[error("Invalid arguments: {0}")]
    InvalidArguments(String),

    #[error("Execution failed: {0}")]
    ExecutionFailed(String),

    #[error("Tool not found: {0}")]
    NotFound(String),
}
```

Now let's build a concrete tool. Say we want the model to be able to look up weather data:

```rust
#[derive(Debug)]
pub struct WeatherTool {
    api_key: String,
}

#[derive(Debug, Deserialize)]
struct WeatherArgs {
    city: String,
    #[serde(default = "default_units")]
    units: String,
}

fn default_units() -> String {
    "celsius".to_string()
}

#[async_trait]
impl Tool for WeatherTool {
    fn name(&self) -> &str {
        "get_weather"
    }

    fn description(&self) -> &str {
        "Get current weather for a city. Returns temperature, humidity, and conditions."
    }

    fn parameters_schema(&self) -> Value {
        serde_json::json!({
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "City name, e.g. 'San Francisco'"
                },
                "units": {
                    "type": "string",
                    "enum": ["celsius", "fahrenheit"],
                    "description": "Temperature units (default: celsius)"
                }
            },
            "required": ["city"]
        })
    }

    async fn execute(&self, arguments: &str) -> Result<String, ToolError> {
        let args: WeatherArgs = serde_json::from_str(arguments)
            .map_err(|e| ToolError::InvalidArguments(e.to_string()))?;

        // In production, this would call a weather API
        // For now, simulate a response
        let result = serde_json::json!({
            "city": args.city,
            "temperature": 22,
            "units": args.units,
            "humidity": 65,
            "conditions": "partly cloudy"
        });

        Ok(result.to_string())
    }
}
```

The key insight: `parameters_schema()` returns a JSON Schema that the model sees, and `execute()` deserializes into a matching Rust struct. If these two drift apart, you'll get deserialization errors at runtime — but at least you'll get *errors*, not silently wrong behavior.

## The Tool Registry

We need a central place to store and look up tools:

```rust
use std::collections::HashMap;
use std::sync::Arc;

pub struct ToolRegistry {
    tools: HashMap<String, Arc<dyn Tool>>,
}

impl ToolRegistry {
    pub fn new() -> Self {
        Self {
            tools: HashMap::new(),
        }
    }

    pub fn register(&mut self, tool: impl Tool + 'static) {
        let name = tool.name().to_string();
        self.tools.insert(name, Arc::new(tool));
    }

    pub fn get(&self, name: &str) -> Option<Arc<dyn Tool>> {
        self.tools.get(name).cloned()
    }

    /// Generate the tools array for the API request
    pub fn to_api_tools(&self) -> Vec<crate::Tool> {
        self.tools
            .values()
            .map(|tool| crate::Tool {
                tool_type: "function".to_string(),
                function: crate::FunctionDef {
                    name: tool.name().to_string(),
                    description: tool.description().to_string(),
                    parameters: tool.parameters_schema(),
                },
            })
            .collect()
    }

    /// Execute a tool call from the model
    pub async fn execute_call(&self, call: &ToolCall) -> ToolResult {
        let tool = match self.get(&call.function.name) {
            Some(t) => t,
            None => {
                return ToolResult {
                    tool_call_id: call.id.clone(),
                    content: format!("Error: unknown tool '{}'", call.function.name),
                    is_error: true,
                };
            }
        };

        match tool.execute(&call.function.arguments).await {
            Ok(result) => ToolResult {
                tool_call_id: call.id.clone(),
                content: result,
                is_error: false,
            },
            Err(e) => ToolResult {
                tool_call_id: call.id.clone(),
                content: format!("Error: {e}"),
                is_error: true,
            },
        }
    }
}

pub struct ToolResult {
    pub tool_call_id: String,
    pub content: String,
    pub is_error: bool,
}

impl ToolResult {
    pub fn to_message(&self) -> Message {
        Message {
            role: Role::Tool,
            content: Some(self.content.clone()),
            tool_calls: None,
            tool_call_id: Some(self.tool_call_id.clone()),
        }
    }
}
```

## The Tool Calling Loop

Now the main event — the agentic loop that handles multi-turn tool calling:

```rust
pub struct ToolCallingAgent {
    client: LlmClient,
    registry: ToolRegistry,
    max_iterations: u32,
}

impl ToolCallingAgent {
    pub fn new(client: LlmClient, registry: ToolRegistry) -> Self {
        Self {
            client,
            registry,
            max_iterations: 10, // Safety limit
        }
    }

    pub async fn run(
        &self,
        system_prompt: &str,
        user_message: &str,
    ) -> Result<AgentResult, LlmError> {
        let tools = self.registry.to_api_tools();

        let mut messages = vec![
            Message {
                role: Role::System,
                content: Some(system_prompt.to_string()),
                tool_calls: None,
                tool_call_id: None,
            },
            Message {
                role: Role::User,
                content: Some(user_message.to_string()),
                tool_calls: None,
                tool_call_id: None,
            },
        ];

        let mut iterations = 0;
        let mut all_tool_calls = Vec::new();

        loop {
            iterations += 1;
            if iterations > self.max_iterations {
                return Err(LlmError::Api {
                    status: 0,
                    message: format!(
                        "Agent exceeded max iterations ({})",
                        self.max_iterations
                    ),
                });
            }

            let request = ChatRequest {
                model: "gpt-4o".to_string(),
                messages: messages.clone(),
                temperature: Some(0.0), // Deterministic for tool calling
                max_tokens: Some(4096),
                tools: Some(tools.clone()),
            };

            let response = self.client.chat(&request).await?;
            let choice = &response.choices[0];
            let assistant_message = choice.message.clone();

            // Add assistant's response to conversation
            messages.push(assistant_message.clone());

            match choice.finish_reason {
                Some(FinishReason::ToolCalls) => {
                    let tool_calls = assistant_message
                        .tool_calls
                        .as_ref()
                        .expect("finish_reason=tool_calls but no tool_calls");

                    // Execute all tool calls in parallel
                    let futures: Vec<_> = tool_calls
                        .iter()
                        .map(|tc| self.registry.execute_call(tc))
                        .collect();

                    let results = futures::future::join_all(futures).await;

                    for result in &results {
                        messages.push(result.to_message());
                        all_tool_calls.push(result.content.clone());
                    }

                    // Continue the loop — model will process tool results
                }
                Some(FinishReason::Stop) | None => {
                    // Model finished with a text response
                    return Ok(AgentResult {
                        response: assistant_message
                            .content
                            .unwrap_or_default(),
                        tool_calls_made: all_tool_calls,
                        iterations,
                    });
                }
                Some(FinishReason::Length) => {
                    return Ok(AgentResult {
                        response: assistant_message
                            .content
                            .unwrap_or_else(|| "(truncated)".to_string()),
                        tool_calls_made: all_tool_calls,
                        iterations,
                    });
                }
                _ => {
                    return Err(LlmError::Api {
                        status: 0,
                        message: "Unexpected finish reason".to_string(),
                    });
                }
            }
        }
    }
}

#[derive(Debug)]
pub struct AgentResult {
    pub response: String,
    pub tool_calls_made: Vec<String>,
    pub iterations: u32,
}
```

A few design decisions worth explaining:

**Parallel tool execution.** When the model requests multiple tool calls in one turn, we execute them all concurrently with `join_all`. This can dramatically reduce latency — if the model wants weather for three cities, we fetch all three simultaneously.

**Max iterations.** This is a hard safety limit. Without it, a confused model could loop forever, burning through your API budget. I've seen it happen with poorly-designed tools that always return ambiguous results.

**Temperature 0.** For tool calling, you want determinism. Creative temperature is for prose generation, not for deciding which function to call with what arguments.

## Type-Safe Tool Definitions With Macros

Writing `parameters_schema()` by hand is tedious and error-prone. Let's use `schemars` to generate JSON Schema from Rust types:

```toml
[dependencies]
schemars = "0.8"
```

```rust
use schemars::JsonSchema;

#[derive(Debug, Deserialize, JsonSchema)]
#[schemars(description = "Search a database for records matching a query")]
struct SearchArgs {
    /// The search query string
    query: String,
    /// Maximum number of results to return (1-100)
    #[schemars(range(min = 1, max = 100))]
    limit: Option<u32>,
    /// Filter by category name
    category: Option<String>,
}

fn schema_for<T: JsonSchema>() -> Value {
    let schema = schemars::schema_for!(T);
    serde_json::to_value(schema).unwrap()
}
```

Now the JSON schema is derived directly from the Rust type. Change the struct, and the schema updates automatically. No drift possible. This is the kind of guarantee that makes me genuinely prefer Rust for this work.

Here's a macro to reduce boilerplate even further:

```rust
macro_rules! define_tool {
    (
        name: $name:expr,
        description: $desc:expr,
        args: $args_type:ty,
        handler: $handler:expr
    ) => {{
        struct GeneratedTool;

        #[async_trait]
        impl Tool for GeneratedTool {
            fn name(&self) -> &str {
                $name
            }

            fn description(&self) -> &str {
                $desc
            }

            fn parameters_schema(&self) -> Value {
                let schema = schemars::schema_for!($args_type);
                serde_json::to_value(schema).unwrap()
            }

            async fn execute(&self, arguments: &str) -> Result<String, ToolError> {
                let args: $args_type = serde_json::from_str(arguments)
                    .map_err(|e| ToolError::InvalidArguments(e.to_string()))?;
                let result = $handler(args).await?;
                Ok(serde_json::to_string(&result)
                    .map_err(|e| ToolError::ExecutionFailed(e.to_string()))?)
            }
        }

        GeneratedTool
    }};
}
```

Usage:

```rust
let calculator = define_tool! {
    name: "calculate",
    description: "Evaluate a mathematical expression",
    args: CalculateArgs,
    handler: |args: CalculateArgs| async move {
        // Simple expression evaluation
        let result = evaluate_expression(&args.expression)?;
        Ok::<_, ToolError>(serde_json::json!({ "result": result }))
    }
};

registry.register(calculator);
```

## Error Handling in Tool Calls

How you report errors back to the model matters more than you'd think. A good error message lets the model self-correct. A bad one leads to retry loops:

```rust
impl ToolRegistry {
    pub async fn execute_call_with_context(&self, call: &ToolCall) -> ToolResult {
        let tool = match self.get(&call.function.name) {
            Some(t) => t,
            None => {
                let available: Vec<_> = self.tools.keys().collect();
                return ToolResult {
                    tool_call_id: call.id.clone(),
                    content: format!(
                        "Tool '{}' not found. Available tools: {}",
                        call.function.name,
                        available.join(", ")
                    ),
                    is_error: true,
                };
            }
        };

        // Validate JSON before execution
        if serde_json::from_str::<Value>(&call.function.arguments).is_err() {
            return ToolResult {
                tool_call_id: call.id.clone(),
                content: format!(
                    "Invalid JSON in arguments. Received: {}",
                    &call.function.arguments
                ),
                is_error: true,
            };
        }

        match tool.execute(&call.function.arguments).await {
            Ok(result) => ToolResult {
                tool_call_id: call.id.clone(),
                content: result,
                is_error: false,
            },
            Err(ToolError::InvalidArguments(msg)) => ToolResult {
                tool_call_id: call.id.clone(),
                content: format!(
                    "Invalid arguments for '{}': {msg}. Expected schema: {}",
                    call.function.name,
                    serde_json::to_string_pretty(&tool.parameters_schema()).unwrap()
                ),
                is_error: true,
            },
            Err(e) => ToolResult {
                tool_call_id: call.id.clone(),
                content: format!("Tool execution error: {e}"),
                is_error: true,
            },
        }
    }
}
```

Notice how `InvalidArguments` includes the expected schema in the error message. This gives the model enough context to fix its arguments on the next attempt. Without it, you get frustrating retry loops where the model just tries the same malformed call again.

## Putting It All Together

```rust
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let client = LlmClient::openai(std::env::var("OPENAI_API_KEY")?);

    let mut registry = ToolRegistry::new();
    registry.register(WeatherTool {
        api_key: "demo-key".to_string(),
    });

    let agent = ToolCallingAgent::new(client, registry);

    let result = agent
        .run(
            "You are a helpful assistant with access to weather data.",
            "What's the weather like in Tokyo and London right now? Compare them.",
        )
        .await?;

    println!("Response: {}", result.response);
    println!("Tool calls made: {}", result.tool_calls_made.len());
    println!("Iterations: {}", result.iterations);

    Ok(())
}
```

The model will call `get_weather` twice (Tokyo and London), receive both results, and synthesize a comparison. Two tool calls, executed in parallel, one final response. Clean.

## What's Next

We've got tools wired up and a working agent loop. In lesson 4, we're going to tackle embeddings and vector search — the foundation of RAG (Retrieval-Augmented Generation). Because the most powerful thing you can do with tool calling is give the model access to your own data through semantic search.
