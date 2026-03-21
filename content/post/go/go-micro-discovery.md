---
title: 'Lesson 3: Service Discovery — Finding services without hardcoding URLs'
author: Atharva Pandey
series: "Go Microservices Patterns"
lesson: 3
keywords:
  - Go
  - golang
  - microservices
  - service discovery
  - consul
  - kubernetes
  - DNS
tags:
  - Go tutorial
  - golang
  - microservices
date: '2024-11-06T00:00:00.000Z'
---

When I first started building Go microservices, every service had a config file with a list of URLs. `order-service-url: http://10.0.1.42:8080`. That worked fine until the service moved, scaled out, or the IP changed during a deployment. Then the deployment would fail, someone would update the config, redeploy, and we'd write a Jira ticket to "fix the discovery mechanism eventually." Service discovery is that fix — it's how services find each other without hardcoding network locations.

## The Problem

Hardcoded service addresses are fragile in every dimension — scaling, deployment, failure recovery.

```go
// WRONG — hardcoded address, breaks on redeploy or scale-out
type Config struct {
    InventoryServiceURL string `env:"INVENTORY_SERVICE_URL,default=http://10.0.1.42:8080"`
    PaymentServiceURL   string `env:"PAYMENT_SERVICE_URL,default=http://10.0.1.55:8080"`
    UserServiceURL      string `env:"USER_SERVICE_URL,default=http://10.0.1.61:8080"`
}

func NewOrderService(cfg Config) *OrderService {
    return &OrderService{
        inventoryClient: newHTTPClient(cfg.InventoryServiceURL),
        paymentClient:   newHTTPClient(cfg.PaymentServiceURL),
        userClient:      newHTTPClient(cfg.UserServiceURL),
    }
}
```

Scale the inventory service to three instances — the order service still only talks to one. Deploy inventory to a new host — the hardcoded IP is wrong until manually updated. An instance is restarted and gets a new IP — no traffic reaches it until config is updated.

## The Idiomatic Way

The three practical service discovery mechanisms for Go microservices, in ascending complexity:

**1. DNS-based discovery (simplest — works in Kubernetes out of the box)**

In Kubernetes, every Service gets a DNS entry: `<service-name>.<namespace>.svc.cluster.local`. Your Go service doesn't need any special client — the standard `net/http` client resolves DNS normally, and Kubernetes handles load balancing.

```go
// RIGHT — DNS-based discovery in Kubernetes
type Config struct {
    InventoryServiceURL string `env:"INVENTORY_SERVICE_URL,default=http://inventory-service.default.svc.cluster.local:8080"`
    PaymentServiceURL   string `env:"PAYMENT_SERVICE_URL,default=http://payment-service.default.svc.cluster.local:8080"`
}
```

The URL uses a DNS name, not an IP. Kubernetes updates the DNS record when pods scale or restart. Your code is unchanged. This covers 90% of production use cases.

**2. Environment-variable injection (works everywhere)**

A more portable pattern is having the orchestrator inject the service URL at deploy time. In Kubernetes this is a ConfigMap or a service URL interpolated by Helm.

```go
// RIGHT — URL injected by the orchestrator, code doesn't know the mechanism
func NewOrderService() *OrderService {
    inventoryURL := os.Getenv("INVENTORY_SERVICE_URL")
    if inventoryURL == "" {
        log.Fatal("INVENTORY_SERVICE_URL is required")
    }
    return &OrderService{
        inventoryClient: newResilientClient(inventoryURL),
    }
}

// A client with timeout and retry built in
func newResilientClient(baseURL string) *ServiceClient {
    return &ServiceClient{
        base: baseURL,
        http: &http.Client{
            Timeout: 5 * time.Second,
            Transport: &http.Transport{
                MaxIdleConns:    100,
                IdleConnTimeout: 90 * time.Second,
            },
        },
    }
}
```

**3. Client-side discovery with a service registry (Consul)**

For more dynamic environments, a service registry like Consul lets services register themselves and others query for live instances. Go has a solid Consul client:

```go
// RIGHT — client-side discovery via Consul
import "github.com/hashicorp/consul/api"

type ConsulResolver struct {
    client *api.Client
}

func NewConsulResolver(addr string) (*ConsulResolver, error) {
    cfg := api.DefaultConfig()
    cfg.Address = addr
    client, err := api.NewClient(cfg)
    if err != nil {
        return nil, fmt.Errorf("consul client: %w", err)
    }
    return &ConsulResolver{client: client}, nil
}

// Resolve returns a random healthy instance of the named service
func (r *ConsulResolver) Resolve(ctx context.Context, service string) (string, error) {
    services, _, err := r.client.Health().Service(service, "", true, nil)
    if err != nil {
        return "", fmt.Errorf("consul query: %w", err)
    }
    if len(services) == 0 {
        return "", fmt.Errorf("no healthy instances of %s", service)
    }

    // Simple random selection — use a proper load balancer for production
    entry := services[rand.Intn(len(services))]
    addr := fmt.Sprintf("http://%s:%d", entry.Service.Address, entry.Service.Port)
    return addr, nil
}
```

## In The Wild

A platform I worked on ran a mix of Kubernetes and some legacy VMs. Kubernetes services used DNS discovery natively — zero code changes required. The legacy VMs registered themselves with Consul on startup and deregistered on graceful shutdown via a deferred call.

```go
// service startup — register with Consul
func registerWithConsul(client *api.Client, service string, port int) (string, error) {
    id := fmt.Sprintf("%s-%s", service, uuid.New().String())
    reg := &api.AgentServiceRegistration{
        ID:   id,
        Name: service,
        Port: port,
        Check: &api.AgentServiceCheck{
            HTTP:                           fmt.Sprintf("http://localhost:%d/health", port),
            Interval:                       "10s",
            DeregisterCriticalServiceAfter: "30s",
        },
    }
    if err := client.Agent().ServiceRegister(reg); err != nil {
        return "", fmt.Errorf("consul register: %w", err)
    }
    return id, nil
}
```

Health checks were critical: Consul uses the health endpoint to determine which instances are eligible for discovery. An unhealthy instance was deregistered within 30 seconds, and clients requesting that service would never receive its address. We went from needing manual intervention on failed deployments to automatic traffic cutover.

## The Gotchas

**DNS caching causes stale resolution.** The Go DNS resolver respects TTLs but your OS DNS cache might not. On Linux, set `GODEBUG=netdns=go` to use Go's pure-Go resolver that respects TTLs correctly, rather than delegating to `cgo`.

**Health checks must reflect actual readiness.** A service that returns 200 on `/health` but can't serve traffic because its database connection is broken is worse than no health check — it lies to the service registry. Health checks should verify the service can actually do work.

**gRPC with discovery needs client-side load balancing.** HTTP/1.1 with a Kubernetes Service gets load balancing from kube-proxy. gRPC connections are long-lived — load balancing happens at connection establishment, not per request. Use gRPC's built-in balancer with headless Services (`ClusterIP: None`) for proper per-RPC load balancing.

**Don't call the registry on every request.** Cache discovered addresses with a short TTL (10–30 seconds). Calling Consul or DNS on every outbound request adds latency and creates a single point of failure in your critical path.

## Key Takeaway

Service discovery is how services find each other without operators maintaining lists of IP addresses. In Kubernetes, DNS-based discovery works out of the box — use service DNS names rather than IPs and let the platform handle the rest. In more dynamic environments, a service registry like Consul provides health-aware discovery with client-side load balancing. Whichever mechanism you choose, embed health checks from day one — they're the signal that makes discovery trustworthy.

---

[← Lesson 2: Inter-Service Communication](/post/go/go-micro-communication) | [Course Index](/series/go-microservices-patterns) | [Next → Lesson 4: Distributed Tracing](/post/go/go-micro-tracing)
