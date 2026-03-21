---
title: 'Lesson 8: Designing Interfaces for Libraries — Libraries export interfaces, apps consume them'
author: Atharva Pandey
series: "Go Interfaces & Abstraction Design"
lesson: 8
keywords:
  - Go
  - golang
  - interfaces
  - library design
  - API design
  - extensibility
tags:
  - Go tutorial
  - golang
  - interfaces
date: '2025-05-08T00:00:00.000Z'
---

Writing a library for other Go developers is a fundamentally different design problem than writing application code. In an application, you control every call site. If an interface needs to change, you update all the callers. In a library, your callers are other people, other teams, other binaries you have never seen — and when you change a public interface, you break them all in ways you cannot fix yourself. This constraint forces a discipline that makes library-authored Go interfaces among the most carefully designed in the ecosystem.

The core inversion that separates library design from application design: libraries export interfaces so callers can provide implementations; applications consume interfaces to decouple dependencies. These are opposite directions, and confusing them leads to library APIs that are hard to extend and application patterns that do not belong.

## The Problem

The most common mistake in library design is exporting a concrete type and adding methods to it forever:

```go
// WRONG — library exports a fat concrete type, no extension points
package emaillib

type Client struct {
    host     string
    port     int
    auth     smtp.Auth
    rateLimit int
}

func NewClient(host string, port int, auth smtp.Auth) *Client {
    return &Client{host: host, port: port, auth: auth, rateLimit: 100}
}

func (c *Client) Send(to, subject, body string) error { /* ... */ }
func (c *Client) SendHTML(to, subject, html string) error { /* ... */ }
func (c *Client) SendWithAttachment(to, subject, body string, files []string) error { /* ... */ }
func (c *Client) SetRateLimit(n int) { c.rateLimit = n }
```

This looks fine until a caller needs to swap the transport for testing, or wants to use a different SMTP backend, or needs to add retry logic without modifying the library. There are no extension points. The library is a closed box.

The second mistake is exporting an interface with too many methods, making it impossible for library users to provide their own implementations without implementing everything:

```go
// WRONG — 8-method interface that callers must fully implement to extend
type Transport interface {
    Dial(host string, port int) error
    Authenticate(auth smtp.Auth) error
    SendEnvelope(from string, to []string) error
    WriteHeader(key, val string) error
    WriteBody(r io.Reader) error
    Flush() error
    Reset() error
    Close() error
}
```

Any caller who wants to provide a custom transport for testing has to implement all eight methods. Most of them are internal protocol steps that callers have no business thinking about.

## The Idiomatic Way

A well-designed library exports small, focused interfaces at the points where callers legitimately need to provide their own implementations. Everything else is concrete.

```go
// RIGHT — library exports a small interface for the meaningful extension point
package emaillib

// Transport is the only interface callers need to implement to swap the sender.
type Transport interface {
    Send(from string, to []string, msg []byte) error
}

// Client is a concrete type with all its capabilities exposed.
type Client struct {
    transport Transport
    rateLimit int
    log       *slog.Logger
}

func NewClient(transport Transport) *Client {
    return &Client{
        transport: transport,
        rateLimit: 100,
    }
}

func (c *Client) SendMessage(msg *Message) error {
    raw, err := msg.Render()
    if err != nil {
        return fmt.Errorf("rendering message: %w", err)
    }
    return c.transport.Send(msg.From, msg.To, raw)
}
```

The library provides a default transport:

```go
// SMTPTransport is the production implementation, exported for direct use.
type SMTPTransport struct {
    host string
    port int
    auth smtp.Auth
}

func NewSMTPTransport(host string, port int, auth smtp.Auth) *SMTPTransport {
    return &SMTPTransport{host: host, port: port, auth: auth}
}

func (t *SMTPTransport) Send(from string, to []string, msg []byte) error {
    return smtp.SendMail(
        fmt.Sprintf("%s:%d", t.host, t.port),
        t.auth,
        from,
        to,
        msg,
    )
}
```

Callers in tests implement `Transport` with one method:

```go
type captureTransport struct {
    sent []capturedEmail
}

func (c *captureTransport) Send(from string, to []string, msg []byte) error {
    c.sent = append(c.sent, capturedEmail{from: from, to: to, raw: msg})
    return nil
}
```

The library author controls the complexity. Callers extend exactly where extension makes sense.

For functional options — another common library pattern — the interface approach keeps the API stable across versions:

```go
// Functional options let callers configure without breaking when new options are added
type Option func(*Client)

func WithRateLimit(n int) Option {
    return func(c *Client) { c.rateLimit = n }
}

func WithLogger(log *slog.Logger) Option {
    return func(c *Client) { c.log = log }
}

// Adding new options later is backward compatible:
// existing callers just don't pass the new option.
func NewClientWithOptions(transport Transport, opts ...Option) *Client {
    c := &Client{transport: transport, rateLimit: 100}
    for _, o := range opts {
        o(c)
    }
    return c
}
```

This is how `net/http`, `google.golang.org/grpc`, and the AWS SDK all handle configuration. Options accumulate over time without breaking callers who wrote against the initial version.

## In The Wild

The Go database/sql package is a masterclass in library interface design. The `driver.Driver` interface has one method:

```go
type Driver interface {
    Open(name string) (Conn, error)
}
```

Database vendors implement this one interface to register their driver. The rest of the `database/sql` package is concrete — `*sql.DB`, `*sql.Tx`, `*sql.Rows`. Users of `database/sql` call concrete methods. Implementors of drivers implement small, focused interfaces. The extension points and the usage points are completely separate.

When I built an internal notification library used across six services, I started with a fat `Notifier` interface:

```go
// First attempt — too wide, callers couldn't implement it
type Notifier interface {
    SendEmail(ctx context.Context, to, subject, body string) error
    SendSMS(ctx context.Context, to, message string) error
    SendSlack(ctx context.Context, channel, message string) error
    SendPush(ctx context.Context, deviceToken, title, body string) error
}
```

Three of the six services used only email. Two used email and Slack. One used all four. The interface forced every service to declare a dependency on all four channels even when they used one.

The rewrite used a channel-per-interface design with a composite dispatcher:

```go
type EmailSender interface {
    SendEmail(ctx context.Context, to, subject, body string) error
}

type SMSSender interface {
    SendSMS(ctx context.Context, to, message string) error
}

type SlackSender interface {
    SendSlack(ctx context.Context, channel, message string) error
}

// Dispatcher composes channels — services that use all of them use Dispatcher
type Dispatcher struct {
    email EmailSender
    sms   SMSSender
    slack SlackSender
}
```

Each service imported only what it used. Tests implemented only what they needed. Adding a push notification channel meant adding a `PushSender` interface and an optional field on `Dispatcher` — existing code compiled without changes.

## The Gotchas

**Exported interfaces are a commitment.** Once you ship an interface as part of your public API, removing or changing a method is a breaking change. Adding a method to an existing interface is a breaking change for anyone who implemented it. Be conservative. Start narrow and add methods only when there is demonstrated need from multiple callers.

**Use `//go:build` or versioned packages for genuinely breaking changes.** If you must break an interface, the Go module versioning convention (`v2`, `v3` suffixes) gives callers a migration path. Do not silently break existing consumers.

**Document which interfaces callers are expected to implement.** In a library, some interfaces are implementation targets (callers implement them) and some are usage targets (callers call their methods). The former should have a comment like `// Implementors of this interface provide custom X behavior.` The latter is just a normal method-set documentation.

## Key Takeaway

🎓 Course Complete! You have reached the end of "Go Interfaces & Abstraction Design." The thread through all eight lessons is the same principle expressed from different angles: interfaces describe behavior, not identity. They belong to consumers, not producers. They should be as narrow as the contract requires and no wider. In application code, this means defining interfaces at the point of use, keeping them small, and letting concrete types be concrete. In library code, it means exporting the narrowest interface at each genuine extension point and making everything else concrete. Together, these principles produce Go code that is easy to test, easy to compose, and easy to maintain as requirements evolve.

---

← [Lesson 7: The io.Reader/Writer Ecosystem](/post/go/go-iface-io-ecosystem) | [Course Index](/series/go-interfaces-abstraction-design)
