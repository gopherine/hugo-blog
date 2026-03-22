---
title: "Lesson 9: Function-Like Proc Macros — sql!() and friends"
author: Atharva Pandey
series: "Rust Macros & Metaprogramming"
lesson: 9
keywords: [Rust, rust, macros, proc-macro, function-like, DSL, SQL, compile-time]
tags: [Rust tutorial, rust, macros, metaprogramming]
date: '2025-02-24T18:30:00.000Z'
---

A colleague once asked me why `sqlx::query!("SELECT * FROM users WHERE id = $1")` can catch SQL errors at compile time. "Is it reading the database during compilation?" Yes. It literally connects to your database, validates the query, checks the types, and generates type-safe Rust code — all before your program runs. That's a function-like proc macro doing things that feel illegal. And the mechanics behind it are more straightforward than you'd think.

## What Makes Function-Like Proc Macros Different

Derive macros attach to structs. Attribute macros attach to items. Function-like proc macros look like function calls — `my_macro!(...)` — but unlike `macro_rules!` macros, they're full proc macros that can run arbitrary code.

The signature:

```rust
#[proc_macro]
pub fn my_macro(input: TokenStream) -> TokenStream {
    // input: everything between the parentheses/brackets/braces
    // return: the replacement code
    todo!()
}
```

One argument (the input tokens), one return (the output tokens). The input doesn't need to be valid Rust syntax. It can be SQL, HTML, a configuration language, or any sequence of tokens the macro knows how to parse.

This is the key differentiator. Derive and attribute macros receive valid Rust items. Function-like macros receive *whatever the caller puts inside the delimiters*. The macro is responsible for making sense of it.

## A Simple Example: env_config!

Let's build a macro that generates a config struct from environment variable definitions:

```rust
// config_macros/src/lib.rs
use proc_macro::TokenStream;
use quote::{quote, format_ident};
use syn::parse::{Parse, ParseStream};
use syn::{Ident, Token, LitStr, punctuated::Punctuated};

struct ConfigField {
    name: Ident,
    env_var: String,
}

impl Parse for ConfigField {
    fn parse(input: ParseStream) -> syn::Result<Self> {
        let name: Ident = input.parse()?;
        input.parse::<Token![=>]>()?;
        let env_var: LitStr = input.parse()?;
        Ok(ConfigField {
            name,
            env_var: env_var.value(),
        })
    }
}

struct ConfigInput {
    fields: Punctuated<ConfigField, Token![,]>,
}

impl Parse for ConfigInput {
    fn parse(input: ParseStream) -> syn::Result<Self> {
        let fields = Punctuated::parse_terminated(input)?;
        Ok(ConfigInput { fields })
    }
}

#[proc_macro]
pub fn env_config(input: TokenStream) -> TokenStream {
    let config = syn::parse_macro_input!(input as ConfigInput);

    let field_defs = config.fields.iter().map(|f| {
        let name = &f.name;
        quote! { pub #name: String }
    });

    let field_inits = config.fields.iter().map(|f| {
        let name = &f.name;
        let env_var = &f.env_var;
        quote! {
            #name: ::std::env::var(#env_var)
                .unwrap_or_else(|_| panic!("missing env var: {}", #env_var))
        }
    });

    let expanded = quote! {
        {
            #[derive(Debug)]
            struct EnvConfig {
                #(#field_defs,)*
            }

            impl EnvConfig {
                fn from_env() -> Self {
                    Self {
                        #(#field_inits,)*
                    }
                }
            }

            EnvConfig::from_env()
        }
    };

    TokenStream::from(expanded)
}
```

Usage:

```rust
use config_macros::env_config;

fn main() {
    // Set these before running, or this will panic
    std::env::set_var("APP_HOST", "localhost");
    std::env::set_var("APP_PORT", "8080");
    std::env::set_var("DATABASE_URL", "postgres://localhost/mydb");

    let config = env_config!(
        host => "APP_HOST",
        port => "APP_PORT",
        database_url => "DATABASE_URL",
    );

    println!("{:?}", config);
    // EnvConfig { host: "localhost", port: "8080", database_url: "postgres://localhost/mydb" }
}
```

The input `host => "APP_HOST"` isn't valid Rust. The macro defines its own grammar through the `Parse` implementation. `syn`'s `Punctuated` type handles comma-separated lists, and each `ConfigField` parses an identifier, `=>`, and a string literal.

## Parsing Custom Syntax

The real power of function-like macros is custom parsing. Let's build a `html!` macro that generates string output from a template-like syntax:

```rust
// html_macros/src/lib.rs
use proc_macro::TokenStream;
use quote::quote;
use syn::parse::{Parse, ParseStream};
use syn::{Ident, LitStr, Token, braced, token};

enum HtmlNode {
    Element {
        tag: String,
        children: Vec<HtmlNode>,
    },
    Text(String),
}

impl HtmlNode {
    fn to_tokens(&self) -> proc_macro2::TokenStream {
        match self {
            HtmlNode::Text(text) => {
                quote! { output.push_str(#text); }
            }
            HtmlNode::Element { tag, children } => {
                let open = format!("<{}>", tag);
                let close = format!("</{}>", tag);
                let child_tokens: Vec<_> = children.iter()
                    .map(|c| c.to_tokens())
                    .collect();
                quote! {
                    output.push_str(#open);
                    #(#child_tokens)*
                    output.push_str(#close);
                }
            }
        }
    }
}

impl Parse for HtmlNode {
    fn parse(input: ParseStream) -> syn::Result<Self> {
        if input.peek(LitStr) {
            let lit: LitStr = input.parse()?;
            return Ok(HtmlNode::Text(lit.value()));
        }

        let tag: Ident = input.parse()?;
        let content;
        braced!(content in input);

        let mut children = Vec::new();
        while !content.is_empty() {
            children.push(content.parse()?);
        }

        Ok(HtmlNode::Element {
            tag: tag.to_string(),
            children,
        })
    }
}

struct HtmlInput {
    nodes: Vec<HtmlNode>,
}

impl Parse for HtmlInput {
    fn parse(input: ParseStream) -> syn::Result<Self> {
        let mut nodes = Vec::new();
        while !input.is_empty() {
            nodes.push(input.parse()?);
        }
        Ok(HtmlInput { nodes })
    }
}

#[proc_macro]
pub fn html(input: TokenStream) -> TokenStream {
    let html_input = syn::parse_macro_input!(input as HtmlInput);

    let node_tokens: Vec<_> = html_input.nodes.iter()
        .map(|n| n.to_tokens())
        .collect();

    let expanded = quote! {
        {
            let mut output = String::new();
            #(#node_tokens)*
            output
        }
    };

    TokenStream::from(expanded)
}
```

Usage:

```rust
use html_macros::html;

fn main() {
    let page = html! {
        div {
            h1 { "Hello, World!" }
            p { "This is generated at compile time." }
            ul {
                li { "Item one" }
                li { "Item two" }
                li { "Item three" }
            }
        }
    };

    println!("{}", page);
    // <div><h1>Hello, World!</h1><p>This is generated at compile time.</p><ul><li>Item one</li><li>Item two</li><li>Item three</li></ul></div>
}
```

The input is a custom DSL — not Rust, not HTML, but something in between. The macro parses it into an AST (`HtmlNode`), then generates Rust string-building code from that AST.

## Compile-Time Validation

Function-like macros can validate their input and produce compile errors for invalid syntax. Here's a `regex!` macro that validates the regex pattern at compile time:

```rust
// regex_macros/src/lib.rs
use proc_macro::TokenStream;
use quote::quote;
use syn::{parse_macro_input, LitStr};

#[proc_macro]
pub fn checked_regex(input: TokenStream) -> TokenStream {
    let pattern = parse_macro_input!(input as LitStr);
    let pattern_str = pattern.value();

    // Validate the regex at compile time
    if let Err(e) = regex_syntax::parse(&pattern_str) {
        return syn::Error::new(
            pattern.span(),
            format!("invalid regex: {}", e),
        ).to_compile_error().into();
    }

    let expanded = quote! {
        ::regex::Regex::new(#pattern_str).unwrap()
    };

    TokenStream::from(expanded)
}
```

With this macro, `checked_regex!("[invalid")` produces a compile error pointing at the exact string literal, rather than panicking at runtime. The `regex_syntax` crate (which `regex` uses internally) provides the parser.

## Integrating with External Tools

This is where function-like macros get wild. Since they're regular Rust code running at compile time, they can:

- Read files from disk
- Connect to databases
- Call external programs
- Parse configuration files

Here's a macro that reads a JSON file at compile time and generates a struct:

```rust
// json_macros/src/lib.rs
use proc_macro::TokenStream;
use quote::{quote, format_ident};
use syn::{parse_macro_input, LitStr};
use std::collections::HashMap;

#[proc_macro]
pub fn json_struct(input: TokenStream) -> TokenStream {
    let file_path = parse_macro_input!(input as LitStr);
    let path = file_path.value();

    let content = match std::fs::read_to_string(&path) {
        Ok(c) => c,
        Err(e) => {
            return syn::Error::new(
                file_path.span(),
                format!("cannot read {}: {}", path, e),
            ).to_compile_error().into();
        }
    };

    let json: HashMap<String, serde_json::Value> = match serde_json::from_str(&content) {
        Ok(v) => v,
        Err(e) => {
            return syn::Error::new(
                file_path.span(),
                format!("invalid JSON: {}", e),
            ).to_compile_error().into();
        }
    };

    let field_defs = json.iter().map(|(key, val)| {
        let name = format_ident!("{}", key);
        let ty = match val {
            serde_json::Value::String(_) => quote! { String },
            serde_json::Value::Number(_) => quote! { f64 },
            serde_json::Value::Bool(_) => quote! { bool },
            _ => quote! { String },
        };
        quote! { pub #name: #ty }
    });

    let expanded = quote! {
        #[derive(Debug)]
        pub struct GeneratedConfig {
            #(#field_defs,)*
        }
    };

    TokenStream::from(expanded)
}
```

The macro reads a file, parses it as JSON, and generates a struct with fields matching the JSON keys. If the file doesn't exist or contains invalid JSON, you get a compile error. This is the same principle behind `include_str!` and `include_bytes!`, just more powerful.

## Function-Like vs. macro_rules!

Why would you use a function-like proc macro instead of `macro_rules!`?

**Arbitrary parsing.** `macro_rules!` can only match Rust token patterns. Proc macros can parse anything — SQL, HTML, TOML, custom DSLs. If your input isn't valid Rust tokens, you need a proc macro.

**Complex logic.** `macro_rules!` is limited to pattern substitution and recursion. Proc macros run regular Rust code — loops, conditionals, data structures, I/O, the whole language.

**Better error messages.** With `syn::Error`, you can produce precisely-located compiler errors. `macro_rules!` errors are often confusing and point at the wrong location.

**Identifier manipulation.** `format_ident!` lets you create new identifiers from strings. `macro_rules!` can't concatenate or transform identifier names.

The tradeoff: proc macros require a separate crate, add compile-time dependencies (`syn`, `quote`), and take longer to compile. For simple pattern-based macros, `macro_rules!` is still the right choice.

## Practical Tips

**Keep parsing and code generation separate.** Parse the input into an intermediate representation (structs, enums), then generate code from that representation. Don't try to generate code while parsing — it makes debugging much harder.

```rust
// Good: separate phases
fn parse_input(input: TokenStream) -> MyConfig { /* ... */ }
fn generate_code(config: &MyConfig) -> TokenStream { /* ... */ }

#[proc_macro]
pub fn my_macro(input: TokenStream) -> TokenStream {
    let config = parse_input(input);
    generate_code(&config)
}
```

**Test the parsing separately.** Since proc macro crates are regular Rust, you can write unit tests for your parser:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use syn::parse_str;

    #[test]
    fn test_parse_config_field() {
        let field: ConfigField = parse_str("host => \"APP_HOST\"").unwrap();
        assert_eq!(field.env_var, "APP_HOST");
    }
}
```

Note: you need to use `proc_macro2::TokenStream` instead of `proc_macro::TokenStream` in test contexts. The `proc_macro` types are only available during actual compilation.

**Handle empty input gracefully.** Always consider what happens when someone writes `my_macro!()` with no arguments. Panicking is rude — return a clear error.

**Avoid side effects where possible.** A macro that reads files is fine. A macro that writes files, makes network requests, or modifies global state is asking for trouble. Build systems assume compilation is deterministic and side-effect-free. Breaking that assumption leads to caching issues and flaky builds.

Next lesson: `syn` and `quote` in depth. We've been using them lightly — now it's time to understand the full API for parsing Rust syntax and generating code.
