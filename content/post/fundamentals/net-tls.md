---
title: "Lesson 3: TLS Handshake — What happens in those 2 round trips"
author: Atharva Pandey
keywords:
  - TLS
  - TLS 1.3
  - handshake
  - certificates
  - encryption
  - networking
  - backend engineering
tags:
  - fundamentals
  - networking
series: "Networking for Backend Engineers"
lesson: 3
date: '2024-05-30T00:00:00.000Z'
---

A colleague once asked me why adding TLS to a service increased our P99 latency by 50ms. She had measured it carefully, switching between HTTP and HTTPS in a load test. My first instinct was to say "encryption overhead" but that's wrong — modern CPUs with AES-NI can encrypt gigabytes per second. The actual cost is the handshake. Once I explained what was actually happening in those first few round trips, the answer to "how do we fix it" became obvious: stop creating new connections.

## How It Works

TLS (Transport Layer Security) runs on top of TCP. It provides three things: confidentiality (data is encrypted), integrity (data hasn't been tampered with), and authentication (you're talking to who you think you're talking to).

**TLS 1.2 Handshake (the old way)**

TLS 1.2 requires two round trips before application data flows:

```
Client                                  Server
  |                                       |
  |---- ClientHello ---------------------->|
  |     (TLS version, cipher suites,      |
  |      client random, SNI)              |
  |                                       |
  |<--- ServerHello -----------------------|
  |<--- Certificate -----------------------|
  |<--- ServerKeyExchange (if DHE/ECDHE) --|
  |<--- ServerHelloDone -------------------|
  |                                       |  <- 1 RTT
  |---- ClientKeyExchange ---------------->|
  |---- ChangeCipherSpec ----------------->|
  |---- Finished (encrypted) ------------>|
  |                                       |
  |<--- ChangeCipherSpec ------------------|
  |<--- Finished (encrypted) -------------|
  |                                       |  <- 2 RTT
  |==== Application Data ================>|
```

The `ClientHello` advertises which cipher suites and TLS versions the client supports. The server picks one and responds with its certificate. The key exchange happens in round trip 1 (client sends its key share), and both sides derive the session keys. Round trip 2 confirms the handshake wasn't tampered with. Only then can application data flow.

**TLS 1.3 Handshake (the new way)**

TLS 1.3 (RFC 8446, 2018) redesigned the handshake to require only one round trip for new connections:

```
Client                                  Server
  |                                       |
  |---- ClientHello ---------------------->|
  |     (key_share, supported_versions,   |
  |      signature_algorithms, SNI)       |
  |                                       |
  |<--- ServerHello -----------------------|
  |<--- {EncryptedExtensions} ------------|
  |<--- {Certificate} --------------------|
  |<--- {CertificateVerify} --------------|
  |<--- {Finished} -----------------------|
  |                                       |  <- 1 RTT
  |---- {Finished} ----------------------->|
  |==== Application Data ================>|
```

TLS 1.3 moves the key share into the `ClientHello`. The client sends its ECDHE public key guess (it picks one from the server's likely preferences) so the server can derive the key immediately and start encrypting its response. The server's certificate and the `Finished` message are now encrypted. The entire second flight is encrypted.

**0-RTT Session Resumption**

Both TLS 1.2 and 1.3 support session resumption — reusing cryptographic state from a previous connection to skip part of the handshake. TLS 1.3 introduced **0-RTT** (zero round trip time resumption). The client sends a `pre_shared_key` extension and can include application data in the very first flight.

```
Client                                  Server
  |                                       |
  |---- ClientHello + early_data -------->|
  |     (pre_shared_key, key_share)       |
  |==== Application Data (0-RTT) ========>|
  |                                       |
  |<--- ServerHello + ... ----------------|
  |<--- {Finished} -----------------------|
  |                                       |
  |---- {Finished} ----------------------->|
  |==== Application Data (1-RTT) ========>|
```

**Certificate validation**

When the server sends its certificate, the client validates it:
1. The certificate chain leads to a trusted root CA.
2. The domain name matches the certificate's Subject Alternative Names.
3. The certificate hasn't expired.
4. The certificate hasn't been revoked (via OCSP or CRL — often skipped in practice).

This is where OCSP stapling comes in: the server fetches and includes the OCSP response in the handshake, so the client doesn't need a separate network request to check revocation.

**After the handshake**

Once keys are established, data is encrypted with a symmetric cipher — AES-128-GCM or ChaCha20-Poly1305 in TLS 1.3. These are authenticated encryption schemes: encryption and integrity checking in one operation. The CPU cost of this is negligible on modern hardware. The TLS overhead you actually feel in production is almost always handshake latency, not encryption throughput.

## Why It Matters

Every service-to-service call in a microservices architecture that goes over TLS pays the handshake tax on new connections. If your service discovery returns a different backend instance every request and you're creating new connections, you're paying 1-2 RTTs per request before the actual work starts. At 10ms RTT between data centers, that's 10-20ms of pure protocol overhead.

mTLS (mutual TLS) adds a client certificate to the mix — the server verifies the client's identity too. This is how service meshes do zero-trust authentication. The handshake gets slightly longer, but you eliminate network-level service identity tokens.

## Production Example

Here's how I configure TLS in Go for production, with session resumption and sensible cipher suites:

```go
package main

import (
    "crypto/tls"
    "net/http"
    "time"
)

func newTLSConfig(certFile, keyFile string) (*tls.Config, error) {
    cert, err := tls.LoadX509KeyPair(certFile, keyFile)
    if err != nil {
        return nil, err
    }

    return &tls.Config{
        Certificates: []tls.Certificate{cert},
        MinVersion:   tls.VersionTLS12,
        // Prefer TLS 1.3 — it's faster and more secure
        // Go's crypto/tls will negotiate TLS 1.3 automatically if both sides support it

        // TLS 1.2 cipher suites — ordered by preference
        // TLS 1.3 cipher suites are fixed and not configurable
        CipherSuites: []uint16{
            tls.TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384,
            tls.TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384,
            tls.TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256,
            tls.TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256,
            tls.TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305_SHA256,
            tls.TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305_SHA256,
        },
        // Enable session tickets for TLS 1.2 session resumption
        SessionTicketsDisabled: false,
        // Curve preferences
        CurvePreferences: []tls.CurveID{
            tls.X25519,
            tls.CurveP256,
        },
    }, nil
}

// For the client side — internal service calls
func newTLSClient() *http.Client {
    return &http.Client{
        Transport: &http.Transport{
            TLSClientConfig: &tls.Config{
                MinVersion: tls.VersionTLS12,
                // For internal services with internal CA
                // RootCAs: loadInternalCA(),
            },
            // Keep connections alive to amortize handshake cost
            MaxIdleConnsPerHost: 20,
            IdleConnTimeout:     90 * time.Second,
            // TLS handshake timeout
            TLSHandshakeTimeout: 10 * time.Second,
        },
        Timeout: 30 * time.Second,
    }
}
```

To measure how long your TLS handshake is actually taking:

```go
// Use httptrace to instrument TLS timing
trace := &httptrace.ClientTrace{
    TLSHandshakeStart: func() {
        tlsStart = time.Now()
    },
    TLSHandshakeDone: func(state tls.ConnectionState, err error) {
        tlsDuration = time.Since(tlsStart)
        log.Printf("TLS handshake: %v (version: %x, resumed: %v)",
            tlsDuration, state.Version, state.DidResume)
    },
}
req = req.WithContext(httptrace.WithClientTrace(req.Context(), trace))
```

`state.DidResume` tells you whether session resumption worked. If it's always false, your connection pool isn't reusing connections effectively.

## The Tradeoffs

**TLS 1.2 vs TLS 1.3**: TLS 1.3 is strictly better for new connections (1 RTT vs 2 RTT) and removes weak cipher options. The only reason to keep TLS 1.2 support is for old clients. If you control both ends, mandate TLS 1.3.

**Session tickets vs session IDs**: Session tickets store encrypted session state on the client (server is stateless). Session IDs require server-side state (problematic across a pool of servers unless shared). Session tickets are the modern approach but require careful key rotation — if ticket keys leak, past sessions can be decrypted.

**0-RTT replay risk**: 0-RTT data can be replayed by an attacker (they capture and re-send the first flight). Only use 0-RTT for genuinely idempotent operations. POST requests that charge a credit card must not use 0-RTT.

**Certificate pinning**: Pinning the server's certificate or public key in the client prevents MITM attacks even if a CA is compromised. But it makes certificate rotation painful. Reasonable for high-security internal services, usually not worth it for public APIs.

**mTLS complexity**: Mutual TLS eliminates the need for application-layer API keys between services. But certificate lifecycle management is operationally heavy. A service mesh (Istio, Linkerd) handles this automatically — worth considering before rolling your own.

## Key Takeaway

The TLS handshake costs 1-2 round trips of setup before a single byte of application data flows. TLS 1.3 cuts this to 1 RTT (0 RTT for resumption). The symmetric encryption after the handshake is essentially free on modern CPUs. The practical implication: keep connections alive, size your connection pools to avoid re-handshaking under load, and instrument `httptrace` to see when resumption fails. Understanding TLS at this level makes the difference between guessing at latency problems and diagnosing them.

---

**Previous:** [Lesson 2: HTTP/2 and HTTP/3](/post/fundamentals/net-http2-http3/)
**Next:** [Lesson 4: DNS — Resolution, caching, and why changes take time](/post/fundamentals/net-dns/)
