---
title: "Lesson 8: Secure HTTP Defaults — Your production server needs these headers"
author: Atharva Pandey
series: "Go Security in Production"
lesson: 8
keywords:
  - Go
  - golang
  - security
tags:
  - Go tutorial
  - golang
  - security
date: '2025-03-10T00:00:00.000Z'
---

When I run the Go HTTP server in the standard library with no configuration, it does a lot of things right — it is fast, it handles HTTP/2, it is well-tested. But it also does a handful of things that are wrong for production: no timeouts, no response headers that protect against common browser-based attacks, and server identification information in responses. These are not bugs in the standard library; they are defaults appropriate for development that need to be changed before you deploy.

This lesson is a checklist. Every Go HTTP server I ship has all of these. They take about twenty minutes to add and they close several classes of vulnerabilities.

## The Problem

Go's `http.Server` has zero-value timeouts — which means no timeouts at all:

```go
// WRONG — no timeouts, vulnerable to slowloris and resource exhaustion
func main() {
    mux := http.NewServeMux()
    mux.HandleFunc("/", handler)

    // This server will hold connections open forever
    // A client that sends headers one byte at a time will occupy a goroutine indefinitely
    http.ListenAndServe(":8080", mux)
}
```

Without `ReadTimeout`, a slow client that never finishes sending the request body holds a goroutine indefinitely. Without `WriteTimeout`, a slow response write holds another. Without `IdleTimeout`, keep-alive connections are never closed. At scale, these add up to goroutine and file descriptor exhaustion.

Security response headers are absent by default — no Content Security Policy, no X-Frame-Options, no X-Content-Type-Options:

```go
// WRONG — handler with no security headers
func handlePage(w http.ResponseWriter, r *http.Request) {
    // No Content-Security-Policy: susceptible to XSS
    // No X-Frame-Options: susceptible to clickjacking
    // No X-Content-Type-Options: susceptible to MIME sniffing
    // No Referrer-Policy: full URL sent to third parties
    w.Header().Set("Content-Type", "text/html")
    fmt.Fprint(w, pageHTML)
}
```

A third problem is the default `Server` response header, which advertises what software you are running:

```go
// Default behavior — response includes "Server: Go-http-1.1" or similar
// Helps attackers fingerprint your stack and look for version-specific CVEs
```

## The Idiomatic Way

Set timeouts on every `http.Server` instance:

```go
// RIGHT — server with explicit timeouts
func newServer(addr string, handler http.Handler) *http.Server {
    return &http.Server{
        Addr:         addr,
        Handler:      handler,
        ReadTimeout:  5 * time.Second,
        WriteTimeout: 10 * time.Second,
        IdleTimeout:  120 * time.Second,
        // ReadHeaderTimeout can be set separately for longer body reads:
        ReadHeaderTimeout: 2 * time.Second,
    }
}
```

These values are not universal — a file upload endpoint needs a longer `ReadTimeout`. The right approach is to set a reasonable default on the server and override at the handler level using `http.TimeoutHandler` or context deadlines for routes that need more time.

Write a security headers middleware that covers the common browser-based attack vectors:

```go
// RIGHT — comprehensive security headers middleware
func SecureHeaders(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        h := w.Header()

        // Prevent MIME type sniffing
        h.Set("X-Content-Type-Options", "nosniff")

        // Prevent clickjacking — only allow framing from same origin
        h.Set("X-Frame-Options", "SAMEORIGIN")

        // Force HTTPS for 1 year; include subdomains
        h.Set("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

        // Restrict referrer information sent to third parties
        h.Set("Referrer-Policy", "strict-origin-when-cross-origin")

        // Disable browser features not needed by your app
        h.Set("Permissions-Policy", "camera=(), microphone=(), geolocation=()")

        // Content Security Policy — adjust for your app's actual needs
        h.Set("Content-Security-Policy",
            "default-src 'self'; "+
            "script-src 'self'; "+
            "style-src 'self' 'unsafe-inline'; "+
            "img-src 'self' data: https:; "+
            "connect-src 'self'; "+
            "frame-ancestors 'none'")

        // Remove server identification
        h.Set("Server", "")

        next.ServeHTTP(w, r)
    })
}
```

Apply the middleware globally so no route can accidentally skip it:

```go
// RIGHT — middleware applied at the root
func main() {
    mux := http.NewServeMux()
    mux.HandleFunc("/api/", handleAPI)
    mux.HandleFunc("/health", handleHealth)

    srv := newServer(":443", SecureHeaders(mux))
    log.Fatal(srv.ListenAndServeTLS("cert.pem", "key.pem"))
}
```

## In The Wild

**CORS.** If your API is consumed by a browser from a different origin, you need CORS headers. The key mistake is `Access-Control-Allow-Origin: *` on an endpoint that also uses `Access-Control-Allow-Credentials: true` — this combination is rejected by browsers but reflects an intention to allow any origin to make credentialed requests, which is almost certainly wrong. Be explicit:

```go
// RIGHT — CORS with explicit allowed origins
func CORSMiddleware(allowedOrigins []string) func(http.Handler) http.Handler {
    allowedSet := make(map[string]bool, len(allowedOrigins))
    for _, o := range allowedOrigins {
        allowedSet[o] = true
    }

    return func(next http.Handler) http.Handler {
        return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
            origin := r.Header.Get("Origin")
            if allowedSet[origin] {
                w.Header().Set("Access-Control-Allow-Origin", origin)
                w.Header().Set("Access-Control-Allow-Credentials", "true")
                w.Header().Set("Vary", "Origin")
            }
            if r.Method == http.MethodOptions {
                w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
                w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization")
                w.WriteHeader(http.StatusNoContent)
                return
            }
            next.ServeHTTP(w, r)
        })
    }
}
```

**Rate limiting.** An HTTP server without rate limiting is vulnerable to brute force and denial of service. For simple rate limiting, `golang.org/x/time/rate` provides a token bucket implementation. Apply it per-IP or per-user depending on the endpoint:

```go
var limiter = rate.NewLimiter(rate.Every(time.Second), 10) // 10 requests/second globally

func RateLimitMiddleware(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        if !limiter.Allow() {
            http.Error(w, "rate limit exceeded", http.StatusTooManyRequests)
            return
        }
        next.ServeHTTP(w, r)
    })
}
```

## The Gotchas

**`WriteTimeout` and streaming responses.** If you have a server-sent events endpoint or a streaming response, `WriteTimeout` will close the connection after the timeout expires even if the client is actively reading. Use `http.ResponseController.SetWriteDeadline` to extend or clear the deadline for specific handlers.

**CSP and SPAs.** The Content Security Policy above restricts `script-src` to `'self'`. Single-page applications that use CDN-hosted scripts, Google Analytics, or inline event handlers will break. Build CSP incrementally using the `Content-Security-Policy-Report-Only` header first — it reports violations without blocking, letting you see what would break before enforcing.

**HSTS and mixed content.** Once you set HSTS with `max-age=31536000`, browsers will refuse to load your site over HTTP for a year. Make absolutely sure all assets (images, fonts, scripts) are served over HTTPS before setting this header. A single HTTP asset in your critical path will break your site for users who have the HSTS policy cached.

**`X-Forwarded-For` and `RemoteAddr`.** When your server is behind a load balancer or reverse proxy, `r.RemoteAddr` is the proxy IP, not the client IP. The client IP is in `X-Forwarded-For`. Trusting `X-Forwarded-For` without validating that it came from a trusted proxy is a security issue — a client can spoof it. Trust the header only if the connection came from a known proxy IP.

## Key Takeaway

A Go HTTP server with zero configuration is not production-safe. Set timeouts on every `http.Server`, add a security headers middleware as the outermost layer of your stack, be explicit about CORS origins, and add rate limiting to externally exposed endpoints. These are not optional hardening steps — they are the baseline your server should start from.

---

**Go Security in Production**

Previous: [Lesson 7: JWT Caveats — JWTs are not sessions](/post/go/go-sec-jwt/)
Next: [Lesson 9: Dependency Scanning — govulncheck before you deploy](/post/go/go-sec-dependency-scanning/)
