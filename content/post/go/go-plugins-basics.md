---
title: "Lesson 1: Go Plugins and hashicorp/go-plugin — Extending Go apps without recompiling"
author: Atharva Pandey
series: "Go Plugin Systems"
lesson: 1
keywords:
  - Go plugins
  - hashicorp go-plugin
  - plugin architecture
  - golang plugin
  - extensible Go apps
  - go build -buildmode=plugin
tags:
  - Go tutorial
  - golang
  - plugins
date: "2024-08-26T00:00:00.000Z"
---

The first time I needed a plugin system in Go, I went straight to `plugin.Open` in the standard library. Ten minutes later I was reading about RTLD flags and shared library loading on Linux, discovering that plugins had to be compiled with the same Go toolchain version as the host, and learning that once a plugin was loaded it could not be unloaded. My enthusiasm dropped sharply. Then I found `hashicorp/go-plugin` and understood that the Go community had largely agreed: real plugin systems in Go should run plugins as separate processes communicating over RPC, not as shared libraries in the same process.

This lesson covers why the standard `plugin` package is limited, how `hashicorp/go-plugin` works, and how to build a real extension point for your application.

## The Problem

The Go standard library's `plugin` package compiles plugins as `.so` shared libraries and loads them at runtime with `plugin.Open`. On paper this is what you want: load new code without recompiling. In practice:

- **Toolchain pinning.** The plugin and the host binary must be compiled with the exact same Go version. If you ship a product and a user writes a plugin against Go 1.21 but your binary was compiled with Go 1.22, loading fails at runtime with a cryptic mismatch error.
- **Linux only (effectively).** The plugin package works on Linux and macOS but has limited support on Windows and does not support cross-compilation at all.
- **No unloading.** Once a plugin is loaded, the memory is never freed. For long-running services that load and reload plugins frequently, this is a memory leak.
- **Crash isolation.** A bug in the plugin panics the entire host process. There is no safety boundary.
- **Version evolution.** Changing an interface in the host means rebuilding all plugins. There is no compatibility layer.

`hashicorp/go-plugin` solves all of these by running each plugin as a separate subprocess. The host and plugin communicate over a local RPC connection (either net/rpc or gRPC). The plugin is a normal Go binary, compiled independently. The host launches it, speaks to it over the wire, and if it crashes, the host catches the subprocess death and can restart it or report a clean error.

## How It Works

The architecture has three pieces:

1. **An interface** that defines what the plugin can do — this is a plain Go interface in shared code.
2. **A plugin binary** that implements the interface and serves it over RPC.
3. **A host binary** that launches the plugin subprocess and calls the interface through an RPC client.

The library handles the subprocess lifecycle, RPC handshake, health checking, and version negotiation for you.

**Step 1: Define the interface in a shared package.**

```go
// shared/greeter.go
package shared

type Greeter interface {
    Greet(name string) (string, error)
}
```

**Step 2: Write the RPC layer.** `go-plugin` needs you to write a thin RPC wrapper for each interface. This is boilerplate, but it is mechanical:

```go
// shared/greeter_rpc.go
package shared

import "net/rpc"

// GreeterRPC is the client-side stub
type GreeterRPC struct{ client *rpc.Client }

func (g *GreeterRPC) Greet(name string) (string, error) {
    var resp string
    err := g.client.Call("Plugin.Greet", name, &resp)
    return resp, err
}

// GreeterRPCServer is the server-side handler (runs in the plugin process)
type GreeterRPCServer struct{ Impl Greeter }

func (s *GreeterRPCServer) Greet(name string, resp *string) error {
    var err error
    *resp, err = s.Impl.Greet(name)
    return err
}

// GreeterPlugin wraps everything for go-plugin
type GreeterPlugin struct{ Impl Greeter }

func (p *GreeterPlugin) Server(*plugin.MuxBroker) (interface{}, error) {
    return &GreeterRPCServer{Impl: p.Impl}, nil
}

func (GreeterPlugin) Client(b *plugin.MuxBroker, c *rpc.Client) (interface{}, error) {
    return &GreeterRPC{client: c}, nil
}
```

**Step 3: Write the plugin binary.**

```go
// plugins/hello/main.go
package main

import (
    "fmt"
    "github.com/hashicorp/go-plugin"
    "yourapp/shared"
)

type HelloGreeter struct{}

func (g *HelloGreeter) Greet(name string) (string, error) {
    return fmt.Sprintf("Hello, %s!", name), nil
}

func main() {
    plugin.Serve(&plugin.ServeConfig{
        HandshakeConfig: shared.HandshakeConfig,
        Plugins: plugin.PluginSet{
            "greeter": &shared.GreeterPlugin{Impl: &HelloGreeter{}},
        },
    })
}
```

**Step 4: Load and use the plugin in the host.**

```go
// host/main.go
package main

import (
    "fmt"
    "os/exec"
    "github.com/hashicorp/go-plugin"
    "yourapp/shared"
)

func main() {
    client := plugin.NewClient(&plugin.ClientConfig{
        HandshakeConfig: shared.HandshakeConfig,
        Plugins:         plugin.PluginSet{"greeter": &shared.GreeterPlugin{}},
        Cmd:             exec.Command("./plugins/hello"),
    })
    defer client.Kill()

    rpcClient, err := client.Client()
    if err != nil {
        panic(err)
    }

    raw, err := rpcClient.Dispense("greeter")
    if err != nil {
        panic(err)
    }

    greeter := raw.(shared.Greeter)
    result, _ := greeter.Greet("world")
    fmt.Println(result) // Hello, world!
}
```

The `HandshakeConfig` is a struct with a magic cookie key/value pair that prevents the plugin from being launched accidentally as a standalone binary:

```go
var HandshakeConfig = plugin.HandshakeConfig{
    ProtocolVersion:  1,
    MagicCookieKey:   "GREETER_PLUGIN",
    MagicCookieValue: "hello",
}
```

## In Practice

**Plugin discovery.** In real applications, you want to discover plugins dynamically. A common pattern is to scan a directory for executables:

```go
func discoverPlugins(dir string) ([]string, error) {
    entries, err := os.ReadDir(dir)
    if err != nil {
        return nil, err
    }
    var paths []string
    for _, e := range entries {
        if !e.IsDir() && isExecutable(filepath.Join(dir, e.Name())) {
            paths = append(paths, filepath.Join(dir, e.Name()))
        }
    }
    return paths, nil
}
```

**Plugin reload.** Because plugins are separate processes, you can reload a plugin by killing the existing client and starting a new one:

```go
func (m *PluginManager) Reload(name string) error {
    if existing, ok := m.clients[name]; ok {
        existing.Kill()
    }
    newClient := plugin.NewClient(m.configs[name])
    m.clients[name] = newClient
    return nil
}
```

This is impossible with the standard `plugin` package.

**Health checking.** `go-plugin` includes built-in health checking — the host periodically pings the plugin subprocess. If the plugin crashes or stops responding, the host gets an error on the next RPC call. You can wrap calls with retry logic that relaunch the plugin:

```go
func (m *PluginManager) CallWithRetry(name string, fn func(Greeter) error) error {
    greeter, err := m.get(name)
    if err != nil {
        return err
    }
    if err := fn(greeter); err != nil {
        if plugin.IsError(err) { // subprocess died
            m.Reload(name)
            greeter, err = m.get(name)
            if err != nil {
                return err
            }
            return fn(greeter)
        }
        return err
    }
    return nil
}
```

## The Gotchas

**The shared interface must be versioned carefully.** If you add a method to the interface and deploy the new host without rebuilding all plugins, old plugins will not have the new method. Version your interface (add a `Version() int` method) and use `ProtocolVersion` in the handshake config to detect mismatches early.

**Subprocess stderr is piped by default.** Plugin stdout goes to the RPC channel. Plugin stderr is captured and can be read or logged by the host. Configuring this correctly matters for debugging: if the plugin panics and prints a stack trace to stderr, you want to see that in your logs.

**Process count scales with plugin instances.** Each `plugin.Client` spawns a subprocess. If you instantiate one client per request, you will rapidly exhaust file descriptors and process limits. Pool your clients.

**The standard `plugin` package and `go-plugin` are completely separate.** Many people confuse them. The standard library `plugin` package does shared libraries. `hashicorp/go-plugin` does subprocess RPC. They do not interact.

**Windows support.** `go-plugin` supports Windows via TCP rather than Unix sockets. This works but adds configuration. If Windows is a target platform, test there explicitly.

## Key Takeaway

The standard `plugin` package's shared library approach has too many restrictions for production use. `hashicorp/go-plugin` takes a different approach — plugins as subprocesses communicating over RPC — and gets you crash isolation, independent compilation, and live reload for free. The boilerplate for the RPC layer is real, but it is mechanical and one-time-per-interface. For anything beyond toy extensibility, the subprocess model is the right foundation. The next lesson shows how to replace net/rpc with gRPC for better schema evolution and multi-language plugin support.

---

**Next:** [Lesson 2: gRPC-Based Plugin Architecture — How Terraform and Vault do plugins](/post/go/go-plugins-grpc/)
