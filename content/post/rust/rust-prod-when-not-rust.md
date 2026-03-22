---
title: "Lesson 10: When Not to Use Rust — Honest trade-offs"
author: Atharva Pandey
series: "Rust in Production — Real-World Architecture"
lesson: 10
keywords: [Rust, rust, architecture, production, trade-offs, language comparison, pragmatism]
tags: [Rust tutorial, rust, architecture, production]
date: '2025-11-05T11:46:00.000Z'
---

I like Rust. I've written 9 lessons about using it in production. I think it's one of the most well-designed languages of the last twenty years. And I'm about to spend an entire article telling you when you shouldn't use it.

Because the most dangerous engineers aren't the ones who don't know Rust — they're the ones who think Rust is always the answer. I've been that engineer. I once argued for rewriting a Flask API endpoint in Rust because "the response time was too high." The response time was 200ms, and 180ms of that was a database query. Rust would have saved us maybe 5ms of JSON serialization. My team lead asked me to go take a walk.

He was right.

## The Iteration Speed Tax

Let's start with the thing people don't want to talk about: Rust is slow to develop in. Not runtime slow — development velocity slow.

I can build a REST API in Go in a day. Authentication, database, CRUD endpoints, tests, Docker image. In Python with FastAPI, half a day. In Rust, two to three days for the same thing — and I'm experienced.

The reasons:

**Compile times.** Even with all the optimizations from Lesson 5, a clean build of a medium Rust project takes 2-5 minutes. An incremental build takes 10-30 seconds. In Go, both are under 5 seconds. In Python, there's no compile step at all. Over the course of a week, those 30-second waits add up to hours.

**The borrow checker learning curve.** Your first month in Rust, you'll fight the compiler on basic things. Your third month, you'll fight it on async lifetime issues. Your sixth month, you'll finally stop fighting — but it took six months.

**The ecosystem is smaller.** Python has a library for everything. Go has a library for most things. Rust has excellent libraries for systems-level work, but if you need to parse a specific proprietary file format or integrate with an obscure SaaS API, you might be writing it yourself.

**Refactoring is expensive.** Changing a type in Rust means updating every place that touches it. The compiler makes sure you don't miss anything — but "not missing anything" can mean changing 40 files for what should have been a 5-minute rename.

This isn't a flaw in Rust. It's a deliberate trade-off: spend more time writing correct code upfront so you spend less time debugging incorrect code later. If your project lives for 5 years, that's a great trade. If your project is a prototype that might be thrown away in 3 months, it's a terrible one.

## Specific Cases Where Rust Is the Wrong Choice

### 1. Prototypes and MVPs

You're building a new product. You don't know if anyone wants it. You need to ship something in two weeks, show it to users, and iterate based on feedback.

Use Python. Use TypeScript. Use Go if you want types. Use whatever gets you to users fastest. The performance characteristics of your language do not matter when you have 12 users. Product-market fit matters. Speed of iteration matters. Code quality can be improved later — if there's a "later."

I've seen teams burn 3 months writing a "proper" Rust backend for a product that got cancelled after launch because nobody wanted it. The Rust code was beautiful. It had zero users.

### 2. Data Science and ML Pipelines

Python owns this space and it's not close. PyTorch, TensorFlow, pandas, scikit-learn, Jupyter notebooks, the entire ecosystem. Can you do ML in Rust? Sort of. Should you? Almost certainly not.

The exceptions: if you're deploying a trained model in a latency-critical inference pipeline, wrapping the hot path in Rust (or C++) makes sense. But training? Data exploration? Feature engineering? Python is the answer.

```python
# This takes 20 minutes to write in Python
import pandas as pd
from sklearn.ensemble import RandomForestClassifier

df = pd.read_csv("data.csv")
X = df.drop("target", axis=1)
y = df["target"]
model = RandomForestClassifier().fit(X, y)
print(f"Accuracy: {model.score(X, y)}")
```

The Rust equivalent would require choosing between half a dozen incomplete ML crates, writing your own CSV-to-tensor pipeline, and spending a week on something that gives you no advantage over the Python version.

### 3. Quick Scripts and Automation

Need a script that backs up a database, sends a Slack notification, and cleans up old files? Use Python. Use Bash. Use whatever your team can read and modify in 5 minutes.

```bash
#!/bin/bash
pg_dump mydb | gzip > /backups/mydb-$(date +%Y%m%d).sql.gz
curl -X POST $SLACK_WEBHOOK -d '{"text":"Backup complete"}'
find /backups -mtime +30 -delete
```

This is 4 lines. The Rust version would be 80 lines, require a Cargo.toml with 5 dependencies, and take 2 minutes to compile. For a script that runs once a day from a cron job, that overhead is absurd.

### 4. Rapid API Development Where Performance Doesn't Matter

Most web APIs are I/O bound. They spend 90% of their time waiting for database queries, external API calls, or network I/O. The language runtime contributes maybe 1-5% of the total response time.

If your API's p99 latency is 500ms and 480ms of that is database queries, rewriting from Go to Rust won't help. Fix your queries. Add caching. Optimize your schema. The language isn't the bottleneck.

Go is almost certainly the right choice here. It's fast enough, has a great HTTP ecosystem, compiles quickly, and your team can hire Go developers much more easily than Rust developers.

### 5. Teams Without Rust Experience

This one is uncomfortable but important. If nobody on your team knows Rust and you have a deadline in 3 months, do not adopt Rust. The learning curve is real — 3 to 6 months before an experienced developer is productive in Rust. The async model adds another month. The borrow checker adds frustration that slows everyone down.

Start small. Let one or two interested engineers build a non-critical tool in Rust. Let them learn the idioms. Once the team has a core of 2-3 people who can review Rust code competently, then consider a production service.

I've seen teams adopt Rust because their CTO saw a conference talk. Six months later, half the team was frustrated, the other half had left, and the codebase was full of `.clone()` and `Arc<Mutex<>>` everywhere — Rust code that had none of Rust's benefits because nobody understood how to write idiomatic Rust.

### 6. Dynamic/Plugin Systems

If your application needs to load and execute user-provided code at runtime, Rust is a poor fit. Python, Lua, and JavaScript have mature embedding stories. Rust compiles to native code — there's no "eval" and no "import at runtime" (well, there's `libloading` for dynamic libraries, but it's unsafe and fragile).

WebAssembly is changing this — you can compile Rust to WASM and run it in a sandbox. But if your users are writing plugins, they probably want to write them in Python or TypeScript, not Rust.

## The Decision Framework

When I'm choosing a language for a new service, I ask these questions in order:

**1. Is this a prototype or a long-lived service?**
- Prototype → Python/TypeScript/Go
- Long-lived → Continue to question 2

**2. Is performance a primary requirement (latency, throughput, memory)?**
- No → Go (or Python, depending on the domain)
- Yes → Continue to question 3

**3. Is correctness critical (financial, safety, data integrity)?**
- No → Go (it's fast enough and simpler)
- Yes → Continue to question 4

**4. Does the team have Rust experience?**
- No → Go, and start building Rust expertise on the side
- Yes → Rust is likely the right choice

**5. Is the ecosystem available?**
- Check for crates that cover your needs: database, serialization, HTTP, domain-specific libraries
- If you'd need to write more than 20% of your dependencies from scratch, reconsider

This isn't a hard formula. It's a conversation starter. Every project has unique constraints.

## What Rust Excels At

To be clear, the list of things Rust is *great* at is long:

- **High-throughput data pipelines.** Parsing, transforming, routing millions of events per second.
- **Infrastructure tooling.** Proxies, load balancers, databases, message brokers.
- **Embedded systems.** Microcontrollers, IoT devices, resource-constrained environments.
- **CLI tools.** Fast startup, single binary, no runtime dependency.
- **WebAssembly.** Rust's WASM story is the best of any systems language.
- **Anything where memory safety matters at scale.** Network services handling untrusted input, parsers for complex formats, cryptographic libraries.
- **Long-lived services where p99 latency matters.** No GC pauses, no stop-the-world collections, predictable performance.

If your problem is on this list, Rust is probably the right answer. If it's not, think hard about whether the development speed cost is worth the runtime benefits.

## The Maturity Argument

There's a subtler point here about ecosystem maturity. Go's `database/sql`, `net/http`, and `encoding/json` are battle-tested standard library packages that work for 90% of use cases. Rust's equivalents — `sqlx`, `axum`, `serde` — are excellent, but they're third-party crates that can have breaking changes, go unmaintained, or fork.

The Rust ecosystem is getting more stable every year. `tokio`, `serde`, and `axum` are essentially foundational at this point. But you still need to vendor your dependencies, audit your supply chain, and have a plan for when a crate you depend on stops being maintained.

```toml
# Cargo.toml — pin your dependencies
[dependencies]
axum = "=0.7.4"      # exact version pin
serde = "=1.0.195"
tokio = "=1.35.1"
```

In Go, you pin with `go.sum`. In Python, you pin with `pip freeze`. In Rust, you use `Cargo.lock` (always commit it for binaries) and optionally exact version pins for critical dependencies.

## The Hiring Reality

I'm going to be blunt: hiring Rust developers is hard. The talent pool is smaller than Go, Java, Python, or TypeScript. Senior Rust engineers are rare and expensive. If your company isn't in a position to offer competitive compensation or an interesting technical challenge, you'll struggle to staff a team.

This doesn't mean "don't use Rust." It means factor hiring into your decision. If you're a 4-person startup, one Rust enthusiast can carry a service. If you're a 200-person company trying to migrate your entire backend to Rust, you need a realistic plan for where those engineers are coming from.

Some companies solve this by hiring strong systems engineers (C++, Go, C) and training them in Rust. That works — but budget 3-6 months of reduced productivity per engineer.

## Closing Thoughts on This Course

Over these 10 lessons, we've covered how to structure large Rust applications, model domains with the type system, implement hexagonal architecture, build CQRS systems, manage multi-crate workspaces, version APIs, use compile-time feature flags, migrate from other languages, survive production incidents, and — now — know when not to use Rust at all.

The common thread isn't "Rust is the best language." It's "understand your tools well enough to use them appropriately." Rust gives you guarantees that no other mainstream language matches — memory safety without garbage collection, fearless concurrency, and a type system that can encode your business rules. But those guarantees have a cost in development speed, learning curve, and ecosystem breadth.

Use Rust where it shines. Use something else where it doesn't. And whichever language you use, invest in architecture — because good architecture in any language beats bad architecture in the "best" language, every time.
