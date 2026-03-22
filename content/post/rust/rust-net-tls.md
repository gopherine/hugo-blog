---
title: "Lesson 6: TLS — rustls and native TLS"
author: Atharva Pandey
series: "Rust Networking & Distributed Systems"
lesson: 6
keywords: [Rust, rust, networking, distributed, TLS, SSL, rustls, native-tls, encryption, certificates]
tags: [Rust tutorial, rust, networking, distributed-systems]
date: '2025-05-23T19:10:00.000Z'
---

I'll never forget the 3am page that turned out to be an expired TLS certificate. Our automated renewal had been silently failing for two weeks, nobody noticed because the cert was still valid, and then at 2:47am on a Sunday it expired and every client started getting connection errors. We had monitoring for CPU, memory, disk, latency, error rates — but not for certificate expiry. That was the day I decided to actually understand TLS instead of just copy-pasting cert paths into config files.

## The Two Camps: rustls vs native-tls

Rust has two main TLS libraries, and the choice between them isn't trivial.

**rustls** — a pure-Rust TLS implementation. No C dependencies, no OpenSSL, no linking headaches. It only supports TLS 1.2 and 1.3 (deliberately dropping support for older, insecure versions). It's audited, well-maintained, and increasingly the default choice in the Rust ecosystem.

**native-tls** — a wrapper around the platform's TLS library. On Linux that's OpenSSL, on macOS it's Secure Transport, on Windows it's SChannel. It does whatever your OS does, which means it supports whatever your OS supports — including older TLS versions if needed.

My take: use rustls unless you have a specific reason not to. The "specific reasons" are usually: corporate environments that mandate OpenSSL for compliance, or needing to connect to ancient servers that only speak TLS 1.0/1.1.

## TLS Basics with tokio-rustls

```toml
[dependencies]
tokio = { version = "1", features = ["full"] }
tokio-rustls = "0.26"
rustls = "0.23"
rustls-pemfile = "2"
webpki-roots = "0.26"
```

### TLS Client

Connecting to an HTTPS server is the most common use case. You need to configure a `ClientConfig` with root certificates and then wrap your TCP connection in a TLS layer.

```rust
use rustls::ClientConfig;
use std::sync::Arc;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;
use tokio_rustls::TlsConnector;
use rustls::pki_types::ServerName;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Build TLS config with Mozilla's root certificates
    let mut root_store = rustls::RootCertStore::empty();
    root_store.extend(webpki_roots::TLS_SERVER_ROOTS.iter().cloned());

    let config = ClientConfig::builder()
        .with_root_certificates(root_store)
        .with_no_client_auth();

    let connector = TlsConnector::from(Arc::new(config));

    // Connect TCP
    let tcp_stream = TcpStream::connect("www.rust-lang.org:443").await?;

    // Wrap in TLS
    let server_name = ServerName::try_from("www.rust-lang.org")?;
    let mut tls_stream = connector.connect(server_name, tcp_stream).await?;

    // Send an HTTP request over TLS
    let request = "GET / HTTP/1.1\r\nHost: www.rust-lang.org\r\nConnection: close\r\n\r\n";
    tls_stream.write_all(request.as_bytes()).await?;

    // Read the response
    let mut response = Vec::new();
    tls_stream.read_to_end(&mut response).await?;

    let text = String::from_utf8_lossy(&response);
    // Just print the first few lines
    for line in text.lines().take(15) {
        println!("{line}");
    }

    Ok(())
}
```

That `webpki_roots` crate bundles Mozilla's root certificate store, which is the same set of CAs trusted by Firefox. This means your Rust application doesn't depend on the system's certificate store — it's self-contained and predictable across platforms.

### TLS Server

Running a TLS server requires a certificate and private key. For development, you can generate self-signed certs. For production, you'd use Let's Encrypt or your organization's CA.

```bash
# Generate a self-signed cert for testing
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem \
  -days 365 -nodes -subj "/CN=localhost"
```

```rust
use rustls::ServerConfig;
use rustls_pemfile::{certs, private_key};
use std::fs::File;
use std::io::BufReader;
use std::sync::Arc;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio_rustls::TlsAcceptor;

fn load_tls_config(
    cert_path: &str,
    key_path: &str,
) -> Result<ServerConfig, Box<dyn std::error::Error>> {
    let cert_file = &mut BufReader::new(File::open(cert_path)?);
    let key_file = &mut BufReader::new(File::open(key_path)?);

    let cert_chain: Vec<_> = certs(cert_file).filter_map(|r| r.ok()).collect();
    let key = private_key(key_file)?
        .ok_or("no private key found")?;

    let config = ServerConfig::builder()
        .with_no_client_auth()
        .with_single_cert(cert_chain, key)?;

    Ok(config)
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let config = load_tls_config("cert.pem", "key.pem")?;
    let acceptor = TlsAcceptor::from(Arc::new(config));

    let listener = TcpListener::bind("127.0.0.1:8443").await?;
    println!("TLS server on https://127.0.0.1:8443");

    loop {
        let (tcp_stream, addr) = listener.accept().await?;
        let acceptor = acceptor.clone();

        tokio::spawn(async move {
            match acceptor.accept(tcp_stream).await {
                Ok(mut tls_stream) => {
                    println!("TLS connection from {addr}");

                    let mut buf = vec![0u8; 4096];
                    match tls_stream.read(&mut buf).await {
                        Ok(n) => {
                            let request = String::from_utf8_lossy(&buf[..n]);
                            println!("Request from {addr}:\n{request}");

                            let response = "HTTP/1.1 200 OK\r\n\
                                Content-Type: text/plain\r\n\
                                Content-Length: 13\r\n\
                                Connection: close\r\n\r\n\
                                Hello, TLS!\r\n";

                            let _ = tls_stream.write_all(response.as_bytes()).await;
                        }
                        Err(e) => eprintln!("Read error from {addr}: {e}"),
                    }
                }
                Err(e) => {
                    eprintln!("TLS handshake failed from {addr}: {e}");
                }
            }
        });
    }
}
```

The TLS handshake can fail for many reasons — expired cert, wrong hostname, unsupported cipher suite, client rejecting your cert. Always handle the `accept` error gracefully. I've seen servers panic on handshake failures and take down the entire process.

## Mutual TLS (mTLS)

In service-to-service communication, you often want both sides to present certificates. This is mutual TLS — the server authenticates the client, not just the other way around. It's the gold standard for zero-trust networking.

```rust
use rustls::{ClientConfig, RootCertStore, ServerConfig};
use rustls_pemfile::{certs, private_key};
use std::fs::File;
use std::io::BufReader;
use std::sync::Arc;

fn load_certs(path: &str) -> Vec<rustls::pki_types::CertificateDer<'static>> {
    let file = &mut BufReader::new(File::open(path).unwrap());
    certs(file).filter_map(|r| r.ok()).collect()
}

fn load_key(path: &str) -> rustls::pki_types::PrivateKeyDer<'static> {
    let file = &mut BufReader::new(File::open(path).unwrap());
    private_key(file).unwrap().unwrap()
}

fn mtls_server_config(
    cert_path: &str,
    key_path: &str,
    ca_cert_path: &str,
) -> ServerConfig {
    let server_certs = load_certs(cert_path);
    let server_key = load_key(key_path);

    // Load the CA cert that signed client certificates
    let mut client_root_store = RootCertStore::empty();
    let ca_certs = load_certs(ca_cert_path);
    for cert in ca_certs {
        client_root_store.add(cert).unwrap();
    }

    let client_verifier = rustls::server::WebPkiClientVerifier::builder(
        Arc::new(client_root_store),
    )
    .build()
    .unwrap();

    ServerConfig::builder()
        .with_client_cert_verifier(client_verifier)
        .with_single_cert(server_certs, server_key)
        .unwrap()
}

fn mtls_client_config(
    cert_path: &str,
    key_path: &str,
    ca_cert_path: &str,
) -> ClientConfig {
    let client_certs = load_certs(cert_path);
    let client_key = load_key(key_path);

    // Load the CA cert that signed the server certificate
    let mut root_store = RootCertStore::empty();
    let ca_certs = load_certs(ca_cert_path);
    for cert in ca_certs {
        root_store.add(cert).unwrap();
    }

    ClientConfig::builder()
        .with_root_certificates(root_store)
        .with_client_auth_cert(client_certs, client_key)
        .unwrap()
}
```

The setup is symmetrical — both sides have a certificate and key, and both sides know the CA that signed the other's certificate. In Kubernetes environments, tools like cert-manager handle all the certificate generation and rotation for you. But understanding what's happening underneath helps enormously when debugging "mTLS isn't working" issues.

## Certificate Inspection

Sometimes you need to programmatically inspect a server's certificate — check the expiry date, verify the Subject Alternative Names, or extract the issuer.

```rust
use rustls::ClientConfig;
use std::sync::Arc;
use tokio::net::TcpStream;
use tokio_rustls::TlsConnector;
use rustls::pki_types::ServerName;

async fn inspect_certificate(
    domain: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    let mut root_store = rustls::RootCertStore::empty();
    root_store.extend(webpki_roots::TLS_SERVER_ROOTS.iter().cloned());

    let config = ClientConfig::builder()
        .with_root_certificates(root_store)
        .with_no_client_auth();

    let connector = TlsConnector::from(Arc::new(config));

    let tcp_stream = TcpStream::connect(format!("{domain}:443")).await?;
    let server_name = ServerName::try_from(domain.to_string())?;
    let tls_stream = connector.connect(server_name, tcp_stream).await?;

    // Get the peer certificate chain
    let (_, conn) = tls_stream.get_ref();

    if let Some(certs) = conn.peer_certificates() {
        println!("Certificate chain for {domain}:");
        for (i, cert) in certs.iter().enumerate() {
            println!("  Certificate {i}: {} bytes", cert.len());

            // Parse with x509-parser for detailed info
            // (add x509-parser = "0.16" to dependencies)
            //
            // use x509_parser::prelude::*;
            // let (_, parsed) = X509Certificate::from_der(cert).unwrap();
            // println!("    Subject: {}", parsed.subject());
            // println!("    Issuer: {}", parsed.issuer());
            // println!("    Not Before: {}", parsed.validity().not_before);
            // println!("    Not After: {}", parsed.validity().not_after);
        }
    }

    let protocol = conn.protocol_version();
    let cipher = conn.negotiated_cipher_suite();
    println!("\n  Protocol: {protocol:?}");
    println!("  Cipher: {cipher:?}");

    Ok(())
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    inspect_certificate("github.com").await?;
    println!();
    inspect_certificate("google.com").await?;
    Ok(())
}
```

Build a monitoring tool around this and you'll never get woken up by an expired cert again. Check expiry dates daily, alert when a cert is within 14 days of expiring. Problem solved.

## The native-tls Alternative

If you need native-tls (for OpenSSL compatibility or platform integration), the API is similar but slightly different:

```toml
[dependencies]
tokio = { version = "1", features = ["full"] }
tokio-native-tls = "0.3"
native-tls = "0.2"
```

```rust
use native_tls::TlsConnector as NativeTlsConnector;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;
use tokio_native_tls::TlsConnector;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let native_connector = NativeTlsConnector::new()?;
    let connector = TlsConnector::from(native_connector);

    let tcp_stream = TcpStream::connect("www.rust-lang.org:443").await?;
    let mut tls_stream = connector.connect("www.rust-lang.org", tcp_stream).await?;

    let request = "GET / HTTP/1.1\r\nHost: www.rust-lang.org\r\nConnection: close\r\n\r\n";
    tls_stream.write_all(request.as_bytes()).await?;

    let mut response = String::new();
    tls_stream.read_to_string(&mut response).await?;

    println!("Response length: {} bytes", response.len());
    Ok(())
}
```

Simpler API, but less control. You can't easily inspect cipher suites, protocol versions, or the full certificate chain. And you inherit whatever security properties (or lack thereof) your platform's TLS library has.

## TLS Configuration Best Practices

After running TLS in production across dozens of services, here are the things that actually matter:

**Always set ALPN protocols.** If you're doing HTTP/2 or gRPC, you need to negotiate the application protocol during the TLS handshake:

```rust
let mut config = ClientConfig::builder()
    .with_root_certificates(root_store)
    .with_no_client_auth();

config.alpn_protocols = vec![b"h2".to_vec(), b"http/1.1".to_vec()];
```

**Rotate certificates before they expire.** Build monitoring. I said this already but I'll say it again because it's that important.

**Use strong cipher suites.** rustls does this by default — it only supports modern, safe ciphersuites. If you're using native-tls/OpenSSL, you need to explicitly configure this.

**Pin certificates in high-security environments.** Certificate pinning means your client only trusts a specific certificate or CA, not the entire system trust store. This protects against compromised CAs.

```rust
use rustls::{RootCertStore, ClientConfig};

fn pinned_config(pinned_cert_path: &str) -> ClientConfig {
    let mut root_store = RootCertStore::empty();

    // Only trust this specific CA — not the system store
    let pinned_certs = load_certs(pinned_cert_path);
    for cert in pinned_certs {
        root_store.add(cert).unwrap();
    }

    ClientConfig::builder()
        .with_root_certificates(root_store)
        .with_no_client_auth()
}
```

## What's Next

TLS ensures nobody can read or tamper with your data in transit. But the network is still unreliable — connections drop, servers restart, cloud providers have blips. Next, we'll build resilient HTTP clients with retry strategies and exponential backoff.
