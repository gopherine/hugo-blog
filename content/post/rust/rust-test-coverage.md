---
title: "Lesson 9: Code Coverage — tarpaulin and llvm-cov"
author: Atharva Pandey
series: "Rust Testing Masterclass"
lesson: 9
keywords: [Rust, rust, testing, code coverage, tarpaulin, llvm-cov, cargo-llvm-cov, test coverage]
tags: [Rust tutorial, rust, testing]
date: '2024-08-30T07:55:00.000Z'
---

I worked on a team that had a strict "90% code coverage" policy. Every PR had to hit that number or it got rejected. The result? People wrote tests like `assert!(true)` just to touch lines. We had 92% coverage and bugs everywhere. Coverage is a useful signal. It's a terrible goal.

That said, knowing *which* lines your tests don't touch is genuinely valuable. It tells you where your blind spots are. Here's how to measure it in Rust.

## The Problem

You've written a hundred tests. They all pass. But are they actually testing the code that matters? Coverage tools answer two questions:

1. Which lines of code are executed by at least one test?
2. Which lines are never touched — and are therefore untested?

Low coverage on a critical module is a red flag. But high coverage doesn't mean your tests are good — it just means your tests *run* that code. They might not check the results, test edge cases, or verify error handling. Coverage measures execution, not correctness.

With that caveat firmly in mind, here's how to set it up.

## Option 1: cargo-tarpaulin

`tarpaulin` is the most popular coverage tool for Rust on Linux. It uses ptrace to track executed lines.

```bash
cargo install cargo-tarpaulin
```

**Important:** tarpaulin only works on Linux (x86_64). If you're on macOS or Windows, skip to llvm-cov below.

### Basic Usage

```bash
# Generate coverage report
cargo tarpaulin

# HTML report
cargo tarpaulin --out html

# With specific output directory
cargo tarpaulin --out html --output-dir coverage/
```

Sample output:

```
running 15 tests
test tests::test_add ... ok
test tests::test_subtract ... ok
test tests::test_multiply ... ok
...

|| Tested/Total Lines:
|| src/lib.rs: 45/52 (86.54%)
|| src/parser.rs: 23/30 (76.67%)
||
|| 68/82 (82.93%) covered
```

### Configuration

Create a `tarpaulin.toml` for persistent settings:

```toml
[default]
# Exclude test code from coverage
exclude-files = ["tests/*", "benches/*"]

# Run with all features
all-features = true

# Output formats
out = ["html", "lcov"]
output-dir = "coverage"

# Timeout for long tests
timeout = "120s"

# Ignore specific functions
# ignored = ["debug_dump", "test_helper_*"]
```

### Excluding Code from Coverage

Sometimes you legitimately don't want to count certain code:

```rust
// This function is only for debugging, don't count it
#[cfg(not(tarpaulin_include))]
fn debug_dump(data: &[u8]) {
    for byte in data {
        print!("{:02x} ", byte);
    }
    println!();
}
```

The `#[cfg(not(tarpaulin_include))]` attribute tells tarpaulin to skip this function. Use it for debug helpers, unreachable error paths, and code that's genuinely impossible to test.

## Option 2: cargo-llvm-cov (Recommended)

`cargo-llvm-cov` uses LLVM's built-in instrumentation. It works on Linux, macOS, and Windows, and produces more accurate results than tarpaulin.

```bash
cargo install cargo-llvm-cov
```

### Basic Usage

```bash
# Text summary
cargo llvm-cov

# HTML report
cargo llvm-cov --html

# Open in browser automatically
cargo llvm-cov --open

# JSON output (for CI integration)
cargo llvm-cov --json --output-path coverage.json

# LCOV format (for Codecov, Coveralls, etc.)
cargo llvm-cov --lcov --output-path lcov.info
```

### What the Output Looks Like

```
Filename                      Regions    Missed Regions     Cover   Functions  Missed Functions  Executed       Lines      Missed Lines     Cover
---                           ---        ---                ---     ---        ---               ---            ---        ---              ---
src/lib.rs                    42         5                  88.10%  12         1                 91.67%         156        18               88.46%
src/parser.rs                 28         8                  71.43%  8          2                 75.00%         95         24               74.74%
---                           ---        ---                ---     ---        ---               ---            ---        ---              ---
TOTAL                         70         13                 81.43%  20         3                 85.00%         251        42               83.27%
```

The HTML report is where the real value is. It shows your source code with lines colored green (covered) or red (not covered). You can immediately see which branches and error paths your tests miss.

### Including Integration Tests

By default, `cargo llvm-cov` runs unit tests. To include integration tests and doc tests:

```bash
# Include doctests
cargo llvm-cov --doctests

# Include all test types
cargo llvm-cov --all-targets

# Run specific test binary
cargo llvm-cov --test integration_tests
```

### Filtering

```bash
# Only show coverage for specific files
cargo llvm-cov --html -- --lib

# Ignore specific files
cargo llvm-cov --ignore-filename-regex "generated|proto"
```

## A Practical Example

Here's a module with some intentional coverage gaps:

```rust
pub struct Calculator {
    history: Vec<(String, f64)>,
}

impl Calculator {
    pub fn new() -> Self {
        Calculator {
            history: Vec::new(),
        }
    }

    pub fn add(&mut self, a: f64, b: f64) -> f64 {
        let result = a + b;
        self.history.push(("add".to_string(), result));
        result
    }

    pub fn subtract(&mut self, a: f64, b: f64) -> f64 {
        let result = a - b;
        self.history.push(("subtract".to_string(), result));
        result
    }

    pub fn divide(&mut self, a: f64, b: f64) -> Result<f64, String> {
        if b == 0.0 {
            return Err("division by zero".to_string());
        }
        let result = a / b;
        self.history.push(("divide".to_string(), result));
        Ok(result)
    }

    pub fn sqrt(&mut self, a: f64) -> Result<f64, String> {
        if a < 0.0 {
            return Err("cannot take square root of negative number".to_string());
        }
        let result = a.sqrt();
        self.history.push(("sqrt".to_string(), result));
        Ok(result)
    }

    pub fn last_result(&self) -> Option<f64> {
        self.history.last().map(|(_, r)| *r)
    }

    pub fn history_summary(&self) -> String {
        if self.history.is_empty() {
            return "No operations performed.".to_string();
        }
        self.history
            .iter()
            .map(|(op, result)| format!("{}: {}", op, result))
            .collect::<Vec<_>>()
            .join("\n")
    }

    pub fn clear_history(&mut self) {
        self.history.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_add() {
        let mut calc = Calculator::new();
        assert_eq!(calc.add(2.0, 3.0), 5.0);
    }

    #[test]
    fn test_subtract() {
        let mut calc = Calculator::new();
        assert_eq!(calc.subtract(5.0, 3.0), 2.0);
    }

    #[test]
    fn test_divide_success() {
        let mut calc = Calculator::new();
        assert_eq!(calc.divide(10.0, 2.0).unwrap(), 5.0);
    }

    // NOTE: We "forgot" to test:
    // - divide by zero
    // - sqrt (both success and negative)
    // - last_result
    // - history_summary
    // - clear_history
}
```

Running `cargo llvm-cov --open` would show the `divide` error path, all of `sqrt`, `last_result`, `history_summary`, and `clear_history` in red. That's your TODO list for test coverage.

The fix:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    // ... existing tests ...

    #[test]
    fn test_divide_by_zero() {
        let mut calc = Calculator::new();
        let err = calc.divide(10.0, 0.0).unwrap_err();
        assert_eq!(err, "division by zero");
    }

    #[test]
    fn test_sqrt_positive() {
        let mut calc = Calculator::new();
        assert_eq!(calc.sqrt(9.0).unwrap(), 3.0);
    }

    #[test]
    fn test_sqrt_negative() {
        let mut calc = Calculator::new();
        assert!(calc.sqrt(-1.0).is_err());
    }

    #[test]
    fn test_last_result() {
        let mut calc = Calculator::new();
        assert!(calc.last_result().is_none());
        calc.add(1.0, 2.0);
        assert_eq!(calc.last_result(), Some(3.0));
    }

    #[test]
    fn test_history_empty() {
        let calc = Calculator::new();
        assert_eq!(calc.history_summary(), "No operations performed.");
    }

    #[test]
    fn test_history_with_operations() {
        let mut calc = Calculator::new();
        calc.add(1.0, 2.0);
        calc.subtract(5.0, 3.0);
        let summary = calc.history_summary();
        assert!(summary.contains("add: 3"));
        assert!(summary.contains("subtract: 2"));
    }

    #[test]
    fn test_clear_history() {
        let mut calc = Calculator::new();
        calc.add(1.0, 2.0);
        calc.clear_history();
        assert!(calc.last_result().is_none());
        assert_eq!(calc.history_summary(), "No operations performed.");
    }
}
```

## Coverage in CI

### GitHub Actions with llvm-cov

```yaml
name: Coverage

on: [push, pull_request]

jobs:
  coverage:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
        with:
          components: llvm-tools-preview
      - name: Install cargo-llvm-cov
        uses: taiki-e/install-action@cargo-llvm-cov
      - name: Generate coverage
        run: cargo llvm-cov --lcov --output-path lcov.info
      - name: Upload to Codecov
        uses: codecov/codecov-action@v4
        with:
          files: lcov.info
          fail_ci_if_error: true
```

### Setting Coverage Thresholds

You can fail CI if coverage drops below a threshold:

```bash
# Fail if total coverage is below 80%
cargo llvm-cov --fail-under-lines 80
```

But be thoughtful about this. A hard threshold incentivizes gaming the metric instead of writing useful tests. I prefer using coverage as a *review tool* — part of the PR diff — rather than a gate.

## What Coverage Doesn't Tell You

I want to be blunt about this because I've seen teams waste enormous energy chasing coverage numbers.

**100% line coverage does not mean your code is correct.** Consider:

```rust
fn calculate_discount(price: f64, is_member: bool) -> f64 {
    if is_member {
        price * 0.9
    } else {
        price
    }
}

#[test]
fn test_discount() {
    // 100% line coverage!
    calculate_discount(100.0, true);
    calculate_discount(100.0, false);
    // But we never checked the return values...
}
```

Both branches are covered. But the test doesn't actually verify anything. It's theatre.

**Coverage doesn't measure:**
- Whether assertions are meaningful
- Whether edge cases are tested
- Whether error handling is correct
- Whether concurrent code has race conditions
- Whether performance meets requirements

Use coverage to find blind spots — modules with suspiciously low coverage, error branches that are never exercised, dead code that could be removed. Don't use it as proof that your code works.

## My Coverage Philosophy

Here's what I actually do in practice:

1. **Run coverage after writing tests** to see what I missed.
2. **Focus on critical modules** — parsers, validators, business logic. I don't chase coverage on CLI argument parsing or logging setup.
3. **Investigate 0% files** — they're either dead code (delete them) or completely untested (fix that).
4. **Don't set numeric targets.** Coverage should go up naturally as you write tests. If you need a target, use it as a floor (e.g., "don't let it drop below current") rather than a ceiling.
5. **Review the HTML report** as part of major refactors. It shows exactly which new paths need tests.

## What's Next

We've covered correctness — but how fast is your code? Benchmarking tells you, and more importantly, it tells you when performance regresses. We'll set up `criterion` next and learn to measure performance properly.
