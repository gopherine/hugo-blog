---
title: "Lesson 2: Parsing — Tokens to AST, recursive descent"
author: Atharva Pandey
series: "Compilers and Interpreters"
lesson: 2
keywords:
  - parser
  - recursive descent
  - AST
  - abstract syntax tree
  - parsing
  - Pratt parser
  - compiler frontend
tags:
  - fundamentals
  - compilers
date: "2024-07-25T00:00:00.000Z"
---

The lexer gave us tokens. The tokens are still flat — a sequence with no hierarchy. The expression `1 + 2 * 3` produces six tokens, but it does not yet say that `2 * 3` should be computed before adding `1`. That grouping, that hierarchy, is what the parser builds. By the time the parser is done, we have a tree. A specific kind of tree: an Abstract Syntax Tree, or AST. Everything after the parser works on this tree: evaluation, type checking, code generation, optimization. The tree is the program.

This lesson builds a recursive descent parser with Pratt-style operator precedence for the same small language from Lesson 1.

## The Problem

Consider how `1 + 2 * 3` should parse. Mathematically, multiplication binds more tightly than addition, so the result should be `1 + (2 * 3) = 7`, not `(1 + 2) * 3 = 9`. This precedence relationship is not encoded in the tokens — the tokens are just `INT PLUS INT ASTERISK INT`. The parser must know that `*` has higher precedence than `+` and structure the tree accordingly.

Naive approaches — just matching token sequences with if-chains — break down quickly. What about parentheses that override precedence? What about right-associative operators like exponentiation (`2 ^ 3 ^ 2` is `2 ^ (3 ^ 2)` not `(2 ^ 3) ^ 2`)? What about prefix vs infix vs postfix positions for operators? A flat token scanner cannot handle these cleanly.

Recursive descent parsing and Pratt parsing together handle all of this elegantly with small, composable functions.

## How It Works

**The AST types** represent the structure of the program:

```go
type Node interface {
    TokenLiteral() string
}

type Statement interface {
    Node
    statementNode()
}

type Expression interface {
    Node
    expressionNode()
}

// --- Expressions ---

type IntegerLiteral struct {
    Token Token
    Value int64
}
func (il *IntegerLiteral) expressionNode() {}
func (il *IntegerLiteral) TokenLiteral() string { return il.Token.Literal }

type Identifier struct {
    Token Token
    Value string
}
func (i *Identifier) expressionNode() {}
func (i *Identifier) TokenLiteral() string { return i.Token.Literal }

type InfixExpression struct {
    Token    Token // the operator token
    Left     Expression
    Operator string
    Right    Expression
}
func (ie *InfixExpression) expressionNode() {}
func (ie *InfixExpression) TokenLiteral() string { return ie.Token.Literal }

type PrefixExpression struct {
    Token    Token
    Operator string
    Right    Expression
}
func (pe *PrefixExpression) expressionNode() {}
func (pe *PrefixExpression) TokenLiteral() string { return pe.Token.Literal }

// --- Statements ---

type LetStatement struct {
    Token Token // the LET token
    Name  *Identifier
    Value Expression
}
func (ls *LetStatement) statementNode() {}
func (ls *LetStatement) TokenLiteral() string { return ls.Token.Literal }

type Program struct {
    Statements []Statement
}
```

**Pratt parsing** assigns each token type two functions and a precedence:

- A **prefix parse function** — called when the token appears at the start of an expression (e.g., `-5` uses the minus as a prefix, integer literals and identifiers are prefixes by definition).
- An **infix parse function** — called when the token appears between two expressions (e.g., `+`, `*`, `==`).

Precedence levels:

```go
const (
    _ int = iota
    LOWEST
    EQUALS      // ==
    LESSGREATER // < or >
    SUM         // +
    PRODUCT     // *
    PREFIX      // -x or !x
    CALL        // fn(x)
)

var precedences = map[TokenType]int{
    EQ:       EQUALS,
    NEQ:      EQUALS,
    LT:       LESSGREATER,
    GT:       LESSGREATER,
    PLUS:     SUM,
    MINUS:    SUM,
    SLASH:    PRODUCT,
    ASTERISK: PRODUCT,
}
```

The parser:

```go
type Parser struct {
    l         *Lexer
    cur       Token
    peek      Token
    errors    []string
    prefixFns map[TokenType]prefixParseFn
    infixFns  map[TokenType]infixParseFn
}

type prefixParseFn func() Expression
type infixParseFn func(Expression) Expression

func NewParser(l *Lexer) *Parser {
    p := &Parser{l: l}
    p.prefixFns = map[TokenType]prefixParseFn{
        IDENT:  p.parseIdentifier,
        INT:    p.parseIntegerLiteral,
        BANG:   p.parsePrefixExpression,
        MINUS:  p.parsePrefixExpression,
        LPAREN: p.parseGroupedExpression,
    }
    p.infixFns = map[TokenType]infixParseFn{
        PLUS:     p.parseInfixExpression,
        MINUS:    p.parseInfixExpression,
        SLASH:    p.parseInfixExpression,
        ASTERISK: p.parseInfixExpression,
        EQ:       p.parseInfixExpression,
        NEQ:      p.parseInfixExpression,
        LT:       p.parseInfixExpression,
        GT:       p.parseInfixExpression,
    }
    // prime both cur and peek
    p.nextToken()
    p.nextToken()
    return p
}
```

The heart of Pratt parsing — `parseExpression`:

```go
func (p *Parser) parseExpression(precedence int) Expression {
    prefix := p.prefixFns[p.cur.Type]
    if prefix == nil {
        p.errors = append(p.errors, fmt.Sprintf(
            "no prefix parse function for %s found", p.cur.Type))
        return nil
    }
    leftExp := prefix()

    for p.peek.Type != SEMICOLON && precedence < p.peekPrecedence() {
        infix := p.infixFns[p.peek.Type]
        if infix == nil {
            return leftExp
        }
        p.nextToken()
        leftExp = infix(leftExp)
    }
    return leftExp
}

func (p *Parser) peekPrecedence() int {
    if prec, ok := precedences[p.peek.Type]; ok {
        return prec
    }
    return LOWEST
}

func (p *Parser) curPrecedence() int {
    if prec, ok := precedences[p.cur.Type]; ok {
        return prec
    }
    return LOWEST
}
```

The key insight: `parseExpression` loops as long as the *next* operator has higher precedence than the current context. This is what makes `1 + 2 * 3` parse as `1 + (2 * 3)` — when we are parsing the `+` expression with precedence `SUM`, we see `*` with precedence `PRODUCT`. Since `PRODUCT > SUM`, we continue the loop and let `2 * 3` bind first.

Parsing infix and prefix:

```go
func (p *Parser) parseInfixExpression(left Expression) Expression {
    expr := &InfixExpression{
        Token:    p.cur,
        Operator: p.cur.Literal,
        Left:     left,
    }
    precedence := p.curPrecedence()
    p.nextToken()
    expr.Right = p.parseExpression(precedence)
    return expr
}

func (p *Parser) parsePrefixExpression() Expression {
    expr := &PrefixExpression{Token: p.cur, Operator: p.cur.Literal}
    p.nextToken()
    expr.Right = p.parseExpression(PREFIX)
    return expr
}

func (p *Parser) parseGroupedExpression() Expression {
    p.nextToken()
    exp := p.parseExpression(LOWEST)
    if !p.expectPeek(RPAREN) {
        return nil
    }
    return exp
}
```

Parenthesized expressions are just prefix expressions that consume their interior and then expect a closing paren. The grouped content is parsed with `LOWEST` precedence — everything inside the parens binds tightly to each other before returning.

## In Practice

**Parsing `let x = 10 + y;`:**

```go
func (p *Parser) parseLetStatement() *LetStatement {
    stmt := &LetStatement{Token: p.cur}
    if !p.expectPeek(IDENT) {
        return nil
    }
    stmt.Name = &Identifier{Token: p.cur, Value: p.cur.Literal}
    if !p.expectPeek(ASSIGN) {
        return nil
    }
    p.nextToken()
    stmt.Value = p.parseExpression(LOWEST)
    if p.peek.Type == SEMICOLON {
        p.nextToken()
    }
    return stmt
}
```

`expectPeek` advances only if the next token matches:

```go
func (p *Parser) expectPeek(t TokenType) bool {
    if p.peek.Type == t {
        p.nextToken()
        return true
    }
    p.errors = append(p.errors, fmt.Sprintf(
        "expected next token to be %s, got %s instead",
        t, p.peek.Type))
    return false
}
```

**The AST for `1 + 2 * 3`:**

```
InfixExpression(+)
├── IntegerLiteral(1)
└── InfixExpression(*)
    ├── IntegerLiteral(2)
    └── IntegerLiteral(3)
```

Evaluating this tree gives 7, as expected.

**Testing.** Parse a string and assert on the structure of the resulting AST. I test each construct (let statements, infix expressions, prefix expressions, grouped expressions) in isolation:

```go
func TestInfixExpression(t *testing.T) {
    tests := []struct {
        input    string
        left     int64
        op       string
        right    int64
    }{
        {"5 + 6;", 5, "+", 6},
        {"5 - 6;", 5, "-", 6},
        {"5 * 6;", 5, "*", 6},
    }
    for _, tt := range tests {
        p := NewParser(New(tt.input))
        prog := p.ParseProgram()
        // assert len(prog.Statements) == 1
        // assert infix.Left == tt.left, infix.Operator == tt.op, etc.
    }
}
```

## The Gotchas

**Error recovery is the hard part.** A good parser does not stop at the first error — it recovers and continues parsing so it can report multiple errors in one pass. The simplest recovery strategy for statement-based languages is to skip tokens until you find a synchronization point (a semicolon, a newline, the next keyword). Implement a `synchronize()` function that advances until a safe restart point.

**Precedence tables must be exact.** Getting a precedence level wrong by one means your expressions parse incorrectly. Test the edge cases: `1 + 2 + 3` (left-associative, should be `(1+2)+3`), `!-5` (prefix chains), `(1+2)*3` (groups override precedence), `-2 * -3`.

**`nil` propagation in error cases.** When a parse function returns `nil` (due to an error), callers must check before dereferencing. It is tempting to elide nil checks in the happy path, but they bite during error recovery. Defensive nil checks throughout the expression builder keep the parser from crashing on bad input.

**Adding new syntax is additive.** To add a new operator or construct, you register a new prefix or infix function. The `parseExpression` loop does not change. This is what makes Pratt parsing extensible — you do not rewrite the core algorithm to add `++`, call expressions, or index expressions.

## Key Takeaway

A recursive descent parser with Pratt-style operator precedence builds an AST from tokens by associating parse functions with token types and using a precedence loop to handle operator binding correctly. The design is extensible: adding new syntax means adding new parse functions and precedence entries, not restructuring the core. Error accumulation over error panicking keeps the parser usable on real, imperfect input. With a working parser, you have a tree — and a tree is something you can walk, transform, and evaluate.

---

**Previous:** [Lesson 1: Lexing — Turning text into tokens](/post/fundamentals/compiler-lexing/)

**Next:** [Lesson 3: AST and Evaluation — Walking the tree to compute results](/post/fundamentals/compiler-ast-eval/)
