---
title: 'Lesson 4: time Package Gotchas — Timezones, monotonic clocks, and the bug in your cron'
author: Atharva Pandey
series: "Go Standard Library Mastery"
lesson: 4
keywords:
  - Go
  - golang
  - time
  - timezone
  - monotonic clock
  - time.Time
  - cron
  - DST
  - stdlib
tags:
  - Go tutorial
  - golang
  - stdlib
date: '2024-11-18T00:00:00.000Z'
---

The `time` package looks straightforward right up until the moment a bug report arrives saying "the nightly job didn't run last Sunday." It never runs on the Sunday when daylight saving time ends. Because that Sunday has 25 hours, and the cron expression fires twice at the ambiguous time, and the second firing is skipped because the code thinks it already ran. I've debugged this exact bug, and the root cause is always the same: someone — usually me — assumed time is simpler than it is.

Go's `time` package has genuinely subtle design decisions. The monotonic clock reading embedded in `time.Time`. The difference between `time.Local` and `time.UTC` and why you should almost always use UTC for storage. The wall clock vs monotonic distinction that affects how you measure elapsed time. These aren't esoteric — they affect every service that touches time, which is every service.

## The Problem

The classic DST bug in a scheduled task:

```go
// WRONG — uses wall clock time for duration measurement
func scheduleNextRun(last time.Time, interval time.Duration) time.Time {
    return last.Add(interval) // fine normally, wrong across DST transitions
}

// ALSO WRONG — comparing times with == when they might have different timezones
func alreadyRan(lastRun time.Time) bool {
    today := time.Now().Truncate(24 * time.Hour) // midnight in LOCAL time
    lastRunDay := lastRun.Truncate(24 * time.Hour)
    return today == lastRunDay // WRONG: == compares instant, zone offset, and monotonic
}
```

The second function has two bugs. `time.Now().Truncate` truncates in UTC by default — midnight UTC is not midnight in any other timezone. And `==` on `time.Time` values compares wall clock time, timezone, and location pointer — two `time.Time` values representing the same instant but in different timezones are not `==`. Use `.Equal()` or `.UTC().Equal()` instead.

Measuring elapsed time with wall clock has a different failure mode:

```go
// WRONG — wall clock can go backwards (NTP sync, leap second, DST)
start := time.Now()
expensiveOperation()
elapsed := time.Since(start) // CORRECT — time.Since uses monotonic clock
```

Actually `time.Since` is correct — it uses the monotonic clock. But if you had done `end := time.Now(); elapsed := end.Sub(start)`, that would also use the monotonic clock. The issue is when you serialize a `time.Time` to string, store it, and then reconstruct it — the reconstructed value has no monotonic component.

## The Idiomatic Way

Always store and transmit times in UTC:

```go
// Always work in UTC for storage and comparison
func nowUTC() time.Time {
    return time.Now().UTC()
}

// Parse with explicit timezone — never rely on local timezone for parsing
func parseTime(s string) (time.Time, error) {
    // RFC 3339 includes timezone offset — always use it for API timestamps
    t, err := time.Parse(time.RFC3339, s)
    if err != nil {
        return time.Time{}, fmt.Errorf("parse time: %w", err)
    }
    return t.UTC(), nil // normalize to UTC after parsing
}

// Compare times correctly
func isSameDay(a, b time.Time, loc *time.Location) bool {
    // Convert both to the same location before comparing calendar date
    ay, am, ad := a.In(loc).Date()
    by, bm, bd := b.In(loc).Date()
    return ay == by && am == bm && ad == bd
}
```

The timezone-aware same-day check matters for user-facing features like "show me today's activity." Today is a calendar concept that depends on which timezone the user is in.

Loading timezone data correctly:

```go
// Load timezone from the IANA database — always prefer this over offsets
loc, err := time.LoadLocation("America/New_York")
if err != nil {
    return fmt.Errorf("load timezone: %w", err)
}

// Convert a UTC time to a user's local time for display
userTime := utcTime.In(loc)
fmt.Println(userTime.Format("2006-01-02 15:04:05 MST"))
```

In containers with `FROM scratch` or `FROM distroless/static`, the IANA timezone database isn't included. `time.LoadLocation` will fail. Fix this by including `tzdata` in your Docker image or by importing the `time/tzdata` package:

```go
// Import to embed timezone data in the binary — no external files needed
import _ "time/tzdata"
```

This increases binary size by about 450KB but eliminates the dependency on the host's timezone database — ideal for scratch containers.

For scheduling work at wall-clock times (daily jobs, hourly jobs), the correct approach uses absolute time calculation rather than duration addition:

```go
// Calculate the next occurrence of a wall-clock time in a specific timezone
func nextOccurrence(hour, minute int, loc *time.Location) time.Time {
    now := time.Now().In(loc)
    next := time.Date(now.Year(), now.Month(), now.Day(), hour, minute, 0, 0, loc)
    if !next.After(now) {
        next = next.Add(24 * time.Hour)
    }
    // If the time doesn't exist (DST spring-forward), this adds one hour
    // to land in valid time. If it's ambiguous (DST fall-back), it picks
    // the first occurrence.
    return next.UTC()
}

// Scheduler that fires at a wall-clock time, DST-aware
func runDailyAt(ctx context.Context, hour, minute int, loc *time.Location, fn func()) {
    for {
        next := nextOccurrence(hour, minute, loc)
        timer := time.NewTimer(time.Until(next))
        select {
        case <-ctx.Done():
            timer.Stop()
            return
        case <-timer.C:
            fn()
        }
    }
}
```

## In The Wild

The monotonic clock reading in `time.Time` is worth understanding. When you call `time.Now()`, the returned `time.Time` has both a wall clock reading and a monotonic clock reading. The wall clock can jump (NTP adjustments). The monotonic clock only moves forward. Go automatically uses the monotonic reading for `time.Since`, `time.Until`, and `t.Sub(u)` when both values have monotonic readings.

When does the monotonic reading disappear? When you marshal to JSON (RFC3339 has no monotonic field), when you use `t.Round()` or `t.Truncate()`, or when you create a time with `time.Date()`. Strip the monotonic reading explicitly with `t.Round(0)` when you want to ensure two times compare wall-clock only:

```go
// Strip the monotonic clock reading for storage or serialization comparison
stored := time.Now().Round(0)
loaded, _ := time.Parse(time.RFC3339Nano, stored.Format(time.RFC3339Nano))
// stored.Equal(loaded) is now comparing wall clock only — correct
```

## The Gotchas

**`time.Sleep` is not precise.** The Go runtime can wake up a goroutine later than requested, especially under load. Never use `time.Sleep` for anything requiring precision. For wall-clock-accurate scheduling, recalculate the next target time on each iteration.

**`time.Ticker` drifts.** A `time.Ticker` with a 1-hour interval fires every hour of elapsed time, not every hour of wall time. After a DST transition, the ticker is an hour off relative to wall time. Use the scheduler pattern above for wall-clock-accurate work.

**`time.Time{}` is not zero time.** `time.Time{}` (the zero value) represents January 1, year 1, 00:00:00 UTC. It's not the Unix epoch. Use `t.IsZero()` to check for unset times, not comparison to `time.Time{}` or `time.Unix(0,0)`.

**Timezone names are not unique.** "EST" means -5:00 in some contexts and -4:00 in others (during EDT). Never parse timezone abbreviations — use full IANA names like "America/New_York" or UTC offsets like "+05:30". Only `time.Parse` with `time.RFC3339` (which includes an offset) is reliable.

## Key Takeaway

Store all times in UTC. Compare with `.Equal()`, not `==`. Use `time.LoadLocation` with IANA names, never timezone abbreviations. Import `time/tzdata` in containers that don't have the IANA database on disk. Use absolute time calculation for wall-clock scheduling, not duration addition. Understand when the monotonic clock is and isn't present in a `time.Time` value.

---

**Previous:** [Lesson 3: encoding/json Beyond Basics](/post/go/go-stdlib-json/)
**Next:** [Lesson 5: os and filepath — Cross-platform file operations that actually work](/post/go/go-stdlib-os-filepath/)
