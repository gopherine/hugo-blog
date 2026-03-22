---
title: "Lesson 4: WebSocket Servers and Clients — Real-time communication"
author: Atharva Pandey
series: "Rust Networking & Distributed Systems"
lesson: 4
keywords: [Rust, rust, networking, distributed, WebSocket, real-time, tokio-tungstenite, chat]
tags: [Rust tutorial, rust, networking, distributed-systems]
date: '2025-05-18T08:20:00.000Z'
---

I built my first WebSocket server to power a live dashboard that showed deployment status across our fleet. The alternative was polling every 2 seconds — 500 browser tabs hitting the API, each getting back the same "nothing changed" response 99% of the time. WebSockets turned that from 250 requests/second of wasted work into a handful of persistent connections that only sent data when something actually happened.

## HTTP vs WebSockets — When Do You Need Them?

HTTP is request-response. Client asks, server answers. Great for most things. But some use cases fundamentally don't fit that model:

- **Live feeds** — stock prices, sports scores, chat messages. Polling wastes bandwidth and adds latency.
- **Collaborative editing** — Google Docs-style features where multiple users see changes in real time.
- **Gaming** — player positions, game state updates at 60fps. HTTP can't keep up.
- **Notifications** — server needs to push to the client without the client asking.

WebSockets give you a full-duplex, persistent connection. Both sides can send messages at any time. The connection starts as an HTTP upgrade request and then switches to the WebSocket protocol — so it works through proxies and load balancers that understand HTTP.

In Rust, the go-to library is **tokio-tungstenite** for async WebSocket support.

## A Simple Echo Server

```toml
[dependencies]
tokio = { version = "1", features = ["full"] }
tokio-tungstenite = "0.24"
futures-util = "0.3"
```

```rust
use futures_util::{SinkExt, StreamExt};
use tokio::net::TcpListener;
use tokio_tungstenite::accept_async;
use tokio_tungstenite::tungstenite::Message;

#[tokio::main]
async fn main() {
    let listener = TcpListener::bind("127.0.0.1:9001")
        .await
        .expect("Failed to bind");

    println!("WebSocket server on ws://127.0.0.1:9001");

    while let Ok((stream, addr)) = listener.accept().await {
        tokio::spawn(async move {
            let ws_stream = match accept_async(stream).await {
                Ok(ws) => ws,
                Err(e) => {
                    eprintln!("WebSocket handshake failed for {addr}: {e}");
                    return;
                }
            };

            println!("Connected: {addr}");
            let (mut write, mut read) = ws_stream.split();

            while let Some(msg) = read.next().await {
                match msg {
                    Ok(Message::Text(text)) => {
                        println!("From {addr}: {text}");
                        let reply = Message::Text(format!("Echo: {text}"));
                        if write.send(reply).await.is_err() {
                            break;
                        }
                    }
                    Ok(Message::Binary(data)) => {
                        println!("Binary from {addr}: {} bytes", data.len());
                        if write.send(Message::Binary(data)).await.is_err() {
                            break;
                        }
                    }
                    Ok(Message::Ping(payload)) => {
                        if write.send(Message::Pong(payload)).await.is_err() {
                            break;
                        }
                    }
                    Ok(Message::Close(_)) => {
                        println!("Client {addr} sent close");
                        break;
                    }
                    Err(e) => {
                        eprintln!("Error from {addr}: {e}");
                        break;
                    }
                    _ => {}
                }
            }

            println!("Disconnected: {addr}");
        });
    }
}
```

The `split()` call is important — it gives you independent read and write halves. Without it, you'd need to own the entire connection to send a message, which means you couldn't read and write concurrently. The split lets you pass the write half to a different task if needed.

## A Chat Server

Let's build something more interesting — a broadcast chat server where every message from any client gets sent to all other connected clients. This is the classic WebSocket use case and it demonstrates the key architectural pattern: shared state between connections.

```rust
use futures_util::{SinkExt, StreamExt};
use std::collections::HashMap;
use std::sync::Arc;
use tokio::net::TcpListener;
use tokio::sync::{broadcast, RwLock};
use tokio_tungstenite::accept_async;
use tokio_tungstenite::tungstenite::Message;

type Peers = Arc<RwLock<HashMap<String, String>>>; // addr -> username

#[derive(Clone, Debug)]
struct ChatMessage {
    username: String,
    content: String,
}

#[tokio::main]
async fn main() {
    let listener = TcpListener::bind("127.0.0.1:9001")
        .await
        .expect("Failed to bind");

    let (broadcast_tx, _) = broadcast::channel::<ChatMessage>(256);
    let peers: Peers = Arc::new(RwLock::new(HashMap::new()));

    println!("Chat server on ws://127.0.0.1:9001");

    while let Ok((stream, addr)) = listener.accept().await {
        let broadcast_tx = broadcast_tx.clone();
        let mut broadcast_rx = broadcast_tx.subscribe();
        let peers = peers.clone();
        let addr_str = addr.to_string();

        tokio::spawn(async move {
            let ws_stream = match accept_async(stream).await {
                Ok(ws) => ws,
                Err(e) => {
                    eprintln!("Handshake failed for {addr}: {e}");
                    return;
                }
            };

            let (mut write, mut read) = ws_stream.split();

            // First message is the username
            let username = match read.next().await {
                Some(Ok(Message::Text(name))) => name.trim().to_string(),
                _ => {
                    eprintln!("Client {addr} didn't send username");
                    return;
                }
            };

            peers.write().await.insert(addr_str.clone(), username.clone());

            let join_msg = format!(">>> {username} joined the chat");
            let _ = write.send(Message::Text(format!("Welcome, {username}!"))).await;
            let _ = broadcast_tx.send(ChatMessage {
                username: "system".into(),
                content: join_msg,
            });

            println!("{username} connected from {addr}");

            // Spawn a task to forward broadcast messages to this client
            let username_clone = username.clone();
            let write = Arc::new(tokio::sync::Mutex::new(write));
            let write_clone = write.clone();

            let forward_task = tokio::spawn(async move {
                while let Ok(msg) = broadcast_rx.recv().await {
                    // Don't echo back to sender
                    if msg.username == username_clone {
                        continue;
                    }
                    let text = format!("[{}] {}", msg.username, msg.content);
                    let mut w = write_clone.lock().await;
                    if w.send(Message::Text(text)).await.is_err() {
                        break;
                    }
                }
            });

            // Read messages from this client
            while let Some(msg) = read.next().await {
                match msg {
                    Ok(Message::Text(text)) => {
                        let text = text.trim().to_string();
                        if text.is_empty() {
                            continue;
                        }
                        println!("[{username}] {text}");
                        let _ = broadcast_tx.send(ChatMessage {
                            username: username.clone(),
                            content: text,
                        });
                    }
                    Ok(Message::Close(_)) | Err(_) => break,
                    _ => {}
                }
            }

            // Cleanup
            forward_task.abort();
            peers.write().await.remove(&addr_str);

            let _ = broadcast_tx.send(ChatMessage {
                username: "system".into(),
                content: format!(">>> {username} left the chat"),
            });

            println!("{username} disconnected");
        });
    }
}
```

The architecture here is worth studying. Each connection has two concurrent loops — one reading from the WebSocket and publishing to the broadcast channel, and one receiving from the broadcast channel and writing to the WebSocket. The broadcast channel is the glue that connects all clients without them knowing about each other.

I used `broadcast` instead of `mpsc` because every subscriber gets every message. With `mpsc`, you'd need to maintain a list of senders and manually fan out messages. Broadcast handles that automatically — at the cost of potentially dropping messages if a slow consumer falls behind (configurable via the channel capacity).

## A WebSocket Client

```rust
use futures_util::{SinkExt, StreamExt};
use tokio_tungstenite::connect_async;
use tokio_tungstenite::tungstenite::Message;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let url = "ws://127.0.0.1:9001";
    let (ws_stream, response) = connect_async(url).await?;

    println!("Connected to {url}");
    println!("Response status: {}", response.status());

    let (mut write, mut read) = ws_stream.split();

    // Send username
    write.send(Message::Text("rustacean".into())).await?;

    // Spawn reader
    let reader = tokio::spawn(async move {
        while let Some(msg) = read.next().await {
            match msg {
                Ok(Message::Text(text)) => println!("{text}"),
                Ok(Message::Close(_)) => {
                    println!("Server closed connection");
                    break;
                }
                Err(e) => {
                    eprintln!("Error: {e}");
                    break;
                }
                _ => {}
            }
        }
    });

    // Read from stdin and send
    let stdin = tokio::io::stdin();
    let mut stdin_reader = tokio::io::BufReader::new(stdin);
    let mut line = String::new();

    loop {
        line.clear();
        use tokio::io::AsyncBufReadExt;
        match stdin_reader.read_line(&mut line).await {
            Ok(0) => break, // EOF
            Ok(_) => {
                let trimmed = line.trim();
                if trimmed == "/quit" {
                    break;
                }
                write.send(Message::Text(trimmed.to_string())).await?;
            }
            Err(e) => {
                eprintln!("stdin error: {e}");
                break;
            }
        }
    }

    let _ = write.send(Message::Close(None)).await;
    let _ = reader.await;

    Ok(())
}
```

## Integrating with Axum

If you're already running an Axum web server, you probably don't want a separate WebSocket listener. Axum has built-in WebSocket support:

```toml
[dependencies]
axum = { version = "0.7", features = ["ws"] }
tokio = { version = "1", features = ["full"] }
futures-util = "0.3"
tower-http = { version = "0.5", features = ["cors"] }
```

```rust
use axum::{
    extract::ws::{Message, WebSocket, WebSocketUpgrade},
    extract::State,
    response::IntoResponse,
    routing::get,
    Router,
};
use futures_util::{SinkExt, StreamExt};
use std::sync::Arc;
use tokio::sync::broadcast;

#[derive(Clone)]
struct AppState {
    tx: broadcast::Sender<String>,
}

async fn ws_handler(
    ws: WebSocketUpgrade,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    ws.on_upgrade(move |socket| handle_socket(socket, state))
}

async fn handle_socket(socket: WebSocket, state: Arc<AppState>) {
    let (mut sender, mut receiver) = socket.split();
    let mut rx = state.tx.subscribe();

    // Forward broadcasts to this client
    let mut send_task = tokio::spawn(async move {
        while let Ok(msg) = rx.recv().await {
            if sender.send(Message::Text(msg)).await.is_err() {
                break;
            }
        }
    });

    // Receive from this client and broadcast
    let tx = state.tx.clone();
    let mut recv_task = tokio::spawn(async move {
        while let Some(Ok(msg)) = receiver.next().await {
            if let Message::Text(text) = msg {
                let _ = tx.send(text);
            }
        }
    });

    // If either task finishes, abort the other
    tokio::select! {
        _ = &mut send_task => recv_task.abort(),
        _ = &mut recv_task => send_task.abort(),
    }
}

#[tokio::main]
async fn main() {
    let (tx, _) = broadcast::channel(256);
    let state = Arc::new(AppState { tx });

    let app = Router::new()
        .route("/ws", get(ws_handler))
        .with_state(state);

    let listener = tokio::net::TcpListener::bind("127.0.0.1:3000")
        .await
        .unwrap();

    println!("Axum + WebSocket on http://127.0.0.1:3000");
    axum::serve(listener, app).await.unwrap();
}
```

This is the pattern I reach for in production. The HTTP and WebSocket endpoints live on the same port, share the same middleware stack (auth, logging, CORS), and the WebSocket upgrade happens seamlessly.

## Heartbeats and Connection Health

WebSocket connections can go stale without either side noticing. A client might lose network, or a NAT gateway might silently drop the connection. Ping/pong frames are the standard solution.

```rust
use std::time::Duration;
use tokio::time::interval;

async fn handle_socket_with_heartbeat(socket: WebSocket) {
    let (mut sender, mut receiver) = socket.split();
    let mut heartbeat = interval(Duration::from_secs(15));
    let timeout = Duration::from_secs(45); // 3 missed heartbeats
    let mut last_pong = tokio::time::Instant::now();

    loop {
        tokio::select! {
            msg = receiver.next() => {
                match msg {
                    Some(Ok(Message::Pong(_))) => {
                        last_pong = tokio::time::Instant::now();
                    }
                    Some(Ok(Message::Text(text))) => {
                        // Handle text message
                        println!("Received: {text}");
                    }
                    Some(Ok(Message::Close(_))) | None => break,
                    Some(Err(_)) => break,
                    _ => {}
                }
            }
            _ = heartbeat.tick() => {
                if last_pong.elapsed() > timeout {
                    println!("Client timed out — no pong in {timeout:?}");
                    break;
                }
                if sender.send(Message::Ping(vec![1, 2, 3, 4])).await.is_err() {
                    break;
                }
            }
        }
    }
}
```

Send a ping every 15 seconds. If you don't get a pong within 45 seconds (three missed intervals), consider the connection dead and clean it up. I've seen production systems leak thousands of dead connections because nobody implemented heartbeats. Don't be that system.

## Scaling WebSocket Servers

Single-server WebSockets are straightforward. Multi-server is where it gets interesting. If user A connects to server 1 and user B connects to server 2, how does A's message reach B?

The answer is always an external pub/sub system — Redis, NATS, or Kafka. Each server subscribes to a shared topic. When a message arrives on any server, it publishes to the topic. All servers receive it and forward to their local clients.

```rust
// Pseudocode for the pattern — we'll cover message queues in lesson 9
//
// For each server instance:
// 1. Subscribe to "chat:messages" on Redis/NATS
// 2. When a WebSocket client sends a message:
//    - Publish to "chat:messages"
// 3. When a message arrives from "chat:messages":
//    - Broadcast to all local WebSocket clients
//
// This makes the WebSocket server stateless (almost).
// The pub/sub system is the shared state.
```

This is the reason I chose broadcast channels in our chat server — the same pattern scales up. Swap the in-process broadcast channel for a network pub/sub system and the rest of the code stays the same.

## What's Next

WebSockets handle the "push data to clients" use case. But there's a piece of infrastructure beneath all of this that most developers take for granted until it breaks — DNS. Next, we'll dig into DNS resolution and build custom resolvers in Rust.
