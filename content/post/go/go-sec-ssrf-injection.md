---
title: "Lesson 4: SSRF and Injection — The URL your user gave you might be localhost"
author: Atharva Pandey
series: "Go Security in Production"
lesson: 4
keywords:
  - Go
  - golang
  - security
tags:
  - Go tutorial
  - golang
  - security
date: '2024-09-18T00:00:00.000Z'
---

I reviewed a Go service that let users provide a "webhook URL" — we would call that URL when their account had a notification. The service fetched a preview of the URL to display in the UI. The developer who built it figured that since it only fetched the URL and never executed what was returned, it was safe. They had not considered that `http://169.254.169.254/latest/meta-data/iam/security-credentials/` is also a URL.

SSRF — Server-Side Request Forgery — is the class of vulnerability where an attacker tricks your server into making an HTTP request to a target of their choosing, typically to access internal services that are not exposed to the internet. AWS instance metadata, Redis, internal Kubernetes API servers, admin dashboards, other services in your VPC — all are reachable from a server that blindly follows user-supplied URLs.

## The Problem

The naive implementation fetches whatever URL the user supplies:

```go
// WRONG — unconditional fetch of user-supplied URL
func handleWebhookPreview(w http.ResponseWriter, r *http.Request) {
    userURL := r.URL.Query().Get("url")
    if userURL == "" {
        http.Error(w, "url required", http.StatusBadRequest)
        return
    }

    resp, err := http.Get(userURL)
    if err != nil {
        http.Error(w, "fetch failed", http.StatusBadGateway)
        return
    }
    defer resp.Body.Close()

    // userURL could be:
    // http://169.254.169.254/latest/meta-data/   (AWS metadata)
    // http://localhost:6379/                      (Redis)
    // http://10.0.0.1/admin                      (internal admin)
    // file:///etc/passwd                          (local file read via some Go HTTP stacks)

    io.Copy(w, resp.Body)
}
```

A related vulnerability is SQL injection when user input is concatenated into query strings instead of using parameterized queries:

```go
// WRONG — SQL injection via string concatenation
func getUserByEmail(db *sql.DB, email string) (*User, error) {
    query := "SELECT id, name, email FROM users WHERE email = '" + email + "'"
    // email = "' OR '1'='1" => returns all users
    // email = "'; DROP TABLE users; --" => destroys table

    row := db.QueryRow(query)
    var u User
    if err := row.Scan(&u.ID, &u.Name, &u.Email); err != nil {
        return nil, err
    }
    return &u, nil
}
```

Both vulnerabilities share the same root cause: user-controlled data is used directly in a privileged operation without being validated or escaped.

## The Idiomatic Way

For SSRF, build a safe HTTP client that validates the resolved IP address before connecting. The key insight is that you must validate *after* DNS resolution, not before — an attacker can set up DNS to return an internal IP that looked safe at the hostname level:

```go
// RIGHT — SSRF-safe HTTP client with IP validation
import (
    "context"
    "fmt"
    "net"
    "net/http"
    "net/url"
)

func isPrivateIP(ip net.IP) bool {
    privateRanges := []string{
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "127.0.0.0/8",
        "169.254.0.0/16", // link-local / AWS metadata
        "::1/128",
        "fc00::/7",
    }
    for _, cidr := range privateRanges {
        _, network, _ := net.ParseCIDR(cidr)
        if network.Contains(ip) {
            return true
        }
    }
    return false
}

func newSSRFSafeClient() *http.Client {
    dialer := &net.Dialer{}
    transport := &http.Transport{
        DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
            host, port, err := net.SplitHostPort(addr)
            if err != nil {
                return nil, err
            }
            ips, err := net.DefaultResolver.LookupIPAddr(ctx, host)
            if err != nil {
                return nil, err
            }
            for _, ip := range ips {
                if isPrivateIP(ip.IP) {
                    return nil, fmt.Errorf("request to private IP %s is not allowed", ip.IP)
                }
            }
            return dialer.DialContext(ctx, network, net.JoinHostPort(ips[0].IP.String(), port))
        },
    }
    return &http.Client{Transport: transport}
}
```

You also want to restrict schemes. Only allow `http` and `https` — no `file://`, `ftp://`, or `gopher://`:

```go
// RIGHT — scheme validation before fetching
func validateWebhookURL(rawURL string) error {
    u, err := url.Parse(rawURL)
    if err != nil {
        return fmt.Errorf("invalid URL: %w", err)
    }
    if u.Scheme != "http" && u.Scheme != "https" {
        return fmt.Errorf("scheme %q is not allowed; use http or https", u.Scheme)
    }
    if u.Host == "" {
        return errors.New("URL must have a host")
    }
    return nil
}
```

For SQL injection, use parameterized queries — always. The `database/sql` package makes this trivially easy:

```go
// RIGHT — parameterized query, injection impossible
func getUserByEmail(db *sql.DB, email string) (*User, error) {
    const query = `SELECT id, name, email FROM users WHERE email = $1`
    row := db.QueryRow(query, email) // email is a parameter, not interpolated
    var u User
    if err := row.Scan(&u.ID, &u.Name, &u.Email); err != nil {
        return nil, fmt.Errorf("query user by email: %w", err)
    }
    return &u, nil
}
```

The placeholder syntax varies by driver (`$1` for PostgreSQL, `?` for MySQL and SQLite), but the principle is identical: never build a query string by concatenating user data.

## In The Wild

**Command injection.** If you call `exec.Command` with any user-supplied input, you have potential command injection. The safest rule is to never pass user input as an argument to a shell. If you must, use `exec.Command` with separate arguments rather than `exec.Command("sh", "-c", userInput)`:

```go
// WRONG — shell injection if filename contains semicolons or backticks
cmd := exec.Command("sh", "-c", "convert "+userFilename+" output.png")

// RIGHT — filename is a separate argument, not interpolated into shell command
cmd := exec.Command("convert", userFilename, "output.png")
```

**XML and JSON injection.** If you build XML by concatenating strings, `<` and `&` in user input will corrupt the document or allow attribute injection. Use `encoding/xml` with proper marshaling. Go's XML encoder escapes special characters automatically.

**Template injection.** Go's `html/template` package is injection-safe when used correctly — it auto-escapes values. The danger is using `text/template` for HTML output, or calling `template.HTML()` to mark user input as safe when it is not.

## The Gotchas

**DNS rebinding.** An attacker can set a domain's DNS TTL to zero, pass the IP validation check on first resolution, then have DNS return an internal IP on the actual connection. To defend against this, resolve DNS once in your custom dialer and use the resolved IP for the connection — which is what the implementation above does.

**Redirects.** `http.Client` follows redirects by default. An SSRF-safe client that validates the initial URL can be bypassed if the target URL returns a 302 redirect to an internal address. Override `CheckRedirect` on the client to validate each redirect target:

```go
client.CheckRedirect = func(req *http.Request, via []*http.Request) error {
    return validateWebhookURL(req.URL.String())
}
```

**IPv6.** Private IPv6 ranges (`::1`, `fc00::/7`, `fe80::/10`) must be included in your blocklist. IPv6 addresses in URLs can also be percent-encoded or represented in unusual ways; parse with `net.ParseIP` after resolving rather than trying to pattern-match the raw string.

## Key Takeaway

User-supplied URLs are code paths through your server, not passive data. Before fetching any URL from user input, validate the scheme, resolve the hostname, and block private IP ranges — including the link-local range where cloud provider metadata lives. For SQL, parameterized queries are the only acceptable approach; concatenation is always wrong.

---

**Go Security in Production**

Previous: [Lesson 3: TLS Configuration — Your default HTTP server is unencrypted](/post/go/go-sec-tls/)
Next: [Lesson 5: Auth Middleware — Authentication is not authorization](/post/go/go-sec-auth-middleware/)
