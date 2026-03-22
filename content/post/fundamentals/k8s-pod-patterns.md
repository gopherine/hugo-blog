---
title: "Lesson 1: Pod Design Patterns — Sidecar, ambassador, adapter"
author: Atharva Pandey
keywords:
  - Kubernetes
  - pod design patterns
  - sidecar pattern
  - ambassador pattern
  - adapter pattern
  - container patterns
tags:
  - fundamentals
  - kubernetes
  - devops
series: "Kubernetes Patterns"
lesson: 1
date: '2024-05-24T00:00:00.000Z'
---

The first time I deployed a service to Kubernetes, I put everything in a single container. Logging, metrics, TLS termination, the actual application — all in one Docker image. It worked, but the image was massive, updating any single concern meant rebuilding and redeploying the whole thing, and the application team had to understand infrastructure concerns they shouldn't need to care about. The sidecar pattern was the thing that changed how I thought about container composition.

Kubernetes gives you the pod as its smallest deployable unit, and a pod can contain multiple containers. Most tutorials gloss over this, but the multi-container pod is one of Kubernetes' most powerful design primitives. The three canonical patterns — sidecar, ambassador, and adapter — cover most of the use cases where you want to decompose a pod into specialized containers.

## The Pattern

**Sidecar**: A helper container that extends or enhances the main container without changing it. The sidecar shares the pod's lifecycle, network, and optionally volumes with the main container. Classic examples: a log shipping container that reads log files written by the app and forwards them to Elasticsearch; a metrics exporter that scrapes the app's local metrics endpoint and exposes them in Prometheus format; an Envoy proxy that handles all network traffic on behalf of the app.

**Ambassador**: A proxy container that acts as an interface between the application container and the external world. The app connects to `localhost`, and the ambassador container handles the actual external connection, including service discovery, load balancing, retries, and protocol translation. The app doesn't need to know which backend it's talking to.

**Adapter**: A container that normalizes the output of the main container into a standard format. If you have a legacy application that emits metrics in a non-standard format, an adapter container can translate them to Prometheus metrics without touching the legacy app. The adapter reads the app's output and produces standardized output for the external system.

## How It Works

All three patterns rely on the fact that containers in a pod share the same network namespace. They can communicate over `localhost` as if they were processes on the same machine, without going through any service mesh or external network hop.

```yaml
# Sidecar example: app + log forwarder
apiVersion: v1
kind: Pod
metadata:
  name: web-app
spec:
  volumes:
    - name: shared-logs
      emptyDir: {}

  containers:
    - name: web-app
      image: mycompany/web-app:1.4.2
      volumeMounts:
        - name: shared-logs
          mountPath: /var/log/app

    - name: log-forwarder          # sidecar
      image: fluent/fluent-bit:2.1
      volumeMounts:
        - name: shared-logs
          mountPath: /var/log/app
          readOnly: true
      env:
        - name: FLUENT_ELASTICSEARCH_HOST
          value: "elasticsearch.logging.svc.cluster.local"
```

The `emptyDir` volume is the shared substrate — it's created when the pod starts and deleted when the pod terminates. The app writes logs to `/var/log/app`. The sidecar reads from the same path and forwards. Neither container knows much about the other.

For the ambassador pattern, the connection happens over localhost:

```yaml
  containers:
    - name: app
      image: mycompany/app:latest
      env:
        - name: DB_HOST
          value: "localhost:5432"   # connects to ambassador, not DB directly

    - name: pg-ambassador           # ambassador
      image: pgbouncer:1.18
      env:
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: db-credentials
              key: connection-string
```

The app always connects to `localhost:5432`. The ambassador handles connection pooling, TLS, and failover to read replicas. If you need to change the backend database, you update the ambassador configuration — the app config doesn't change.

## Production Example

The most pervasive sidecar in production Kubernetes today is the Envoy proxy in a service mesh (Istio or Linkerd). Every pod in a mesh-enabled namespace gets an Envoy sidecar injected automatically by a mutating admission webhook. The sidecar intercepts all inbound and outbound traffic using iptables rules applied at pod startup. The application sends traffic to `localhost:15001`, Envoy handles mTLS, retries, circuit breaking, and tracing — completely transparently to the app.

Here's a simplified view of what Istio injects:

```yaml
# Automatically injected by Istio's MutatingAdmissionWebhook
initContainers:
  - name: istio-init
    image: istio/proxyv2:1.18.0
    args: ["istio-iptables", "-p", "15001", "-z", "15006", ...]
    # sets up iptables rules to redirect all traffic through Envoy

containers:
  - name: your-app
    image: mycompany/app:latest
    # your app, unchanged

  - name: istio-proxy              # the sidecar
    image: istio/proxyv2:1.18.0
    args: ["proxy", "sidecar", ...]
    ports:
      - containerPort: 15090       # Prometheus metrics
      - containerPort: 15021       # health check
```

The application team deploys their app without thinking about mTLS. The platform team controls the Envoy configuration via `VirtualService`, `DestinationRule`, and `PeerAuthentication` resources. The sidecar pattern makes this separation of concerns possible at the infrastructure level.

## The Gotchas

**Startup ordering**: Containers in a pod start in parallel by default. If your app tries to connect to the ambassador sidecar before it's ready, you get connection refused errors on startup. The fix pre-Kubernetes 1.28 was clunky: add a sleep in the app container's startup command. From Kubernetes 1.28 onward, native sidecar support via `initContainers` with `restartPolicy: Always` starts sidecars before app containers and stops them after.

**Resource accounting**: Each sidecar consumes CPU and memory from the node. A fleet of 500 pods with an Envoy sidecar that each consume 50m CPU and 64Mi memory is 25 CPU cores and 32 GB of memory just for sidecars. This isn't theoretical — I've seen teams surprised by how much their node resource requirements grew after enabling Istio. Set resource requests and limits on sidecars explicitly.

**Logging confusion**: When a pod has multiple containers, `kubectl logs pod-name` fails and you have to specify the container: `kubectl logs pod-name -c sidecar-name`. New engineers consistently hit this. Make sure your runbooks document the container names for each pod type.

**Volume mounts for sidecar-only data**: If the sidecar has data it wants to persist between restarts (e.g., a buffered log queue), use a `hostPath` or `emptyDir` volume. Don't use a `PersistentVolumeClaim` for this — the PVC lifecycle and the pod lifecycle don't align the way you expect, and you'll leak volumes.

## Key Takeaway

The sidecar, ambassador, and adapter patterns are how you decompose cross-cutting concerns (observability, security, protocol translation) out of your application container without modifying your application. The mechanism is straightforward: containers in a pod share the network namespace and can share volumes, so they can cooperate without complex IPC. In practice, the sidecar pattern is pervasive — if you're running a service mesh, you're already running sidecars at scale. The key discipline is treating sidecars as first-class infrastructure: give them resource limits, account for their startup ordering, and make sure your operational tooling (logging, metrics, debugging) handles multi-container pods correctly.

---

**Next:** [Lesson 2: Deployments and Scaling — Rolling updates, HPA, VPA](/post/fundamentals/k8s-deployments/)
