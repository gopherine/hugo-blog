---
title: "Lesson 4: Code Generation — From AST to bytecode or machine code"
author: Atharva Pandey
series: "Compilers and Interpreters"
lesson: 4
keywords:
  - code generation
  - bytecode
  - virtual machine
  - compiler backend
  - stack machine
  - LLVM
  - instruction set
  - compilers
tags:
  - fundamentals
  - compilers
date: "2024-12-20T00:00:00.000Z"
---

The tree-walking interpreter in Lesson 3 works, but it has a ceiling. Every time you evaluate an expression, you traverse the AST from scratch. No caching, no precomputed form, no optimization. For a REPL or a small scripting language this is fine. For a production language runtime — something that runs for hours, executes millions of operations — you want a tighter inner loop. That tighter loop is a virtual machine executing bytecode. And the step that produces bytecode from the AST is code generation.

This lesson walks through compiling the same small language to a stack-based bytecode and executing it on a simple virtual machine. You will see where constants live, how variable access becomes an index operation, how control flow becomes jumps, and why this is fundamentally faster than tree-walking.

## The Problem

In a tree-walking interpreter, evaluating `1 + 2 + 3` means:
1. Traverse the outer `InfixExpression(+)` node.
2. Recursively traverse `InfixExpression(+)` on the left.
3. Recursively traverse `IntegerLiteral(1)`.
4. Recursively traverse `IntegerLiteral(2)`.
5. Compute `1 + 2`, return.
6. Recursively traverse `IntegerLiteral(3)`.
7. Compute `3 + 3`, return.

Seven steps, with Go function calls, type switches, interface dispatch, and heap allocations at each level. For a tight inner loop in user code, this overhead accumulates.

Bytecode compiles this to a flat sequence of instructions that a virtual machine executes in a tight loop:

```
OpConstant 0   ; push constant pool[0] (value: 1)
OpConstant 1   ; push constant pool[1] (value: 2)
OpAdd          ; pop two, push sum
OpConstant 2   ; push constant pool[2] (value: 3)
OpAdd          ; pop two, push sum
```

The VM executes these instructions in a `for` loop with a single `switch`. No recursion, no interface dispatch. Just reading bytes and operating on a stack.

## How It Works

**Instructions and opcodes** are the instruction set of your virtual machine:

```go
type Opcode byte

const (
    OpConstant  Opcode = iota // push a constant from the pool
    OpAdd                     // pop two integers, push sum
    OpSub
    OpMul
    OpDiv
    OpTrue                    // push true
    OpFalse                   // push false
    OpNull
    OpEqual
    OpNotEqual
    OpGreaterThan
    OpMinus                   // negate
    OpBang                    // logical not
    OpJump                    // unconditional jump
    OpJumpNotTruthy           // conditional jump (if not true, jump)
    OpSetGlobal               // store top of stack in global slot
    OpGetGlobal               // push value from global slot
    OpSetLocal                // store top of stack in local slot
    OpGetLocal                // push value from local slot
    OpCall                    // call function
    OpReturnValue             // return with a value
    OpReturn                  // return without value (null)
    OpPop                     // discard top of stack
)
```

Instructions are byte sequences. Some opcodes take operands encoded as big-endian bytes immediately following the opcode:

```go
type Definition struct {
    Name          string
    OperandWidths []int // width of each operand in bytes
}

var definitions = map[Opcode]*Definition{
    OpConstant:  {"OpConstant", []int{2}},     // one 2-byte operand (constant pool index)
    OpAdd:       {"OpAdd", []int{}},
    OpJump:      {"OpJump", []int{2}},         // jump target (2-byte offset)
    OpSetGlobal: {"OpSetGlobal", []int{2}},
    OpGetGlobal: {"OpGetGlobal", []int{2}},
    OpSetLocal:  {"OpSetLocal", []int{1}},     // local slot index (1 byte, max 256 locals)
    OpGetLocal:  {"OpGetLocal", []int{1}},
    OpCall:      {"OpCall", []int{1}},         // number of arguments
    // ...
}

type Instructions []byte

func Make(op Opcode, operands ...int) []byte {
    def, ok := definitions[op]
    if !ok {
        return []byte{}
    }
    out := []byte{byte(op)}
    for i, o := range operands {
        width := def.OperandWidths[i]
        switch width {
        case 2:
            out = append(out, byte(o>>8), byte(o))
        case 1:
            out = append(out, byte(o))
        }
    }
    return out
}
```

**The compiler** walks the AST and emits instructions:

```go
type Compiler struct {
    instructions Instructions
    constants    []Object        // constant pool
    globals      SymbolTable     // global variable name → index
    scopes       []CompilationScope
}

type CompilationScope struct {
    instructions Instructions
    lastInstruction  EmittedInstruction
    previousInstruction EmittedInstruction
}

func (c *Compiler) Compile(node Node) error {
    switch node := node.(type) {
    case *Program:
        for _, s := range node.Statements {
            if err := c.Compile(s); err != nil {
                return err
            }
        }
    case *ExpressionStatement:
        if err := c.Compile(node.Expression); err != nil {
            return err
        }
        c.emit(OpPop) // discard expression result (not a return value)

    case *InfixExpression:
        if err := c.Compile(node.Left); err != nil { return err }
        if err := c.Compile(node.Right); err != nil { return err }
        switch node.Operator {
        case "+": c.emit(OpAdd)
        case "-": c.emit(OpSub)
        case "*": c.emit(OpMul)
        case "/": c.emit(OpDiv)
        case "==": c.emit(OpEqual)
        case "!=": c.emit(OpNotEqual)
        case ">": c.emit(OpGreaterThan)
        default:
            return fmt.Errorf("unknown operator: %s", node.Operator)
        }

    case *IntegerLiteral:
        integer := &Integer{Value: node.Value}
        c.emit(OpConstant, c.addConstant(integer))

    case *LetStatement:
        symbol := c.globals.Define(node.Name.Value)
        if err := c.Compile(node.Value); err != nil { return err }
        c.emit(OpSetGlobal, symbol.Index)

    case *Identifier:
        symbol, ok := c.globals.Resolve(node.Value)
        if !ok {
            return fmt.Errorf("undefined variable %s", node.Value)
        }
        c.emit(OpGetGlobal, symbol.Index)

    case *IfExpression:
        if err := c.Compile(node.Condition); err != nil { return err }
        // emit a conditional jump with a placeholder offset
        jumpNotTruthyPos := c.emit(OpJumpNotTruthy, 9999)
        if err := c.Compile(node.Consequence); err != nil { return err }
        // emit unconditional jump over the else block
        jumpPos := c.emit(OpJump, 9999)
        // back-patch the conditional jump to here
        afterConsequence := len(c.currentInstructions())
        c.changeOperand(jumpNotTruthyPos, afterConsequence)
        if node.Alternative != nil {
            if err := c.Compile(node.Alternative); err != nil { return err }
        }
        afterAlternative := len(c.currentInstructions())
        c.changeOperand(jumpPos, afterAlternative)
    }
    return nil
}
```

The `changeOperand` / back-patching trick is how compilers handle forward jumps: emit a jump with a placeholder target, compile the body, then go back and fill in the real target address.

**The virtual machine** executes the bytecode:

```go
type VM struct {
    constants    []Object
    globals      []Object
    stack        [StackSize]Object
    sp           int // stack pointer: points to next free slot
    frames       [MaxFrames]*Frame
    framesIndex  int
}

type Frame struct {
    fn          *CompiledFunction
    ip          int // instruction pointer
    basePointer int // points to first local in this frame's stack window
}

func (vm *VM) Run() error {
    var ip int
    var ins Instructions
    var op Opcode

    for vm.currentFrame().ip < len(vm.currentFrame().Instructions())-1 {
        vm.currentFrame().ip++
        ip = vm.currentFrame().ip
        ins = vm.currentFrame().Instructions()
        op = Opcode(ins[ip])

        switch op {
        case OpConstant:
            constIndex := int(ins[ip+1])<<8 | int(ins[ip+2])
            vm.currentFrame().ip += 2
            vm.push(vm.constants[constIndex])

        case OpAdd, OpSub, OpMul, OpDiv:
            right := vm.pop().(*Integer).Value
            left := vm.pop().(*Integer).Value
            switch op {
            case OpAdd: vm.push(&Integer{Value: left + right})
            case OpSub: vm.push(&Integer{Value: left - right})
            case OpMul: vm.push(&Integer{Value: left * right})
            case OpDiv: vm.push(&Integer{Value: left / right})
            }

        case OpSetGlobal:
            globalIndex := int(ins[ip+1])<<8 | int(ins[ip+2])
            vm.currentFrame().ip += 2
            vm.globals[globalIndex] = vm.pop()

        case OpGetGlobal:
            globalIndex := int(ins[ip+1])<<8 | int(ins[ip+2])
            vm.currentFrame().ip += 2
            vm.push(vm.globals[globalIndex])

        case OpJumpNotTruthy:
            target := int(ins[ip+1])<<8 | int(ins[ip+2])
            vm.currentFrame().ip += 2
            condition := vm.pop()
            if !isTruthy(condition) {
                vm.currentFrame().ip = target - 1 // -1 because loop increments
            }

        case OpCall:
            numArgs := int(ins[ip+1])
            vm.currentFrame().ip++
            fn := vm.stack[vm.sp-1-numArgs].(*CompiledFunction)
            frame := NewFrame(fn, vm.sp-numArgs)
            vm.pushFrame(frame)

        case OpReturnValue:
            returnValue := vm.pop()
            frame := vm.popFrame()
            vm.sp = frame.basePointer - 1 // pop function and args
            vm.push(returnValue)
        }
    }
    return nil
}
```

## In Practice

**Disassembling bytecode** is essential for debugging. Write a `Disassemble(ins Instructions) string` function that decodes each instruction using the `definitions` map and prints it as human-readable text. Whenever the compiler or VM behaves unexpectedly, disassemble the generated bytecode and read it.

**Local variables vs globals.** In the code above, global variables use a flat array (`vm.globals`) indexed by a symbol table. Local variables use the stack itself — the base pointer marks where the current frame's locals begin. `OpGetLocal N` reads `vm.stack[basePointer + N]`. This is exactly how most real VMs, including the CPython VM and the JVM, handle locals.

**LLVM as a code generation backend.** If you want to target native machine code rather than a custom VM, LLVM is the standard backend. You walk the AST and emit LLVM IR (a textual intermediate representation) or use an LLVM Go binding like `llir/llvm`. LLVM handles register allocation, instruction selection, and optimization. The Go compiler itself uses a similar multi-stage approach: AST to SSA, SSA to machine code.

## The Gotchas

**Back-patching and forward references.** The compiler emits jumps with placeholder operands and patches them later. This works for one pass over a linear AST but gets complicated with nested control flow. Keep a stack of "patch positions" for each nested scope that needs back-patching.

**Stack depth analysis.** A stack machine needs a stack large enough for the deepest expression. You can compute the maximum stack depth statically (count push minus pop at each point), or just allocate a generous fixed stack. For production VMs, static analysis prevents stack overflow at compile time.

**Function compilation creates sub-bytecodes.** Each function compiles to its own `CompiledFunction` with its own `Instructions` byte slice. The main program's bytecode references these function objects as constants. Compiling nested functions requires a scope stack in the compiler to track which instruction list you are emitting into.

**Closures over local variables.** When an inner function references a local of an outer function, the local must survive after the outer function returns. This requires "upvalues" (Lua's term) or heap-escaping locals. The compiler must detect captured variables and emit special get/set ops that reference the closure's upvalue array rather than the stack. This is the hardest part of a realistic compiler.

## Key Takeaway

Code generation transforms the AST into a flat sequence of bytecode instructions executed by a virtual machine. The compiler walks the AST exactly as the tree-walking evaluator did, but instead of computing results immediately, it emits instructions. The VM then executes those instructions in a tight loop: no recursion, no interface dispatch, just reading bytes and operating on a stack. The performance difference over tree-walking is significant — often 5–10x for numeric workloads. Understanding this path — source text to tokens to AST to bytecode to execution — gives you a complete mental model of how programming languages work.

---

**Previous:** [Lesson 3: AST and Evaluation — Walking the tree to compute results](/post/fundamentals/compiler-ast-eval/)

---

🎓 **Course Complete!** You have finished *Compilers and Interpreters*. You built a lexer that turns text into tokens, a Pratt parser that builds an AST, a tree-walking evaluator with closures and environments, and a bytecode compiler with a stack-based virtual machine. These are the foundational pieces of every language runtime in existence. The details change; the structure does not.
