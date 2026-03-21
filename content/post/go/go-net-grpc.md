---
title: 'Lesson 8: gRPC Basics and Streaming — Protobuf on the wire, types in your code'
author: Atharva Pandey
series: "Go Networking & Distributed Systems"
lesson: 8
keywords:
  - Go
  - golang
  - gRPC
  - protobuf
  - streaming
  - RPC
  - HTTP/2
  - microservices
tags:
  - Go tutorial
  - golang
  - networking
date: '2025-05-15T00:00:00.000Z'
---

My team migrated a set of internal service APIs from JSON over HTTP/1.1 to gRPC roughly two years ago. The motivating factors were type safety across service boundaries, binary serialization that was 5-10x smaller on the wire, and bidirectional streaming that HTTP/1.1 can't do at all. The migration took about a week per service and the performance improvements were immediately visible in our latency percentiles.

gRPC is a Remote Procedure Call framework that runs over HTTP/2. The interface is defined in Protocol Buffers — a language-neutral schema language — and the gRPC toolchain generates client and server stubs in Go. You call a method; the framework handles serialization, connection management, and streaming.

## The Problem

JSON over HTTP works fine for public APIs and human-readable payloads. For internal service-to-service communication, it has friction points that accumulate:

```go
// Typical JSON-over-HTTP client — fragile and verbose
func getUser(ctx context.Context, id string) (*User, error) {
    req, _ := http.NewRequestWithContext(ctx, "GET", baseURL+"/users/"+id, nil)
    resp, err := httpClient.Do(req)
    if err != nil {
        return nil, err
    }
    defer resp.Body.Close()

    // The contract is enforced by convention, not the type system.
    // The server could change the response shape and the compiler won't tell you.
    var u User
    if err := json.NewDecoder(resp.Body).Decode(&u); err != nil {
        return nil, err
    }
    return &u, nil
}
```

No type-checked contract between client and server. No streaming. No bidirectional communication. Header-based metadata handling is manual. Versioning is informal.

## The Idiomatic Way

Start with the proto definition. This is the contract between services:

```protobuf
// user.proto
syntax = "proto3";
package user.v1;
option go_package = "example.com/gen/user/v1;userv1";

service UserService {
  rpc GetUser(GetUserRequest) returns (GetUserResponse);
  rpc WatchUserEvents(WatchUserEventsRequest) returns (stream UserEvent);
  rpc BulkCreateUsers(stream CreateUserRequest) returns (BulkCreateResponse);
}

message GetUserRequest  { string user_id = 1; }
message GetUserResponse { User user = 1; }

message User {
  string id     = 1;
  string email  = 2;
  string name   = 3;
  int64  created_at_unix = 4;
}

message WatchUserEventsRequest { string user_id = 1; }
message UserEvent {
  string event_type = 1;
  User   user       = 2;
  int64  timestamp  = 3;
}

message CreateUserRequest { string email = 1; string name = 2; }
message BulkCreateResponse { int32 created = 1; repeated string errors = 2; }
```

Generate the Go code with `protoc` and the gRPC plugin. Then implement the server:

```go
// Server implementation
type userServer struct {
    userv1.UnimplementedUserServiceServer
    db    *sql.DB
    events chan UserEvent // internal event stream
}

func (s *userServer) GetUser(ctx context.Context, req *userv1.GetUserRequest) (*userv1.GetUserResponse, error) {
    if req.UserId == "" {
        return nil, status.Error(codes.InvalidArgument, "user_id is required")
    }

    u, err := s.db.GetUser(ctx, req.UserId)
    if errors.Is(err, sql.ErrNoRows) {
        return nil, status.Errorf(codes.NotFound, "user %s not found", req.UserId)
    }
    if err != nil {
        return nil, status.Errorf(codes.Internal, "get user: %v", err)
    }

    return &userv1.GetUserResponse{
        User: &userv1.User{
            Id:            u.ID,
            Email:         u.Email,
            Name:          u.Name,
            CreatedAtUnix: u.CreatedAt.Unix(),
        },
    }, nil
}

// Server-side streaming: the server sends a stream of events to the client.
func (s *userServer) WatchUserEvents(req *userv1.WatchUserEventsRequest, stream userv1.UserService_WatchUserEventsServer) error {
    for {
        select {
        case <-stream.Context().Done():
            return nil // client disconnected
        case evt := <-s.events:
            if evt.UserID != req.UserId {
                continue
            }
            if err := stream.Send(&userv1.UserEvent{
                EventType: evt.Type,
                User:      toProtoUser(evt.User),
                Timestamp: evt.At.Unix(),
            }); err != nil {
                return err // client error — stop the stream
            }
        }
    }
}
```

The generated `UnimplementedUserServiceServer` provides default implementations that return `codes.Unimplemented`. Embedding it means your struct satisfies the interface even before you implement every method — future-proofing for proto additions.

The client side is clean and type-safe:

```go
func main() {
    conn, err := grpc.NewClient(
        "user-service:50051",
        grpc.WithTransportCredentials(insecure.NewCredentials()), // use TLS in prod
        grpc.WithDefaultCallOptions(grpc.MaxCallRecvMsgSize(4*1024*1024)),
    )
    if err != nil {
        log.Fatalf("dial: %v", err)
    }
    defer conn.Close()

    client := userv1.NewUserServiceClient(conn)

    // Unary call
    resp, err := client.GetUser(ctx, &userv1.GetUserRequest{UserId: "user-123"})
    if err != nil {
        // Check the status code
        if status.Code(err) == codes.NotFound {
            log.Println("user not found")
        }
        log.Fatalf("get user: %v", err)
    }
    log.Println("got user:", resp.User.Name)

    // Server streaming — receive events until context is cancelled
    stream, err := client.WatchUserEvents(ctx, &userv1.WatchUserEventsRequest{UserId: "user-123"})
    if err != nil {
        log.Fatalf("watch: %v", err)
    }
    for {
        evt, err := stream.Recv()
        if err == io.EOF {
            break
        }
        if err != nil {
            log.Fatalf("recv: %v", err)
        }
        log.Printf("event: %s", evt.EventType)
    }
}
```

## In The Wild

Interceptors are gRPC's middleware. Add them to handle logging, metrics, authentication, and recovery uniformly across all methods:

```go
// Unary interceptor for request logging and metrics
func loggingInterceptor(
    ctx context.Context,
    req any,
    info *grpc.UnaryServerInfo,
    handler grpc.UnaryHandler,
) (any, error) {
    start := time.Now()
    resp, err := handler(ctx, req)
    dur := time.Since(start)

    code := codes.OK
    if err != nil {
        code = status.Code(err)
    }

    slog.InfoContext(ctx, "grpc request",
        "method", info.FullMethod,
        "duration_ms", dur.Milliseconds(),
        "code", code.String(),
    )
    grpcRequestDuration.WithLabelValues(info.FullMethod, code.String()).Observe(dur.Seconds())
    return resp, err
}

// Register the interceptor
server := grpc.NewServer(
    grpc.ChainUnaryInterceptor(
        recoveryInterceptor, // catch panics first
        authInterceptor,     // then auth
        loggingInterceptor,  // then log
    ),
)
```

Reflection is worth enabling in non-production environments. It lets tools like `grpcurl` and Postman discover your service's methods without a local copy of the proto file:

```go
import "google.golang.org/grpc/reflection"

if os.Getenv("ENV") != "production" {
    reflection.Register(server)
}
```

## The Gotchas

**Error mapping.** gRPC status codes do not map one-to-one to HTTP status codes. `codes.NotFound` is gRPC's 404, `codes.InvalidArgument` is 400, `codes.Unavailable` is 503. When you expose gRPC services through a gateway that translates to HTTP, the mapping needs to be explicit. The `grpc-gateway` project handles this automatically if you add HTTP annotations to your proto definitions.

**Message size limits.** The default maximum message size is 4MB in both directions. Large payloads — file uploads, bulk exports — should use streaming instead of a single large message. Use `grpc.MaxRecvMsgSize` and `grpc.MaxSendMsgSize` if you must raise the limit.

**Proto field numbering.** Proto field numbers are permanent. Once a field is deployed in production, its number cannot change. Removing a field leaves its number reserved forever. Always use `reserved` to mark removed field numbers and prevent accidental reuse.

**`insecure.NewCredentials()` is not for production.** Plain gRPC without TLS exposes your traffic to interception. In Kubernetes, use mutual TLS via a service mesh or configure TLS certificates directly. The code examples above use `insecure` for clarity — replace it before deploying.

## Key Takeaway

gRPC gives you a schema-first API with generated, type-safe clients; binary serialization that's more compact than JSON; and streaming primitives that HTTP/1.1 doesn't have. The operational cost is the proto toolchain and slightly more complex error handling. For internal service-to-service communication at any scale, the tradeoff is usually worth it.

🎓 **Course Complete!** You've finished Go Networking & Distributed Systems. You now know how to build resilient, observable, scalable networked services in Go — from HTTP client tuning through retries, circuit breaking, connection pooling, message queues, consistency, the outbox pattern, and gRPC streaming.

---

**Previous:** [Lesson 7: Outbox Pattern](/post/go/go-net-outbox/)
**Up next:** Go Deployment & Operations — starting with static binaries
