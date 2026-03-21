---
title: 'Lesson 3: encoding/json Beyond Basics — Custom marshalers, streaming, and the traps'
author: Atharva Pandey
series: "Go Standard Library Mastery"
lesson: 3
keywords:
  - Go
  - golang
  - encoding/json
  - JSON
  - custom marshaler
  - streaming
  - json.Decoder
  - json.Encoder
  - stdlib
tags:
  - Go tutorial
  - golang
  - stdlib
date: '2024-09-30T00:00:00.000Z'
---

`encoding/json` is one of the first packages every Go developer uses and one of the last they fully understand. The basic `json.Marshal` / `json.Unmarshal` API is approachable. But the package contains a whole layer of capabilities — custom marshaling, streaming decoders, `json.RawMessage` for deferred parsing, interface-type fields, and a surprising number of edge cases — that separate the code that works in demos from the code that works in production with adversarial inputs.

I've shipped bugs in all of these areas. Let me walk through them so you don't have to.

## The Problem

The naive approach works until it doesn't:

```go
// APPEARS CORRECT, has multiple production issues
type Event struct {
    ID        int       `json:"id"`
    Type      string    `json:"type"`
    Timestamp time.Time `json:"timestamp"`
    Data      any       `json:"data"` // interface{} — opens a can of worms
}

func handleEvent(body io.Reader) (*Event, error) {
    var e Event
    return &e, json.NewDecoder(body).Decode(&e)
}
```

Issue one: `any` (alias for `interface{}`) for the `Data` field. When the JSON decoder encounters a JSON object for this field, it produces `map[string]interface{}`. Numbers become `float64`. A JSON integer 1234567890123 fits in an `int64` but is lossily converted to `float64(1234567890123)` — which is a different value. You've silently corrupted data.

Issue two: `time.Time` marshals and unmarshals to RFC 3339 format by default, which is usually what you want. But if your API uses Unix timestamps, you need custom marshaling.

Issue three: no size limit on the decoder. A maliciously large JSON payload will be decoded in full, allocating memory proportionally to the input size.

## The Idiomatic Way

Custom marshalers let you control exactly how a type is encoded and decoded:

```go
// UnixTime marshals as a Unix timestamp (int64) instead of RFC3339 string
type UnixTime struct{ time.Time }

func (t UnixTime) MarshalJSON() ([]byte, error) {
    return []byte(strconv.FormatInt(t.Unix(), 10)), nil
}

func (t *UnixTime) UnmarshalJSON(data []byte) error {
    var unix int64
    if err := json.Unmarshal(data, &unix); err != nil {
        return err
    }
    t.Time = time.Unix(unix, 0).UTC()
    return nil
}

// Usage
type Event struct {
    ID        int      `json:"id"`
    Type      string   `json:"type"`
    Timestamp UnixTime `json:"timestamp"`
}
```

For the dynamic `Data` field — where the structure depends on the `Type` — use `json.RawMessage` to defer parsing:

```go
type Event struct {
    ID        int             `json:"id"`
    Type      string          `json:"type"`
    Timestamp UnixTime        `json:"timestamp"`
    Data      json.RawMessage `json:"data"` // raw bytes, not parsed yet
}

type ClickData struct { X, Y int; ElementID string }
type PurchaseData struct { ProductID string; Amount float64; Currency string }

func parseEvent(body io.Reader) (*Event, error) {
    // Limit to 1MB to prevent memory exhaustion
    limited := io.LimitReader(body, 1<<20)

    var e Event
    if err := json.NewDecoder(limited).Decode(&e); err != nil {
        return nil, fmt.Errorf("decode event: %w", err)
    }

    return &e, nil
}

// Parse the Data field once the Type is known
func processEvent(e *Event) error {
    switch e.Type {
    case "click":
        var d ClickData
        if err := json.Unmarshal(e.Data, &d); err != nil {
            return fmt.Errorf("decode click data: %w", err)
        }
        return handleClick(d)

    case "purchase":
        var d PurchaseData
        if err := json.Unmarshal(e.Data, &d); err != nil {
            return fmt.Errorf("decode purchase data: %w", err)
        }
        return handlePurchase(d)

    default:
        return fmt.Errorf("unknown event type: %s", e.Type)
    }
}
```

`json.RawMessage` is just `[]byte` with custom JSON marshaling. It holds the raw JSON bytes of a field without parsing them. You can unmarshal the outer struct immediately and then unmarshal the `Data` field later, once you know the type.

For large JSON responses — bulk API results, analytics exports — use streaming:

```go
// Stream a large JSON array without loading it all into memory
func processLargeResponse(body io.Reader) error {
    dec := json.NewDecoder(body)

    // Read the opening '['
    if _, err := dec.Token(); err != nil {
        return fmt.Errorf("read array start: %w", err)
    }

    // Decode elements one at a time
    for dec.More() {
        var item Item
        if err := dec.Decode(&item); err != nil {
            return fmt.Errorf("decode item: %w", err)
        }
        if err := processItem(item); err != nil {
            return err
        }
    }

    // Read the closing ']'
    if _, err := dec.Token(); err != nil {
        return fmt.Errorf("read array end: %w", err)
    }

    return nil
}
```

`dec.More()` returns true while there are more array elements (or object fields). The decoder reads and parses one element at a time. Memory usage is constant regardless of array size.

## In The Wild

A common pattern in event-driven systems is a discriminated union — a field that determines the structure of sibling fields. Custom `UnmarshalJSON` handles this cleanly:

```go
type Notification struct {
    Kind    string `json:"kind"`
    Payload any    // populated in UnmarshalJSON based on Kind
}

func (n *Notification) UnmarshalJSON(data []byte) error {
    // First pass: decode only the discriminator field
    var raw struct {
        Kind string          `json:"kind"`
        Payload json.RawMessage `json:"payload"`
    }
    if err := json.Unmarshal(data, &raw); err != nil {
        return err
    }
    n.Kind = raw.Kind

    // Second pass: decode the payload based on Kind
    switch raw.Kind {
    case "email":
        var p EmailPayload
        if err := json.Unmarshal(raw.Payload, &p); err != nil {
            return err
        }
        n.Payload = p
    case "sms":
        var p SMSPayload
        if err := json.Unmarshal(raw.Payload, &p); err != nil {
            return err
        }
        n.Payload = p
    default:
        return fmt.Errorf("unknown notification kind: %q", raw.Kind)
    }
    return nil
}
```

After unmarshaling, `n.Payload` is the correct concrete type and you can type-assert without reflection.

## The Gotchas

**`omitempty` and zero values.** The `omitempty` struct tag omits a field when it's the zero value for its type. For numeric fields, `omitempty` omits 0. For boolean fields, it omits `false`. For pointers, it omits `nil`. If 0 or `false` are valid values you need to transmit, use a pointer — `*int`, `*bool` — so the zero value is `nil` (omitted) and the pointer to zero is non-nil (transmitted).

**`json.Number` for precise numeric decoding.** When you need to decode numbers without float64 precision loss, use `json.Decoder.UseNumber()`. This makes the decoder produce `json.Number` values (a string alias) for numeric fields decoded into `interface{}`. You can then convert to the precise type you need:

```go
dec := json.NewDecoder(r)
dec.UseNumber()
var v any
dec.Decode(&v)
// v is now map[string]interface{} with json.Number for numbers
```

**Unmarshaling into a nil pointer panics.** `json.Unmarshal(data, nil)` panics. Always pass a non-nil pointer: `var v MyType; json.Unmarshal(data, &v)`.

**`encoding/json` is reflection-based and relatively slow.** For hot paths that encode millions of small objects per second, consider `encoding/json/v2` (experimental in the standard library as of Go 1.24) or `github.com/json-iterator/go` as a drop-in replacement with the same API but faster encoding.

## Key Takeaway

`encoding/json` covers 95% of JSON use cases out of the box. For the other 5%: use `json.RawMessage` to defer parsing until you know the type; implement `MarshalJSON`/`UnmarshalJSON` for types that need non-default representations; use `json.Decoder` with `dec.More()` for streaming large arrays; and limit input size with `io.LimitReader`. These patterns handle everything from event webhooks to bulk data exports.

---

**Previous:** [Lesson 2: io Patterns](/post/go/go-stdlib-io/)
**Next:** [Lesson 4: time Package Gotchas — Timezones, monotonic clocks, and the bug in your cron](/post/go/go-stdlib-time/)
