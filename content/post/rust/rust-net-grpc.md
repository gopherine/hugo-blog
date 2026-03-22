---
title: "Lesson 3: gRPC with tonic — High-performance RPC"
author: Atharva Pandey
series: "Rust Networking & Distributed Systems"
lesson: 3
keywords: [Rust, rust, networking, distributed, gRPC, tonic, protobuf, RPC, microservices]
tags: [Rust tutorial, rust, networking, distributed-systems]
date: '2025-05-16T11:30:00.000Z'
---

The first time I used gRPC in production, I was skeptical. We already had REST APIs that worked fine — why add protobuf compilation, code generation, and an entirely new protocol? Then our team grew to four services in three languages, and the answer became painfully obvious. Every REST endpoint had slightly different JSON field naming, different error formats, and documentation that was always a version behind. gRPC eliminated all of that overnight.

## Why gRPC Over REST?

It's not about performance — though gRPC is faster thanks to HTTP/2 multiplexing and binary protobuf encoding. The real win is the **contract**. Your `.proto` file is the single source of truth. Generate clients in Rust, Go, Python, TypeScript — they all speak the exact same protocol. No more arguing about whether it's `user_id` or `userId` or `UserID`.

gRPC also gives you streaming for free. Unary calls (request → response), server streaming, client streaming, bidirectional streaming — all defined in the proto file, all type-safe.

In Rust, the de facto gRPC framework is **tonic**. It's built on hyper and tower, so it inherits excellent HTTP/2 support and a composable middleware stack. Let's build something real.

## Setting Up

```toml
# Cargo.toml
[package]
name = "grpc-demo"
version = "0.1.0"
edition = "2021"

[[bin]]
name = "server"
path = "src/server.rs"

[[bin]]
name = "client"
path = "src/client.rs"

[dependencies]
tonic = "0.12"
prost = "0.13"
tokio = { version = "1", features = ["full"] }
tokio-stream = "0.1"

[build-dependencies]
tonic-build = "0.12"
```

## Defining the Service

Create a `proto/` directory and define your service. I'm going to build a task management service because it's more interesting than yet another greeter.

```protobuf
// proto/tasks.proto
syntax = "proto3";

package tasks;

service TaskService {
    // Unary: create a task
    rpc CreateTask(CreateTaskRequest) returns (Task);

    // Unary: get a task by ID
    rpc GetTask(GetTaskRequest) returns (Task);

    // Server streaming: list all tasks matching a filter
    rpc ListTasks(ListTasksRequest) returns (stream Task);

    // Unary: update task status
    rpc UpdateStatus(UpdateStatusRequest) returns (Task);
}

message CreateTaskRequest {
    string title = 1;
    string description = 2;
    Priority priority = 3;
}

message GetTaskRequest {
    string id = 1;
}

message ListTasksRequest {
    optional Priority priority_filter = 1;
    optional Status status_filter = 2;
}

message UpdateStatusRequest {
    string id = 1;
    Status status = 2;
}

message Task {
    string id = 1;
    string title = 2;
    string description = 3;
    Priority priority = 4;
    Status status = 5;
    int64 created_at = 6;
}

enum Priority {
    PRIORITY_UNSPECIFIED = 0;
    LOW = 1;
    MEDIUM = 2;
    HIGH = 3;
    CRITICAL = 4;
}

enum Status {
    STATUS_UNSPECIFIED = 0;
    TODO = 1;
    IN_PROGRESS = 2;
    DONE = 3;
    CANCELLED = 4;
}
```

A few proto best practices that'll save you headaches: always have an `UNSPECIFIED` zero value for enums (protobuf uses 0 as the default), use `optional` for fields that are genuinely optional, and number your fields carefully because those numbers are the wire format — changing them breaks compatibility.

## Code Generation

The build script tells tonic-build to compile the proto file:

```rust
// build.rs
fn main() -> Result<(), Box<dyn std::error::Error>> {
    tonic_build::compile_protos("proto/tasks.proto")?;
    Ok(())
}
```

This generates Rust types for all your messages and a trait for the server plus a client struct. You never edit the generated code — it lives in your build directory and gets included via `tonic::include_proto!`.

## The Server

```rust
// src/server.rs
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;
use tonic::{Request, Response, Status};

pub mod tasks {
    tonic::include_proto!("tasks");
}

use tasks::task_service_server::{TaskService, TaskServiceServer};
use tasks::{
    CreateTaskRequest, GetTaskRequest, ListTasksRequest, Task,
    UpdateStatusRequest,
};

#[derive(Debug, Default)]
struct TaskStore {
    tasks: RwLock<HashMap<String, Task>>,
    counter: RwLock<u64>,
}

impl TaskStore {
    async fn next_id(&self) -> String {
        let mut counter = self.counter.write().await;
        *counter += 1;
        format!("task-{counter:04}")
    }
}

#[tonic::async_trait]
impl TaskService for Arc<TaskStore> {
    async fn create_task(
        &self,
        request: Request<CreateTaskRequest>,
    ) -> Result<Response<Task>, Status> {
        let req = request.into_inner();

        if req.title.is_empty() {
            return Err(Status::invalid_argument("title is required"));
        }

        let id = self.next_id().await;
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs() as i64;

        let task = Task {
            id: id.clone(),
            title: req.title,
            description: req.description,
            priority: req.priority,
            status: tasks::Status::Todo as i32,
            created_at: now,
        };

        self.tasks.write().await.insert(id, task.clone());
        println!("Created task: {} — {}", task.id, task.title);

        Ok(Response::new(task))
    }

    async fn get_task(
        &self,
        request: Request<GetTaskRequest>,
    ) -> Result<Response<Task>, Status> {
        let id = &request.into_inner().id;

        let tasks = self.tasks.read().await;
        match tasks.get(id) {
            Some(task) => Ok(Response::new(task.clone())),
            None => Err(Status::not_found(format!("Task {id} not found"))),
        }
    }

    type ListTasksStream =
        tokio_stream::wrappers::ReceiverStream<Result<Task, Status>>;

    async fn list_tasks(
        &self,
        request: Request<ListTasksRequest>,
    ) -> Result<Response<Self::ListTasksStream>, Status> {
        let filter = request.into_inner();
        let tasks = self.tasks.read().await;

        let filtered: Vec<Task> = tasks
            .values()
            .filter(|t| {
                if let Some(p) = filter.priority_filter {
                    if t.priority != p {
                        return false;
                    }
                }
                if let Some(s) = filter.status_filter {
                    if t.status != s {
                        return false;
                    }
                }
                true
            })
            .cloned()
            .collect();

        let (tx, rx) = tokio::sync::mpsc::channel(32);

        tokio::spawn(async move {
            for task in filtered {
                if tx.send(Ok(task)).await.is_err() {
                    break; // Client disconnected
                }
            }
        });

        Ok(Response::new(tokio_stream::wrappers::ReceiverStream::new(rx)))
    }

    async fn update_status(
        &self,
        request: Request<UpdateStatusRequest>,
    ) -> Result<Response<Task>, Status> {
        let req = request.into_inner();

        let mut tasks = self.tasks.write().await;
        match tasks.get_mut(&req.id) {
            Some(task) => {
                task.status = req.status;
                println!("Updated {} status to {}", task.id, req.status);
                Ok(Response::new(task.clone()))
            }
            None => Err(Status::not_found(format!("Task {} not found", req.id))),
        }
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let addr = "127.0.0.1:50051".parse()?;
    let store = Arc::new(TaskStore::default());

    println!("TaskService listening on {addr}");

    tonic::transport::Server::builder()
        .add_service(TaskServiceServer::from_arc(store))
        .serve(addr)
        .await?;

    Ok(())
}
```

A few things worth calling out. The `Arc<TaskStore>` implements `TaskService` — tonic needs the service to be `Send + Sync + 'static`, and wrapping in Arc is the standard approach. The `RwLock` gives us concurrent reads and exclusive writes, which is the right primitive for a read-heavy task store.

For the streaming endpoint, we create an `mpsc` channel and spawn a task to feed items into it. The `ReceiverStream` adapter turns the channel receiver into a `Stream` that tonic can send over the wire. If the client disconnects mid-stream, the `send` fails and we break out of the loop — no resource leaks.

## The Client

```rust
// src/client.rs
pub mod tasks {
    tonic::include_proto!("tasks");
}

use tasks::task_service_client::TaskServiceClient;
use tasks::{CreateTaskRequest, GetTaskRequest, ListTasksRequest, UpdateStatusRequest};
use tokio_stream::StreamExt;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut client = TaskServiceClient::connect("http://127.0.0.1:50051").await?;

    // Create some tasks
    let task1 = client
        .create_task(CreateTaskRequest {
            title: "Implement user auth".into(),
            description: "Add JWT-based authentication".into(),
            priority: tasks::Priority::High as i32,
        })
        .await?
        .into_inner();
    println!("Created: {} — {}", task1.id, task1.title);

    let task2 = client
        .create_task(CreateTaskRequest {
            title: "Write API docs".into(),
            description: "Document all endpoints".into(),
            priority: tasks::Priority::Medium as i32,
        })
        .await?
        .into_inner();
    println!("Created: {} — {}", task2.id, task2.title);

    let task3 = client
        .create_task(CreateTaskRequest {
            title: "Fix login bug".into(),
            description: "Users can't log in with special chars in password".into(),
            priority: tasks::Priority::Critical as i32,
        })
        .await?
        .into_inner();
    println!("Created: {} — {}", task3.id, task3.title);

    // Get a specific task
    let fetched = client
        .get_task(GetTaskRequest {
            id: task1.id.clone(),
        })
        .await?
        .into_inner();
    println!("\nFetched: {fetched:?}");

    // Update status
    let updated = client
        .update_status(UpdateStatusRequest {
            id: task1.id.clone(),
            status: tasks::Status::InProgress as i32,
        })
        .await?
        .into_inner();
    println!("Updated: {} is now status={}", updated.id, updated.status);

    // Stream all tasks
    println!("\nAll tasks:");
    let mut stream = client
        .list_tasks(ListTasksRequest {
            priority_filter: None,
            status_filter: None,
        })
        .await?
        .into_inner();

    while let Some(task) = stream.next().await {
        let task = task?;
        println!(
            "  [{id}] {title} (priority={p}, status={s})",
            id = task.id,
            title = task.title,
            p = task.priority,
            s = task.status,
        );
    }

    Ok(())
}
```

## Error Handling in gRPC

gRPC has its own error model — `Status` codes that map to well-defined semantics. This is honestly one of the best parts of gRPC. Instead of every service inventing its own error JSON, you get a standard set of codes.

```rust
use tonic::Status;

// Common status codes and when to use them:
fn demonstrate_errors() {
    // Client sent bad data
    let _ = Status::invalid_argument("email field is malformed");

    // Resource doesn't exist
    let _ = Status::not_found("user 42 not found");

    // Client doesn't have permission
    let _ = Status::permission_denied("admin role required");

    // Resource already exists
    let _ = Status::already_exists("username taken");

    // Precondition failed (e.g., optimistic concurrency)
    let _ = Status::failed_precondition("version mismatch");

    // Server is overloaded
    let _ = Status::resource_exhausted("rate limit exceeded");

    // Something unexpected went wrong
    let _ = Status::internal("database connection failed");

    // Deadline exceeded
    let _ = Status::deadline_exceeded("operation timed out");
}
```

The key discipline: use `internal` sparingly. If you can give the caller a more specific code, do it. `internal` basically means "something went wrong and I can't tell you what," which makes debugging from the client side miserable.

## Interceptors (Middleware)

Tonic's interceptor system lets you add cross-cutting concerns — authentication, logging, metrics — without touching service logic.

```rust
use tonic::{Request, Status};
use tonic::service::Interceptor;

#[derive(Clone)]
struct AuthInterceptor {
    valid_tokens: Vec<String>,
}

impl Interceptor for AuthInterceptor {
    fn call(&mut self, req: Request<()>) -> Result<Request<()>, Status> {
        let token = req
            .metadata()
            .get("authorization")
            .and_then(|v| v.to_str().ok())
            .map(|v| v.trim_start_matches("Bearer "));

        match token {
            Some(t) if self.valid_tokens.iter().any(|v| v == t) => Ok(req),
            Some(_) => Err(Status::unauthenticated("invalid token")),
            None => Err(Status::unauthenticated("missing authorization header")),
        }
    }
}

// Usage on the server:
// let auth = AuthInterceptor { valid_tokens: vec!["secret123".into()] };
// let service = TaskServiceServer::with_interceptor(store, auth);
//
// Usage on the client:
// let channel = tonic::transport::Channel::from_static("http://127.0.0.1:50051")
//     .connect()
//     .await?;
// let client = TaskServiceClient::with_interceptor(channel, move |mut req: Request<()>| {
//     req.metadata_mut().insert(
//         "authorization",
//         "Bearer secret123".parse().unwrap(),
//     );
//     Ok(req)
// });
```

## Reflection and Health Checks

In production, you want two things: gRPC reflection (so tools like `grpcurl` can introspect your service) and health checks (so load balancers know you're alive).

```toml
[dependencies]
tonic-reflection = "0.12"
tonic-health = "0.12"
```

```rust
use tonic_health::server::health_reporter;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let addr = "127.0.0.1:50051".parse()?;
    let store = Arc::new(TaskStore::default());

    // Health check
    let (mut health_reporter, health_service) = health_reporter();
    health_reporter
        .set_serving::<TaskServiceServer<Arc<TaskStore>>>()
        .await;

    // Reflection
    let reflection = tonic_reflection::server::Builder::configure()
        .register_encoded_file_descriptor_set(tasks::FILE_DESCRIPTOR_SET)
        .build_v1()?;

    println!("TaskService listening on {addr}");

    tonic::transport::Server::builder()
        .add_service(health_service)
        .add_service(reflection)
        .add_service(TaskServiceServer::from_arc(store))
        .serve(addr)
        .await?;

    Ok(())
}
```

For the reflection descriptor set, add this to your `build.rs`:

```rust
fn main() -> Result<(), Box<dyn std::error::Error>> {
    tonic_build::configure()
        .file_descriptor_set_path("src/tasks_descriptor.bin")
        .compile_protos(&["proto/tasks.proto"], &["proto/"])?;
    Ok(())
}
```

## When to Choose gRPC

gRPC isn't always the right call. Here's my rough decision framework:

- **Use gRPC** when you have service-to-service communication across teams or languages, when you need streaming, or when strong API contracts matter more than human readability.
- **Use REST** when your consumers are browsers (gRPC-web exists but it's clunky), when you want curl-friendly debugging, or when the overhead of protobuf tooling isn't justified for a simple CRUD API.
- **Use both** via gRPC-gateway if you need to serve both. I've done this in production and it works well — the proto file remains the source of truth and you get REST endpoints for free.

Next lesson, we'll build real-time WebSocket servers and clients — because sometimes you need data pushed to you without asking for it.
