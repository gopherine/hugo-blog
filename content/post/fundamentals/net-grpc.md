---
title: "Lesson 6: gRPC and Protobuf — Binary protocols and streaming"
author: Atharva Pandey
keywords:
  - gRPC
  - protobuf
  - protocol buffers
  - binary protocol
  - streaming RPC
  - networking
  - backend engineering
tags:
  - fundamentals
  - networking
series: "Networking for Backend Engineers"
lesson: 6
date: '2024-07-17T00:00:00.000Z'
---

The first JSON API I replaced with gRPC was passing `[]Order` objects around — each order had about 40 fields, most of which the caller never used. The JSON payload for a list of 100 orders was around 180KB. After the migration it was 22KB, and the serialization time in benchmarks dropped by 8x. But more than the performance numbers, what I noticed was the schema. Proto files are contracts. When a field changes, you know it. With JSON, you find out when things break in production.

## How It Works

**Protocol Buffers**

Protobuf is a language-neutral, binary serialization format. You define your data schema in `.proto` files:

```protobuf
syntax = "proto3";
package orders.v1;
option go_package = "github.com/example/orders/gen/orders/v1;ordersv1";

message Order {
  string  id          = 1;
  string  customer_id = 2;
  double  total       = 3;
  Status  status      = 4;
  repeated LineItem items = 5;
  google.protobuf.Timestamp created_at = 6;
}

enum Status {
  STATUS_UNSPECIFIED = 0;
  STATUS_PENDING     = 1;
  STATUS_CONFIRMED   = 2;
  STATUS_SHIPPED     = 3;
}

message LineItem {
  string product_id = 1;
  int32  quantity   = 2;
  double price      = 3;
}
```

Each field has a number (1, 2, 3...). This number is what's actually encoded on the wire, not the field name. This is key to binary compactness and backward compatibility. Adding a new field with a new number is backward-compatible: old parsers ignore unknown fields.

The binary encoding uses varints (variable-length integers) and field tags. An integer like `42` encodes as 1 byte. The string "hello" encodes as 6 bytes (1 byte length + 5 bytes content). Compare to JSON's `"order_id":"abc123"` — 20 bytes of quotes, colons, and field name for 6 bytes of actual content.

**gRPC**

gRPC is an RPC framework that runs on top of HTTP/2 and uses Protobuf for serialization. You define services in `.proto` files:

```protobuf
service OrderService {
  // Unary RPC
  rpc GetOrder(GetOrderRequest) returns (Order);

  // Server streaming — server sends multiple responses
  rpc ListOrders(ListOrdersRequest) returns (stream Order);

  // Client streaming — client sends multiple requests
  rpc CreateOrders(stream CreateOrderRequest) returns (CreateOrdersResponse);

  // Bidirectional streaming
  rpc SyncOrders(stream SyncRequest) returns (stream SyncResponse);
}
```

The `protoc` compiler generates Go code from this definition:

```bash
protoc --go_out=. --go-grpc_out=. orders.proto
```

Generated code includes:
- Struct types with `Marshal`/`Unmarshal` methods
- An `OrderServiceServer` interface your server implements
- An `OrderServiceClient` type you use to make calls

**HTTP/2 Framing for gRPC**

Each gRPC call is an HTTP/2 stream. The request body is a 5-byte framing header followed by the Protobuf payload:

```
Byte 0:    Compression flag (0 = uncompressed, 1 = compressed)
Bytes 1-4: Message length (big-endian uint32)
Bytes 5+:  Protobuf-encoded message
```

For streaming RPCs, the server sends multiple frames on the same HTTP/2 stream. Each frame has the same 5-byte header. The stream ends with a `HEADERS` frame containing the gRPC status code in the `grpc-status` trailer.

**The gRPC Status Model**

gRPC has its own status codes that map loosely to HTTP status codes:

```go
// Status codes your handlers should return
codes.OK               // 0  — success
codes.NotFound         // 5  — resource doesn't exist
codes.AlreadyExists    // 6  — resource already created
codes.PermissionDenied // 7  — authenticated but not authorized
codes.Unavailable      // 14 — server can't handle request right now (retryable)
codes.Internal         // 13 — unexpected server error
codes.InvalidArgument  // 3  — client sent bad input
codes.DeadlineExceeded // 4  — timeout
```

Unlike HTTP, gRPC sends status codes in trailers, not headers. This allows streaming responses to report an error after partially streaming data.

## Why It Matters

gRPC gives you three things HTTP/JSON doesn't:

1. **Strong typing with schema**: The `.proto` file is the contract. If you change a field type or remove a required field, the code won't compile or the CI will catch breaking changes. Compare to JSON where schema drift is discovered at runtime.

2. **Streaming primitives**: HTTP/JSON doesn't have a standard pattern for streaming. You can do it with chunked transfer or SSE, but there's no contract-first definition of streaming endpoints. gRPC's `stream` keyword in the proto definition means your generated client code handles framing, backpressure, and completion.

3. **Performance**: For high-frequency internal calls — service meshes doing thousands of RPCs per second — the Protobuf serialization overhead is a fraction of JSON. More importantly, HTTP/2 multiplexing means concurrent RPC calls share a connection without head-of-line blocking (at the application layer).

## Production Example

A complete gRPC service implementation in Go:

```go
package main

import (
    "context"
    "fmt"
    "log"
    "net"
    "time"

    "google.golang.org/grpc"
    "google.golang.org/grpc/codes"
    "google.golang.org/grpc/keepalive"
    "google.golang.org/grpc/status"
    ordersv1 "github.com/example/gen/orders/v1"
)

type orderServer struct {
    ordersv1.UnimplementedOrderServiceServer
    store OrderStore
}

// Unary RPC
func (s *orderServer) GetOrder(ctx context.Context, req *ordersv1.GetOrderRequest) (*ordersv1.Order, error) {
    if req.Id == "" {
        return nil, status.Error(codes.InvalidArgument, "id is required")
    }

    order, err := s.store.Get(ctx, req.Id)
    if err != nil {
        if isNotFound(err) {
            return nil, status.Errorf(codes.NotFound, "order %s not found", req.Id)
        }
        return nil, status.Errorf(codes.Internal, "failed to get order: %v", err)
    }
    return order, nil
}

// Server streaming RPC
func (s *orderServer) ListOrders(req *ordersv1.ListOrdersRequest, stream ordersv1.OrderService_ListOrdersServer) error {
    cursor := ""
    for {
        // Check if client cancelled
        select {
        case <-stream.Context().Done():
            return status.Error(codes.Canceled, "client cancelled")
        default:
        }

        orders, nextCursor, err := s.store.ListBatch(stream.Context(), req.CustomerId, cursor, 100)
        if err != nil {
            return status.Errorf(codes.Internal, "list failed: %v", err)
        }

        for _, order := range orders {
            if err := stream.Send(order); err != nil {
                return err // Client disconnected
            }
        }

        if nextCursor == "" {
            return nil // All orders sent
        }
        cursor = nextCursor
    }
}

func main() {
    lis, err := net.Listen("tcp", ":50051")
    if err != nil {
        log.Fatalf("listen: %v", err)
    }

    // Production server configuration
    srv := grpc.NewServer(
        // Keep-alive settings to detect dead connections
        grpc.KeepaliveParams(keepalive.ServerParameters{
            MaxConnectionIdle:     15 * time.Minute,
            MaxConnectionAge:      30 * time.Minute,
            MaxConnectionAgeGrace: 5 * time.Second,
            Time:                  5 * time.Second,
            Timeout:               1 * time.Second,
        }),
        grpc.KeepaliveEnforcementPolicy(keepalive.EnforcementPolicy{
            MinTime:             5 * time.Second,
            PermitWithoutStream: true,
        }),
        // Add interceptors for logging, tracing, auth
        grpc.ChainUnaryInterceptor(
            loggingInterceptor,
            authInterceptor,
        ),
        grpc.ChainStreamInterceptor(
            streamLoggingInterceptor,
        ),
    )

    ordersv1.RegisterOrderServiceServer(srv, &orderServer{})

    log.Printf("gRPC server listening on :50051")
    if err := srv.Serve(lis); err != nil {
        log.Fatalf("serve: %v", err)
    }
}

// Client with connection pooling and retry
func newOrderClient(addr string) (ordersv1.OrderServiceClient, error) {
    conn, err := grpc.Dial(addr,
        grpc.WithInsecure(), // Use grpc.WithTransportCredentials for TLS
        grpc.WithKeepaliveParams(keepalive.ClientParameters{
            Time:                10 * time.Second,
            Timeout:             3 * time.Second,
            PermitWithoutStream: true,
        }),
        grpc.WithDefaultServiceConfig(`{
            "methodConfig": [{
                "name": [{"service": "orders.v1.OrderService"}],
                "retryPolicy": {
                    "maxAttempts": 3,
                    "initialBackoff": "0.1s",
                    "maxBackoff": "1s",
                    "backoffMultiplier": 2,
                    "retryableStatusCodes": ["UNAVAILABLE"]
                }
            }]
        }`),
    )
    if err != nil {
        return nil, fmt.Errorf("dial: %w", err)
    }
    return ordersv1.NewOrderServiceClient(conn), nil
}
```

Using `buf` for proto management (the modern alternative to raw `protoc`):

```yaml
# buf.yaml
version: v1
lint:
  use:
    - DEFAULT
breaking:
  use:
    - FILE  # Detect breaking changes against the previous version
```

```bash
buf generate  # Generate code
buf lint      # Check for style issues
buf breaking --against .git#branch=main  # Check for breaking changes
```

## The Tradeoffs

**gRPC vs REST/JSON for public APIs**: gRPC is excellent for internal service communication. For public APIs, REST/JSON has broader client support, is easier to debug in a browser, and is expected by most third-party integrations. The pattern I use: gRPC internally, REST/JSON at the edge with `grpc-gateway` to translate.

**Schema evolution**: Adding fields is safe (new field numbers). Renaming fields is safe (the number is what matters). Changing field types is not safe. Removing a field is safe if you reserve the field number to prevent reuse. Team discipline around these rules matters.

**Protobuf debugging**: Binary format is not human-readable. `grpcurl` is your friend:

```bash
# List services
grpcurl -plaintext localhost:50051 list

# Call a method
grpcurl -plaintext -d '{"id":"order-123"}' \
  localhost:50051 orders.v1.OrderService/GetOrder
```

**gRPC-web for browsers**: Browsers can't use gRPC directly (HTTP/2 trailers aren't accessible via browser APIs). `grpc-web` adds a proxy layer. For browser clients, REST or SSE is usually simpler.

**Metadata and context propagation**: gRPC metadata (headers) is how you pass authentication tokens, trace IDs, and other context. It's not automatic — you need to explicitly propagate it through interceptors. Wire it up once and don't think about it again.

## Key Takeaway

gRPC is HTTP/2 + Protobuf with a structured RPC framework on top. The binary encoding is compact, the schema is the contract, and streaming is a first-class primitive rather than an afterthought. The practical gains are most visible in high-frequency internal service communication: better serialization performance, bidirectional streaming, and schema enforcement that catches API contract violations at compile time rather than runtime. For public-facing APIs, REST/JSON remains the pragmatic choice — use gRPC-gateway to bridge them.

---

**Previous:** [Lesson 5: WebSockets](/post/fundamentals/net-websockets/)
**Next:** [Lesson 7: Service Mesh — Sidecar proxies and mTLS without code](/post/fundamentals/net-service-mesh/)
