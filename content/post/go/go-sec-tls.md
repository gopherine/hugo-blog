---
title: "Lesson 3: TLS Configuration — Your default HTTP server is unencrypted"
author: Atharva Pandey
series: "Go Security in Production"
lesson: 3
keywords:
  - Go
  - golang
  - security
tags:
  - Go tutorial
  - golang
  - security
date: '2024-08-12T00:00:00.000Z'
---

When I started writing Go HTTP servers, I thought TLS was someone else's problem. A load balancer in front of my service handled HTTPS termination, so my service only ever spoke plain HTTP on the internal network. That is a common and often defensible architecture. But it means that if anyone gains access to your internal network — a compromised service, a misconfigured cloud security group, a rogue container — all traffic between your services is plaintext. I learned this lesson not from a breach but from a penetration test report that listed it as a finding with a crisp paragraph explaining exactly why it mattered.

Understanding Go's TLS defaults — and their weaknesses — gives you the knowledge to make an informed choice, not just inherit whatever the standard library ships with.

## The Problem

The most obvious problem is running plain HTTP when you should not be:

```go
// WRONG — plain HTTP, all traffic unencrypted
func main() {
    mux := http.NewServeMux()
    mux.HandleFunc("/api/", handleAPI)

    srv := &http.Server{
        Addr:    ":8080",
        Handler: mux,
    }
    log.Fatal(srv.ListenAndServe())
}
```

Even in an internal-only service, plain HTTP is a problem. More subtle is using `ListenAndServeTLS` with all the defaults — which means TLS 1.0 and 1.1 are still negotiable, weak cipher suites are available, and server-side cipher preference is not enforced:

```go
// WRONG — TLS enabled but insecure defaults
func main() {
    mux := http.NewServeMux()
    log.Fatal(http.ListenAndServeTLS(":443", "cert.pem", "key.pem", mux))
}
```

`http.ListenAndServeTLS` uses `tls.Config{}` with zero-value settings. That means minimum TLS version is TLS 1.0, which is vulnerable to POODLE and BEAST. It also does not set timeouts, which leaves the server open to slowloris attacks.

A third failure is building an HTTP client that skips certificate verification — common during development, dangerous when it leaks to production:

```go
// WRONG — disabling certificate verification
client := &http.Client{
    Transport: &http.Transport{
        TLSClientConfig: &tls.Config{
            InsecureSkipVerify: true, // do NOT ship this
        },
    },
}
```

`InsecureSkipVerify: true` means man-in-the-middle attacks are completely undetected. The entire point of TLS is eliminated.

## The Idiomatic Way

Build a `tls.Config` explicitly and attach it to both server and client. Do not rely on the zero-value defaults:

```go
// RIGHT — hardened TLS configuration for an HTTP server
import (
    "crypto/tls"
    "net/http"
    "time"
)

func newTLSConfig() *tls.Config {
    return &tls.Config{
        MinVersion: tls.VersionTLS12,
        CurvePreferences: []tls.CurveID{
            tls.CurveP256,
            tls.X25519,
        },
        PreferServerCipherSuites: true,
        CipherSuites: []uint16{
            tls.TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384,
            tls.TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384,
            tls.TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305,
            tls.TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305,
            tls.TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256,
            tls.TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256,
        },
    }
}

func main() {
    mux := http.NewServeMux()
    mux.HandleFunc("/api/", handleAPI)

    srv := &http.Server{
        Addr:         ":443",
        Handler:      mux,
        TLSConfig:    newTLSConfig(),
        ReadTimeout:  5 * time.Second,
        WriteTimeout: 10 * time.Second,
        IdleTimeout:  120 * time.Second,
    }

    log.Fatal(srv.ListenAndServeTLS("cert.pem", "key.pem"))
}
```

Setting `MinVersion: tls.VersionTLS12` drops TLS 1.0 and 1.1. Setting `PreferServerCipherSuites: true` ensures your curated list is used over whatever the client prefers. Setting timeouts prevents slow-connection attacks.

For mTLS between internal services — where both sides present certificates — the config adds client certificate verification:

```go
// RIGHT — mutual TLS for service-to-service communication
func newMTLSConfig(caCertPEM []byte) (*tls.Config, error) {
    caPool := x509.NewCertPool()
    if !caPool.AppendCertsFromPEM(caCertPEM) {
        return nil, errors.New("failed to parse CA certificate")
    }

    return &tls.Config{
        MinVersion: tls.VersionTLS12,
        ClientCAs:  caPool,
        ClientAuth: tls.RequireAndVerifyClientCert,
    }, nil
}
```

On the client side, always use the system cert pool unless you have a specific reason to override it:

```go
// RIGHT — client that respects system cert pool and enforces TLS 1.2+
func newSecureHTTPClient() *http.Client {
    tlsCfg := &tls.Config{
        MinVersion: tls.VersionTLS12,
    }
    return &http.Client{
        Transport: &http.Transport{
            TLSClientConfig: tlsCfg,
        },
        Timeout: 10 * time.Second,
    }
}
```

## In The Wild

**Let's Encrypt with `golang.org/x/crypto/acme/autocert`.** If you are running a public-facing service, use `autocert` to provision and renew certificates automatically. It handles the ACME challenge, certificate storage, and renewal. You do not need to manage `cert.pem` and `key.pem` manually:

```go
m := &autocert.Manager{
    Cache:      autocert.DirCache("/var/cache/certs"),
    Prompt:     autocert.AcceptTOS,
    HostPolicy: autocert.HostWhitelist("example.com"),
}
srv := &http.Server{
    Addr:      ":443",
    TLSConfig: m.TLSConfig(),
}
```

**TLS 1.3.** If `MinVersion` is `tls.VersionTLS13`, Go 1.18+ will use only TLS 1.3 — which has a simplified, more secure cipher suite negotiation. If you control both ends of the connection (service-to-service), TLS 1.3 minimum is an excellent choice.

**Certificate pinning.** For mobile or desktop clients communicating with your API, consider certificate pinning — verifying the leaf certificate fingerprint rather than just the chain. In Go, implement this via a custom `VerifyPeerCertificate` function in `tls.Config`.

## The Gotchas

**Cipher suite configuration is ignored for TLS 1.3.** Go's TLS 1.3 implementation ignores the `CipherSuites` field — TLS 1.3 has a fixed set of secure cipher suites, and the negotiation is handled internally. Do not waste time trying to configure them; it will silently have no effect.

**`http.DefaultClient` has no TLS hardening.** Any package-level code or third-party library that calls `http.Get` is using `http.DefaultClient`, which has no timeouts and default TLS settings. Initialize a custom client in `main` and inject it rather than relying on the default.

**Certificate rotation.** When you update certificates on disk, the running server needs to reload them. Go's `tls.Config` supports a `GetCertificate` callback that lets you return a certificate dynamically, enabling zero-downtime rotation without restarting the process.

**HSTS headers.** TLS alone does not prevent a downgrade attack if a browser can be tricked into making a plain HTTP request first. Add the `Strict-Transport-Security` header to tell browsers to always use HTTPS. We will cover this in detail in Lesson 8.

## Key Takeaway

The zero-value `tls.Config` in Go is not production-safe. Set `MinVersion: tls.VersionTLS12` at minimum, enumerate your cipher suites, always set timeouts on the `http.Server`, and never set `InsecureSkipVerify: true` outside of a test that is explicitly labeled as doing so. TLS configuration is three lines of code; there is no excuse for skipping it.

---

**Go Security in Production**

Previous: [Lesson 2: Secret Handling — Env vars are not a vault](/post/go/go-sec-secrets/)
Next: [Lesson 4: SSRF and Injection — The URL your user gave you might be localhost](/post/go/go-sec-ssrf-injection/)
