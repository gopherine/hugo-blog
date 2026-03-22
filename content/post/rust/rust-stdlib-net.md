---
title: "Lesson 6: std::net — TCP, UDP, sockets"
author: Atharva Pandey
series: "Rust Standard Library Mastery"
lesson: 6
keywords: [Rust, rust, standard library, net, TCP, UDP, sockets, networking, TcpListener, TcpStream]
tags: [Rust tutorial, rust, stdlib]
date: '2024-09-27T09:40:00.000Z'
---

The first network program I ever wrote — a chat server in college — had a bug where it would block forever waiting for one client's message while all the other clients hung. Classic single-threaded socket mistake. Rust's `std::net` module gives you the same low-level socket primitives, but the ownership system actually helps you avoid some of those pitfalls.

## TCP — The Reliable One

TCP gives you ordered, reliable byte streams. You connect, you send bytes, they arrive in order (or the connection dies trying). That's the deal.

### TCP Server

```rust
use std::io::{self, BufRead, BufReader, Write};
use std::net::{TcpListener, TcpStream};
use std::thread;

fn handle_client(mut stream: TcpStream) -> io::Result<()> {
    let peer = stream.peer_addr()?;
    println!("New connection from {peer}");

    let reader = BufReader::new(stream.try_clone()?);

    for line in reader.lines() {
        let line = line?;
        println!("[{peer}] {line}");

        // Echo it back with a prefix
        let response = format!("echo: {line}\n");
        stream.write_all(response.as_bytes())?;
        stream.flush()?;

        if line.trim() == "quit" {
            break;
        }
    }

    println!("Connection closed: {peer}");
    Ok(())
}

fn main() -> io::Result<()> {
    let listener = TcpListener::bind("127.0.0.1:7878")?;
    println!("Listening on {}", listener.local_addr()?);

    // Accept connections and spawn a thread for each
    for stream in listener.incoming() {
        match stream {
            Ok(stream) => {
                thread::spawn(move || {
                    if let Err(e) = handle_client(stream) {
                        eprintln!("Client error: {e}");
                    }
                });
            }
            Err(e) => eprintln!("Accept error: {e}"),
        }
    }

    Ok(())
}
```

A few things to note here:

- `TcpListener::bind()` creates a socket and starts listening. It's not connected to anything yet — it's waiting for connections.
- `listener.incoming()` returns an infinite iterator of incoming connections. Each call blocks until a new client connects.
- `stream.try_clone()` creates a duplicate handle to the same underlying socket. This lets you have one thread reading and another writing — or in this case, lets the `BufReader` own one handle while we write to the other.

### TCP Client

```rust
use std::io::{self, BufRead, BufReader, Write};
use std::net::TcpStream;

fn main() -> io::Result<()> {
    let mut stream = TcpStream::connect("127.0.0.1:7878")?;
    println!("Connected to {}", stream.peer_addr()?);

    // Send a message
    stream.write_all(b"hello from rust\n")?;
    stream.flush()?;

    // Read the response
    let mut reader = BufReader::new(stream.try_clone()?);
    let mut response = String::new();
    reader.read_line(&mut response)?;
    println!("Server said: {response}");

    // Send quit
    stream.write_all(b"quit\n")?;
    stream.flush()?;

    Ok(())
}
```

### Connection Timeouts

Default TCP connect can hang for a long time if the remote host isn't responding. Use `connect_timeout`:

```rust
use std::net::{TcpStream, SocketAddr};
use std::time::Duration;
use std::io;

fn connect_with_retry(addr: &str, retries: u32, timeout: Duration) -> io::Result<TcpStream> {
    let addr: SocketAddr = addr.parse()
        .map_err(|e| io::Error::new(io::ErrorKind::InvalidInput, e))?;

    for attempt in 1..=retries {
        match TcpStream::connect_timeout(&addr, timeout) {
            Ok(stream) => {
                println!("Connected on attempt {attempt}");
                return Ok(stream);
            }
            Err(e) if attempt < retries => {
                eprintln!("Attempt {attempt} failed: {e}. Retrying...");
                std::thread::sleep(Duration::from_millis(500 * attempt as u64));
            }
            Err(e) => return Err(e),
        }
    }

    unreachable!()
}

fn main() -> io::Result<()> {
    // This will fail since nothing is listening — but demonstrates the pattern
    match connect_with_retry("127.0.0.1:9999", 3, Duration::from_secs(2)) {
        Ok(_stream) => println!("Connected!"),
        Err(e) => println!("All attempts failed: {e}"),
    }

    Ok(())
}
```

### Read/Write Timeouts

Once connected, you should also set timeouts on reads and writes to avoid hanging forever:

```rust
use std::net::TcpStream;
use std::time::Duration;
use std::io::{self, Read};

fn main() -> io::Result<()> {
    // Assuming a server is running on 7878
    if let Ok(mut stream) = TcpStream::connect("127.0.0.1:7878") {
        // Set timeouts — crucial for production code
        stream.set_read_timeout(Some(Duration::from_secs(5)))?;
        stream.set_write_timeout(Some(Duration::from_secs(5)))?;

        // Disable Nagle's algorithm for low-latency
        stream.set_nodelay(true)?;

        let mut buf = [0u8; 1024];
        match stream.read(&mut buf) {
            Ok(n) => println!("Read {n} bytes"),
            Err(ref e) if e.kind() == io::ErrorKind::WouldBlock => {
                println!("Read timed out");
            }
            Err(e) => return Err(e),
        }
    }

    Ok(())
}
```

`set_nodelay(true)` disables Nagle's algorithm, which batches small writes together for efficiency. Turn it off when you need low latency (interactive protocols, games). Leave it on when you're sending bulk data.

## UDP — The Fast One

UDP is connectionless. You send datagrams — discrete messages — and they might arrive out of order, might arrive duplicated, or might not arrive at all. But it's fast and simple, which makes it perfect for real-time data where occasional loss is acceptable.

```rust
use std::net::UdpSocket;
use std::io;

fn main() -> io::Result<()> {
    // UDP "server"
    let socket = UdpSocket::bind("127.0.0.1:8888")?;
    println!("Listening on {}", socket.local_addr()?);

    // Set a timeout so we don't block forever in this example
    socket.set_read_timeout(Some(std::time::Duration::from_secs(1)))?;

    // Receive datagrams
    let mut buf = [0u8; 1024];
    match socket.recv_from(&mut buf) {
        Ok((bytes_read, src_addr)) => {
            let msg = std::str::from_utf8(&buf[..bytes_read]).unwrap_or("<invalid utf8>");
            println!("Got {bytes_read} bytes from {src_addr}: {msg}");

            // Send response back
            socket.send_to(b"ACK", src_addr)?;
        }
        Err(ref e) if e.kind() == io::ErrorKind::WouldBlock => {
            println!("No data received (timeout)");
        }
        Err(e) => return Err(e),
    }

    Ok(())
}
```

### UDP Client

```rust
use std::net::UdpSocket;
use std::io;

fn main() -> io::Result<()> {
    // Bind to any available port
    let socket = UdpSocket::bind("127.0.0.1:0")?;
    println!("Client bound to {}", socket.local_addr()?);

    // Send to the server
    socket.send_to(b"Hello UDP!", "127.0.0.1:8888")?;

    // If you're only talking to one destination, "connect" it
    // This doesn't establish a real connection — it just sets the default target
    socket.connect("127.0.0.1:8888")?;
    socket.send(b"Hello again!")?; // No need to specify address

    // Receive
    let mut buf = [0u8; 1024];
    socket.set_read_timeout(Some(std::time::Duration::from_secs(2)))?;
    match socket.recv(&mut buf) {
        Ok(n) => {
            let response = std::str::from_utf8(&buf[..n]).unwrap_or("<invalid>");
            println!("Response: {response}");
        }
        Err(e) => println!("No response: {e}"),
    }

    Ok(())
}
```

The `connect()` on a UDP socket is a bit of a misnomer — there's no handshake. It just sets a default destination so you can use `send()` instead of `send_to()`. The kernel also filters incoming packets to only deliver ones from that address.

## Address Resolution

`std::net` provides types for working with IP addresses and socket addresses:

```rust
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr, ToSocketAddrs};

fn main() {
    // IP addresses
    let v4 = Ipv4Addr::new(127, 0, 0, 1);
    let v6 = Ipv6Addr::new(0, 0, 0, 0, 0, 0, 0, 1);
    let v4_generic: IpAddr = IpAddr::V4(v4);
    let v6_generic: IpAddr = IpAddr::V6(v6);

    println!("IPv4: {v4}");
    println!("IPv6: {v6}");
    println!("Loopback? {}", v4.is_loopback());
    println!("Loopback? {}", v6.is_loopback());

    // Socket addresses (IP + port)
    let addr = SocketAddr::new(v4_generic, 8080);
    println!("Socket addr: {addr}");

    // Parse from string
    let parsed: SocketAddr = "192.168.1.1:3000".parse().unwrap();
    println!("Parsed: {parsed}");

    // DNS resolution with ToSocketAddrs
    // This does actual DNS lookup — blocks until resolved
    if let Ok(addrs) = "example.com:80".to_socket_addrs() {
        for addr in addrs {
            println!("example.com resolved to: {addr}");
        }
    }
}
```

`ToSocketAddrs` is the trait that does DNS resolution. When you pass `"hostname:port"` to `TcpStream::connect()`, it calls `to_socket_addrs()` internally. This is a blocking operation — another reason you might want an async runtime for network-heavy code.

## A Multi-Threaded TCP Server

Here's a more realistic example — a simple key-value store over TCP:

```rust
use std::collections::HashMap;
use std::io::{self, BufRead, BufReader, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::{Arc, Mutex};
use std::thread;

type Store = Arc<Mutex<HashMap<String, String>>>;

fn handle_client(mut stream: TcpStream, store: Store) -> io::Result<()> {
    let peer = stream.peer_addr()?;
    let reader = BufReader::new(stream.try_clone()?);

    stream.write_all(b"OK connected. Commands: GET <key>, SET <key> <value>, QUIT\n")?;

    for line in reader.lines() {
        let line = line?;
        let parts: Vec<&str> = line.trim().splitn(3, ' ').collect();

        let response = match parts.as_slice() {
            ["GET", key] => {
                let store = store.lock().unwrap();
                match store.get(*key) {
                    Some(val) => format!("VALUE {val}\n"),
                    None => "NOT_FOUND\n".to_string(),
                }
            }
            ["SET", key, value] => {
                let mut store = store.lock().unwrap();
                store.insert(key.to_string(), value.to_string());
                "OK\n".to_string()
            }
            ["QUIT"] => {
                stream.write_all(b"BYE\n")?;
                break;
            }
            _ => "ERROR unknown command\n".to_string(),
        };

        stream.write_all(response.as_bytes())?;
        stream.flush()?;
    }

    println!("{peer} disconnected");
    Ok(())
}

fn main() -> io::Result<()> {
    let store: Store = Arc::new(Mutex::new(HashMap::new()));
    let listener = TcpListener::bind("127.0.0.1:6379")?;
    println!("KV store listening on {}", listener.local_addr()?);

    for stream in listener.incoming() {
        let stream = stream?;
        let store = Arc::clone(&store);

        thread::spawn(move || {
            if let Err(e) = handle_client(stream, store) {
                eprintln!("Client error: {e}");
            }
        });
    }

    Ok(())
}
```

You can test this with `telnet 127.0.0.1 6379` or `nc`. Type `SET mykey myvalue`, then `GET mykey`.

The `Arc<Mutex<HashMap>>` pattern is the simplest way to share mutable state across threads. For a real KV store you'd want something more sophisticated (sharded locks, lock-free structures), but for understanding `std::net`, this is the right level of complexity.

## Non-Blocking I/O

By default, all `std::net` operations block. You can set sockets to non-blocking mode:

```rust
use std::net::TcpListener;
use std::io;

fn main() -> io::Result<()> {
    let listener = TcpListener::bind("127.0.0.1:0")?;
    listener.set_nonblocking(true)?;

    println!("Non-blocking listener on {}", listener.local_addr()?);

    // This returns immediately instead of blocking
    match listener.accept() {
        Ok((stream, addr)) => println!("Got connection from {addr}"),
        Err(ref e) if e.kind() == io::ErrorKind::WouldBlock => {
            println!("No connection ready (WouldBlock)");
        }
        Err(e) => return Err(e),
    }

    Ok(())
}
```

Non-blocking I/O with `std::net` is low-level and painful. You'd have to poll sockets in a loop, manage ready states yourself. This is exactly what async runtimes like `tokio` abstract over — they build on non-blocking sockets plus OS-level event notification (epoll, kqueue). For anything beyond toy examples, use an async runtime for networking. But understanding that it's all built on these primitives is valuable context.

## Shutdown and Half-Close

TCP connections can be shut down in one direction while remaining open in the other:

```rust
use std::net::{TcpStream, Shutdown};
use std::io::{self, Write};

fn main() -> io::Result<()> {
    if let Ok(mut stream) = TcpStream::connect("127.0.0.1:7878") {
        // Send all data
        stream.write_all(b"all the data\n")?;
        stream.flush()?;

        // Signal we're done writing — remote will see EOF on read
        stream.shutdown(Shutdown::Write)?;

        // Can still read from the stream
        // stream.read(...)

        // Shutdown both directions
        // stream.shutdown(Shutdown::Both)?;
    }

    Ok(())
}
```

This is how HTTP/1.0 worked — the client sends a request, shuts down the write side, and the server knows the request is complete when it sees EOF.

## When to Use std::net vs. Async

Here's my rule of thumb: if you're handling fewer than a few hundred concurrent connections and each connection is short-lived, `std::net` with threads is perfectly fine. Thread-per-connection is simple, debuggable, and performs well for moderate concurrency.

Once you need thousands of concurrent connections, or you're building a proxy/gateway where connections are long-lived, switch to async (tokio, async-std). The overhead of one OS thread per connection becomes untenable at that scale.

But always start with `std::net`. It's simpler to reason about, simpler to debug, and async code has its own category of footguns. Graduate to async when the problem demands it, not before.
