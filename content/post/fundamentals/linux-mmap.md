---
title: 'Lesson 8: Memory-Mapped IO — How Databases Read Files'
author: Atharva Pandey
series: "Linux for Backend Engineers"
lesson: 8
keywords:
  - linux
  - memory mapped IO
  - mmap
  - database IO
  - page cache
  - backend engineering
tags:
  - fundamentals
  - linux
  - operating systems
date: '2024-08-13T00:00:00.000Z'
---

I was reading about how RocksDB works one evening and kept seeing references to `mmap` for reads. I had heard of memory-mapped files but thought of them as a niche optimization. Then I realized: SQLite uses mmap. WiredTiger (MongoDB's storage engine) uses mmap. LMDB is built almost entirely around mmap. Even some Postgres configurations use mmap for WAL. Understanding mmap explains a lot about how high-performance storage works, why some databases are so fast for random reads, and why memory and I/O are so deeply intertwined at the OS level.

## How It Actually Works

The traditional I/O path:

```
Disk → Kernel page cache → read() syscall → copies data into user buffer → application
```

There are two copies: one from disk into the kernel's page cache, and one from the page cache into your user-space buffer. There are also two context switches: one into the kernel to read, one back to user space.

Memory-mapped I/O collapses this:

```
Disk → Kernel page cache → mmap region (shared with process) → application
```

`mmap()` maps a file directly into the process's virtual address space. The file's pages in the kernel's page cache become accessible via virtual memory — the process reads file data by dereferencing a pointer, with zero copy between kernel and user space.

When the application accesses a byte in the mmap region:
1. The CPU translates the virtual address via the page table
2. If the page is in the page cache (warm), the access completes directly — no syscall, zero copy
3. If the page is not in the page cache (cold), a page fault occurs, the kernel reads the page from disk into the cache, updates the page table, and resumes the application

The critical insight: for hot pages (frequently accessed), reads are as fast as memory accesses. The OS page cache is the common buffer — it is shared across all processes and persists across file opens. This is why reading the same file from multiple processes is cheap: they share the same physical pages.

Here is a Go example implementing a memory-mapped file reader:

```go
package main

import (
    "encoding/binary"
    "fmt"
    "os"
    "syscall"
    "unsafe"
)

type MmapReader struct {
    data []byte
    size int64
}

func NewMmapReader(path string) (*MmapReader, error) {
    f, err := os.Open(path)
    if err != nil {
        return nil, err
    }
    defer f.Close()

    info, err := f.Stat()
    if err != nil {
        return nil, err
    }
    size := info.Size()
    if size == 0 {
        return &MmapReader{data: []byte{}, size: 0}, nil
    }

    data, err := syscall.Mmap(
        int(f.Fd()),
        0,
        int(size),
        syscall.PROT_READ,
        syscall.MAP_SHARED, // share the kernel's page cache pages
    )
    if err != nil {
        return nil, fmt.Errorf("mmap: %w", err)
    }

    // Advise kernel about access pattern for prefetching
    syscall.Madvise(data, syscall.MADV_RANDOM) // random access — don't readahead

    return &MmapReader{data: data, size: size}, nil
}

// ReadAt reads bytes directly from mapped memory — zero copy, no syscall if pages are warm
func (r *MmapReader) ReadAt(offset int64, length int) []byte {
    if offset+int64(length) > r.size {
        return nil
    }
    return r.data[offset : offset+int64(length)]
}

// ReadUint64 reads a fixed-size value at an offset
func (r *MmapReader) ReadUint64(offset int64) uint64 {
    b := r.ReadAt(offset, 8)
    return binary.LittleEndian.Uint64(b)
}

func (r *MmapReader) Close() error {
    return syscall.Munmap(r.data)
}
```

For a database's hot read path, this is the difference between:
- `pread()` syscall: ~1μs (context switch, kernel, copy, context switch back)
- `mmap` pointer dereference: ~100ns if in cache, ~10ms if not (disk read)

## Why It Matters

**Page cache is shared**: when you open a database file with mmap, the kernel pages are the same pages the OS would use for any file I/O. There is no separate "database buffer pool" competing with the OS. On the other hand, Postgres uses its own buffer pool (`shared_buffers`) precisely to have more control over eviction policy. PostgreSQL deliberately does not use mmap for data files — it manages its own cache and uses `pread()`/`pwrite()`. SQLite and LMDB take the opposite approach: rely on the OS page cache entirely.

**Durability**: writes to a `MAP_SHARED` mapping appear in the file but may sit in the page cache before being flushed to disk. `msync(MS_SYNC)` forces pages to disk (like `fsync` but for mmap regions). Without `msync`, a power loss can lose recent writes.

**Large databases and address space**: mmap works best when the working set fits in virtual address space. On 64-bit systems this is essentially unlimited — you can map a 1 TB file. The OS page cache is still bounded by physical RAM; cold pages must be read from disk.

## Production Example

In Go, mmap is most useful for read-heavy workloads over large, stable files — think SST files in a log-structured merge tree, a precomputed index file, or a lookup table loaded at startup:

```go
// Example: loading a precomputed IP geolocation database
type GeoIPDB struct {
    reader *MmapReader
    index  []indexEntry // in-memory index — small, always hot
}

type indexEntry struct {
    StartIP uint32
    Offset  int64
}

func (db *GeoIPDB) Lookup(ip uint32) (*GeoRecord, error) {
    // Binary search the in-memory index to find offset in file
    lo, hi := 0, len(db.index)-1
    for lo <= hi {
        mid := (lo + hi) / 2
        if db.index[mid].StartIP <= ip {
            lo = mid + 1
        } else {
            hi = mid - 1
        }
    }
    if hi < 0 {
        return nil, ErrNotFound
    }

    offset := db.index[hi].Offset
    // Read record directly from mmap region — no syscall, no copy
    data := db.reader.ReadAt(offset, recordSize)
    return parseGeoRecord(data), nil
}
```

For understanding your Go service's I/O characteristics, `/proc/self/smaps` shows mmap regions and how much of each is resident in physical RAM:

```bash
# Show all mmap regions and their RSS
cat /proc/$(pgrep myservice)/smaps | awk '
    /^[0-9a-f]+-/{region=$0}
    /^Rss:/{print $2 " KB  " region}
' | sort -rn | head -20
```

Kernel tuning for mmap-heavy workloads:

```bash
# vm.swappiness: lower = prefer to keep file pages in RAM, swap anonymous memory first
sysctl -w vm.swappiness=10

# Disable transparent hugepages for databases — they cause latency spikes
echo never > /sys/kernel/mm/transparent_hugepage/enabled

# vm.dirty_ratio: when to start synchronous writes for dirty pages
sysctl -w vm.dirty_ratio=5
sysctl -w vm.dirty_background_ratio=2
```

## The Tradeoffs

**mmap vs read(): the database debate**: mmap has better latency for warm reads (no syscall) but worse behavior for cold reads (page faults can stall execution unpredictably). `pread()` with a buffer pool gives the database control over I/O scheduling and eviction. This is why Postgres, MySQL InnoDB, and most OLTP databases use their own buffer pools rather than mmap for data files.

**Memory pressure**: if your system is under memory pressure, the kernel can evict mmap pages. The next access page-faults and reads from disk. With your own buffer pool, you control what gets evicted.

**Huge pages**: mmap regions benefit significantly from huge pages (2MB instead of 4KB pages). Fewer TLB entries needed, fewer TLB misses. `madvise(MADV_HUGEPAGE)` requests huge pages for a region; `MAP_HUGETLB` allocates directly from the huge page pool.

**`MADV_WILLNEED` and `MADV_DONTNEED`**: you can hint to the kernel about future access patterns. `MADV_WILLNEED` triggers prefetching for pages you're about to access. `MADV_DONTNEED` tells the kernel those pages are no longer needed and can be reclaimed. Databases use these aggressively for sequential scans vs. cache management.

## Key Takeaway

Memory-mapped I/O maps files directly into the process's virtual address space, sharing the kernel's page cache without copy overhead. Warm reads are as fast as memory access — no syscall, no copy. Cold reads trigger page faults and disk reads. The OS page cache serves as the buffer. mmap is powerful for read-heavy workloads on large stable files, but for transactional databases, explicit buffer pool management (as Postgres does) gives better control over I/O scheduling and eviction policy.

---

**Previous:** [Lesson 7: Containers from Scratch](/post/fundamentals/linux-containers/)

---

🎓 **Course Complete — Linux for Backend Engineers**

You've covered the OS foundations that every backend service runs on: how processes and threads relate to goroutines, how virtual memory works and what RSS actually means, how file descriptors are managed, how TCP connections are established and torn down, how epoll enables Go's high-concurrency I/O model, how signals and graceful shutdown work, how containers are just namespaces and cgroups, and how memory-mapped I/O lets databases read files at memory speed. These are the building blocks that every system above them — databases, runtimes, orchestration platforms — is built from.
