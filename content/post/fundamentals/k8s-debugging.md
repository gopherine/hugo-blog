---
title: "Lesson 5: Debugging in Kubernetes — kubectl tricks that save hours"
author: Atharva Pandey
keywords:
  - Kubernetes
  - kubectl
  - debugging
  - troubleshooting
  - pod debugging
  - Kubernetes debugging
tags:
  - fundamentals
  - kubernetes
  - devops
series: "Kubernetes Patterns"
lesson: 5
date: '2025-03-18T00:00:00.000Z'
---

The most stressful hours of my on-call life have been in Kubernetes clusters where something was wrong but nothing was obviously broken. Pods in `Pending` for reasons that weren't clear. Services returning 503s but all pods showing as `Running`. Memory usage climbing slowly across a fleet for two days before things started dying. Over years of debugging production Kubernetes issues, I've accumulated a set of commands and mental models that cut through the noise. This article is that collection.

## The Pattern

Kubernetes debugging follows a top-down investigation model. Start at the workload level (is the Deployment healthy?), descend to pod level (are pods scheduled and running?), then container level (what are the logs saying?), then network level (can pods reach the services they depend on?), then resource level (is the node under pressure?). Most issues reveal themselves before you reach the bottom.

The second pattern: always look at events. Events are Kubernetes' changelog for what happened to a resource. They're time-stamped, named by the resource they relate to, and contain human-readable reason codes. They're the first thing I check when something is wrong.

## How It Works

**Starting point: workload overview**

```bash
# Get a quick overview of everything in a namespace
kubectl get all -n production

# See which pods are not Running/Completed
kubectl get pods -n production --field-selector=status.phase!=Running

# See pod resource usage (requires metrics-server)
kubectl top pods -n production --sort-by=memory

# See events for the namespace, sorted by time
kubectl get events -n production --sort-by='.lastTimestamp'
```

**Investigating a specific pod**

```bash
# Full description — scheduling decisions, events, volume mounts, conditions
kubectl describe pod <pod-name> -n production

# Previous container logs (if the container crashed and restarted)
kubectl logs <pod-name> -n production --previous

# Follow logs in real time from all containers in a pod
kubectl logs <pod-name> -n production --all-containers=true -f

# Logs from a specific container in a multi-container pod
kubectl logs <pod-name> -c sidecar-name -n production

# Get logs from all pods in a deployment simultaneously (using labels)
kubectl logs -l app=api-server -n production --all-containers=true
```

**The ephemeral container trick** — debugging a running pod without a shell in the image:

```bash
# Attach a debug container to a running pod (Kubernetes 1.23+)
kubectl debug -it <pod-name> -n production \
  --image=nicolaka/netshoot \
  --target=api \        # share the process namespace of the api container
  -- bash

# Inside the debug container you can:
# - curl internal services: curl http://other-service.production.svc.cluster.local
# - check DNS: nslookup kubernetes.default
# - inspect processes: ps aux
# - check network: ss -tlnp, netstat -rn
# - tcpdump: tcpdump -i eth0 port 5432
```

This is invaluable for distroless images that have no shell. You attach a debug container that has full networking tools and shares the network namespace (and optionally process namespace) of the target container.

**Investigating scheduling failures (pod stuck in Pending)**

```bash
# The events section of describe will show the reason
kubectl describe pod <pending-pod> -n production
# Look for: "0/3 nodes are available: 3 Insufficient memory"
# Or: "pod has unbound immediate PersistentVolumeClaims"
# Or: "0/3 nodes are available: 3 node(s) had untolerated taint"

# Check node capacity and allocations
kubectl describe nodes | grep -A 5 "Allocated resources"

# Check if taints are blocking scheduling
kubectl get nodes -o custom-columns=NAME:.metadata.name,TAINTS:.spec.taints
```

**Investigating network issues**

```bash
# Check if a service exists and has endpoints
kubectl get svc payment-service -n production
kubectl get endpoints payment-service -n production
# If ENDPOINTS shows <none>, the service selector doesn't match any pod labels

# Verify pod labels match service selector
kubectl get pods -n production -l app=payment-service
kubectl get svc payment-service -n production -o yaml | grep -A 5 selector

# Test DNS resolution from within the cluster
kubectl run dns-test --image=busybox:1.36 --rm -it --restart=Never -- \
  nslookup payment-service.production.svc.cluster.local

# Test HTTP connectivity from within the cluster
kubectl run curl-test --image=curlimages/curl:latest --rm -it --restart=Never -- \
  curl -v http://payment-service.production.svc.cluster.local/health
```

## Production Example

One of the most common and confusing failure modes: pods are `Running` but the service returns 503. Here's the full debugging chain:

```bash
# Step 1: Confirm pods are actually running and passing health checks
kubectl get pods -n production -l app=api-server
# STATUS: Running ✓

# Step 2: Check if the service has endpoints
kubectl get endpoints api-server -n production
# ENDPOINTS: <none>  ← The problem is here

# Step 3: Why are there no endpoints?
kubectl get pods -n production -l app=api-server --show-labels
# NAME              LABELS
# api-server-xyz    app=api,version=v2   ← label is "app=api" not "app=api-server"

kubectl get svc api-server -n production -o yaml | grep -A 3 selector
# selector:
#   app: api-server   ← selector expects "app=api-server", pods have "app=api"

# Step 4: Fix the label mismatch
kubectl patch deployment api-server -n production \
  --type='json' \
  -p='[{"op": "replace", "path": "/spec/template/metadata/labels/app", "value": "api-server"}]'
```

Label mismatches are one of the most common causes of 503s. The service selector and the pod labels must match exactly. Another common cause: readiness probe failures. The pod is running but the readiness probe is failing, so Kubernetes removes it from the service's endpoints. Check:

```bash
kubectl describe pod <pod-name> -n production | grep -A 10 "Readiness"
# If you see: Readiness probe failed: HTTP probe failed with statuscode: 500
# The pod is unhealthy. Check logs for the application error.
```

**Debugging OOMKilled pods**:

```bash
# Check pod restart count and last termination reason
kubectl get pods -n production
# RESTARTS: 47 is a red flag

kubectl describe pod <pod-name> -n production | grep -A 5 "Last State"
# Last State: Terminated
#   Reason: OOMKilled
#   Exit Code: 137
#   Started: ...
#   Finished: ...

# Check memory limits vs actual usage
kubectl top pod <pod-name> -n production --containers
# If actual usage is close to the limit, the limit needs to be raised
# OR the application has a memory leak

# Get VPA recommendation if VPA is installed
kubectl describe vpa <vpa-name> -n production | grep -A 20 "Container Recommendations"
```

## The Gotchas

**`kubectl exec` vs `kubectl debug`**: `kubectl exec` requires a shell in the container image. For minimal images (distroless, scratch), there's no shell. Use `kubectl debug` with `--image=nicolaka/netshoot` to attach a debug container. Since Kubernetes 1.25, you can also create a copy of a pod with debug tools:

```bash
kubectl debug <pod-name> -n production --copy-to=debug-pod \
  --image=nicolaka/netshoot --share-processes
```

**Log retention**: By default, Kubernetes keeps the last few MB of logs per container per pod. For pods that have been running for days, you may not have logs going back far enough to find the root cause of an intermittent issue. Ensure your cluster is shipping logs to a centralized system (Loki, Elasticsearch, Datadog). Never rely on `kubectl logs` for post-incident analysis — by the time you're investigating, the relevant logs may have been rotated out.

**`kubectl port-forward` for debugging services**:

```bash
# Forward a local port to a pod port — useful for debugging a service directly
kubectl port-forward pod/<pod-name> 8080:8080 -n production
# Now curl localhost:8080 hits the pod directly, bypassing the service

# Forward to a service (automatically selects a ready pod)
kubectl port-forward svc/api-server 8080:80 -n production
```

**jsonpath and custom columns for scripted debugging**: When you need to quickly extract specific fields from multiple resources:

```bash
# Get image versions for all pods in a deployment
kubectl get pods -n production -l app=api-server \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[0].image}{"\n"}{end}'

# Get all pods with their node assignment and resource requests
kubectl get pods -n production -o custom-columns=\
NAME:.metadata.name,NODE:.spec.nodeName,CPU-REQ:.spec.containers[0].resources.requests.cpu
```

## Key Takeaway

Kubernetes debugging is systematic, not intuitive. The top-down model (workload → pod → container → network → resources) prevents you from chasing symptoms at the wrong layer. The single most valuable command is `kubectl describe` — read the events section first. For network issues, always check endpoints before assuming an application bug. For scheduling issues, check node resource pressure and taints. For mysterious 503s, check the label selector match between services and pods. For OOMKilled pods, check VPA recommendations and watch actual vs requested memory. Add `kubectl debug` with a netshoot image to your muscle memory — it's the Swiss army knife for containers with no shell.

---

**Previous:** [Lesson 4: Operators and CRDs — Extending Kubernetes with your own resources](/post/fundamentals/k8s-operators/)

---

🎓 **Course Complete!** You've finished "Kubernetes Patterns." From pod design patterns through deployments and autoscaling, configuration management, Operators and CRDs, and finally production debugging — you now have a practical toolkit for building, operating, and troubleshooting Kubernetes workloads at scale.
