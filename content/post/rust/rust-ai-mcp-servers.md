---
title: "Lesson 6: Building MCP Servers in Rust — Model Context Protocol"
author: Atharva Pandey
series: "Rust and AI/LLM Integration"
lesson: 6
keywords: [Rust, rust, AI, LLM, MCP, Model Context Protocol, server, tools, resources, stdio]
tags: [Rust tutorial, rust, ai, llm]
date: '2025-08-22T10:23:00.000Z'
---

The first time I heard about MCP, I dismissed it as yet another protocol nobody would adopt. Then Claude Desktop shipped with MCP support, then Cursor, then Windsurf, then half the AI tools I use daily. Turns out when Anthropic publishes a spec and immediately supports it in their flagship products, adoption happens fast.

MCP — Model Context Protocol — is a standardized way for AI models to discover and use tools, access data sources, and interact with external systems. Think of it as USB for AI: a universal interface so models don't need custom integrations for every data source. And Rust is a fantastic language for building MCP servers because they need to be fast, reliable, and run for a long time without leaking memory.

## What MCP Actually Is

At its core, MCP uses JSON-RPC 2.0 over stdio or HTTP+SSE. A server exposes three types of capabilities:

1. **Tools** — Functions the model can call (like the tool calling we built in Lesson 3, but standardized)
2. **Resources** — Data the model can read (files, database records, API responses)
3. **Prompts** — Reusable prompt templates with parameters

The transport is intentionally simple. For local servers, it's stdin/stdout. For remote servers, it's HTTP with Server-Sent Events. No gRPC, no WebSockets, no custom binary protocols.

## The JSON-RPC Layer

Let's start from the ground up. Every MCP message is a JSON-RPC 2.0 request or response:

```rust
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Serialize, Deserialize)]
pub struct JsonRpcRequest {
    pub jsonrpc: String,
    pub id: Option<Value>,
    pub method: String,
    #[serde(default)]
    pub params: Option<Value>,
}

#[derive(Debug, Serialize)]
pub struct JsonRpcResponse {
    pub jsonrpc: String,
    pub id: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<JsonRpcError>,
}

#[derive(Debug, Serialize)]
pub struct JsonRpcError {
    pub code: i32,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<Value>,
}

impl JsonRpcResponse {
    pub fn success(id: Option<Value>, result: Value) -> Self {
        Self {
            jsonrpc: "2.0".to_string(),
            id,
            result: Some(result),
            error: None,
        }
    }

    pub fn error(id: Option<Value>, code: i32, message: String) -> Self {
        Self {
            jsonrpc: "2.0".to_string(),
            id,
            result: None,
            error: Some(JsonRpcError {
                code,
                message,
                data: None,
            }),
        }
    }
}
```

## MCP Server Skeleton

The server lifecycle has three phases: initialization, normal operation, and shutdown. During init, the client and server exchange capabilities:

```rust
use std::collections::HashMap;
use std::io::{self, BufRead, Write};

#[derive(Debug, Serialize)]
pub struct ServerCapabilities {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tools: Option<ToolsCapability>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub resources: Option<ResourcesCapability>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub prompts: Option<PromptsCapability>,
}

#[derive(Debug, Serialize)]
pub struct ToolsCapability {}

#[derive(Debug, Serialize)]
pub struct ResourcesCapability {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub subscribe: Option<bool>,
}

#[derive(Debug, Serialize)]
pub struct PromptsCapability {}

#[derive(Debug, Serialize)]
pub struct ServerInfo {
    pub name: String,
    pub version: String,
}

pub struct McpServer {
    info: ServerInfo,
    capabilities: ServerCapabilities,
    tools: HashMap<String, Box<dyn McpTool>>,
    resources: HashMap<String, Box<dyn McpResource>>,
}

pub trait McpTool: Send + Sync {
    fn name(&self) -> &str;
    fn description(&self) -> &str;
    fn input_schema(&self) -> Value;
    fn execute(&self, arguments: Value) -> Result<Vec<ToolContent>, String>;
}

#[derive(Debug, Serialize)]
#[serde(tag = "type")]
pub enum ToolContent {
    #[serde(rename = "text")]
    Text { text: String },
    #[serde(rename = "image")]
    Image { data: String, mime_type: String },
}

pub trait McpResource: Send + Sync {
    fn uri(&self) -> &str;
    fn name(&self) -> &str;
    fn description(&self) -> &str;
    fn mime_type(&self) -> &str;
    fn read(&self) -> Result<Vec<ResourceContent>, String>;
}

#[derive(Debug, Serialize)]
pub struct ResourceContent {
    pub uri: String,
    pub text: Option<String>,
    pub blob: Option<String>, // base64
    pub mime_type: Option<String>,
}
```

## The Message Loop

The stdio transport reads one JSON-RPC message per line from stdin and writes responses to stdout. stderr is for logging — never write protocol messages to stderr:

```rust
impl McpServer {
    pub fn new(name: &str, version: &str) -> Self {
        Self {
            info: ServerInfo {
                name: name.to_string(),
                version: version.to_string(),
            },
            capabilities: ServerCapabilities {
                tools: Some(ToolsCapability {}),
                resources: Some(ResourcesCapability { subscribe: None }),
                prompts: None,
            },
            tools: HashMap::new(),
            resources: HashMap::new(),
        }
    }

    pub fn add_tool(&mut self, tool: impl McpTool + 'static) {
        let name = tool.name().to_string();
        self.tools.insert(name, Box::new(tool));
    }

    pub fn add_resource(&mut self, resource: impl McpResource + 'static) {
        let uri = resource.uri().to_string();
        self.resources.insert(uri, Box::new(resource));
    }

    pub fn run(&self) -> io::Result<()> {
        let stdin = io::stdin();
        let mut stdout = io::stdout();

        eprintln!("MCP server '{}' v{} starting", self.info.name, self.info.version);

        for line in stdin.lock().lines() {
            let line = line?;
            if line.trim().is_empty() {
                continue;
            }

            let request: JsonRpcRequest = match serde_json::from_str(&line) {
                Ok(r) => r,
                Err(e) => {
                    eprintln!("Failed to parse request: {e}");
                    let response = JsonRpcResponse::error(
                        None,
                        -32700,
                        format!("Parse error: {e}"),
                    );
                    let out = serde_json::to_string(&response).unwrap();
                    writeln!(stdout, "{out}")?;
                    stdout.flush()?;
                    continue;
                }
            };

            eprintln!("Received: {} (id: {:?})", request.method, request.id);

            let response = self.handle_request(&request);
            let out = serde_json::to_string(&response).unwrap();
            writeln!(stdout, "{out}")?;
            stdout.flush()?;
        }

        eprintln!("MCP server shutting down");
        Ok(())
    }

    fn handle_request(&self, request: &JsonRpcRequest) -> JsonRpcResponse {
        match request.method.as_str() {
            "initialize" => self.handle_initialize(request),
            "initialized" => {
                // Notification — no response needed, but we return one
                // because the message loop always writes
                JsonRpcResponse::success(request.id.clone(), Value::Null)
            }
            "tools/list" => self.handle_tools_list(request),
            "tools/call" => self.handle_tools_call(request),
            "resources/list" => self.handle_resources_list(request),
            "resources/read" => self.handle_resources_read(request),
            "ping" => JsonRpcResponse::success(
                request.id.clone(),
                serde_json::json!({}),
            ),
            _ => JsonRpcResponse::error(
                request.id.clone(),
                -32601,
                format!("Method not found: {}", request.method),
            ),
        }
    }

    fn handle_initialize(&self, request: &JsonRpcRequest) -> JsonRpcResponse {
        JsonRpcResponse::success(
            request.id.clone(),
            serde_json::json!({
                "protocolVersion": "2024-11-05",
                "capabilities": self.capabilities,
                "serverInfo": self.info,
            }),
        )
    }

    fn handle_tools_list(&self, request: &JsonRpcRequest) -> JsonRpcResponse {
        let tools: Vec<Value> = self
            .tools
            .values()
            .map(|tool| {
                serde_json::json!({
                    "name": tool.name(),
                    "description": tool.description(),
                    "inputSchema": tool.input_schema(),
                })
            })
            .collect();

        JsonRpcResponse::success(
            request.id.clone(),
            serde_json::json!({ "tools": tools }),
        )
    }

    fn handle_tools_call(&self, request: &JsonRpcRequest) -> JsonRpcResponse {
        let params = match &request.params {
            Some(p) => p,
            None => {
                return JsonRpcResponse::error(
                    request.id.clone(),
                    -32602,
                    "Missing params".to_string(),
                );
            }
        };

        let tool_name = params["name"].as_str().unwrap_or("");
        let arguments = params.get("arguments").cloned().unwrap_or(Value::Object(Default::default()));

        let tool = match self.tools.get(tool_name) {
            Some(t) => t,
            None => {
                return JsonRpcResponse::error(
                    request.id.clone(),
                    -32602,
                    format!("Unknown tool: {tool_name}"),
                );
            }
        };

        match tool.execute(arguments) {
            Ok(content) => JsonRpcResponse::success(
                request.id.clone(),
                serde_json::json!({
                    "content": content,
                    "isError": false,
                }),
            ),
            Err(e) => JsonRpcResponse::success(
                request.id.clone(),
                serde_json::json!({
                    "content": [{ "type": "text", "text": e }],
                    "isError": true,
                }),
            ),
        }
    }

    fn handle_resources_list(&self, request: &JsonRpcRequest) -> JsonRpcResponse {
        let resources: Vec<Value> = self
            .resources
            .values()
            .map(|r| {
                serde_json::json!({
                    "uri": r.uri(),
                    "name": r.name(),
                    "description": r.description(),
                    "mimeType": r.mime_type(),
                })
            })
            .collect();

        JsonRpcResponse::success(
            request.id.clone(),
            serde_json::json!({ "resources": resources }),
        )
    }

    fn handle_resources_read(&self, request: &JsonRpcRequest) -> JsonRpcResponse {
        let uri = request
            .params
            .as_ref()
            .and_then(|p| p["uri"].as_str())
            .unwrap_or("");

        let resource = match self.resources.get(uri) {
            Some(r) => r,
            None => {
                return JsonRpcResponse::error(
                    request.id.clone(),
                    -32602,
                    format!("Unknown resource: {uri}"),
                );
            }
        };

        match resource.read() {
            Ok(contents) => JsonRpcResponse::success(
                request.id.clone(),
                serde_json::json!({ "contents": contents }),
            ),
            Err(e) => JsonRpcResponse::error(
                request.id.clone(),
                -32603,
                format!("Resource read failed: {e}"),
            ),
        }
    }
}
```

## Building a Practical MCP Server

Let's build something useful — an MCP server that exposes file system operations and a simple key-value store:

```rust
use std::fs;
use std::path::{Path, PathBuf};

// Tool: Read a file
struct ReadFileTool {
    allowed_root: PathBuf,
}

impl McpTool for ReadFileTool {
    fn name(&self) -> &str {
        "read_file"
    }

    fn description(&self) -> &str {
        "Read the contents of a file. Path must be within the allowed directory."
    }

    fn input_schema(&self) -> Value {
        serde_json::json!({
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to the file"
                }
            },
            "required": ["path"]
        })
    }

    fn execute(&self, arguments: Value) -> Result<Vec<ToolContent>, String> {
        let rel_path = arguments["path"]
            .as_str()
            .ok_or("Missing 'path' argument")?;

        let full_path = self.allowed_root.join(rel_path);

        // Security: prevent path traversal
        let canonical = full_path
            .canonicalize()
            .map_err(|e| format!("Invalid path: {e}"))?;

        if !canonical.starts_with(&self.allowed_root) {
            return Err("Path traversal detected — access denied".to_string());
        }

        let content = fs::read_to_string(&canonical)
            .map_err(|e| format!("Failed to read file: {e}"))?;

        Ok(vec![ToolContent::Text { text: content }])
    }
}

// Tool: List directory
struct ListDirTool {
    allowed_root: PathBuf,
}

impl McpTool for ListDirTool {
    fn name(&self) -> &str {
        "list_directory"
    }

    fn description(&self) -> &str {
        "List files and directories at a path."
    }

    fn input_schema(&self) -> Value {
        serde_json::json!({
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative directory path (use '.' for root)"
                }
            },
            "required": ["path"]
        })
    }

    fn execute(&self, arguments: Value) -> Result<Vec<ToolContent>, String> {
        let rel_path = arguments["path"].as_str().unwrap_or(".");
        let full_path = self.allowed_root.join(rel_path);

        let canonical = full_path
            .canonicalize()
            .map_err(|e| format!("Invalid path: {e}"))?;

        if !canonical.starts_with(&self.allowed_root) {
            return Err("Path traversal detected".to_string());
        }

        let mut entries = Vec::new();
        for entry in fs::read_dir(&canonical).map_err(|e| format!("Read dir failed: {e}"))? {
            let entry = entry.map_err(|e| format!("Entry error: {e}"))?;
            let file_type = if entry.file_type().map(|t| t.is_dir()).unwrap_or(false) {
                "dir"
            } else {
                "file"
            };
            let name = entry.file_name().to_string_lossy().to_string();
            entries.push(format!("[{file_type}] {name}"));
        }

        entries.sort();
        Ok(vec![ToolContent::Text {
            text: entries.join("\n"),
        }])
    }
}

// Tool: Search files by content
struct SearchTool {
    allowed_root: PathBuf,
}

impl McpTool for SearchTool {
    fn name(&self) -> &str {
        "search_files"
    }

    fn description(&self) -> &str {
        "Search for files containing a text pattern. Returns matching lines with file paths."
    }

    fn input_schema(&self) -> Value {
        serde_json::json!({
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Text pattern to search for"
                },
                "file_extension": {
                    "type": "string",
                    "description": "Optional file extension filter (e.g., 'rs', 'toml')"
                }
            },
            "required": ["pattern"]
        })
    }

    fn execute(&self, arguments: Value) -> Result<Vec<ToolContent>, String> {
        let pattern = arguments["pattern"]
            .as_str()
            .ok_or("Missing 'pattern'")?;
        let extension = arguments["file_extension"].as_str();

        let mut results = Vec::new();
        self.search_recursive(&self.allowed_root, pattern, extension, &mut results)
            .map_err(|e| format!("Search failed: {e}"))?;

        if results.is_empty() {
            Ok(vec![ToolContent::Text {
                text: "No matches found.".to_string(),
            }])
        } else {
            Ok(vec![ToolContent::Text {
                text: results.join("\n"),
            }])
        }
    }
}

impl SearchTool {
    fn search_recursive(
        &self,
        dir: &Path,
        pattern: &str,
        extension: Option<&str>,
        results: &mut Vec<String>,
    ) -> io::Result<()> {
        if results.len() >= 100 {
            return Ok(()); // Limit results
        }

        for entry in fs::read_dir(dir)? {
            let entry = entry?;
            let path = entry.path();

            if path.is_dir() {
                // Skip hidden directories
                if path
                    .file_name()
                    .map(|n| n.to_string_lossy().starts_with('.'))
                    .unwrap_or(false)
                {
                    continue;
                }
                self.search_recursive(&path, pattern, extension, results)?;
            } else if path.is_file() {
                // Check extension filter
                if let Some(ext) = extension {
                    if path.extension().map(|e| e.to_string_lossy().to_string())
                        != Some(ext.to_string())
                    {
                        continue;
                    }
                }

                // Read and search
                if let Ok(content) = fs::read_to_string(&path) {
                    for (line_num, line) in content.lines().enumerate() {
                        if line.contains(pattern) {
                            let rel_path = path
                                .strip_prefix(&self.allowed_root)
                                .unwrap_or(&path);
                            results.push(format!(
                                "{}:{}: {}",
                                rel_path.display(),
                                line_num + 1,
                                line.trim()
                            ));

                            if results.len() >= 100 {
                                results.push("... (results truncated at 100)".to_string());
                                return Ok(());
                            }
                        }
                    }
                }
            }
        }

        Ok(())
    }
}
```

## Wiring It Up

```rust
fn main() -> io::Result<()> {
    let project_root = std::env::current_dir()?;

    let mut server = McpServer::new("rust-project-server", "0.1.0");

    server.add_tool(ReadFileTool {
        allowed_root: project_root.clone(),
    });
    server.add_tool(ListDirTool {
        allowed_root: project_root.clone(),
    });
    server.add_tool(SearchTool {
        allowed_root: project_root,
    });

    server.run()
}
```

To use this with Claude Desktop, add it to your MCP config:

```json
{
  "mcpServers": {
    "rust-project": {
      "command": "/path/to/your/compiled/binary",
      "args": [],
      "cwd": "/path/to/your/project"
    }
  }
}
```

That's it. Claude can now browse your project files, search for patterns, and read specific files — all through a standardized protocol.

## Security Considerations

I cannot stress this enough — path traversal is the number one security risk in file-based MCP servers. Always:

1. Canonicalize paths before checking them
2. Verify the canonical path starts with your allowed root
3. Never trust user-supplied paths without validation
4. Limit result sizes to prevent memory exhaustion
5. Consider read-only vs. read-write access carefully

```rust
fn validate_path(root: &Path, user_path: &str) -> Result<PathBuf, String> {
    // Reject obvious traversal attempts early
    if user_path.contains("..") {
        return Err("Path contains '..' — rejected".to_string());
    }

    let full = root.join(user_path);
    let canonical = full
        .canonicalize()
        .map_err(|_| format!("Path does not exist: {user_path}"))?;

    let root_canonical = root
        .canonicalize()
        .map_err(|_| "Root path invalid".to_string())?;

    if !canonical.starts_with(&root_canonical) {
        return Err("Access denied — outside allowed directory".to_string());
    }

    Ok(canonical)
}
```

## Testing MCP Servers

You can test your server by piping JSON-RPC messages through stdin:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}' | cargo run
```

Or build a test harness:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    fn make_request(method: &str, params: Option<Value>) -> JsonRpcRequest {
        JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: Some(Value::Number(1.into())),
            method: method.to_string(),
            params,
        }
    }

    #[test]
    fn test_initialize() {
        let server = McpServer::new("test", "0.1.0");
        let request = make_request("initialize", None);
        let response = server.handle_request(&request);

        assert!(response.result.is_some());
        let result = response.result.unwrap();
        assert_eq!(result["protocolVersion"], "2024-11-05");
    }

    #[test]
    fn test_tools_list() {
        let mut server = McpServer::new("test", "0.1.0");
        server.add_tool(ReadFileTool {
            allowed_root: PathBuf::from("/tmp"),
        });

        let request = make_request("tools/list", None);
        let response = server.handle_request(&request);

        let tools = response.result.unwrap()["tools"].as_array().unwrap().clone();
        assert_eq!(tools.len(), 1);
        assert_eq!(tools[0]["name"], "read_file");
    }
}
```

## What's Next

We've built an MCP server from scratch — JSON-RPC transport, tool execution, resource access, and security guardrails. This is a server that real AI tools can connect to.

In Lesson 7, we're switching gears to on-device inference. No API calls, no network latency, no per-token costs. We'll run models locally using ONNX Runtime and the candle framework — because sometimes the best AI integration is one that doesn't phone home at all.
