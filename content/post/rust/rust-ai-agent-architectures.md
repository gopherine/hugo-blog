---
title: "Lesson 5: Agent Architectures in Rust — ReAct, planning, and loops"
author: Atharva Pandey
series: "Rust and AI/LLM Integration"
lesson: 5
keywords: [Rust, rust, AI, LLM, agents, ReAct, planning, agentic, state machine, orchestration]
tags: [Rust tutorial, rust, ai, llm]
date: '2025-08-20T13:08:00.000Z'
---

I built my first "AI agent" by stuffing a system prompt into a while loop and hoping for the best. It worked — sometimes. Other times it'd get stuck in infinite loops, burn through $50 of API credits hallucinating tool calls that didn't exist, or confidently produce completely wrong answers after three rounds of "reasoning."

The problem wasn't the LLM. The problem was me treating agent design as an afterthought. Good agents need structure — clear state machines, well-defined stopping conditions, and guardrails that prevent runaway behavior. This is where Rust's type system pays massive dividends, because you can encode these constraints at the type level.

## Agent Patterns Overview

There are three main patterns you'll encounter in the wild:

1. **ReAct** (Reason + Act): The model alternates between thinking ("I should look up the weather") and acting (calling tools). Most common pattern today.
2. **Plan-then-Execute**: The model first creates a full plan, then executes each step. Better for complex multi-step tasks.
3. **Reflexion**: The model executes, evaluates its own output, and iterates. Good for code generation and self-correction.

Let's implement each one.

## The ReAct Agent

ReAct is the workhorse pattern. The model gets a thought-action-observation loop:

```
Thought: I need to find the user's account balance
Action: get_balance(user_id="12345")
Observation: {"balance": 42.50, "currency": "USD"}
Thought: Now I can answer the question
Answer: Your balance is $42.50
```

Here's a proper implementation:

```rust
use std::fmt;

#[derive(Debug, Clone)]
pub enum AgentStep {
    Thought(String),
    Action {
        tool_name: String,
        arguments: String,
    },
    Observation(String),
    FinalAnswer(String),
    Error(String),
}

impl fmt::Display for AgentStep {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            AgentStep::Thought(t) => write!(f, "Thought: {t}"),
            AgentStep::Action { tool_name, arguments } => {
                write!(f, "Action: {tool_name}({arguments})")
            }
            AgentStep::Observation(o) => write!(f, "Observation: {o}"),
            AgentStep::FinalAnswer(a) => write!(f, "Answer: {a}"),
            AgentStep::Error(e) => write!(f, "Error: {e}"),
        }
    }
}

pub struct ReactAgent {
    client: LlmClient,
    registry: ToolRegistry,
    max_steps: usize,
    model: String,
}

impl ReactAgent {
    pub fn new(client: LlmClient, registry: ToolRegistry) -> Self {
        Self {
            client,
            registry,
            max_steps: 15,
            model: "gpt-4o".to_string(),
        }
    }

    pub async fn run(&self, task: &str) -> Result<AgentTrace, LlmError> {
        let mut trace = AgentTrace::new(task.to_string());
        let tools = self.registry.to_api_tools();

        let system_prompt = format!(
            "You are a helpful assistant that solves tasks step by step.\n\
             \n\
             For each step, you should:\n\
             1. Think about what you need to do next\n\
             2. Use a tool if needed\n\
             3. Observe the result\n\
             4. Repeat until you have enough information to answer\n\
             \n\
             Available tools: {}\n\
             \n\
             When you have the final answer, respond directly without calling any tools.",
            self.registry
                .tools
                .keys()
                .cloned()
                .collect::<Vec<_>>()
                .join(", ")
        );

        let mut messages = vec![
            Message {
                role: Role::System,
                content: Some(system_prompt),
                tool_calls: None,
                tool_call_id: None,
            },
            Message {
                role: Role::User,
                content: Some(task.to_string()),
                tool_calls: None,
                tool_call_id: None,
            },
        ];

        for step in 0..self.max_steps {
            let request = ChatRequest {
                model: self.model.clone(),
                messages: messages.clone(),
                temperature: Some(0.0),
                max_tokens: Some(4096),
                tools: Some(tools.clone()),
            };

            let response = self.client.chat(&request).await?;
            let choice = &response.choices[0];

            match choice.finish_reason {
                Some(FinishReason::ToolCalls) => {
                    let assistant_msg = choice.message.clone();
                    messages.push(assistant_msg.clone());

                    // Log any reasoning the model included
                    if let Some(ref content) = assistant_msg.content {
                        if !content.trim().is_empty() {
                            trace.add_step(AgentStep::Thought(content.clone()));
                        }
                    }

                    let tool_calls = assistant_msg.tool_calls.as_ref().unwrap();

                    for tc in tool_calls {
                        trace.add_step(AgentStep::Action {
                            tool_name: tc.function.name.clone(),
                            arguments: tc.function.arguments.clone(),
                        });

                        let result = self.registry.execute_call(tc).await;
                        trace.add_step(AgentStep::Observation(result.content.clone()));

                        messages.push(result.to_message());
                    }
                }
                Some(FinishReason::Stop) | None => {
                    let answer = choice
                        .message
                        .content
                        .clone()
                        .unwrap_or_else(|| "(empty response)".to_string());

                    trace.add_step(AgentStep::FinalAnswer(answer.clone()));
                    trace.final_answer = Some(answer);
                    return Ok(trace);
                }
                _ => {
                    trace.add_step(AgentStep::Error(format!(
                        "Unexpected finish reason at step {step}"
                    )));
                    break;
                }
            }
        }

        trace.add_step(AgentStep::Error(format!(
            "Reached max steps ({})",
            self.max_steps
        )));

        Ok(trace)
    }
}

#[derive(Debug)]
pub struct AgentTrace {
    pub task: String,
    pub steps: Vec<AgentStep>,
    pub final_answer: Option<String>,
}

impl AgentTrace {
    fn new(task: String) -> Self {
        Self {
            task,
            steps: Vec::new(),
            final_answer: None,
        }
    }

    fn add_step(&mut self, step: AgentStep) {
        eprintln!("[Step {}] {}", self.steps.len() + 1, &step);
        self.steps.push(step);
    }

    pub fn succeeded(&self) -> bool {
        self.final_answer.is_some()
    }

    pub fn tool_calls_count(&self) -> usize {
        self.steps
            .iter()
            .filter(|s| matches!(s, AgentStep::Action { .. }))
            .count()
    }
}
```

The `AgentTrace` is important. In production, you need to inspect *why* an agent did what it did. Logging each step makes debugging possible when things go sideways — and they will go sideways.

## The Plan-then-Execute Agent

For complex tasks, thinking step by step is inefficient. The model wastes tokens re-reading context every iteration. Better to plan upfront:

```rust
#[derive(Debug, Deserialize)]
pub struct Plan {
    pub goal: String,
    pub steps: Vec<PlanStep>,
}

#[derive(Debug, Deserialize)]
pub struct PlanStep {
    pub description: String,
    pub tool: Option<String>,
    pub expected_output: String,
    pub depends_on: Vec<usize>,
}

pub struct PlanExecuteAgent {
    client: LlmClient,
    registry: ToolRegistry,
    model: String,
}

impl PlanExecuteAgent {
    pub fn new(client: LlmClient, registry: ToolRegistry) -> Self {
        Self {
            client,
            registry,
            model: "gpt-4o".to_string(),
        }
    }

    async fn create_plan(&self, task: &str) -> Result<Plan, LlmError> {
        let tools_desc: Vec<String> = self
            .registry
            .tools
            .iter()
            .map(|(name, tool)| format!("- {name}: {}", tool.description()))
            .collect();

        let prompt = format!(
            "Create a step-by-step plan to accomplish this task.\n\
             \n\
             Task: {task}\n\
             \n\
             Available tools:\n{}\n\
             \n\
             Respond with a JSON object with this structure:\n\
             {{\n\
               \"goal\": \"overall goal\",\n\
               \"steps\": [\n\
                 {{\n\
                   \"description\": \"what this step does\",\n\
                   \"tool\": \"tool_name or null if no tool needed\",\n\
                   \"expected_output\": \"what we expect\",\n\
                   \"depends_on\": [0, 1]\n\
                 }}\n\
               ]\n\
             }}",
            tools_desc.join("\n")
        );

        let response = self
            .client
            .builder()
            .system("You are a planning assistant. Output valid JSON only.")
            .user(&prompt)
            .temperature(0.0)
            .send()
            .await?;

        let content = response.choices[0]
            .message
            .content
            .as_ref()
            .ok_or(LlmError::EmptyResponse)?;

        // Strip markdown code fences if present
        let json_str = content
            .trim()
            .strip_prefix("```json")
            .unwrap_or(content.trim())
            .strip_prefix("```")
            .unwrap_or(content.trim())
            .strip_suffix("```")
            .unwrap_or(content.trim());

        let plan: Plan = serde_json::from_str(json_str)
            .map_err(|e| LlmError::Deserialize(format!("{e}: {json_str}")))?;

        Ok(plan)
    }

    pub async fn run(&self, task: &str) -> Result<PlanExecutionResult, LlmError> {
        let plan = self.create_plan(task).await?;
        eprintln!("Plan created with {} steps:", plan.steps.len());
        for (i, step) in plan.steps.iter().enumerate() {
            eprintln!("  {i}: {} (tool: {:?})", step.description, step.tool);
        }

        let mut step_results: Vec<Option<String>> = vec![None; plan.steps.len()];

        for (i, step) in plan.steps.iter().enumerate() {
            // Check dependencies are satisfied
            for &dep in &step.depends_on {
                if dep >= i || step_results[dep].is_none() {
                    return Err(LlmError::Api {
                        status: 0,
                        message: format!("Step {i} depends on unsatisfied step {dep}"),
                    });
                }
            }

            let result = if let Some(ref tool_name) = step.tool {
                // Use the model to generate tool arguments based on context
                let context: String = step
                    .depends_on
                    .iter()
                    .filter_map(|&dep| {
                        step_results[dep]
                            .as_ref()
                            .map(|r| format!("Step {dep} result: {r}"))
                    })
                    .collect::<Vec<_>>()
                    .join("\n");

                let arg_prompt = format!(
                    "Generate the JSON arguments for the '{}' tool.\n\
                     Step description: {}\n\
                     Context from previous steps:\n{}\n\
                     \n\
                     Output only the JSON arguments object.",
                    tool_name, step.description, context
                );

                let arg_response = self
                    .client
                    .builder()
                    .system("Output valid JSON only. No explanation.")
                    .user(&arg_prompt)
                    .temperature(0.0)
                    .send()
                    .await?;

                let arguments = arg_response.choices[0]
                    .message
                    .content
                    .clone()
                    .unwrap_or_else(|| "{}".to_string());

                let tool = self
                    .registry
                    .get(tool_name)
                    .ok_or_else(|| LlmError::Api {
                        status: 0,
                        message: format!("Tool '{tool_name}' not found"),
                    })?;

                match tool.execute(&arguments).await {
                    Ok(r) => r,
                    Err(e) => format!("Tool error: {e}"),
                }
            } else {
                // No tool — this is a reasoning step
                step.expected_output.clone()
            };

            eprintln!("Step {i} completed: {}", &result[..result.len().min(100)]);
            step_results[i] = Some(result);
        }

        // Synthesize final answer
        let all_results: String = step_results
            .iter()
            .enumerate()
            .filter_map(|(i, r)| r.as_ref().map(|r| format!("Step {i}: {r}")))
            .collect::<Vec<_>>()
            .join("\n\n");

        let synthesis = self
            .client
            .builder()
            .system("Synthesize the results of a multi-step plan into a clear answer.")
            .user(&format!(
                "Original task: {task}\n\nStep results:\n{all_results}"
            ))
            .temperature(0.3)
            .send()
            .await?;

        Ok(PlanExecutionResult {
            plan,
            step_results: step_results.into_iter().flatten().collect(),
            final_answer: synthesis.choices[0]
                .message
                .content
                .clone()
                .unwrap_or_default(),
        })
    }
}

#[derive(Debug)]
pub struct PlanExecutionResult {
    pub plan: Plan,
    pub step_results: Vec<String>,
    pub final_answer: String,
}
```

The plan-then-execute pattern has a big advantage: you can inspect and modify the plan before execution. In a production system, you might show the plan to a human for approval before running it. This is way harder with ReAct, where each step emerges dynamically.

## State Machine Agent

For the most control, model the agent as an explicit state machine. This is my preferred approach for production systems:

```rust
#[derive(Debug, Clone, PartialEq)]
pub enum AgentState {
    Idle,
    Thinking,
    Acting { tool: String, args: String },
    Observing { result: String },
    Reflecting { assessment: String },
    Finished { answer: String },
    Failed { reason: String },
}

pub struct StateMachineAgent {
    client: LlmClient,
    registry: ToolRegistry,
    state: AgentState,
    history: Vec<AgentState>,
    max_transitions: usize,
}

impl StateMachineAgent {
    pub fn new(client: LlmClient, registry: ToolRegistry) -> Self {
        Self {
            client,
            registry,
            state: AgentState::Idle,
            history: Vec::new(),
            max_transitions: 20,
        }
    }

    fn transition(&mut self, new_state: AgentState) {
        eprintln!("State: {:?} -> {:?}", self.state, new_state);
        self.history.push(self.state.clone());
        self.state = new_state;
    }

    fn is_terminal(&self) -> bool {
        matches!(self.state, AgentState::Finished { .. } | AgentState::Failed { .. })
    }

    pub async fn run(&mut self, task: &str) -> Result<String, LlmError> {
        self.transition(AgentState::Thinking);
        let mut messages = vec![
            Message {
                role: Role::User,
                content: Some(task.to_string()),
                tool_calls: None,
                tool_call_id: None,
            },
        ];

        let mut transitions = 0;

        while !self.is_terminal() && transitions < self.max_transitions {
            transitions += 1;

            match &self.state {
                AgentState::Thinking => {
                    let response = self
                        .client
                        .builder()
                        .system("Solve the task. Use tools when needed.")
                        .user(&format!(
                            "Task: {task}\n\nHistory: {} steps taken",
                            self.history.len()
                        ))
                        .temperature(0.0)
                        .send()
                        .await?;

                    let choice = &response.choices[0];
                    match choice.finish_reason {
                        Some(FinishReason::ToolCalls) => {
                            if let Some(ref tcs) = choice.message.tool_calls {
                                let tc = &tcs[0];
                                self.transition(AgentState::Acting {
                                    tool: tc.function.name.clone(),
                                    args: tc.function.arguments.clone(),
                                });
                            }
                        }
                        _ => {
                            let answer = choice
                                .message
                                .content
                                .clone()
                                .unwrap_or_default();
                            self.transition(AgentState::Finished { answer });
                        }
                    }
                }

                AgentState::Acting { tool, args } => {
                    let tool_name = tool.clone();
                    let tool_args = args.clone();

                    match self.registry.get(&tool_name) {
                        Some(t) => match t.execute(&tool_args).await {
                            Ok(result) => {
                                self.transition(AgentState::Observing {
                                    result: result.clone(),
                                });
                                messages.push(Message {
                                    role: Role::Tool,
                                    content: Some(result),
                                    tool_calls: None,
                                    tool_call_id: Some("call".to_string()),
                                });
                            }
                            Err(e) => {
                                self.transition(AgentState::Failed {
                                    reason: format!("Tool error: {e}"),
                                });
                            }
                        },
                        None => {
                            self.transition(AgentState::Failed {
                                reason: format!("Unknown tool: {tool_name}"),
                            });
                        }
                    }
                }

                AgentState::Observing { .. } => {
                    // After observation, go back to thinking
                    self.transition(AgentState::Thinking);
                }

                _ => break,
            }
        }

        match &self.state {
            AgentState::Finished { answer } => Ok(answer.clone()),
            AgentState::Failed { reason } => Err(LlmError::Api {
                status: 0,
                message: reason.clone(),
            }),
            _ => Err(LlmError::Api {
                status: 0,
                message: format!("Agent stuck in state: {:?}", self.state),
            }),
        }
    }
}
```

Why go through the ceremony of an explicit state machine? Because you can now:

- **Serialize agent state** and resume later (long-running tasks)
- **Visualize the state graph** for debugging
- **Add guards** on transitions (e.g., "can't call tool X more than 3 times")
- **Test specific transitions** without running the full loop

## Guardrails

Every production agent needs guardrails. Here are the ones I always implement:

```rust
pub struct AgentGuardrails {
    /// Maximum total tokens across all LLM calls
    max_total_tokens: u64,
    /// Maximum number of tool calls
    max_tool_calls: u32,
    /// Disallowed tool sequences (prevent loops)
    disallowed_sequences: Vec<Vec<String>>,
    /// Budget limit in USD
    max_cost_usd: f64,

    // Runtime tracking
    tokens_used: u64,
    tool_calls_made: u32,
    recent_tools: Vec<String>,
    estimated_cost: f64,
}

impl AgentGuardrails {
    pub fn check_tool_call(&mut self, tool_name: &str) -> Result<(), String> {
        self.tool_calls_made += 1;
        if self.tool_calls_made > self.max_tool_calls {
            return Err(format!(
                "Tool call limit exceeded ({}/{})",
                self.tool_calls_made, self.max_tool_calls
            ));
        }

        // Check for loops
        self.recent_tools.push(tool_name.to_string());
        for seq in &self.disallowed_sequences {
            if self.recent_tools.ends_with(seq) {
                return Err(format!(
                    "Detected disallowed tool sequence: {:?}",
                    seq
                ));
            }
        }

        Ok(())
    }

    pub fn record_usage(&mut self, tokens: u64, cost: f64) -> Result<(), String> {
        self.tokens_used += tokens;
        self.estimated_cost += cost;

        if self.tokens_used > self.max_total_tokens {
            return Err(format!(
                "Token limit exceeded ({}/{})",
                self.tokens_used, self.max_total_tokens
            ));
        }

        if self.estimated_cost > self.max_cost_usd {
            return Err(format!(
                "Cost limit exceeded (${:.4}/${:.4})",
                self.estimated_cost, self.max_cost_usd
            ));
        }

        Ok(())
    }
}
```

The loop detection is critical. I once had an agent that alternated between "search for X" and "search for Y" endlessly because neither search returned what it expected. A 3-item sequence detector catches that pattern.

## Choosing the Right Architecture

Here's my rule of thumb after building dozens of agents:

- **Simple Q&A with tools**: ReAct. Don't overthink it.
- **Multi-step workflows** (research, report generation): Plan-then-Execute. Especially if you want human-in-the-loop approval.
- **Production systems with SLAs**: State machine. The explicitness is worth the boilerplate.
- **Self-improving tasks** (code generation, writing): Reflexion. But honestly, most people should start with ReAct and only switch when they hit its limits.

The temptation is to build the most sophisticated architecture from day one. Resist it. Start with ReAct, measure where it fails, and only add complexity to address specific failure modes.

## What's Next

We've built three agent architectures, each with different trade-offs. In Lesson 6, we'll implement MCP (Model Context Protocol) servers — the emerging standard for giving AI models access to external tools and data sources. If agents are the brain, MCP is the nervous system.
