---
title: "Lesson 1: Lexing — Turning text into tokens"
author: Atharva Pandey
series: "Compilers and Interpreters"
lesson: 1
keywords:
  - lexer
  - tokenizer
  - lexical analysis
  - compiler frontend
  - scanner
  - tokens
  - compilers
  - interpreters
tags:
  - fundamentals
  - compilers
date: "2024-05-06T00:00:00.000Z"
---

I wrote my first lexer as a side project after reading the first chapter of "Writing an Interpreter in Go." I expected it to be hard. It was not. A lexer is conceptually one of the simpler pieces of a compiler: read characters, group them into meaningful chunks, throw away whitespace. What surprised me was how much clarity it brought to everything downstream. Once I had tokens instead of characters, every subsequent step became easier to reason about. The text became structured. And the structure was entirely my design.

This lesson builds a lexer from scratch for a small expression language. No libraries, no generators, just a loop and a switch.

## The Problem

A computer program is text. A string of characters. Before you can do anything meaningful with it — parse it into a tree, evaluate it, compile it to machine code — you need to transform that flat string into something your program can reason about structurally.

Consider the expression: `let x = 10 + y;`

To a human this is obvious. To a program, it is forty-something bytes. Which characters form the keyword `let`? Where does the identifier `x` end? Is `10` one token or two? Is `+` an operator or part of `+=`? The lexer answers all of these questions. Its job is to turn the raw character stream into a sequence of *tokens* — labelled chunks with meaning.

Without a lexer, every subsequent stage of the compiler would need to parse characters directly. The parser would spend half its logic handling whitespace, string boundaries, and multi-character operators. The lexer does that work once, cleanly, so nothing else has to.

## How It Works

A lexer is a state machine. It reads characters one at a time and decides which state it is in: reading an identifier, reading a number, reading a string literal, skipping whitespace. When it finishes a token, it emits it and resets.

Start with a token type:

```go
type TokenType string

const (
    // Literals
    INT    TokenType = "INT"
    IDENT  TokenType = "IDENT"
    STRING TokenType = "STRING"

    // Keywords
    LET    TokenType = "LET"
    IF     TokenType = "IF"
    ELSE   TokenType = "ELSE"
    RETURN TokenType = "RETURN"

    // Operators and delimiters
    ASSIGN   TokenType = "="
    PLUS     TokenType = "+"
    MINUS    TokenType = "-"
    ASTERISK TokenType = "*"
    SLASH    TokenType = "/"
    BANG     TokenType = "!"
    EQ       TokenType = "=="
    NEQ      TokenType = "!="
    LT       TokenType = "<"
    GT       TokenType = ">"
    LPAREN   TokenType = "("
    RPAREN   TokenType = ")"
    LBRACE   TokenType = "{"
    RBRACE   TokenType = "}"
    SEMICOLON TokenType = ";"
    COMMA    TokenType = ","

    // Special
    EOF     TokenType = "EOF"
    ILLEGAL TokenType = "ILLEGAL"
)

type Token struct {
    Type    TokenType
    Literal string
    Line    int // for error messages
    Col     int
}
```

The `Literal` field holds the actual text: for an INT token, it holds `"42"`; for an IDENT token, it holds `"myVar"`. This lets later stages recover the original text when needed.

Now the lexer itself:

```go
type Lexer struct {
    input        string
    pos          int  // current reading position
    readPos      int  // next position (lookahead)
    ch           byte // current character
    line, col    int
}

func New(input string) *Lexer {
    l := &Lexer{input: input, line: 1, col: 0}
    l.readChar() // prime the pump
    return l
}

func (l *Lexer) readChar() {
    if l.readPos >= len(l.input) {
        l.ch = 0 // NUL == EOF
    } else {
        l.ch = l.input[l.readPos]
    }
    l.pos = l.readPos
    l.readPos++
    if l.ch == '\n' {
        l.line++
        l.col = 0
    } else {
        l.col++
    }
}

func (l *Lexer) peekChar() byte {
    if l.readPos >= len(l.input) {
        return 0
    }
    return l.input[l.readPos]
}
```

The `pos`/`readPos` pair is a one-character lookahead. `pos` is where we are; `readPos` is where we will be. This lookahead lets us distinguish `=` (assign) from `==` (equals), `!` from `!=`.

The main method:

```go
func (l *Lexer) NextToken() Token {
    l.skipWhitespace()

    tok := Token{Line: l.line, Col: l.col}

    switch l.ch {
    case '=':
        if l.peekChar() == '=' {
            l.readChar()
            tok = Token{Type: EQ, Literal: "=="}
        } else {
            tok = Token{Type: ASSIGN, Literal: "="}
        }
    case '!':
        if l.peekChar() == '=' {
            l.readChar()
            tok = Token{Type: NEQ, Literal: "!="}
        } else {
            tok = Token{Type: BANG, Literal: "!"}
        }
    case '+': tok = Token{Type: PLUS, Literal: "+"}
    case '-': tok = Token{Type: MINUS, Literal: "-"}
    case '*': tok = Token{Type: ASTERISK, Literal: "*"}
    case '/': tok = Token{Type: SLASH, Literal: "/"}
    case '<': tok = Token{Type: LT, Literal: "<"}
    case '>': tok = Token{Type: GT, Literal: ">"}
    case '(': tok = Token{Type: LPAREN, Literal: "("}
    case ')': tok = Token{Type: RPAREN, Literal: ")"}
    case '{': tok = Token{Type: LBRACE, Literal: "{"}
    case '}': tok = Token{Type: RBRACE, Literal: "}"}
    case ';': tok = Token{Type: SEMICOLON, Literal: ";"}
    case ',': tok = Token{Type: COMMA, Literal: ","}
    case 0:   tok = Token{Type: EOF, Literal: ""}
    default:
        if isLetter(l.ch) {
            tok.Literal = l.readIdentifier()
            tok.Type = lookupIdent(tok.Literal) // keyword or IDENT?
            return tok // already advanced past the token
        } else if isDigit(l.ch) {
            tok.Literal = l.readNumber()
            tok.Type = INT
            return tok
        } else {
            tok = Token{Type: ILLEGAL, Literal: string(l.ch)}
        }
    }

    l.readChar()
    return tok
}

func (l *Lexer) skipWhitespace() {
    for l.ch == ' ' || l.ch == '\t' || l.ch == '\n' || l.ch == '\r' {
        l.readChar()
    }
}

func (l *Lexer) readIdentifier() string {
    start := l.pos
    for isLetter(l.ch) || isDigit(l.ch) {
        l.readChar()
    }
    return l.input[start:l.pos]
}

func (l *Lexer) readNumber() string {
    start := l.pos
    for isDigit(l.ch) {
        l.readChar()
    }
    return l.input[start:l.pos]
}

func isLetter(ch byte) bool {
    return (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || ch == '_'
}

func isDigit(ch byte) bool { return ch >= '0' && ch <= '9' }

var keywords = map[string]TokenType{
    "let": LET, "if": IF, "else": ELSE, "return": RETURN,
}

func lookupIdent(ident string) TokenType {
    if tt, ok := keywords[ident]; ok {
        return tt
    }
    return IDENT
}
```

Running the lexer over `let x = 10 + y;` produces:

```
LET "let"
IDENT "x"
= "="
INT "10"
+ "+"
IDENT "y"
; ";"
EOF ""
```

## In Practice

**Error reporting uses line and column.** I always store `Line` and `Col` on every token. When the parser later reports a syntax error, it can say "unexpected token on line 12, column 4" instead of just "unexpected token." Users need this. Without position information, debugging is painful.

**Tokenizing lazily versus eagerly.** The lexer above is lazy — it produces one token at a time via `NextToken()`. You could also scan the entire input upfront and return a `[]Token`. Lazy is better for large inputs and streaming use cases; eager is simpler to test. For a small interpreter, eager is fine. I usually start eager and switch to lazy if it matters.

**String literals require escape handling.** The code above does not handle strings. A real lexer needs to handle `"hello\nworld"` — read until the closing quote, unescape escape sequences, return the content between quotes as the literal. This is more code but not more complexity; it is the same character-by-character loop with extra cases.

**Testing a lexer is easy.** For each test case, provide input and expected `[]Token`. The lexer should be a pure function of the input. No mocking, no setup. This is a great place to test exhaustively:

```go
func TestLexer(t *testing.T) {
    input := "let x = 10 + y;"
    expected := []Token{
        {LET, "let", 1, 1},
        {IDENT, "x", 1, 5},
        {ASSIGN, "=", 1, 7},
        {INT, "10", 1, 9},
        {PLUS, "+", 1, 12},
        {IDENT, "y", 1, 14},
        {SEMICOLON, ";", 1, 15},
        {EOF, "", 1, 16},
    }
    l := New(input)
    for i, exp := range expected {
        got := l.NextToken()
        if got != exp {
            t.Errorf("token %d: got %+v, want %+v", i, got, exp)
        }
    }
}
```

## The Gotchas

**The ILLEGAL token is your friend.** Do not panic or return an error when you see an unexpected character. Emit an `ILLEGAL` token and keep going. This lets the parser accumulate multiple errors instead of stopping at the first bad character.

**Unicode is harder than ASCII.** The lexer above works on bytes, which means it handles ASCII-only source. Supporting Unicode identifiers (common in modern languages) requires working with `rune` instead of `byte`, handling multi-byte UTF-8 sequences, and potentially using `unicode.IsLetter`. For a learning project, ASCII is fine. For production code, use `bufio.Scanner` with rune-mode.

**Avoid backtracking.** The one-character lookahead in `peekChar` covers almost all cases without backtracking. Lexers that backtrack — rewinding the read position — get complicated fast. The `pos`/`readPos` design makes peek free and keeps the complexity low.

**Comments need a decision.** Are comments tokens or whitespace? If you want tooling (formatters, documentation generators) to preserve comments, they should be tokens. If you just want an interpreter, skip them like whitespace. I usually make them skippable first and add them as tokens later if needed.

## Key Takeaway

A lexer transforms raw text into a flat sequence of tokens, each with a type, a literal value, and a source position. The implementation is a loop with a switch on the current character and one character of lookahead. Keep token types simple, store source positions for error messages, emit ILLEGAL tokens rather than panicking, and test with table-driven tests over representative input. Once you have tokens, the parser has something structured to work with — and that is where the real fun starts.

---

**Next:** [Lesson 2: Parsing — Tokens to AST, recursive descent](/post/fundamentals/compiler-parsing/)
