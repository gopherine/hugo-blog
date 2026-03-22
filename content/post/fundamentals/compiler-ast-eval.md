---
title: "Lesson 3: AST and Evaluation — Walking the tree to compute results"
author: Atharva Pandey
series: "Compilers and Interpreters"
lesson: 3
keywords:
  - AST evaluation
  - tree walking interpreter
  - environment
  - closures
  - interpreter
  - AST walk
  - compilers
tags:
  - fundamentals
  - compilers
date: "2024-10-03T00:00:00.000Z"
---

After the lexer and parser, we have a tree. A beautiful, hierarchical, unambiguous representation of the program. Now we need to make it *do something*. The simplest way to execute a program from its AST is to walk the tree recursively and compute results as you go. No intermediate representation, no bytecode, no machine code — just a recursive function that pattern-matches on node types and returns values.

This is a tree-walking interpreter. It is not the fastest approach (we cover bytecode and code generation in Lesson 4), but it is the most direct. Many production language implementations started here — and some, like early Ruby and Python, stayed here for a long time.

## The Problem

We have a tree like this for `let x = 5; x + 3`:

```
Program
├── LetStatement(x = IntegerLiteral(5))
└── ExpressionStatement
    └── InfixExpression(+)
        ├── Identifier(x)
        └── IntegerLiteral(3)
```

To evaluate this, we need to:
1. Evaluate the `LetStatement`: compute `5`, store it under the name `x`.
2. Evaluate the `ExpressionStatement`: evaluate the infix expression.
3. Evaluate the `InfixExpression(+)`: look up `x` (gets `5`), evaluate `3` (gets `3`), compute `5 + 3`.
4. Return `8`.

The challenge is state. The value of `x` needs to live somewhere between the let statement and the identifier lookup. That somewhere is an **environment** — a mapping from names to values. Managing environments correctly is what gives us variable scope, closures, and functions.

## How It Works

**The value system.** The evaluator returns values — Go types that represent the language's runtime values:

```go
type ObjectType string

const (
    INTEGER_OBJ  ObjectType = "INTEGER"
    BOOLEAN_OBJ  ObjectType = "BOOLEAN"
    NULL_OBJ     ObjectType = "NULL"
    RETURN_OBJ   ObjectType = "RETURN_VALUE"
    ERROR_OBJ    ObjectType = "ERROR"
    FUNCTION_OBJ ObjectType = "FUNCTION"
    STRING_OBJ   ObjectType = "STRING"
)

type Object interface {
    Type() ObjectType
    Inspect() string // for REPL display
}

type Integer struct{ Value int64 }
func (i *Integer) Type() ObjectType { return INTEGER_OBJ }
func (i *Integer) Inspect() string  { return fmt.Sprintf("%d", i.Value) }

type Boolean struct{ Value bool }
func (b *Boolean) Type() ObjectType { return BOOLEAN_OBJ }
func (b *Boolean) Inspect() string  { return fmt.Sprintf("%t", b.Value) }

type Null struct{}
func (n *Null) Type() ObjectType { return NULL_OBJ }
func (n *Null) Inspect() string  { return "null" }

type ReturnValue struct{ Value Object }
func (rv *ReturnValue) Type() ObjectType { return RETURN_OBJ }
func (rv *ReturnValue) Inspect() string  { return rv.Value.Inspect() }

type ErrorObj struct{ Message string }
func (e *ErrorObj) Type() ObjectType { return ERROR_OBJ }
func (e *ErrorObj) Inspect() string  { return "ERROR: " + e.Message }
```

**The environment** maps names to values and chains to an outer scope for closures:

```go
type Environment struct {
    store map[string]Object
    outer *Environment
}

func NewEnvironment() *Environment {
    return &Environment{store: make(map[string]Object)}
}

func NewEnclosedEnvironment(outer *Environment) *Environment {
    env := NewEnvironment()
    env.outer = outer
    return env
}

func (e *Environment) Get(name string) (Object, bool) {
    obj, ok := e.store[name]
    if !ok && e.outer != nil {
        obj, ok = e.outer.Get(name)
    }
    return obj, ok
}

func (e *Environment) Set(name string, val Object) Object {
    e.store[name] = val
    return val
}
```

When you look up a name, you check the current scope first. If not found, walk up the outer chain. This implements lexical scoping: inner scopes see outer definitions but can shadow them.

**The evaluator** is one big switch:

```go
var (
    TRUE  = &Boolean{Value: true}
    FALSE = &Boolean{Value: false}
    NULL  = &Null{}
)

func Eval(node Node, env *Environment) Object {
    switch node := node.(type) {

    case *Program:
        return evalProgram(node, env)

    case *ExpressionStatement:
        return Eval(node.Expression, env)

    case *IntegerLiteral:
        return &Integer{Value: node.Value}

    case *Boolean:
        if node.Value { return TRUE }
        return FALSE

    case *PrefixExpression:
        right := Eval(node.Right, env)
        if isError(right) { return right }
        return evalPrefixExpression(node.Operator, right)

    case *InfixExpression:
        left := Eval(node.Left, env)
        if isError(left) { return left }
        right := Eval(node.Right, env)
        if isError(right) { return right }
        return evalInfixExpression(node.Operator, left, right)

    case *LetStatement:
        val := Eval(node.Value, env)
        if isError(val) { return val }
        env.Set(node.Name.Value, val)
        return NULL

    case *Identifier:
        if val, ok := env.Get(node.Value); ok {
            return val
        }
        return &ErrorObj{Message: "identifier not found: " + node.Value}

    case *FunctionLiteral:
        return &Function{Params: node.Params, Body: node.Body, Env: env}

    case *CallExpression:
        fn := Eval(node.Function, env)
        if isError(fn) { return fn }
        args := evalExpressions(node.Arguments, env)
        if len(args) == 1 && isError(args[0]) { return args[0] }
        return applyFunction(fn, args)

    case *ReturnStatement:
        val := Eval(node.ReturnValue, env)
        if isError(val) { return val }
        return &ReturnValue{Value: val}

    case *BlockStatement:
        return evalBlockStatement(node, env)
    }
    return nil
}
```

The infix evaluator handles arithmetic and comparisons:

```go
func evalInfixExpression(op string, left, right Object) Object {
    if left.Type() == INTEGER_OBJ && right.Type() == INTEGER_OBJ {
        return evalIntegerInfixExpression(op, left, right)
    }
    switch op {
    case "==": return nativeBoolToBooleanObject(left == right)
    case "!=": return nativeBoolToBooleanObject(left != right)
    default:
        return &ErrorObj{Message: fmt.Sprintf(
            "unknown operator: %s %s %s", left.Type(), op, right.Type())}
    }
}

func evalIntegerInfixExpression(op string, left, right Object) Object {
    l := left.(*Integer).Value
    r := right.(*Integer).Value
    switch op {
    case "+": return &Integer{Value: l + r}
    case "-": return &Integer{Value: l - r}
    case "*": return &Integer{Value: l * r}
    case "/":
        if r == 0 { return &ErrorObj{Message: "division by zero"} }
        return &Integer{Value: l / r}
    case "<": return nativeBoolToBooleanObject(l < r)
    case ">": return nativeBoolToBooleanObject(l > r)
    case "==": return nativeBoolToBooleanObject(l == r)
    case "!=": return nativeBoolToBooleanObject(l != r)
    default:
        return &ErrorObj{Message: "unknown operator: " + op}
    }
}
```

**Function application** is where closures come alive:

```go
type Function struct {
    Params []*Identifier
    Body   *BlockStatement
    Env    *Environment // the defining environment — this is the closure
}

func applyFunction(fn Object, args []Object) Object {
    function, ok := fn.(*Function)
    if !ok {
        return &ErrorObj{Message: fmt.Sprintf("not a function: %s", fn.Type())}
    }
    extEnv := extendFunctionEnv(function, args)
    evaluated := Eval(function.Body, extEnv)
    return unwrapReturnValue(evaluated)
}

func extendFunctionEnv(fn *Function, args []Object) *Environment {
    env := NewEnclosedEnvironment(fn.Env) // chain to the closure environment
    for i, param := range fn.Params {
        env.Set(param.Value, args[i])
    }
    return env
}

func unwrapReturnValue(obj Object) Object {
    if returnValue, ok := obj.(*ReturnValue); ok {
        return returnValue.Value
    }
    return obj
}
```

When a function is defined, it captures `env` — the environment *at the time of definition*. When it is called, we create a new environment chained to that captured one. This is the closure: the function sees the variables from its definition scope, not the call scope.

## In Practice

**A REPL is five lines away:**

```go
func StartREPL() {
    env := NewEnvironment()
    scanner := bufio.NewScanner(os.Stdin)
    for {
        fmt.Print(">> ")
        if !scanner.Scan() { break }
        l := lexer.New(scanner.Text())
        p := parser.NewParser(l)
        program := p.ParseProgram()
        result := Eval(program, env)
        fmt.Println(result.Inspect())
    }
}
```

The environment persists across REPL inputs — `let x = 5` on one line makes `x` available on the next.

**Error propagation.** Every `Eval` call checks `isError(result)` before continuing. Errors bubble up through the tree without exceptions. The caller at the top of the call stack receives the error and displays it. This means a runtime error in `x / 0` returns an error object, not a panic.

**Singletons for booleans.** I define `TRUE` and `FALSE` as package-level singletons instead of allocating `&Boolean{...}` on every evaluation. Boolean comparisons become pointer equality: `left == right` in the infix evaluator works correctly because both sides are always one of two values.

## The Gotchas

**Infinite recursion in user code.** A user writing `fn f() { f() }; f()` will recurse until the Go stack overflows. The fix is a call depth counter in the environment or evaluator, returning an error when the limit is exceeded.

**Return values must be unwrapped at block boundaries.** The `ReturnValue` wrapper propagates up through nested block statements. A function like:

```
fn() {
    if true { return 5; }
    return 10;
}
```

The inner `if` block returns a `ReturnValue`. If we unwrap it there, the outer block keeps executing and returns `10`. We only unwrap at function application, not at intermediate block evaluation. `evalBlockStatement` checks for `ReturnValue` and stops — but does not unwrap — so it propagates to `applyFunction` which does the unwrapping.

**Shared singleton mutation.** If you ever add mutable state to `TRUE`, `FALSE`, or `NULL`, the singletons become a bug. Keep them immutable.

**Garbage collection is free.** Because we use Go objects for values and Go's GC for memory management, the interpreter does not need its own memory management. This is one of the advantages of writing an interpreter in a GC'd language.

## Key Takeaway

A tree-walking interpreter evaluates programs by recursively pattern-matching on AST node types and returning value objects. The environment, chained for closures, is the key data structure: it maps names to values, supports lexical scoping through the outer chain, and is extended (not mutated) for each function call. Error objects propagate up the call tree without exceptions. The result is a working interpreter in a few hundred lines of code — and once it works, you understand exactly what every language feature means in terms of computation.

---

**Previous:** [Lesson 2: Parsing — Tokens to AST, recursive descent](/post/fundamentals/compiler-parsing/)

**Next:** [Lesson 4: Code Generation — From AST to bytecode or machine code](/post/fundamentals/compiler-codegen/)
