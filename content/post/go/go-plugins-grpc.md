---
title: "Lesson 2: gRPC-Based Plugin Architecture — How Terraform and Vault do plugins"
author: Atharva Pandey
series: "Go Plugin Systems"
lesson: 2
keywords:
  - gRPC plugins
  - hashicorp go-plugin gRPC
  - Terraform plugin protocol
  - Vault plugins
  - golang plugin gRPC
  - proto plugin
  - plugin versioning
tags:
  - Go tutorial
  - golang
  - plugins
date: "2024-11-21T00:00:00.000Z"
---

After I understood how `hashicorp/go-plugin` worked with net/rpc, the next question was obvious: how does Terraform manage hundreds of community-contributed providers, written by different teams, evolving on different schedules, sometimes in languages other than Go? The answer is gRPC. Terraform's provider protocol is a protobuf schema, communicated over the same subprocess-plus-RPC architecture from Lesson 1 — but with gRPC instead of net/rpc. That substitution buys you schema evolution, multi-language support, streaming, and a strongly-typed IDL. This lesson shows you how to build a plugin system using that pattern.

## The Problem

Net/rpc, which we used in Lesson 1, has limits that become real as a plugin ecosystem grows:

**No schema evolution.** If you add a field to an RPC request, old net/rpc plugins break because Go's encoding/gob does not handle missing fields gracefully across binary boundaries. With protobuf, adding fields is backward-compatible — old plugins ignore unknown fields, new plugins treat missing fields as defaults.

**Go only.** Net/rpc uses encoding/gob, which is a Go-specific wire format. If you want to support Python plugins, Rust plugins, or plugins written by teams outside your organization who prefer other languages, net/rpc is a dead end.

**No streaming.** Net/rpc is request-response. If a plugin needs to stream a large result set, emit progress events, or push notifications to the host, you need gRPC's streaming capabilities.

**Schema as documentation.** A proto file is machine-readable documentation. It tells plugin authors exactly what the interface expects, with types, field names, and comments. A Go interface requires them to import your package. Proto files are language-agnostic.

## How It Works

`hashicorp/go-plugin` supports gRPC as a first-class transport. You define your interface in a `.proto` file, generate the Go gRPC client and server stubs, and implement `plugin.GRPCPlugin` instead of `plugin.Plugin`.

**Step 1: Define the interface in proto.**

```protobuf
// proto/greeter.proto
syntax = "proto3";
package greeter;
option go_package = "yourapp/proto/greeter";

service Greeter {
  rpc Greet(GreetRequest) returns (GreetResponse);
  rpc GreetStream(GreetRequest) returns (stream GreetResponse); // streaming variant
}

message GreetRequest {
  string name = 1;
  string locale = 2; // added later — backward compatible
}

message GreetResponse {
  string greeting = 1;
}
```

Generate stubs:

```bash
protoc --go_out=. --go-grpc_out=. proto/greeter.proto
```

**Step 2: Implement `plugin.GRPCPlugin` in the shared package.**

```go
// shared/greeter_grpc_plugin.go
package shared

import (
    "context"
    "github.com/hashicorp/go-plugin"
    "google.golang.org/grpc"
    pb "yourapp/proto/greeter"
)

// Greeter is the Go interface that host code uses
type Greeter interface {
    Greet(ctx context.Context, name, locale string) (string, error)
}

// GreeterGRPCPlugin implements go-plugin's GRPCPlugin interface
type GreeterGRPCPlugin struct {
    plugin.Plugin
    Impl Greeter
}

func (p *GreeterGRPCPlugin) GRPCServer(broker *plugin.GRPCBroker, s *grpc.Server) error {
    pb.RegisterGreeterServer(s, &greeterGRPCServer{impl: p.Impl})
    return nil
}

func (p *GreeterGRPCPlugin) GRPCClient(ctx context.Context, broker *plugin.GRPCBroker, c *grpc.ClientConn) (interface{}, error) {
    return &greeterGRPCClient{client: pb.NewGreeterClient(c)}, nil
}

// Server side (runs in plugin process)
type greeterGRPCServer struct {
    pb.UnimplementedGreeterServer
    impl Greeter
}

func (s *greeterGRPCServer) Greet(ctx context.Context, req *pb.GreetRequest) (*pb.GreetResponse, error) {
    greeting, err := s.impl.Greet(ctx, req.Name, req.Locale)
    if err != nil {
        return nil, err
    }
    return &pb.GreetResponse{Greeting: greeting}, nil
}

// Client side (runs in host process)
type greeterGRPCClient struct {
    client pb.GreeterClient
}

func (c *greeterGRPCClient) Greet(ctx context.Context, name, locale string) (string, error) {
    resp, err := c.client.Greet(ctx, &pb.GreetRequest{Name: name, Locale: locale})
    if err != nil {
        return "", err
    }
    return resp.Greeting, nil
}
```

**Step 3: Plugin binary uses gRPC serve config.**

```go
// plugins/hello/main.go
package main

import (
    "context"
    "fmt"
    "github.com/hashicorp/go-plugin"
    "yourapp/shared"
)

type HelloGreeter struct{}

func (g *HelloGreeter) Greet(ctx context.Context, name, locale string) (string, error) {
    return fmt.Sprintf("Hello, %s! (locale: %s)", name, locale), nil
}

func main() {
    plugin.Serve(&plugin.ServeConfig{
        HandshakeConfig: shared.HandshakeConfig,
        Plugins: plugin.PluginSet{
            "greeter": &shared.GreeterGRPCPlugin{Impl: &HelloGreeter{}},
        },
        GRPCServer: plugin.DefaultGRPCServer,
    })
}
```

**Step 4: Host loads plugin and uses gRPC transport.**

```go
client := plugin.NewClient(&plugin.ClientConfig{
    HandshakeConfig: shared.HandshakeConfig,
    Plugins:         plugin.PluginSet{"greeter": &shared.GreeterGRPCPlugin{}},
    Cmd:             exec.Command("./plugins/hello"),
    AllowedProtocols: []plugin.Protocol{plugin.ProtocolGRPC},
})
defer client.Kill()

rpcClient, _ := client.Client()
raw, _ := rpcClient.Dispense("greeter")
greeter := raw.(shared.Greeter)
result, _ := greeter.Greet(context.Background(), "world", "en-US")
fmt.Println(result)
```

## In Practice

**How Terraform actually does this.** Terraform providers implement the `tfplugin` protobuf schema. The Terraform CLI launches each provider binary as a subprocess, performs a version handshake, and communicates over gRPC. Providers can be written in any language that has a gRPC implementation — there are official Terraform provider SDKs in Go, and community SDKs in Python and other languages. The proto file is the contract.

**Protocol versioning.** In the `HandshakeConfig`, `ProtocolVersion` lets you evolve the protocol. When a plugin connects, both sides exchange versions. If the host supports versions 1 and 2, and the plugin only supports version 1, the host falls back. You implement this by registering multiple plugin implementations under the same name:

```go
Plugins: map[int]plugin.PluginSet{
    1: {"greeter": &shared.GreeterGRPCPluginV1{}},
    2: {"greeter": &shared.GreeterGRPCPluginV2{}},
}
```

**Streaming plugins.** gRPC server streaming lets a plugin stream results back to the host as they are computed. This is ideal for plugins that process large datasets, emit log lines, or push events:

```go
func (s *greeterGRPCServer) GreetStream(req *pb.GreetRequest, stream pb.Greeter_GreetStreamServer) error {
    for i := 0; i < 5; i++ {
        if err := stream.Send(&pb.GreetResponse{
            Greeting: fmt.Sprintf("Hello %s, message %d", req.Name, i),
        }); err != nil {
            return err
        }
    }
    return nil
}
```

On the host side, the client stub exposes a `Recv()` method to consume streamed responses.

**Non-Go plugins.** Because the interface is defined in protobuf, a Python team can implement a plugin by generating Python stubs from the same proto file and implementing the gRPC server. They compile it as a Python script (or PyInstaller binary), and the host launches it exactly the same way. The plugin protocol is language-agnostic.

## The Gotchas

**Proto import paths and module structure.** The `go_package` option in your proto file must match your Go module path. If they diverge, generated code imports the wrong path and nothing compiles. Keep proto files in a `proto/` directory, generated code in `gen/` or alongside the proto, and document this layout clearly for plugin authors.

**`GRPCBroker` for bidirectional connections.** `go-plugin`'s `GRPCBroker` lets the plugin open *additional* gRPC connections back to the host for bidirectional communication. This is powerful but complex. Use it only when the plugin needs to call back into the host — for simple unidirectional calls, ignore it.

**TLS in production.** By default, `go-plugin` uses an auto-generated self-signed certificate for the gRPC connection, verified by passing the certificate from the plugin's stdout to the host via environment variable. This is secure for localhost subprocess communication. If you ever bridge plugins over a network (not recommended but sometimes done in distributed systems), you need proper TLS configuration.

**Process limits at scale.** If your application loads fifty plugins simultaneously, you have fifty subprocesses. Each uses memory, file descriptors, and a gRPC connection. Profile your process table under load. Use lazy loading — only launch a plugin process when it is first needed.

**Debugging plugin crashes.** When a plugin subprocess crashes, the host sees a gRPC connection error. The crash output is in the plugin's stderr, which `go-plugin` captures. Configure a logger on the `ClientConfig` to surface this output. Without it, debugging plugin crashes is painful.

## Key Takeaway

Replacing net/rpc with gRPC in a `go-plugin` architecture gives you schema evolution, multi-language plugin support, streaming, and a machine-readable contract between host and plugin authors. The pattern is exactly what Terraform, Vault, and other HashiCorp tools use for their plugin ecosystems. The proto file is the source of truth. The subprocess boundary gives you crash isolation. Protocol versioning gives you forward compatibility. For any non-trivial plugin system where third parties will write plugins or where the interface will evolve over time, this architecture is the right choice.

---

**Previous:** [Lesson 1: Go Plugins and hashicorp/go-plugin](/post/go/go-plugins-basics/)

---

🎓 **Course Complete!** You have finished *Go Plugin Systems*. You understand why the standard `plugin` package falls short, how to build subprocess-based plugins with `hashicorp/go-plugin`, and how to upgrade to gRPC for schema evolution and multi-language support — the same architecture that powers Terraform providers and Vault plugins.
