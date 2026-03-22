---
title: "Lesson 23: GPU Computing from Rust — wgpu and compute shaders"
author: Atharva Pandey
series: "Rust Concurrency Masterclass"
lesson: 23
keywords: [Rust, rust, concurrency, GPU, wgpu, compute shaders, GPGPU, parallel computing]
tags: [Rust tutorial, rust, concurrency]
date: '2024-12-19T11:35:00.000Z'
---

The first time I ran a matrix multiplication on a GPU, my jaw dropped. A computation that took 8 seconds on an 8-core CPU finished in 40 milliseconds on a mid-range GPU. That's a 200x speedup. GPUs have thousands of cores — small, simple cores designed for massively parallel uniform computation.

Rust's GPU story has gotten remarkably good. `wgpu` gives you cross-platform GPU compute that works on Vulkan, Metal, DX12, and even in the browser via WebGPU. No CUDA lock-in.

## Why GPU Compute?

CPUs have a few powerful cores (8-16 typically). GPUs have thousands of simpler cores (4000+ on modern cards). For problems that are embarrassingly parallel — same operation on millions of data points — the GPU wins by brute force.

Good GPU problems:
- Matrix multiplication
- Neural network inference
- Image/video processing
- Physics simulation
- Monte Carlo methods
- Sorting large arrays

Bad GPU problems:
- Branchy logic
- Sequential algorithms
- Small data (transfer overhead exceeds compute benefit)
- Pointer-heavy data structures

## Setting Up wgpu

```toml
[dependencies]
wgpu = "0.19"
pollster = "0.3"  # for blocking on async wgpu calls
bytemuck = { version = "1.14", features = ["derive"] }
```

wgpu is async-first (because WebGPU is), but `pollster::block_on` lets us use it synchronously.

## Compute Shader Basics

A compute shader is a program that runs on the GPU. It's written in WGSL (WebGPU Shading Language):

```wgsl
@group(0) @binding(0) var<storage, read> input: array<f32>;
@group(0) @binding(1) var<storage, read_write> output: array<f32>;

@compute @workgroup_size(256)
fn main(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let idx = global_id.x;
    if (idx < arrayLength(&input)) {
        output[idx] = input[idx] * input[idx];
    }
}
```

This shader squares every element in an array. The `@workgroup_size(256)` means 256 threads run in each workgroup. The GPU schedules as many workgroups as needed to cover all elements.

## Full Example: Array Squaring on GPU

```rust
use wgpu::util::DeviceExt;

fn main() {
    pollster::block_on(run());
}

async fn run() {
    // 1. Get GPU device
    let instance = wgpu::Instance::default();
    let adapter = instance
        .request_adapter(&wgpu::RequestAdapterOptions {
            power_preference: wgpu::PowerPreference::HighPerformance,
            compatible_surface: None,
            force_fallback_adapter: false,
        })
        .await
        .expect("No GPU adapter found");

    println!("Using GPU: {}", adapter.get_info().name);

    let (device, queue) = adapter
        .request_device(
            &wgpu::DeviceDescriptor {
                label: Some("Compute Device"),
                required_features: wgpu::Features::empty(),
                required_limits: wgpu::Limits::default(),
                memory_hints: wgpu::MemoryHints::Performance,
            },
            None,
        )
        .await
        .unwrap();

    // 2. Prepare input data
    let input_data: Vec<f32> = (0..1_000_000).map(|i| i as f32 * 0.001).collect();
    let input_bytes = bytemuck::cast_slice(&input_data);

    // 3. Create GPU buffers
    let input_buffer = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
        label: Some("Input Buffer"),
        contents: input_bytes,
        usage: wgpu::BufferUsages::STORAGE,
    });

    let output_buffer = device.create_buffer(&wgpu::BufferDescriptor {
        label: Some("Output Buffer"),
        size: input_bytes.len() as u64,
        usage: wgpu::BufferUsages::STORAGE | wgpu::BufferUsages::COPY_SRC,
        mapped_at_creation: false,
    });

    let staging_buffer = device.create_buffer(&wgpu::BufferDescriptor {
        label: Some("Staging Buffer"),
        size: input_bytes.len() as u64,
        usage: wgpu::BufferUsages::MAP_READ | wgpu::BufferUsages::COPY_DST,
        mapped_at_creation: false,
    });

    // 4. Create compute shader
    let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
        label: Some("Compute Shader"),
        source: wgpu::ShaderSource::Wgsl(
            r#"
            @group(0) @binding(0) var<storage, read> input: array<f32>;
            @group(0) @binding(1) var<storage, read_write> output: array<f32>;

            @compute @workgroup_size(256)
            fn main(@builtin(global_invocation_id) global_id: vec3<u32>) {
                let idx = global_id.x;
                if (idx < arrayLength(&input)) {
                    output[idx] = input[idx] * input[idx];
                }
            }
            "#
            .into(),
        ),
    });

    // 5. Create pipeline and bind group
    let pipeline = device.create_compute_pipeline(&wgpu::ComputePipelineDescriptor {
        label: Some("Compute Pipeline"),
        layout: None,
        module: &shader,
        entry_point: Some("main"),
        compilation_options: Default::default(),
        cache: None,
    });

    let bind_group_layout = pipeline.get_bind_group_layout(0);
    let bind_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
        label: Some("Bind Group"),
        layout: &bind_group_layout,
        entries: &[
            wgpu::BindGroupEntry {
                binding: 0,
                resource: input_buffer.as_entire_binding(),
            },
            wgpu::BindGroupEntry {
                binding: 1,
                resource: output_buffer.as_entire_binding(),
            },
        ],
    });

    // 6. Encode and submit commands
    let mut encoder = device.create_command_encoder(&wgpu::CommandEncoderDescriptor {
        label: Some("Command Encoder"),
    });

    {
        let mut pass = encoder.begin_compute_pass(&wgpu::ComputePassDescriptor {
            label: Some("Compute Pass"),
            timestamp_writes: None,
        });
        pass.set_pipeline(&pipeline);
        pass.set_bind_group(0, &bind_group, &[]);

        let num_workgroups = (input_data.len() as u32 + 255) / 256;
        pass.dispatch_workgroups(num_workgroups, 1, 1);
    }

    encoder.copy_buffer_to_buffer(
        &output_buffer,
        0,
        &staging_buffer,
        0,
        input_bytes.len() as u64,
    );

    queue.submit(Some(encoder.finish()));

    // 7. Read results back
    let buffer_slice = staging_buffer.slice(..);
    let (tx, rx) = std::sync::mpsc::channel();
    buffer_slice.map_async(wgpu::MapMode::Read, move |result| {
        tx.send(result).unwrap();
    });
    device.poll(wgpu::Maintain::Wait);
    rx.recv().unwrap().unwrap();

    let data = buffer_slice.get_mapped_range();
    let result: Vec<f32> = bytemuck::cast_slice(&data).to_vec();
    drop(data);
    staging_buffer.unmap();

    // Verify
    println!("Input[0..5]:  {:?}", &input_data[..5]);
    println!("Output[0..5]: {:?}", &result[..5]);

    // Check correctness
    for i in 0..input_data.len() {
        let expected = input_data[i] * input_data[i];
        assert!(
            (result[i] - expected).abs() < 0.001,
            "Mismatch at {}: {} vs {}",
            i,
            result[i],
            expected
        );
    }
    println!("All {} results verified!", result.len());
}
```

That's a lot of setup code. But once the pipeline is configured, dispatching work to the GPU is fast. The setup is one-time; the dispatch is per-computation.

## The GPU Programming Model

Understanding the execution model:

```
Dispatch
 └── Workgroup (256 threads in our example)
      └── Thread (one invocation of the shader)
```

- **Thread** — Runs the shader for one element
- **Workgroup** — A group of threads that can share local memory and synchronize
- **Dispatch** — All workgroups needed to cover the data

For 1,000,000 elements with workgroup size 256, we dispatch ceil(1000000/256) = 3907 workgroups × 256 threads = ~1M shader invocations.

The GPU schedules workgroups across its thousands of cores automatically. You don't manage individual threads.

## Performance Considerations

**Transfer overhead.** Copying data to/from the GPU takes time. For small data sets (under ~100K elements), the transfer cost exceeds the compute benefit. GPU compute only wins when the computation is expensive enough to amortize the transfer.

**Memory access patterns.** GPUs are optimized for *coalesced* memory access — adjacent threads reading adjacent memory locations. Random access patterns kill performance.

**Branching.** GPU threads in a workgroup execute in lockstep (SIMT). If threads in the same group take different branches, both branches execute for all threads (masking the unused one). Heavy branching wastes half your compute.

**Precision.** GPUs are optimized for 32-bit floats. 64-bit double operations are 2-32x slower depending on the GPU. For scientific computing that needs double precision, measure carefully.

## When to Reach for the GPU

A rough guide:

| Data Size | Operation Cost | Verdict |
|-----------|---------------|---------|
| < 10K elements | Any | CPU wins (transfer overhead) |
| 10K-100K | Cheap (add/mul) | Probably CPU |
| 10K-100K | Expensive (trig, matrix) | Maybe GPU |
| > 100K | Any | GPU likely wins |
| > 1M | Any | GPU almost certainly wins |

The crossover point depends on your GPU, CPU, and the specific operation. Profile both.

## Alternatives to wgpu

- **`vulkano`** — Vulkan-specific, lower level than wgpu
- **`ocl`** — OpenCL bindings
- **`cuda-sys`** — NVIDIA CUDA bindings (NVIDIA only)
- **`rust-gpu`** — Write GPU shaders in Rust itself (experimental)

`wgpu` is my recommendation for most use cases. Cross-platform, well-maintained, and the same API works in native apps and WebAssembly.

## The Big Picture

GPU computing is the most extreme form of parallelism available to most programmers:

| Parallelism Type | Scale | Best For |
|-----------------|-------|----------|
| SIMD | 4-16x per core | Vectorizable loops |
| Threads (Rayon) | 8-16x across cores | CPU-bound parallel work |
| GPU | 100-1000x | Massive uniform computation |

They compose too. You can use Rayon to prepare data on the CPU, ship it to the GPU for heavy compute, and process the results with SIMD. Each level of parallelism targets a different bottleneck.

---

Next — testing concurrent code, where we make sure all of this actually works correctly.
