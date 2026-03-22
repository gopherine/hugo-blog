---
title: "Lesson 20: The Actor Model with Rust — Message-passing architectures"
author: Atharva Pandey
series: "Rust Concurrency Masterclass"
lesson: 20
keywords: [Rust, rust, concurrency, actor model, message passing, actors, Actix, concurrent architecture]
tags: [Rust tutorial, rust, concurrency]
date: '2024-12-13T14:20:00.000Z'
---

The first time I saw Erlang's actor model, I thought it was over-engineered. Every piece of state behind a process, every interaction a message. Then I worked on a system with 40 mutexes, 12 deadlock-prone code paths, and a debugging story that involved printf-ing thread IDs into a file and diffing them. I became an actor model convert that week.

Rust doesn't have a built-in actor system like Erlang or Akka. But the building blocks — channels, threads, ownership transfer — make building one surprisingly natural.

## What Is the Actor Model?

An actor is:

1. A thread (or task) with private state
2. A mailbox (channel) for receiving messages
3. A message handler that processes one message at a time

No shared state. No locks. All communication through messages. Each actor owns its data exclusively — just one mutable reference, just one thread accessing it, just Rust's ownership model taken to its logical conclusion.

## Building a Simple Actor

```rust
use std::sync::mpsc;
use std::thread;

// Messages the actor can receive
enum CounterMsg {
    Increment,
    Decrement,
    GetValue(mpsc::Sender<i64>),
    Shutdown,
}

struct CounterActor {
    value: i64,
    receiver: mpsc::Receiver<CounterMsg>,
}

impl CounterActor {
    fn new(receiver: mpsc::Receiver<CounterMsg>) -> Self {
        CounterActor { value: 0, receiver }
    }

    fn run(mut self) {
        while let Ok(msg) = self.receiver.recv() {
            match msg {
                CounterMsg::Increment => self.value += 1,
                CounterMsg::Decrement => self.value -= 1,
                CounterMsg::GetValue(reply) => {
                    let _ = reply.send(self.value);
                }
                CounterMsg::Shutdown => {
                    println!("Counter shutting down with value: {}", self.value);
                    break;
                }
            }
        }
    }
}

// Handle for sending messages to the actor
#[derive(Clone)]
struct CounterHandle {
    sender: mpsc::Sender<CounterMsg>,
}

impl CounterHandle {
    fn spawn() -> Self {
        let (tx, rx) = mpsc::channel();
        thread::spawn(move || {
            CounterActor::new(rx).run();
        });
        CounterHandle { sender: tx }
    }

    fn increment(&self) {
        self.sender.send(CounterMsg::Increment).unwrap();
    }

    fn decrement(&self) {
        self.sender.send(CounterMsg::Decrement).unwrap();
    }

    fn get_value(&self) -> i64 {
        let (tx, rx) = mpsc::channel();
        self.sender.send(CounterMsg::GetValue(tx)).unwrap();
        rx.recv().unwrap()
    }

    fn shutdown(&self) {
        let _ = self.sender.send(CounterMsg::Shutdown);
    }
}

fn main() {
    let counter = CounterHandle::spawn();

    // Multiple threads can interact with the actor through cloned handles
    let mut handles = vec![];
    for _ in 0..10 {
        let counter = counter.clone();
        handles.push(thread::spawn(move || {
            for _ in 0..1000 {
                counter.increment();
            }
        }));
    }

    for h in handles {
        h.join().unwrap();
    }

    println!("Final value: {}", counter.get_value()); // always 10000
    counter.shutdown();
}
```

The `CounterHandle` is the public API. The `CounterActor` is the private implementation. The handle can be cloned and sent to multiple threads — it just wraps a channel sender. All mutation happens inside the actor's single thread.

## A Generic Actor Framework

Let's make this reusable:

```rust
use std::sync::mpsc;
use std::thread;

trait Actor: Send + 'static {
    type Message: Send + 'static;

    fn handle(&mut self, msg: Self::Message) -> bool; // false = stop
}

struct ActorHandle<M: Send + 'static> {
    sender: mpsc::Sender<M>,
    thread: Option<thread::JoinHandle<()>>,
}

impl<M: Send + 'static> ActorHandle<M> {
    fn spawn<A: Actor<Message = M>>(mut actor: A) -> Self {
        let (tx, rx) = mpsc::channel();
        let thread = thread::spawn(move || {
            while let Ok(msg) = rx.recv() {
                if !actor.handle(msg) {
                    break;
                }
            }
        });
        ActorHandle {
            sender: tx,
            thread: Some(thread),
        }
    }

    fn send(&self, msg: M) -> Result<(), mpsc::SendError<M>> {
        self.sender.send(msg)
    }

    fn stop(mut self) {
        drop(self.sender);
        if let Some(thread) = self.thread.take() {
            thread.join().unwrap();
        }
    }
}

// Example: a key-value store actor
use std::collections::HashMap;

struct KvStore {
    data: HashMap<String, String>,
}

enum KvMsg {
    Set(String, String),
    Get(String, mpsc::Sender<Option<String>>),
    Delete(String),
    Dump(mpsc::Sender<HashMap<String, String>>),
    Stop,
}

impl Actor for KvStore {
    type Message = KvMsg;

    fn handle(&mut self, msg: KvMsg) -> bool {
        match msg {
            KvMsg::Set(key, value) => {
                self.data.insert(key, value);
                true
            }
            KvMsg::Get(key, reply) => {
                let _ = reply.send(self.data.get(&key).cloned());
                true
            }
            KvMsg::Delete(key) => {
                self.data.remove(&key);
                true
            }
            KvMsg::Dump(reply) => {
                let _ = reply.send(self.data.clone());
                true
            }
            KvMsg::Stop => false,
        }
    }
}

fn main() {
    let store = ActorHandle::spawn(KvStore {
        data: HashMap::new(),
    });

    store.send(KvMsg::Set("name".into(), "Atharva".into())).unwrap();
    store.send(KvMsg::Set("language".into(), "Rust".into())).unwrap();

    let (tx, rx) = mpsc::channel();
    store.send(KvMsg::Get("name".into(), tx)).unwrap();
    println!("name = {:?}", rx.recv().unwrap());

    let (tx, rx) = mpsc::channel();
    store.send(KvMsg::Dump(tx)).unwrap();
    println!("All data: {:?}", rx.recv().unwrap());

    store.send(KvMsg::Stop).unwrap();
    store.stop();
}
```

## Actor Communication Patterns

### Request-Reply

The pattern we've been using — include a reply channel in the message:

```rust
enum Request {
    Compute {
        input: Vec<i32>,
        reply: mpsc::Sender<i64>,
    },
}
```

This is synchronous from the caller's perspective (they block on `rx.recv()`). For fire-and-forget, just don't include a reply channel.

### Actor-to-Actor

Actors can send messages to each other:

```rust
use std::sync::mpsc;
use std::thread;

enum LogMsg {
    Info(String),
    Error(String),
    Shutdown,
}

enum WorkerMsg {
    Process(i32),
    Shutdown,
}

fn main() {
    // Logger actor
    let (log_tx, log_rx) = mpsc::channel::<LogMsg>();
    let logger = thread::spawn(move || {
        while let Ok(msg) = log_rx.recv() {
            match msg {
                LogMsg::Info(s) => println!("[INFO] {}", s),
                LogMsg::Error(s) => eprintln!("[ERROR] {}", s),
                LogMsg::Shutdown => break,
            }
        }
    });

    // Worker actors that send to the logger
    let mut workers = vec![];
    for id in 0..4 {
        let log_tx = log_tx.clone();
        let (work_tx, work_rx) = mpsc::channel::<WorkerMsg>();

        workers.push((
            work_tx,
            thread::spawn(move || {
                while let Ok(msg) = work_rx.recv() {
                    match msg {
                        WorkerMsg::Process(n) => {
                            let result = n * n;
                            log_tx
                                .send(LogMsg::Info(format!(
                                    "Worker {} processed {} → {}",
                                    id, n, result
                                )))
                                .unwrap();
                        }
                        WorkerMsg::Shutdown => {
                            log_tx
                                .send(LogMsg::Info(format!("Worker {} shutting down", id)))
                                .unwrap();
                            break;
                        }
                    }
                }
            }),
        ));
    }

    // Distribute work
    for i in 0..20 {
        let worker_idx = i % workers.len();
        workers[worker_idx]
            .0
            .send(WorkerMsg::Process(i as i32))
            .unwrap();
    }

    // Shutdown workers
    for (tx, _) in &workers {
        tx.send(WorkerMsg::Shutdown).unwrap();
    }
    for (_, handle) in workers {
        handle.join().unwrap();
    }

    // Shutdown logger
    log_tx.send(LogMsg::Shutdown).unwrap();
    logger.join().unwrap();
}
```

## Supervision: Handling Actor Failures

A key principle from Erlang: let actors crash and restart them.

```rust
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

fn supervised_actor<F>(name: &str, factory: F) -> mpsc::Sender<String>
where
    F: Fn(mpsc::Receiver<String>) + Send + 'static + Clone,
{
    let (tx, rx) = mpsc::channel::<String>();
    let name = name.to_string();

    // Supervisor thread
    thread::spawn(move || {
        let mut restarts = 0;
        loop {
            let factory = factory.clone();
            // The actual receiver is behind Arc<Mutex> for sharing
            let rx_shared = std::sync::Arc::new(std::sync::Mutex::new(rx));
            let name = name.clone();

            // For simplicity, we'll create a new channel on restart
            // In a real system, you'd preserve the mailbox
            println!("[Supervisor] Starting actor '{}' (restart #{})", name, restarts);

            let rx_shared_clone = rx_shared.clone();
            let result = thread::spawn(move || {
                // Simplified: we just pass a dummy receiver
                // Real implementation would reconnect the channel
                println!("[{}] Running", name);
                thread::sleep(Duration::from_secs(5)); // simulate work
            })
            .join();

            match result {
                Ok(()) => {
                    println!("[Supervisor] Actor '{}' exited normally", name);
                    break;
                }
                Err(e) => {
                    restarts += 1;
                    eprintln!("[Supervisor] Actor '{}' panicked: {:?}", name, e);
                    if restarts > 3 {
                        eprintln!("[Supervisor] Too many restarts, giving up");
                        break;
                    }
                    thread::sleep(Duration::from_secs(1)); // backoff
                }
            }

            // Get rx back from the Arc<Mutex>
            rx = std::sync::Arc::try_unwrap(rx_shared)
                .unwrap()
                .into_inner()
                .unwrap();
        }
    });

    tx
}
```

This is a simplified version. Real actor supervision (like in the `actix` or `bastion` crates) handles mailbox persistence, restart strategies (one-for-one, all-for-one), and backoff policies.

## Actor Model Trade-offs

**Advantages:**
- No shared mutable state — each actor owns its data
- No locks, no deadlocks between mutexes
- Easy to reason about — each actor is a sequential program
- Natural concurrency — just spawn more actors
- Fault isolation — one actor crashing doesn't affect others

**Disadvantages:**
- Request-reply pattern adds latency (two channel operations per request)
- Debugging is harder — you're tracing messages, not stepping through code
- Backpressure requires careful design
- Not great for fine-grained parallelism (overhead per message)
- "Channel deadlocks" still possible (actor A waits for reply from B, B waits for A)

## When to Use Actors

Actors work best for:
- Services with distinct stateful components (cache, queue, coordinator)
- Systems where components need independent lifecycles (restart on failure)
- Architectures where you want to scale by adding more actors

Actors don't work well for:
- Tight numeric loops (too much message overhead)
- Shared data structures that need atomic updates across multiple fields
- Simple producer-consumer patterns (just use channels directly)

I use actors when I have 3+ interacting stateful components. Below that threshold, raw channels or shared state is simpler. Above it, the structure actors impose keeps the complexity manageable.

---

Next — CSP-style concurrency, or "what Go channels look like in Rust."
