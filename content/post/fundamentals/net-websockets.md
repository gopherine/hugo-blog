---
title: "Lesson 5: WebSockets — Upgrade, framing, vs SSE vs polling"
author: Atharva Pandey
keywords:
  - WebSockets
  - SSE
  - server-sent events
  - long polling
  - real-time
  - networking
  - backend engineering
tags:
  - fundamentals
  - networking
series: "Networking for Backend Engineers"
lesson: 5
date: '2024-07-01T00:00:00.000Z'
---

When I first built a real-time notification system, I reached for WebSockets because that's what everyone said to use for "real-time." Three months later I was debugging connection drops under load, wrestling with proxy timeouts, and fighting with nginx configuration. When I stepped back and actually thought about the access pattern — server pushing notifications to the browser, never the browser sending data to the server — I replaced WebSockets with Server-Sent Events in a weekend. The code got simpler, the proxies stopped complaining, and we had less to maintain. Picking the right tool requires understanding what each one actually does.

## How It Works

**The Problem: HTTP is Request-Response**

HTTP was designed for request-response: client asks, server answers, connection closes (or idles). This is fine for most things, but not for cases where the server needs to push data to the client without the client asking first.

Three main solutions exist: polling, Server-Sent Events, and WebSockets.

**Long Polling**

The simplest "push" mechanism that isn't really push:

1. Client sends a request.
2. Server holds the request open until it has data to send (or a timeout).
3. Server responds with data.
4. Client immediately sends another request.

```
Client          Server
  |--- GET /events -->|
  |                   | (server waits... has data)
  |<-- 200 {event} ---|
  |--- GET /events -->|
  |                   | (waiting...)
  |                   | (timeout after 30s)
  |<-- 204 empty -----|
  |--- GET /events -->|
```

Simple to implement, works with any HTTP infrastructure. The downside: a new HTTP request (and potentially TCP + TLS handshake) for every event, plus unnecessary load if events are infrequent.

**Server-Sent Events (SSE)**

SSE is a standardized HTTP streaming protocol. A single HTTP response streams events indefinitely:

```
Client                          Server
  |--- GET /events -------------->|
  |   (Connection: keep-alive)    |
  |   (Accept: text/event-stream) |
  |                               |
  |<-- HTTP/1.1 200 OK ------------|
  |<-- Content-Type: text/event-stream
  |<-- Transfer-Encoding: chunked |
  |                               |
  |<-- data: {"type":"msg","id":1}\n\n
  |                               |
  |<-- data: {"type":"msg","id":2}\n\n
  |                               |
  |    (connection stays open)    |
```

The wire format is simple text:

```
id: 123
event: message
data: {"user":"alice","text":"hello"}

id: 124
event: notification
data: {"count":5}

```

The double newline (`\n\n`) terminates each event. The browser's `EventSource` API handles reconnection automatically using the `Last-Event-ID` header — if the connection drops, the browser reconnects and tells the server the last event ID it received.

SSE is unidirectional: server to client only. It works over standard HTTP, uses a single connection, and passes through proxies and load balancers that understand HTTP/1.1 streaming or HTTP/2.

**WebSockets**

WebSockets provide a full-duplex bidirectional channel. The connection starts as HTTP/1.1, then upgrades:

```
Client                                Server
  |--- GET /ws ------------------->|
  |    Upgrade: websocket          |
  |    Connection: Upgrade         |
  |    Sec-WebSocket-Key: <base64> |
  |    Sec-WebSocket-Version: 13   |
  |                                |
  |<-- 101 Switching Protocols ----|
  |    Upgrade: websocket          |
  |    Sec-WebSocket-Accept: <hash>|
  |                                |
  |<====== WebSocket frames ======>|
  |  (bidirectional, both ends     |
  |   can send at any time)        |
```

The `Sec-WebSocket-Key` / `Sec-WebSocket-Accept` exchange is a handshake to prevent accidental WebSocket connections from non-WebSocket HTTP clients.

After the upgrade, the connection speaks a binary framing protocol. Each frame has:
- A 2-byte header with opcode (text/binary/ping/pong/close), FIN bit, masking bit
- A variable-length payload length field (7 bits, 16 bits, or 64 bits)
- A 4-byte masking key (for client-to-server frames — required by the spec)
- The masked payload

WebSockets operate at layer 7 but are not HTTP after the upgrade. They're their own protocol. This matters for proxies, load balancers, and anything that inspects or modifies HTTP traffic — many of them don't speak WebSocket natively and will drop the connection or behave unexpectedly.

## Why It Matters

The choice of protocol shapes your entire backend architecture:

- **Polling** fits when: events are infrequent, you need simplest possible implementation, you have stateless HTTP infrastructure.
- **SSE** fits when: server pushes to clients, you need automatic reconnection, you want standard HTTP behavior, your clients are browsers.
- **WebSockets** fit when: true bidirectional communication is needed (chat, collaborative editing, gaming), low-latency in both directions matters, you control both client and server.

A notification feed is SSE. A chat application is WebSockets. A dashboard that refreshes every 30 seconds is polling. Using WebSockets for a notification feed is over-engineering.

## Production Example

SSE server in Go using standard library:

```go
package main

import (
    "encoding/json"
    "fmt"
    "log"
    "net/http"
    "time"
)

type Event struct {
    ID   int64       `json:"id"`
    Type string      `json:"type"`
    Data interface{} `json:"data"`
}

func sseHandler(w http.ResponseWriter, r *http.Request) {
    // SSE requires these headers
    w.Header().Set("Content-Type", "text/event-stream")
    w.Header().Set("Cache-Control", "no-cache")
    w.Header().Set("Connection", "keep-alive")
    w.Header().Set("Access-Control-Allow-Origin", "*")

    flusher, ok := w.(http.Flusher)
    if !ok {
        http.Error(w, "streaming not supported", http.StatusInternalServerError)
        return
    }

    // Get last event ID for resumption
    lastEventID := r.Header.Get("Last-Event-ID")
    log.Printf("SSE connection from %s, last event ID: %s", r.RemoteAddr, lastEventID)

    // Subscribe to events for this user (implementation-specific)
    userID := r.URL.Query().Get("user_id")
    ch := subscribeToEvents(userID)
    defer unsubscribe(userID, ch)

    for {
        select {
        case event := <-ch:
            data, err := json.Marshal(event.Data)
            if err != nil {
                continue
            }
            fmt.Fprintf(w, "id: %d\n", event.ID)
            fmt.Fprintf(w, "event: %s\n", event.Type)
            fmt.Fprintf(w, "data: %s\n\n", data)
            flusher.Flush() // Push data to client immediately

        case <-time.After(30 * time.Second):
            // Send a comment as keepalive to prevent proxy timeouts
            fmt.Fprintf(w, ": keepalive\n\n")
            flusher.Flush()

        case <-r.Context().Done():
            // Client disconnected
            return
        }
    }
}
```

WebSocket server using the `gorilla/websocket` library:

```go
import "github.com/gorilla/websocket"

var upgrader = websocket.Upgrader{
    ReadBufferSize:  1024,
    WriteBufferSize: 1024,
    CheckOrigin: func(r *http.Request) bool {
        // Validate origin in production
        return r.Header.Get("Origin") == "https://yourapp.example.com"
    },
}

func wsHandler(w http.ResponseWriter, r *http.Request) {
    conn, err := upgrader.Upgrade(w, r, nil)
    if err != nil {
        log.Printf("upgrade error: %v", err)
        return
    }
    defer conn.Close()

    // Set deadlines to detect dead connections
    conn.SetReadDeadline(time.Now().Add(60 * time.Second))
    conn.SetPongHandler(func(string) error {
        conn.SetReadDeadline(time.Now().Add(60 * time.Second))
        return nil
    })

    // Ping to keep connection alive and detect disconnects
    go func() {
        ticker := time.NewTicker(30 * time.Second)
        defer ticker.Stop()
        for range ticker.C {
            if err := conn.WriteControl(websocket.PingMessage, nil,
                time.Now().Add(10*time.Second)); err != nil {
                return
            }
        }
    }()

    for {
        messageType, message, err := conn.ReadMessage()
        if err != nil {
            if websocket.IsUnexpectedCloseError(err,
                websocket.CloseGoingAway, websocket.CloseAbnormalClosure) {
                log.Printf("ws error: %v", err)
            }
            return
        }
        // Echo or process the message
        if err := conn.WriteMessage(messageType, message); err != nil {
            return
        }
    }
}
```

**nginx configuration for WebSocket proxying:**

```nginx
upstream websocket {
    server backend:8080;
}

server {
    location /ws {
        proxy_pass http://websocket;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400; # 24h — don't timeout long-lived WS connections
        proxy_send_timeout 86400;
    }
}
```

## The Tradeoffs

**WebSockets vs SSE for server-push use cases**: SSE reconnects automatically, works over standard HTTP (no upgrade), works with HTTP/2 multiplexing, and works with most proxies without special configuration. WebSockets require proxy configuration, don't multiplex over HTTP/2 (each WebSocket is its own connection), and you manage reconnection yourself. Unless you genuinely need bidirectional communication, SSE is simpler.

**Connection count at scale**: Each WebSocket or SSE connection is a persistent open connection. 100K concurrent users = 100K open connections per server instance. Go handles this fine (goroutines are cheap), but your load balancer, OS file descriptor limits, and memory usage all need tuning. Plan for `ulimit -n 1048576` and monitor file descriptor usage.

**Horizontal scaling and sticky sessions**: WebSocket and SSE connections are stateful — they're pinned to a specific backend instance. Horizontal scaling requires either sticky sessions at the load balancer or an event bus (Redis Pub/Sub, Kafka) to broadcast events across instances so any backend can push to any client.

**WebSockets over HTTP/2**: HTTP/2 has a proposal for WebSockets over HTTP/2 (RFC 8441), but support is limited. Most WebSocket deployments still use HTTP/1.1 upgrade. This means each WebSocket uses a dedicated TCP connection, unlike HTTP/2 streams.

**gRPC streaming vs WebSockets**: For service-to-service communication, gRPC bidirectional streaming is usually better than WebSockets — it's typed, has interceptors, and works within your existing service mesh.

## Key Takeaway

WebSockets provide full-duplex communication but come with operational overhead around proxy configuration, reconnection logic, and horizontal scaling. For server-to-client push use cases — notifications, live feeds, dashboards — SSE is simpler, handles reconnection automatically, and works with standard HTTP infrastructure. Reach for WebSockets when you genuinely need bidirectional low-latency communication. The choice should follow your actual data flow, not the perceived "sophistication" of the protocol.

---

**Previous:** [Lesson 4: DNS](/post/fundamentals/net-dns/)
**Next:** [Lesson 6: gRPC and Protobuf — Binary protocols and streaming](/post/fundamentals/net-grpc/)
