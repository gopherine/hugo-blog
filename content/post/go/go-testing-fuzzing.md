---
title: "Lesson 2: Fuzzing in Go — Let the machine find your edge cases"
author: Atharva Pandey
series: "Go Testing Masterclass"
lesson: 2
keywords:
  - Go
  - golang
  - testing
  - fuzzing
  - fuzz testing
  - FuzzXxx
  - corpus
  - edge cases
tags:
  - Go tutorial
  - golang
  - testing
date: '2024-07-10T00:00:00.000Z'
---

There's a class of bugs that you will never find by thinking about edge cases. You'll think about empty strings, about zero values, about negative numbers. But will you think about the string that's exactly 65,536 bytes? Or the UTF-8 sequence that's technically valid but trips up a specific parser codepath? Or the floating point value that serializes and then fails to deserialize because of a precision edge in your JSON handling? You won't. But a fuzzer will find it in under a minute.

Go added native fuzzing support in 1.18, and it's one of the most underused features in the ecosystem. I ignored it for too long, treating it as something for security researchers working on parsers. Then it found a panic in a URL normalization function I was sure was airtight. I've used it on every non-trivial input-handling function since.

## The Problem

The classic approach to testing a function with arbitrary input is to think up cases manually:

```go
// WRONG — you can't enumerate all the edge cases by hand
func TestNormalizeURL(t *testing.T) {
    cases := []struct {
        input string
        want  string
    }{
        {"http://example.com", "http://example.com/"},
        {"HTTP://EXAMPLE.COM", "http://example.com/"},
        {"http://example.com/path/../other", "http://example.com/other"},
        {"", ""},
    }
    for _, tc := range cases {
        t.Run(tc.input, func(t *testing.T) {
            got := NormalizeURL(tc.input)
            if got != tc.want {
                t.Errorf("got %q, want %q", got, tc.want)
            }
        })
    }
}
```

You've covered the cases you thought of. That's the problem. The URL spec has dozens of edge cases around query encoding, fragment handling, IPv6 literals, and percent-encoding that you're probably not thinking about. Your function might panic on `http://[::1` (incomplete IPv6 bracket). It might silently mangle input with a null byte. It might loop forever on a specially crafted path. Manual tests can't touch this space — it's too large.

A related problem is testing invariants — properties that should hold for *any* valid input, not just specific inputs:

```go
// WRONG — testing specific values instead of invariants
func TestRoundTrip(t *testing.T) {
    data := MyStruct{Name: "hello", Value: 42}
    b, _ := json.Marshal(data)
    var out MyStruct
    json.Unmarshal(b, &out)
    if out != data {
        t.Errorf("round trip failed: got %+v", out)
    }
}
```

This tests one specific value. But round-trip safety needs to hold for all values, including ones with special characters, nil pointers, and large numbers.

## The Idiomatic Way

A fuzz test in Go follows a simple convention: the function is named `FuzzXxx`, takes `*testing.F`, and its fuzz target takes `*testing.T` plus the input types you want fuzzed.

```go
// RIGHT — fuzz test that checks a parser doesn't panic
func FuzzNormalizeURL(f *testing.F) {
    // Seed corpus: known-interesting inputs the fuzzer starts from
    f.Add("http://example.com")
    f.Add("HTTP://EXAMPLE.COM/path/../other?q=1#frag")
    f.Add("")
    f.Add("http://[::1]:8080/path")
    f.Add("ftp://user:pass@host/path")

    f.Fuzz(func(t *testing.T, input string) {
        // Property: NormalizeURL must never panic
        // (If it panics, the fuzzer records the input as a failure)
        defer func() {
            if r := recover(); r != nil {
                t.Errorf("NormalizeURL panicked on input %q: %v", input, r)
            }
        }()
        _ = NormalizeURL(input)
    })
}
```

Run this with `go test -fuzz=FuzzNormalizeURL` and the engine mutates your seed corpus — flipping bits, splicing strings, inserting special bytes — and runs your target function thousands of times per second. Any panic or test failure is recorded as a new corpus entry in `testdata/fuzz/FuzzNormalizeURL/`.

For round-trip invariants, fuzzing is even more powerful:

```go
// RIGHT — fuzz test for encode/decode round-trip invariant
func FuzzMarshalRoundTrip(f *testing.F) {
    f.Add("hello world", 42, true)
    f.Add("", 0, false)
    f.Add("special: \x00\n\t\"\\", -1, true)

    f.Fuzz(func(t *testing.T, name string, value int, flag bool) {
        original := MyStruct{Name: name, Value: value, Flag: flag}

        b, err := Marshal(original)
        if err != nil {
            // Encoding errors on arbitrary input are acceptable
            return
        }

        var decoded MyStruct
        if err := Unmarshal(b, &decoded); err != nil {
            t.Fatalf("Unmarshal failed after Marshal succeeded: input=%+v, bytes=%q, err=%v",
                original, b, err)
        }

        if decoded != original {
            t.Fatalf("round-trip mismatch: original=%+v, decoded=%+v", original, decoded)
        }
    })
}
```

This asserts that for any combination of `name`, `value`, and `flag`, if encoding succeeds, decoding the result must succeed and produce the original value. The fuzzer will find combinations that break this — like name values with embedded null bytes that your encoder strips but your decoder preserves.

## In The Wild

The real value shows up when fuzzing functions at trust boundaries — anywhere you consume external input. I used it on a custom binary protocol deserializer and it found an integer overflow in the length-prefix parsing within two minutes. The function had been in production for a year.

```go
func FuzzDeserializePacket(f *testing.F) {
    // Add real packets from production as seed corpus
    f.Add([]byte{0x01, 0x00, 0x04, 'h', 'e', 'l', 'l'})
    f.Add([]byte{})
    f.Add([]byte{0xFF, 0xFF, 0xFF, 0xFF}) // max length prefix

    f.Fuzz(func(t *testing.T, data []byte) {
        // Should never panic, should always return an error or a valid packet
        pkt, err := DeserializePacket(data)
        if err != nil {
            return // error is a valid outcome for malformed input
        }
        // If we got a packet, it must be re-serializable
        out, err := pkt.Serialize()
        if err != nil {
            t.Fatalf("serialization of deserialized packet failed: %v", err)
        }
        // And the re-serialized form must deserialize back to the same packet
        pkt2, err := DeserializePacket(out)
        if err != nil {
            t.Fatalf("re-deserialization failed: %v", err)
        }
        if !pkt.Equal(pkt2) {
            t.Fatalf("round-trip mismatch")
        }
    })
}
```

The production corpus entry from a real packet means the fuzzer starts from valid shapes and mutates from there — much more effective than starting from random bytes.

## The Gotchas

**Fuzz tests are not run by default.** `go test ./...` runs your fuzz functions as unit tests using only the seed corpus — it doesn't actually fuzz. To fuzz, you must pass `-fuzz=FuzzXxx`. This is intentional: fuzzing is a long-running operation. In CI, you typically run `go test ./...` for speed and save fuzzing for a nightly or dedicated job.

**Corpus entries persist between runs.** When the fuzzer finds a failure, it writes the input to `testdata/fuzz/FuzzXxx/`. Subsequent runs replay those inputs. Commit this directory — it means the specific inputs that once crashed your function are always regression-tested.

**The fuzzer only handles basic types.** The fuzz target can only take `*testing.T` plus `string`, `[]byte`, `bool`, `byte`, `rune`, `float32`, `float64`, and integer types. If your function takes a struct, you need to accept its serialized form and parse it inside the fuzz target.

**Flaky panics are still panics.** If your code has a panic path that's only reachable with specific inputs, the fuzzer will find it. Don't dismiss the finding as "edge case input we'd never receive in production." The fuzzer doesn't know that, and neither does an attacker.

## Key Takeaway

Fuzzing is not a replacement for regular tests — it's a complement. Your unit tests verify known behaviour. Your fuzz tests verify that no *unknown* input can cause panics, crashes, or invariant violations. The effort to write a fuzz test is maybe ten minutes. The effort to debug a production panic caused by an input you never thought to test is measured in hours — or in the pager alert at 2 AM. Pick one.

---

[Course Index](/series/go-testing-masterclass) | [← Lesson 1](/post/go/go-testing-subtests) | [Next → Lesson 3: Integration Tests](/post/go/go-testing-integration)
