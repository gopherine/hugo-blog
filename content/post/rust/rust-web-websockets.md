---
title: "Lesson 8: WebSockets with Axum — Real-time in Rust"
author: Atharva Pandey
series: "Rust Web & API Development"
lesson: 8
keywords: [Rust, rust, web, API, axum, WebSocket, real-time, chat, broadcast, tokio]
tags: [Rust tutorial, rust, web, api]
date: '2024-10-16T09:20:00.000Z'
---

A startup I consulted for was polling their REST API every 500 milliseconds to check for new messages. Forty thousand clients, each making two requests per second. That's 80,000 requests per second to check if anything changed — and 99% of the time, nothing had. They switched to WebSockets, dropped their server count from 12 to 2, and their AWS bill fell by 70%. Polling is fine for dashboards that refresh every 30 seconds. For anything real-time, you want WebSockets.

## WebSocket Basics in Axum

Axum supports WebSockets through the `axum::extract::ws` module. The upgrade handshake, frame parsing, and ping/pong handling are built in.

```toml
[dependencies]
axum = { version = "0.7", features = ["ws"] }
tokio = { version = "1", features = ["full"] }
futures = "0.3"
```

The simplest possible WebSocket handler — an echo server:

```rust
use axum::{
    extract::ws::{Message, WebSocket, WebSocketUpgrade},
    response::Response,
    routing::get,
    Router,
};
use futures::{SinkExt, StreamExt};

async fn ws_handler(ws: WebSocketUpgrade) -> Response {
    ws.on_upgrade(handle_socket)
}

async fn handle_socket(mut socket: WebSocket) {
    while let Some(Ok(msg)) = socket.next().await {
        match msg {
            Message::Text(text) => {
                // Echo the message back
                if socket.send(Message::Text(format!("Echo: {}", text))).await.is_err() {
                    break; // Client disconnected
                }
            }
            Message::Close(_) => break,
            _ => {} // Ignore binary, ping, pong
        }
    }
}

#[tokio::main]
async fn main() {
    let app = Router::new().route("/ws", get(ws_handler));

    let listener = tokio::net::TcpListener::bind("0.0.0.0:3000")
        .await
        .unwrap();
    axum::serve(listener, app).await.unwrap();
}
```

The flow: client sends an HTTP GET with an `Upgrade: websocket` header. `WebSocketUpgrade` extracts the upgrade request. `on_upgrade` takes a closure that runs after the upgrade completes. Inside that closure, you have a `WebSocket` — a bidirectional stream of `Message` values.

## Splitting the Socket

The echo example processes messages sequentially — read one, write one, repeat. In practice, you usually need to read and write concurrently. Maybe you're forwarding messages from other clients while also reading from this one. The solution: split the WebSocket into a sender and receiver.

```rust
use futures::{SinkExt, StreamExt};
use tokio::sync::mpsc;

async fn handle_socket(socket: WebSocket) {
    let (mut sender, mut receiver) = socket.split();

    // Channel for sending messages to this client
    let (tx, mut rx) = mpsc::channel::<String>(32);

    // Task: forward messages from the channel to the WebSocket
    let mut send_task = tokio::spawn(async move {
        while let Some(msg) = rx.recv().await {
            if sender.send(Message::Text(msg)).await.is_err() {
                break;
            }
        }
    });

    // Task: read messages from the WebSocket
    let tx_clone = tx.clone();
    let mut recv_task = tokio::spawn(async move {
        while let Some(Ok(msg)) = receiver.next().await {
            match msg {
                Message::Text(text) => {
                    // Process the message — here we just echo
                    let _ = tx_clone.send(format!("Echo: {}", text)).await;
                }
                Message::Close(_) => break,
                _ => {}
            }
        }
    });

    // If either task finishes, abort the other
    tokio::select! {
        _ = &mut send_task => recv_task.abort(),
        _ = &mut recv_task => send_task.abort(),
    }
}
```

The `mpsc` channel decouples message production from WebSocket writing. Any part of your system can send messages to a client through the channel — other WebSocket handlers, background tasks, database change notifications. The send task drains the channel and pushes messages through the socket.

## Building a Chat Room

Real-time applications usually need broadcast — one message going to many clients. Tokio's `broadcast` channel is perfect for this.

```rust
use axum::{
    extract::{ws::{Message, WebSocket, WebSocketUpgrade}, State},
    response::Response,
    routing::get,
    Router,
};
use futures::{SinkExt, StreamExt};
use std::sync::Arc;
use tokio::sync::broadcast;
use serde::{Deserialize, Serialize};

#[derive(Clone, Serialize, Deserialize)]
struct ChatMessage {
    username: String,
    content: String,
    timestamp: String,
}

#[derive(Clone)]
struct AppState {
    tx: broadcast::Sender<String>,
}

async fn ws_handler(
    ws: WebSocketUpgrade,
    State(state): State<AppState>,
) -> Response {
    ws.on_upgrade(move |socket| handle_socket(socket, state))
}

async fn handle_socket(socket: WebSocket, state: AppState) {
    let (mut sender, mut receiver) = socket.split();

    // Subscribe to broadcast channel
    let mut rx = state.tx.subscribe();

    // Task: forward broadcast messages to this client
    let mut send_task = tokio::spawn(async move {
        while let Ok(msg) = rx.recv().await {
            if sender.send(Message::Text(msg)).await.is_err() {
                break;
            }
        }
    });

    // Task: read messages from this client and broadcast them
    let tx = state.tx.clone();
    let mut recv_task = tokio::spawn(async move {
        while let Some(Ok(msg)) = receiver.next().await {
            match msg {
                Message::Text(text) => {
                    // Parse or just broadcast raw
                    let _ = tx.send(text);
                }
                Message::Close(_) => break,
                _ => {}
            }
        }
    });

    tokio::select! {
        _ = &mut send_task => recv_task.abort(),
        _ = &mut recv_task => send_task.abort(),
    }
}

#[tokio::main]
async fn main() {
    let (tx, _rx) = broadcast::channel(1000);
    let state = AppState { tx };

    let app = Router::new()
        .route("/ws", get(ws_handler))
        .with_state(state);

    let listener = tokio::net::TcpListener::bind("0.0.0.0:3000")
        .await
        .unwrap();

    tracing::info!("listening on 3000");
    axum::serve(listener, app).await.unwrap();
}
```

Every connected client subscribes to the same `broadcast::Sender`. When any client sends a message, it goes into the broadcast channel, and every subscriber receives it. This scales well for moderate rooms — hundreds of clients, no problem. For thousands, you'll want sharding or a pub/sub system like Redis.

## Named Rooms and User Management

A real chat system needs multiple rooms and user tracking. Let's build that.

```rust
use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use tokio::sync::{broadcast, RwLock};

#[derive(Clone)]
struct Room {
    tx: broadcast::Sender<String>,
    users: Arc<RwLock<HashSet<String>>>,
}

impl Room {
    fn new() -> Self {
        let (tx, _) = broadcast::channel(1000);
        Self {
            tx,
            users: Arc::new(RwLock::new(HashSet::new())),
        }
    }
}

#[derive(Clone)]
struct ChatState {
    rooms: Arc<RwLock<HashMap<String, Room>>>,
}

impl ChatState {
    fn new() -> Self {
        Self {
            rooms: Arc::new(RwLock::new(HashMap::new())),
        }
    }

    async fn get_or_create_room(&self, name: &str) -> Room {
        // Try read lock first
        {
            let rooms = self.rooms.read().await;
            if let Some(room) = rooms.get(name) {
                return room.clone();
            }
        }
        // Need write lock to create
        let mut rooms = self.rooms.write().await;
        rooms.entry(name.to_string())
            .or_insert_with(Room::new)
            .clone()
    }

    async fn join_room(&self, room_name: &str, username: &str) -> Room {
        let room = self.get_or_create_room(room_name).await;
        room.users.write().await.insert(username.to_string());

        let join_msg = serde_json::json!({
            "type": "system",
            "content": format!("{} joined the room", username),
            "timestamp": chrono::Utc::now().to_rfc3339(),
        });
        let _ = room.tx.send(join_msg.to_string());

        room
    }

    async fn leave_room(&self, room_name: &str, username: &str) {
        let rooms = self.rooms.read().await;
        if let Some(room) = rooms.get(room_name) {
            room.users.write().await.remove(username);

            let leave_msg = serde_json::json!({
                "type": "system",
                "content": format!("{} left the room", username),
                "timestamp": chrono::Utc::now().to_rfc3339(),
            });
            let _ = room.tx.send(leave_msg.to_string());
        }
    }
}
```

The WebSocket handler now takes room and username from the URL:

```rust
use axum::extract::Path;

async fn ws_handler(
    ws: WebSocketUpgrade,
    Path((room_name, username)): Path<(String, String)>,
    State(state): State<ChatState>,
) -> Response {
    ws.on_upgrade(move |socket| {
        handle_chat_socket(socket, state, room_name, username)
    })
}

async fn handle_chat_socket(
    socket: WebSocket,
    state: ChatState,
    room_name: String,
    username: String,
) {
    let room = state.join_room(&room_name, &username).await;
    let (mut sender, mut receiver) = socket.split();
    let mut rx = room.tx.subscribe();

    let mut send_task = tokio::spawn(async move {
        while let Ok(msg) = rx.recv().await {
            if sender.send(Message::Text(msg)).await.is_err() {
                break;
            }
        }
    });

    let tx = room.tx.clone();
    let username_clone = username.clone();
    let mut recv_task = tokio::spawn(async move {
        while let Some(Ok(msg)) = receiver.next().await {
            match msg {
                Message::Text(text) => {
                    let chat_msg = serde_json::json!({
                        "type": "message",
                        "username": username_clone,
                        "content": text,
                        "timestamp": chrono::Utc::now().to_rfc3339(),
                    });
                    let _ = tx.send(chat_msg.to_string());
                }
                Message::Close(_) => break,
                _ => {}
            }
        }
    });

    tokio::select! {
        _ = &mut send_task => recv_task.abort(),
        _ = &mut recv_task => send_task.abort(),
    }

    // Cleanup when socket closes
    state.leave_room(&room_name, &username).await;
}

// Route: /ws/:room/:username
let app = Router::new()
    .route("/ws/:room/:username", get(ws_handler))
    .with_state(ChatState::new());
```

## Heartbeats and Connection Health

WebSocket connections can go stale silently — a client might lose network without sending a close frame. Implement heartbeats to detect dead connections:

```rust
use std::time::Duration;
use tokio::time;

async fn handle_socket_with_heartbeat(mut socket: WebSocket, state: AppState) {
    let (mut sender, mut receiver) = socket.split();
    let mut rx = state.tx.subscribe();

    let mut heartbeat_interval = time::interval(Duration::from_secs(30));

    loop {
        tokio::select! {
            // Incoming message from client
            msg = receiver.next() => {
                match msg {
                    Some(Ok(Message::Text(text))) => {
                        let _ = state.tx.send(text);
                    }
                    Some(Ok(Message::Pong(_))) => {
                        // Client is alive — reset any dead-client timer
                    }
                    Some(Ok(Message::Close(_))) | None => break,
                    _ => {}
                }
            }
            // Broadcast message to send to this client
            msg = rx.recv() => {
                if let Ok(msg) = msg {
                    if sender.send(Message::Text(msg)).await.is_err() {
                        break;
                    }
                }
            }
            // Heartbeat tick
            _ = heartbeat_interval.tick() => {
                if sender.send(Message::Ping(vec![1, 2, 3].into())).await.is_err() {
                    break; // Client is gone
                }
            }
        }
    }
}
```

The `Ping` message gets an automatic `Pong` reply from compliant WebSocket clients. If the `send` fails, the client is disconnected and we break out of the loop.

## Authenticated WebSockets

WebSocket connections should be authenticated. Since you can't send custom headers in the browser's WebSocket API, the common patterns are:

**Query parameter token:**

```rust
#[derive(Deserialize)]
struct WsAuth {
    token: String,
}

async fn ws_handler(
    ws: WebSocketUpgrade,
    Query(auth): Query<WsAuth>,
    State(state): State<AppState>,
) -> Result<Response, AppError> {
    let claims = state.jwt.validate_token(&auth.token)
        .map_err(|_| AppError::unauthorized("Invalid token"))?;

    Ok(ws.on_upgrade(move |socket| {
        handle_socket(socket, state, claims.sub)
    }))
}

// Connect: ws://localhost:3000/ws?token=eyJhbGciOi...
```

**First-message authentication:**

```rust
async fn handle_socket(mut socket: WebSocket, state: AppState) {
    // First message must be the auth token
    let user_id = match socket.next().await {
        Some(Ok(Message::Text(token))) => {
            match state.jwt.validate_token(&token) {
                Ok(claims) => claims.sub,
                Err(_) => {
                    let _ = socket.send(Message::Close(None)).await;
                    return;
                }
            }
        }
        _ => return,
    };

    // Now the socket is authenticated — proceed with normal handling
    let _ = socket.send(Message::Text(
        serde_json::json!({"type": "authenticated", "user_id": user_id}).to_string()
    )).await;

    // ... rest of handler
}
```

I prefer the query parameter approach — it fails fast at the upgrade stage, before allocating resources for the WebSocket connection.

## Testing WebSockets

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::Body;
    use tokio_tungstenite::tungstenite;

    #[tokio::test]
    async fn test_echo_websocket() {
        let (tx, _) = broadcast::channel(100);
        let state = AppState { tx };
        let app = Router::new()
            .route("/ws", get(ws_handler))
            .with_state(state);

        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .unwrap();
        let addr = listener.local_addr().unwrap();

        tokio::spawn(async move {
            axum::serve(listener, app).await.unwrap();
        });

        let (mut ws_stream, _) = tokio_tungstenite::connect_async(
            format!("ws://{}/ws", addr)
        )
        .await
        .expect("Failed to connect");

        // Send a message
        ws_stream
            .send(tungstenite::Message::Text("hello".to_string()))
            .await
            .unwrap();

        // Read the echoed response
        if let Some(Ok(msg)) = ws_stream.next().await {
            assert_eq!(msg.to_text().unwrap(), "Echo: hello");
        }
    }
}
```

Add `tokio-tungstenite` to dev dependencies for testing:

```toml
[dev-dependencies]
tokio-tungstenite = "0.24"
```

WebSockets in Axum are surprisingly ergonomic once you understand the split pattern and broadcast channels. The type system keeps you honest about message handling, and Tokio's select macro makes concurrent read/write natural.

Next: rate limiting — because without it, one aggressive client can bring down your entire service.
