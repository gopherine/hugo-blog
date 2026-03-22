---
title: "Lesson 7: Implementing Network Protocols — TCP from scratch"
author: Atharva Pandey
series: "Rust for Systems Programming"
lesson: 7
keywords: [Rust, rust, systems programming, TCP, networking, protocol, packet, raw sockets]
tags: [Rust tutorial, rust, systems, low-level]
date: '2025-07-24T10:31:48.000Z'
---

I thought I understood TCP until I tried to implement it. Turns out, "client connects to server, data flows" is about 5% of the story. The other 95% is state machines, retransmission timers, congestion windows, and edge cases that would make your head spin.

But here's the good news: implementing even a simplified TCP teaches you more about networking than any textbook. And Rust's type system is actually perfect for modeling protocol state machines — states become types, invalid transitions become compile errors.

## The Network Stack Architecture

```
┌─────────────────────────────────┐
│  Application (HTTP, DNS, etc.)  │
├─────────────────────────────────┤
│  Transport (TCP, UDP)           │  ← We're building this
├─────────────────────────────────┤
│  Network (IP)                   │  ← And this
├─────────────────────────────────┤
│  Link (Ethernet, WiFi)          │
├─────────────────────────────────┤
│  Physical                       │
└─────────────────────────────────┘
```

We'll use `smoltcp` concepts but build core pieces from scratch to understand what's happening.

## Packet Parsing — The Foundation

Every network protocol starts with parsing headers from byte buffers. Let's build that:

```rust
use std::io;

/// An IPv4 header (simplified — no options)
#[repr(C, packed)]
#[derive(Clone, Copy, Debug)]
pub struct Ipv4Header {
    pub version_ihl: u8,      // Version (4 bits) + IHL (4 bits)
    pub dscp_ecn: u8,         // DSCP (6 bits) + ECN (2 bits)
    pub total_length: [u8; 2], // Big-endian
    pub identification: [u8; 2],
    pub flags_fragment: [u8; 2],
    pub ttl: u8,
    pub protocol: u8,         // 6 = TCP, 17 = UDP
    pub checksum: [u8; 2],
    pub src_addr: [u8; 4],
    pub dst_addr: [u8; 4],
}

impl Ipv4Header {
    pub fn parse(data: &[u8]) -> io::Result<(&Ipv4Header, &[u8])> {
        if data.len() < 20 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "packet too short for IPv4 header",
            ));
        }

        let header = unsafe { &*(data.as_ptr() as *const Ipv4Header) };

        let version = header.version_ihl >> 4;
        if version != 4 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!("expected IPv4, got version {}", version),
            ));
        }

        let ihl = (header.version_ihl & 0x0F) as usize * 4;
        if ihl < 20 || ihl > data.len() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "invalid IHL",
            ));
        }

        Ok((header, &data[ihl..]))
    }

    pub fn total_length(&self) -> u16 {
        u16::from_be_bytes(self.total_length)
    }

    pub fn src(&self) -> [u8; 4] {
        self.src_addr
    }

    pub fn dst(&self) -> [u8; 4] {
        self.dst_addr
    }
}

/// A TCP header
#[repr(C, packed)]
#[derive(Clone, Copy, Debug)]
pub struct TcpHeader {
    pub src_port: [u8; 2],
    pub dst_port: [u8; 2],
    pub seq_num: [u8; 4],
    pub ack_num: [u8; 4],
    pub data_offset_flags: [u8; 2], // Data offset (4 bits) + reserved + flags
    pub window: [u8; 2],
    pub checksum: [u8; 2],
    pub urgent_ptr: [u8; 2],
}

// TCP flags
const FIN: u8 = 0x01;
const SYN: u8 = 0x02;
const RST: u8 = 0x04;
const PSH: u8 = 0x08;
const ACK: u8 = 0x10;

impl TcpHeader {
    pub fn parse(data: &[u8]) -> io::Result<(&TcpHeader, &[u8])> {
        if data.len() < 20 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "packet too short for TCP header",
            ));
        }

        let header = unsafe { &*(data.as_ptr() as *const TcpHeader) };
        let data_offset = ((header.data_offset_flags[0] >> 4) as usize) * 4;

        if data_offset < 20 || data_offset > data.len() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "invalid TCP data offset",
            ));
        }

        Ok((header, &data[data_offset..]))
    }

    pub fn src_port(&self) -> u16 {
        u16::from_be_bytes(self.src_port)
    }

    pub fn dst_port(&self) -> u16 {
        u16::from_be_bytes(self.dst_port)
    }

    pub fn seq(&self) -> u32 {
        u32::from_be_bytes(self.seq_num)
    }

    pub fn ack(&self) -> u32 {
        u32::from_be_bytes(self.ack_num)
    }

    pub fn flags(&self) -> u8 {
        self.data_offset_flags[1] & 0x3F
    }

    pub fn is_syn(&self) -> bool { self.flags() & SYN != 0 }
    pub fn is_ack(&self) -> bool { self.flags() & ACK != 0 }
    pub fn is_fin(&self) -> bool { self.flags() & FIN != 0 }
    pub fn is_rst(&self) -> bool { self.flags() & RST != 0 }

    pub fn window_size(&self) -> u16 {
        u16::from_be_bytes(self.window)
    }
}
```

Notice the `#[repr(C, packed)]` — `packed` prevents any padding between fields, so the struct layout exactly matches the wire format. This lets us cast directly from a byte slice to a header struct.

## The TCP State Machine

TCP's state machine is well-defined (RFC 793). Let's model it with Rust's type system:

```rust
use std::collections::VecDeque;
use std::time::{Duration, Instant};

/// TCP connection state
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum TcpState {
    Closed,
    Listen,
    SynSent,
    SynReceived,
    Established,
    FinWait1,
    FinWait2,
    CloseWait,
    Closing,
    LastAck,
    TimeWait,
}

/// Identifies a TCP connection
#[derive(Debug, Clone, Copy, Hash, PartialEq, Eq)]
pub struct ConnectionId {
    pub local_addr: [u8; 4],
    pub local_port: u16,
    pub remote_addr: [u8; 4],
    pub remote_port: u16,
}

/// A TCP connection (Transmission Control Block)
pub struct TcpConnection {
    pub id: ConnectionId,
    pub state: TcpState,

    // Send sequence space
    pub snd_una: u32,  // Oldest unacknowledged sequence number
    pub snd_nxt: u32,  // Next sequence number to send
    pub snd_wnd: u16,  // Send window (from receiver)

    // Receive sequence space
    pub rcv_nxt: u32,  // Next expected sequence number
    pub rcv_wnd: u16,  // Receive window (our buffer space)

    // Buffers
    pub send_buffer: VecDeque<u8>,
    pub recv_buffer: VecDeque<u8>,

    // Retransmission
    pub unacked_segments: Vec<UnackedSegment>,
    pub rto: Duration,           // Retransmission timeout
    pub srtt: Option<Duration>,  // Smoothed round-trip time
}

pub struct UnackedSegment {
    pub seq: u32,
    pub data: Vec<u8>,
    pub sent_at: Instant,
    pub retransmit_count: u32,
}

impl TcpConnection {
    pub fn new_client(id: ConnectionId) -> Self {
        let isn = generate_isn(); // Initial sequence number

        TcpConnection {
            id,
            state: TcpState::Closed,
            snd_una: isn,
            snd_nxt: isn,
            snd_wnd: 0,
            rcv_nxt: 0,
            rcv_wnd: 65535,
            send_buffer: VecDeque::new(),
            recv_buffer: VecDeque::new(),
            unacked_segments: Vec::new(),
            rto: Duration::from_secs(1),
            srtt: None,
        }
    }

    /// Process an incoming TCP segment
    pub fn on_segment(
        &mut self,
        header: &TcpHeader,
        payload: &[u8],
    ) -> Vec<TcpAction> {
        let mut actions = Vec::new();

        match self.state {
            TcpState::Listen => {
                if header.is_syn() {
                    // Received SYN — send SYN+ACK
                    self.rcv_nxt = header.seq().wrapping_add(1);
                    self.snd_wnd = header.window_size();

                    actions.push(TcpAction::SendSegment {
                        seq: self.snd_nxt,
                        ack: self.rcv_nxt,
                        flags: SYN | ACK,
                        data: Vec::new(),
                    });

                    self.snd_nxt = self.snd_nxt.wrapping_add(1);
                    self.state = TcpState::SynReceived;
                }
            }

            TcpState::SynSent => {
                if header.is_syn() && header.is_ack() {
                    // Received SYN+ACK — send ACK, connection established
                    self.rcv_nxt = header.seq().wrapping_add(1);
                    self.snd_una = header.ack();
                    self.snd_wnd = header.window_size();

                    actions.push(TcpAction::SendSegment {
                        seq: self.snd_nxt,
                        ack: self.rcv_nxt,
                        flags: ACK,
                        data: Vec::new(),
                    });

                    self.state = TcpState::Established;
                    actions.push(TcpAction::ConnectionEstablished);
                }
            }

            TcpState::SynReceived => {
                if header.is_ack() {
                    self.snd_una = header.ack();
                    self.state = TcpState::Established;
                    actions.push(TcpAction::ConnectionEstablished);
                }
            }

            TcpState::Established => {
                // Process ACK
                if header.is_ack() {
                    let ack = header.ack();
                    if wrapping_gt(ack, self.snd_una) {
                        // New data acknowledged
                        self.snd_una = ack;
                        self.unacked_segments.retain(|seg| {
                            wrapping_gt(seg.seq.wrapping_add(seg.data.len() as u32), ack)
                        });
                    }
                }

                // Process incoming data
                if !payload.is_empty() {
                    if header.seq() == self.rcv_nxt {
                        // In-order data — deliver to application
                        self.recv_buffer.extend(payload);
                        self.rcv_nxt = self.rcv_nxt.wrapping_add(payload.len() as u32);
                        actions.push(TcpAction::DataReceived);

                        // Send ACK
                        actions.push(TcpAction::SendSegment {
                            seq: self.snd_nxt,
                            ack: self.rcv_nxt,
                            flags: ACK,
                            data: Vec::new(),
                        });
                    }
                    // Out-of-order data handling would go here
                }

                // Process FIN
                if header.is_fin() {
                    self.rcv_nxt = self.rcv_nxt.wrapping_add(1);
                    actions.push(TcpAction::SendSegment {
                        seq: self.snd_nxt,
                        ack: self.rcv_nxt,
                        flags: ACK,
                        data: Vec::new(),
                    });
                    self.state = TcpState::CloseWait;
                    actions.push(TcpAction::ConnectionClosing);
                }
            }

            TcpState::FinWait1 => {
                if header.is_ack() && header.is_fin() {
                    // Simultaneous close
                    self.rcv_nxt = self.rcv_nxt.wrapping_add(1);
                    actions.push(TcpAction::SendSegment {
                        seq: self.snd_nxt,
                        ack: self.rcv_nxt,
                        flags: ACK,
                        data: Vec::new(),
                    });
                    self.state = TcpState::TimeWait;
                } else if header.is_ack() {
                    self.snd_una = header.ack();
                    self.state = TcpState::FinWait2;
                } else if header.is_fin() {
                    self.rcv_nxt = self.rcv_nxt.wrapping_add(1);
                    actions.push(TcpAction::SendSegment {
                        seq: self.snd_nxt,
                        ack: self.rcv_nxt,
                        flags: ACK,
                        data: Vec::new(),
                    });
                    self.state = TcpState::Closing;
                }
            }

            TcpState::FinWait2 => {
                if header.is_fin() {
                    self.rcv_nxt = self.rcv_nxt.wrapping_add(1);
                    actions.push(TcpAction::SendSegment {
                        seq: self.snd_nxt,
                        ack: self.rcv_nxt,
                        flags: ACK,
                        data: Vec::new(),
                    });
                    self.state = TcpState::TimeWait;
                }
            }

            _ => {}
        }

        actions
    }

    /// Send data from the application
    pub fn send(&mut self, data: &[u8]) -> Vec<TcpAction> {
        if self.state != TcpState::Established {
            return Vec::new();
        }

        self.send_buffer.extend(data);
        self.flush_send_buffer()
    }

    fn flush_send_buffer(&mut self) -> Vec<TcpAction> {
        let mut actions = Vec::new();
        let mss = 1460; // Maximum segment size (typical for Ethernet)

        while !self.send_buffer.is_empty() {
            let in_flight = self.snd_nxt.wrapping_sub(self.snd_una) as usize;
            let window = self.snd_wnd as usize;

            if in_flight >= window {
                break; // Window full
            }

            let can_send = core::cmp::min(window - in_flight, mss);
            let to_send = core::cmp::min(can_send, self.send_buffer.len());

            let segment_data: Vec<u8> = self.send_buffer.drain(..to_send).collect();

            actions.push(TcpAction::SendSegment {
                seq: self.snd_nxt,
                ack: self.rcv_nxt,
                flags: ACK | PSH,
                data: segment_data.clone(),
            });

            self.unacked_segments.push(UnackedSegment {
                seq: self.snd_nxt,
                data: segment_data,
                sent_at: Instant::now(),
                retransmit_count: 0,
            });

            self.snd_nxt = self.snd_nxt.wrapping_add(to_send as u32);
        }

        actions
    }

    /// Initiate connection close
    pub fn close(&mut self) -> Vec<TcpAction> {
        match self.state {
            TcpState::Established => {
                let actions = vec![TcpAction::SendSegment {
                    seq: self.snd_nxt,
                    ack: self.rcv_nxt,
                    flags: FIN | ACK,
                    data: Vec::new(),
                }];
                self.snd_nxt = self.snd_nxt.wrapping_add(1);
                self.state = TcpState::FinWait1;
                actions
            }
            TcpState::CloseWait => {
                let actions = vec![TcpAction::SendSegment {
                    seq: self.snd_nxt,
                    ack: self.rcv_nxt,
                    flags: FIN | ACK,
                    data: Vec::new(),
                }];
                self.snd_nxt = self.snd_nxt.wrapping_add(1);
                self.state = TcpState::LastAck;
                actions
            }
            _ => Vec::new(),
        }
    }
}

/// Actions the TCP stack needs the network layer to perform
#[derive(Debug)]
pub enum TcpAction {
    SendSegment {
        seq: u32,
        ack: u32,
        flags: u8,
        data: Vec<u8>,
    },
    ConnectionEstablished,
    DataReceived,
    ConnectionClosing,
}

/// Compare sequence numbers with wrapping
fn wrapping_gt(a: u32, b: u32) -> bool {
    // a > b considering 32-bit wrapping
    let diff = a.wrapping_sub(b);
    diff > 0 && diff < (1 << 31)
}

fn generate_isn() -> u32 {
    // In production, use a hash of (src_ip, src_port, dst_ip, dst_port, secret)
    // to prevent sequence number prediction attacks
    use std::time::SystemTime;
    SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap()
        .as_micros() as u32
}
```

## Checksums — Getting the Math Right

TCP checksums are mandatory. A single bit flip in transit should be caught:

```rust
/// Compute the Internet checksum (RFC 1071)
pub fn internet_checksum(data: &[u8]) -> u16 {
    let mut sum: u32 = 0;
    let mut i = 0;

    // Sum 16-bit words
    while i + 1 < data.len() {
        sum += u16::from_be_bytes([data[i], data[i + 1]]) as u32;
        i += 2;
    }

    // Handle odd byte
    if i < data.len() {
        sum += (data[i] as u32) << 8;
    }

    // Fold 32-bit sum into 16 bits
    while sum >> 16 != 0 {
        sum = (sum & 0xFFFF) + (sum >> 16);
    }

    !sum as u16
}

/// TCP checksum includes a pseudo-header
pub fn tcp_checksum(
    src_addr: [u8; 4],
    dst_addr: [u8; 4],
    tcp_segment: &[u8],
) -> u16 {
    let tcp_len = tcp_segment.len() as u16;

    // Build pseudo-header + TCP segment
    let mut data = Vec::with_capacity(12 + tcp_segment.len());
    data.extend_from_slice(&src_addr);
    data.extend_from_slice(&dst_addr);
    data.push(0); // Reserved
    data.push(6); // Protocol (TCP)
    data.extend_from_slice(&tcp_len.to_be_bytes());
    data.extend_from_slice(tcp_segment);

    internet_checksum(&data)
}
```

## Building and Sending Packets

```rust
/// Build a complete TCP/IP packet
pub fn build_packet(
    src_addr: [u8; 4],
    dst_addr: [u8; 4],
    src_port: u16,
    dst_port: u16,
    seq: u32,
    ack: u32,
    flags: u8,
    window: u16,
    payload: &[u8],
) -> Vec<u8> {
    let tcp_header_len = 20u8;
    let ip_total_len = 20 + tcp_header_len as u16 + payload.len() as u16;

    let mut packet = Vec::with_capacity(ip_total_len as usize);

    // --- IP Header ---
    packet.push(0x45); // Version 4, IHL 5 (20 bytes)
    packet.push(0x00); // DSCP + ECN
    packet.extend_from_slice(&ip_total_len.to_be_bytes());
    packet.extend_from_slice(&[0x00, 0x00]); // Identification
    packet.extend_from_slice(&[0x40, 0x00]); // Don't Fragment
    packet.push(64);   // TTL
    packet.push(6);    // Protocol: TCP
    packet.extend_from_slice(&[0x00, 0x00]); // Checksum (filled later)
    packet.extend_from_slice(&src_addr);
    packet.extend_from_slice(&dst_addr);

    // Calculate IP checksum
    let ip_checksum = internet_checksum(&packet[..20]);
    packet[10] = (ip_checksum >> 8) as u8;
    packet[11] = ip_checksum as u8;

    // --- TCP Header ---
    let tcp_start = packet.len();
    packet.extend_from_slice(&src_port.to_be_bytes());
    packet.extend_from_slice(&dst_port.to_be_bytes());
    packet.extend_from_slice(&seq.to_be_bytes());
    packet.extend_from_slice(&ack.to_be_bytes());
    packet.push((tcp_header_len / 4) << 4); // Data offset
    packet.push(flags);
    packet.extend_from_slice(&window.to_be_bytes());
    packet.extend_from_slice(&[0x00, 0x00]); // Checksum (filled later)
    packet.extend_from_slice(&[0x00, 0x00]); // Urgent pointer

    // Payload
    packet.extend_from_slice(payload);

    // Calculate TCP checksum
    let tcp_checksum = tcp_checksum(
        src_addr,
        dst_addr,
        &packet[tcp_start..],
    );
    packet[tcp_start + 16] = (tcp_checksum >> 8) as u8;
    packet[tcp_start + 17] = tcp_checksum as u8;

    packet
}
```

## Using smoltcp for Real Work

For actual network stack implementations, `smoltcp` is the go-to Rust crate. It's `no_std` compatible and production-quality:

```rust
use smoltcp::iface::{Config, Interface, SocketSet};
use smoltcp::phy::{Device, Medium};
use smoltcp::socket::tcp;
use smoltcp::time::Instant;
use smoltcp::wire::{EthernetAddress, IpAddress, IpCidr, Ipv4Address};

fn smoltcp_example() {
    // Create a network device (TUN/TAP, raw socket, etc.)
    // let device = ...;

    // Configure the network interface
    let config = Config::new(EthernetAddress([0x02, 0x00, 0x00, 0x00, 0x00, 0x01]).into());

    // let mut iface = Interface::new(config, &mut device, Instant::now());
    // iface.update_ip_addrs(|ip_addrs| {
    //     ip_addrs.push(IpCidr::new(IpAddress::v4(10, 0, 0, 1), 24)).unwrap();
    // });

    // Create sockets
    let tcp_rx_buffer = tcp::SocketBuffer::new(vec![0; 65535]);
    let tcp_tx_buffer = tcp::SocketBuffer::new(vec![0; 65535]);
    let tcp_socket = tcp::Socket::new(tcp_rx_buffer, tcp_tx_buffer);

    let mut sockets = SocketSet::new(vec![]);
    let tcp_handle = sockets.add(tcp_socket);

    // Listen on port 80
    let socket = sockets.get_mut::<tcp::Socket>(tcp_handle);
    socket.listen(80).unwrap();

    // Main loop
    // loop {
    //     let timestamp = Instant::now();
    //     iface.poll(timestamp, &mut device, &mut sockets);
    //
    //     let socket = sockets.get_mut::<tcp::Socket>(tcp_handle);
    //     if socket.may_recv() {
    //         let data = socket.recv(|buffer| {
    //             let len = buffer.len();
    //             (len, buffer.to_vec())
    //         }).unwrap();
    //         // Process data...
    //     }
    // }
}
```

`smoltcp` handles all the gnarly details we glossed over — out-of-order reassembly, Nagle's algorithm, slow start, congestion avoidance, silly window syndrome prevention. Our handwritten TCP was educational. For production, use smoltcp.

## Why Build a Network Stack in Rust?

Three real use cases:

1. **Embedded devices** without an OS network stack. Your microcontroller needs to speak TCP over Ethernet — smoltcp handles this.

2. **Kernel-bypass networking** (DPDK-style). You want maximum throughput and skip the kernel entirely. Rust's safety is valuable when you're managing raw packet buffers at millions of packets per second.

3. **Network appliances** — firewalls, load balancers, VPN gateways. Understanding the packet format at the byte level is essential.

Next lesson, we're going to tackle memory allocation itself — building custom allocators that give you control over every byte.
