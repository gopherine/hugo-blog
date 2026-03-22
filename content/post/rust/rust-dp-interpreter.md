---
title: "Lesson 10: Interpreter Pattern — DSLs and parsing"
author: Atharva Pandey
series: "Rust Design Patterns"
lesson: 10
keywords: [Rust, rust, design patterns, interpreter pattern, DSL, parsing, abstract syntax tree, enum, pattern matching]
tags: [Rust tutorial, rust, design-patterns, architecture]
date: '2025-10-22T12:00:00.000Z'
---

A few years ago I needed to let non-technical users define filtering rules for a data pipeline. The options were: embed Lua, use a YAML config with increasingly awkward syntax, or write a small domain-specific language. I picked the DSL. It took two days in Rust, and the result was a type-safe, sandboxed expression evaluator that couldn't crash the host program no matter what users typed. Try getting that guarantee with dynamic code execution in Python.

The Interpreter pattern is one of those GoF patterns that feels academic until you need it — and then it's exactly what you need. Rust's enums and pattern matching make it one of the most natural places to implement an interpreter.

## The Pattern

The Interpreter pattern defines a grammar as a set of types, then evaluates expressions by walking the tree. In OOP languages, each grammar rule is a class with an `interpret()` method. In Rust, each grammar rule is an enum variant, and `match` does the dispatch.

Let's build a filter expression language — the kind you'd embed in a config file or API:

```
age > 25 AND (status == "active" OR role == "admin")
```

## Step 1: Define the AST

The abstract syntax tree is just an enum:

```rust
#[derive(Debug, Clone)]
pub enum Expr {
    // Literals
    Integer(i64),
    Float(f64),
    Str(String),
    Bool(bool),

    // Variable reference
    Var(String),

    // Comparison operators
    Eq(Box<Expr>, Box<Expr>),
    NotEq(Box<Expr>, Box<Expr>),
    Gt(Box<Expr>, Box<Expr>),
    Lt(Box<Expr>, Box<Expr>),
    Gte(Box<Expr>, Box<Expr>),
    Lte(Box<Expr>, Box<Expr>),

    // Logical operators
    And(Box<Expr>, Box<Expr>),
    Or(Box<Expr>, Box<Expr>),
    Not(Box<Expr>),

    // Arithmetic
    Add(Box<Expr>, Box<Expr>),
    Sub(Box<Expr>, Box<Expr>),
    Mul(Box<Expr>, Box<Expr>),

    // Function call
    Call(String, Vec<Expr>),
}
```

Every expression node is a variant. Binary operators hold two boxed sub-expressions. This is a tree — and Rust enums are the most natural way to represent trees.

## Step 2: Values and the Environment

Expressions evaluate to values. Variables come from a context:

```rust
use std::collections::HashMap;

#[derive(Debug, Clone, PartialEq)]
pub enum Value {
    Integer(i64),
    Float(f64),
    Str(String),
    Bool(bool),
    Null,
}

impl std::fmt::Display for Value {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Value::Integer(n) => write!(f, "{}", n),
            Value::Float(n) => write!(f, "{}", n),
            Value::Str(s) => write!(f, "{}", s),
            Value::Bool(b) => write!(f, "{}", b),
            Value::Null => write!(f, "null"),
        }
    }
}

pub struct Environment {
    variables: HashMap<String, Value>,
    functions: HashMap<String, Box<dyn Fn(&[Value]) -> Result<Value, InterpretError>>>,
}

#[derive(Debug)]
pub enum InterpretError {
    UndefinedVariable(String),
    UndefinedFunction(String),
    TypeMismatch(String),
    DivisionByZero,
    ArityMismatch { name: String, expected: usize, got: usize },
}

impl std::fmt::Display for InterpretError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            InterpretError::UndefinedVariable(name) => {
                write!(f, "undefined variable: {}", name)
            }
            InterpretError::UndefinedFunction(name) => {
                write!(f, "undefined function: {}", name)
            }
            InterpretError::TypeMismatch(msg) => write!(f, "type mismatch: {}", msg),
            InterpretError::DivisionByZero => write!(f, "division by zero"),
            InterpretError::ArityMismatch { name, expected, got } => {
                write!(f, "{}() expects {} args, got {}", name, expected, got)
            }
        }
    }
}
```

## Step 3: The Evaluator

Here's where Rust's pattern matching shines. The entire interpreter is one recursive function:

```rust
impl Environment {
    pub fn new() -> Self {
        let mut env = Self {
            variables: HashMap::new(),
            functions: HashMap::new(),
        };
        env.register_builtins();
        env
    }

    pub fn set(&mut self, name: impl Into<String>, value: Value) {
        self.variables.insert(name.into(), value);
    }

    pub fn register_fn(
        &mut self,
        name: impl Into<String>,
        f: impl Fn(&[Value]) -> Result<Value, InterpretError> + 'static,
    ) {
        self.functions.insert(name.into(), Box::new(f));
    }

    fn register_builtins(&mut self) {
        self.register_fn("len", |args| {
            if args.len() != 1 {
                return Err(InterpretError::ArityMismatch {
                    name: "len".into(),
                    expected: 1,
                    got: args.len(),
                });
            }
            match &args[0] {
                Value::Str(s) => Ok(Value::Integer(s.len() as i64)),
                other => Err(InterpretError::TypeMismatch(
                    format!("len() expects string, got {:?}", other),
                )),
            }
        });

        self.register_fn("contains", |args| {
            if args.len() != 2 {
                return Err(InterpretError::ArityMismatch {
                    name: "contains".into(),
                    expected: 2,
                    got: args.len(),
                });
            }
            match (&args[0], &args[1]) {
                (Value::Str(haystack), Value::Str(needle)) => {
                    Ok(Value::Bool(haystack.contains(needle.as_str())))
                }
                _ => Err(InterpretError::TypeMismatch(
                    "contains() expects two strings".into(),
                )),
            }
        });

        self.register_fn("upper", |args| {
            if args.len() != 1 {
                return Err(InterpretError::ArityMismatch {
                    name: "upper".into(),
                    expected: 1,
                    got: args.len(),
                });
            }
            match &args[0] {
                Value::Str(s) => Ok(Value::Str(s.to_uppercase())),
                other => Err(InterpretError::TypeMismatch(
                    format!("upper() expects string, got {:?}", other),
                )),
            }
        });
    }

    pub fn evaluate(&self, expr: &Expr) -> Result<Value, InterpretError> {
        match expr {
            // Literals evaluate to themselves
            Expr::Integer(n) => Ok(Value::Integer(*n)),
            Expr::Float(n) => Ok(Value::Float(*n)),
            Expr::Str(s) => Ok(Value::Str(s.clone())),
            Expr::Bool(b) => Ok(Value::Bool(*b)),

            // Variable lookup
            Expr::Var(name) => self
                .variables
                .get(name)
                .cloned()
                .ok_or_else(|| InterpretError::UndefinedVariable(name.clone())),

            // Comparison
            Expr::Eq(lhs, rhs) => {
                let l = self.evaluate(lhs)?;
                let r = self.evaluate(rhs)?;
                Ok(Value::Bool(l == r))
            }
            Expr::NotEq(lhs, rhs) => {
                let l = self.evaluate(lhs)?;
                let r = self.evaluate(rhs)?;
                Ok(Value::Bool(l != r))
            }
            Expr::Gt(lhs, rhs) => self.compare_numeric(lhs, rhs, |a, b| a > b),
            Expr::Lt(lhs, rhs) => self.compare_numeric(lhs, rhs, |a, b| a < b),
            Expr::Gte(lhs, rhs) => self.compare_numeric(lhs, rhs, |a, b| a >= b),
            Expr::Lte(lhs, rhs) => self.compare_numeric(lhs, rhs, |a, b| a <= b),

            // Logical
            Expr::And(lhs, rhs) => {
                let l = self.evaluate_bool(lhs)?;
                if !l {
                    return Ok(Value::Bool(false)); // short-circuit
                }
                let r = self.evaluate_bool(rhs)?;
                Ok(Value::Bool(r))
            }
            Expr::Or(lhs, rhs) => {
                let l = self.evaluate_bool(lhs)?;
                if l {
                    return Ok(Value::Bool(true)); // short-circuit
                }
                let r = self.evaluate_bool(rhs)?;
                Ok(Value::Bool(r))
            }
            Expr::Not(inner) => {
                let val = self.evaluate_bool(inner)?;
                Ok(Value::Bool(!val))
            }

            // Arithmetic
            Expr::Add(lhs, rhs) => {
                self.arithmetic(lhs, rhs, |a, b| a + b, |a, b| a + b)
            }
            Expr::Sub(lhs, rhs) => {
                self.arithmetic(lhs, rhs, |a, b| a - b, |a, b| a - b)
            }
            Expr::Mul(lhs, rhs) => {
                self.arithmetic(lhs, rhs, |a, b| a * b, |a, b| a * b)
            }

            // Function call
            Expr::Call(name, args) => {
                let func = self
                    .functions
                    .get(name)
                    .ok_or_else(|| InterpretError::UndefinedFunction(name.clone()))?;

                let evaluated_args: Result<Vec<Value>, _> =
                    args.iter().map(|a| self.evaluate(a)).collect();

                func(&evaluated_args?)
            }
        }
    }

    fn evaluate_bool(&self, expr: &Expr) -> Result<bool, InterpretError> {
        match self.evaluate(expr)? {
            Value::Bool(b) => Ok(b),
            other => Err(InterpretError::TypeMismatch(
                format!("expected bool, got {:?}", other),
            )),
        }
    }

    fn compare_numeric(
        &self,
        lhs: &Expr,
        rhs: &Expr,
        cmp: impl Fn(f64, f64) -> bool,
    ) -> Result<Value, InterpretError> {
        let l = self.evaluate(lhs)?;
        let r = self.evaluate(rhs)?;
        match (&l, &r) {
            (Value::Integer(a), Value::Integer(b)) => {
                Ok(Value::Bool(cmp(*a as f64, *b as f64)))
            }
            (Value::Float(a), Value::Float(b)) => Ok(Value::Bool(cmp(*a, *b))),
            (Value::Integer(a), Value::Float(b)) => {
                Ok(Value::Bool(cmp(*a as f64, *b)))
            }
            (Value::Float(a), Value::Integer(b)) => {
                Ok(Value::Bool(cmp(*a, *b as f64)))
            }
            _ => Err(InterpretError::TypeMismatch(
                format!("cannot compare {:?} and {:?}", l, r),
            )),
        }
    }

    fn arithmetic(
        &self,
        lhs: &Expr,
        rhs: &Expr,
        int_op: impl Fn(i64, i64) -> i64,
        float_op: impl Fn(f64, f64) -> f64,
    ) -> Result<Value, InterpretError> {
        let l = self.evaluate(lhs)?;
        let r = self.evaluate(rhs)?;
        match (&l, &r) {
            (Value::Integer(a), Value::Integer(b)) => {
                Ok(Value::Integer(int_op(*a, *b)))
            }
            (Value::Float(a), Value::Float(b)) => {
                Ok(Value::Float(float_op(*a, *b)))
            }
            (Value::Integer(a), Value::Float(b)) => {
                Ok(Value::Float(float_op(*a as f64, *b)))
            }
            (Value::Float(a), Value::Integer(b)) => {
                Ok(Value::Float(float_op(*a, *b as f64)))
            }
            _ => Err(InterpretError::TypeMismatch(
                format!("cannot do arithmetic on {:?} and {:?}", l, r),
            )),
        }
    }
}
```

The entire evaluator is a `match` on the expression type. Each arm handles one grammar rule. Short-circuit evaluation, type checking, numeric coercion — it's all in one place. Compare this to the OOP Interpreter pattern where each node class has its own `interpret()` method scattered across a dozen files.

## Building Expressions (DSL Constructors)

Writing `Expr::And(Box::new(Expr::Gt(...)), Box::new(...))` is painful. Let's add builder functions:

```rust
impl Expr {
    pub fn var(name: &str) -> Self {
        Expr::Var(name.to_string())
    }

    pub fn int(n: i64) -> Self {
        Expr::Integer(n)
    }

    pub fn string(s: &str) -> Self {
        Expr::Str(s.to_string())
    }

    pub fn gt(self, other: Expr) -> Self {
        Expr::Gt(Box::new(self), Box::new(other))
    }

    pub fn eq(self, other: Expr) -> Self {
        Expr::Eq(Box::new(self), Box::new(other))
    }

    pub fn and(self, other: Expr) -> Self {
        Expr::And(Box::new(self), Box::new(other))
    }

    pub fn or(self, other: Expr) -> Self {
        Expr::Or(Box::new(self), Box::new(other))
    }

    pub fn not(self) -> Self {
        Expr::Not(Box::new(self))
    }

    pub fn call(name: &str, args: Vec<Expr>) -> Self {
        Expr::Call(name.to_string(), args)
    }
}
```

Now the expression from earlier reads almost like the DSL itself:

```rust
fn main() {
    // age > 25 AND (status == "active" OR role == "admin")
    let filter = Expr::var("age")
        .gt(Expr::int(25))
        .and(
            Expr::var("status")
                .eq(Expr::string("active"))
                .or(Expr::var("role").eq(Expr::string("admin"))),
        );

    let mut env = Environment::new();
    env.set("age", Value::Integer(30));
    env.set("status", Value::Str("inactive".into()));
    env.set("role", Value::Str("admin".into()));

    match env.evaluate(&filter) {
        Ok(result) => println!("Filter result: {}", result),
        Err(e) => println!("Error: {}", e),
    }

    // Using built-in functions
    // contains(email, "@company.com") AND len(name) > 2
    let email_filter = Expr::call("contains", vec![
        Expr::var("email"),
        Expr::string("@company.com"),
    ])
    .and(
        Expr::call("len", vec![Expr::var("name")])
            .gt(Expr::int(2)),
    );

    env.set("email", Value::Str("alice@company.com".into()));
    env.set("name", Value::Str("Alice".into()));

    match env.evaluate(&email_filter) {
        Ok(result) => println!("Email filter: {}", result),
        Err(e) => println!("Error: {}", e),
    }
}
```

## A Simple Parser

For a real DSL, you'd parse text into the AST. Here's a minimal recursive descent parser:

```rust
pub struct Parser {
    tokens: Vec<Token>,
    pos: usize,
}

#[derive(Debug, Clone, PartialEq)]
enum Token {
    Ident(String),
    Integer(i64),
    StringLit(String),
    Eq,       // ==
    NotEq,    // !=
    Gt,       // >
    Lt,       // <
    Gte,      // >=
    Lte,      // <=
    And,      // AND
    Or,       // OR
    Not,      // NOT
    LParen,
    RParen,
    Comma,
    Eof,
}

impl Parser {
    pub fn parse(input: &str) -> Result<Expr, String> {
        let tokens = Self::tokenize(input)?;
        let mut parser = Parser { tokens, pos: 0 };
        let expr = parser.parse_or()?;
        if parser.peek() != &Token::Eof {
            return Err(format!("unexpected token: {:?}", parser.peek()));
        }
        Ok(expr)
    }

    fn tokenize(input: &str) -> Result<Vec<Token>, String> {
        let mut tokens = Vec::new();
        let mut chars = input.chars().peekable();

        while let Some(&ch) = chars.peek() {
            match ch {
                ' ' | '\t' | '\n' => { chars.next(); }
                '(' => { tokens.push(Token::LParen); chars.next(); }
                ')' => { tokens.push(Token::RParen); chars.next(); }
                ',' => { tokens.push(Token::Comma); chars.next(); }
                '>' => {
                    chars.next();
                    if chars.peek() == Some(&'=') {
                        chars.next();
                        tokens.push(Token::Gte);
                    } else {
                        tokens.push(Token::Gt);
                    }
                }
                '<' => {
                    chars.next();
                    if chars.peek() == Some(&'=') {
                        chars.next();
                        tokens.push(Token::Lte);
                    } else {
                        tokens.push(Token::Lt);
                    }
                }
                '=' => {
                    chars.next();
                    if chars.next() != Some('=') {
                        return Err("expected '=='".into());
                    }
                    tokens.push(Token::Eq);
                }
                '!' => {
                    chars.next();
                    if chars.next() != Some('=') {
                        return Err("expected '!='".into());
                    }
                    tokens.push(Token::NotEq);
                }
                '"' => {
                    chars.next();
                    let mut s = String::new();
                    while let Some(&c) = chars.peek() {
                        if c == '"' { break; }
                        s.push(c);
                        chars.next();
                    }
                    if chars.next() != Some('"') {
                        return Err("unterminated string".into());
                    }
                    tokens.push(Token::StringLit(s));
                }
                c if c.is_ascii_digit() => {
                    let mut num = String::new();
                    while let Some(&c) = chars.peek() {
                        if !c.is_ascii_digit() { break; }
                        num.push(c);
                        chars.next();
                    }
                    let n: i64 = num.parse().map_err(|e| format!("{}", e))?;
                    tokens.push(Token::Integer(n));
                }
                c if c.is_ascii_alphabetic() || c == '_' => {
                    let mut ident = String::new();
                    while let Some(&c) = chars.peek() {
                        if !c.is_ascii_alphanumeric() && c != '_' { break; }
                        ident.push(c);
                        chars.next();
                    }
                    match ident.as_str() {
                        "AND" | "and" => tokens.push(Token::And),
                        "OR" | "or" => tokens.push(Token::Or),
                        "NOT" | "not" => tokens.push(Token::Not),
                        "true" => tokens.push(Token::Integer(1)),
                        "false" => tokens.push(Token::Integer(0)),
                        _ => tokens.push(Token::Ident(ident)),
                    }
                }
                other => return Err(format!("unexpected character: '{}'", other)),
            }
        }
        tokens.push(Token::Eof);
        Ok(tokens)
    }

    fn peek(&self) -> &Token {
        &self.tokens[self.pos]
    }

    fn advance(&mut self) -> &Token {
        let tok = &self.tokens[self.pos];
        self.pos += 1;
        tok
    }

    fn parse_or(&mut self) -> Result<Expr, String> {
        let mut left = self.parse_and()?;
        while self.peek() == &Token::Or {
            self.advance();
            let right = self.parse_and()?;
            left = Expr::Or(Box::new(left), Box::new(right));
        }
        Ok(left)
    }

    fn parse_and(&mut self) -> Result<Expr, String> {
        let mut left = self.parse_comparison()?;
        while self.peek() == &Token::And {
            self.advance();
            let right = self.parse_comparison()?;
            left = Expr::And(Box::new(left), Box::new(right));
        }
        Ok(left)
    }

    fn parse_comparison(&mut self) -> Result<Expr, String> {
        let left = self.parse_primary()?;

        let op = match self.peek() {
            Token::Eq => Some(Expr::Eq as fn(Box<Expr>, Box<Expr>) -> Expr),
            Token::NotEq => Some(Expr::NotEq as fn(Box<Expr>, Box<Expr>) -> Expr),
            Token::Gt => Some(Expr::Gt as fn(Box<Expr>, Box<Expr>) -> Expr),
            Token::Lt => Some(Expr::Lt as fn(Box<Expr>, Box<Expr>) -> Expr),
            Token::Gte => Some(Expr::Gte as fn(Box<Expr>, Box<Expr>) -> Expr),
            Token::Lte => Some(Expr::Lte as fn(Box<Expr>, Box<Expr>) -> Expr),
            _ => None,
        };

        if let Some(constructor) = op {
            self.advance();
            let right = self.parse_primary()?;
            Ok(constructor(Box::new(left), Box::new(right)))
        } else {
            Ok(left)
        }
    }

    fn parse_primary(&mut self) -> Result<Expr, String> {
        match self.peek().clone() {
            Token::Integer(n) => {
                self.advance();
                Ok(Expr::Integer(n))
            }
            Token::StringLit(s) => {
                self.advance();
                Ok(Expr::Str(s))
            }
            Token::Ident(name) => {
                self.advance();
                // Check if it's a function call
                if self.peek() == &Token::LParen {
                    self.advance(); // consume '('
                    let mut args = Vec::new();
                    if self.peek() != &Token::RParen {
                        args.push(self.parse_or()?);
                        while self.peek() == &Token::Comma {
                            self.advance();
                            args.push(self.parse_or()?);
                        }
                    }
                    if self.peek() != &Token::RParen {
                        return Err("expected ')'".into());
                    }
                    self.advance();
                    Ok(Expr::Call(name, args))
                } else {
                    Ok(Expr::Var(name))
                }
            }
            Token::Not => {
                self.advance();
                let inner = self.parse_comparison()?;
                Ok(Expr::Not(Box::new(inner)))
            }
            Token::LParen => {
                self.advance();
                let expr = self.parse_or()?;
                if self.peek() != &Token::RParen {
                    return Err("expected ')'".into());
                }
                self.advance();
                Ok(expr)
            }
            other => Err(format!("unexpected token: {:?}", other)),
        }
    }
}
```

Now users can write filter expressions as strings:

```rust
fn main() {
    let filter = Parser::parse(
        r#"age > 25 AND (status == "active" OR role == "admin")"#
    ).unwrap();

    let mut env = Environment::new();
    env.set("age", Value::Integer(30));
    env.set("status", Value::Str("inactive".into()));
    env.set("role", Value::Str("admin".into()));

    println!("Result: {}", env.evaluate(&filter).unwrap());

    // Function calls work too
    let filter2 = Parser::parse(
        r#"contains(email, "@company.com") AND len(name) > 2"#
    ).unwrap();

    env.set("email", Value::Str("bob@company.com".into()));
    env.set("name", Value::Str("Bob".into()));

    println!("Result: {}", env.evaluate(&filter2).unwrap());
}
```

## Why Rust Enums Beat OOP Classes Here

In Java's Interpreter pattern, you'd have an `Expression` interface, a `BinaryExpression` abstract class, `AndExpression`, `OrExpression`, `ComparisonExpression`, `LiteralExpression`... each with its own `interpret()` method scattered across a dozen files.

Rust puts the entire grammar in one enum and the entire evaluation in one function. Adding a new expression type means adding an enum variant and a match arm — both in one place. The compiler won't let you forget the match arm because `match` is exhaustive.

This is the expression problem in action. OOP makes it easy to add new types (new class) but hard to add new operations (modify every class). Enums make it easy to add new operations (new function over the enum) but hard to add new types (modify every match). For interpreters, operations (pretty-print, optimize, type-check, evaluate) outnumber types, so enums win.

The Interpreter pattern in Rust is where enums, pattern matching, and recursive types converge into something that feels tailor-made for the language. If you ever need a config language, a query DSL, or a rule engine, this is your blueprint.
