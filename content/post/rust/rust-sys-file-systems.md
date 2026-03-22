---
title: "Lesson 6: Building a File System — From blocks to files"
author: Atharva Pandey
series: "Rust for Systems Programming"
lesson: 6
keywords: [Rust, rust, systems programming, file system, filesystem, inode, block device, FUSE]
tags: [Rust tutorial, rust, systems, low-level]
date: '2025-07-22T16:08:32.000Z'
---

The moment a file system clicked for me was when I stopped thinking of files as "things on disk" and started thinking of them as "names mapped to byte ranges scattered across a block device." That sounds more complicated, but it's actually simpler — because now there's no magic. Just data structures.

Every file system, from FAT16 to ZFS, answers the same fundamental questions: Where does this file's data live on disk? How do I find a file by name? What metadata (size, permissions, timestamps) does each file have? Let's answer all of these by building one from scratch.

## The Block Device Abstraction

File systems don't talk to disks directly. They talk to block devices — abstract things that support reading and writing fixed-size blocks:

```rust
use std::io;

/// Block size — typically 512 bytes or 4096 bytes
const BLOCK_SIZE: usize = 4096;

/// A block of data
type Block = [u8; BLOCK_SIZE];

/// Trait for any block-level storage
pub trait BlockDevice {
    fn read_block(&self, block_num: u64, buf: &mut Block) -> io::Result<()>;
    fn write_block(&self, block_num: u64, buf: &Block) -> io::Result<()>;
    fn total_blocks(&self) -> u64;
}

/// In-memory block device for testing
pub struct MemoryBlockDevice {
    blocks: Vec<Block>,
}

impl MemoryBlockDevice {
    pub fn new(num_blocks: u64) -> Self {
        Self {
            blocks: vec![[0u8; BLOCK_SIZE]; num_blocks as usize],
        }
    }
}

impl BlockDevice for MemoryBlockDevice {
    fn read_block(&self, block_num: u64, buf: &mut Block) -> io::Result<()> {
        let idx = block_num as usize;
        if idx >= self.blocks.len() {
            return Err(io::Error::new(io::ErrorKind::InvalidInput, "block out of range"));
        }
        buf.copy_from_slice(&self.blocks[idx]);
        Ok(())
    }

    fn write_block(&self, block_num: u64, buf: &Block) -> io::Result<()> {
        let idx = block_num as usize;
        if idx >= self.blocks.len() {
            return Err(io::Error::new(io::ErrorKind::InvalidInput, "block out of range"));
        }
        // Interior mutability needed for write — using UnsafeCell in a real impl
        unsafe {
            let ptr = self.blocks.as_ptr().add(idx) as *mut Block;
            (*ptr).copy_from_slice(buf);
        }
        Ok(())
    }

    fn total_blocks(&self) -> u64 {
        self.blocks.len() as u64
    }
}

/// File-backed block device (for real usage)
pub struct FileBlockDevice {
    file: std::fs::File,
    total_blocks: u64,
}

impl FileBlockDevice {
    pub fn open(path: &str, total_blocks: u64) -> io::Result<Self> {
        use std::fs::OpenOptions;
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .open(path)?;

        file.set_len(total_blocks * BLOCK_SIZE as u64)?;

        Ok(Self { file, total_blocks })
    }
}

impl BlockDevice for FileBlockDevice {
    fn read_block(&self, block_num: u64, buf: &mut Block) -> io::Result<()> {
        use std::io::{Read, Seek, SeekFrom};
        let offset = block_num * BLOCK_SIZE as u64;
        let mut file = &self.file;
        file.seek(SeekFrom::Start(offset))?;
        file.read_exact(buf)?;
        Ok(())
    }

    fn write_block(&self, block_num: u64, buf: &Block) -> io::Result<()> {
        use std::io::{Write, Seek, SeekFrom};
        let offset = block_num * BLOCK_SIZE as u64;
        let mut file = &self.file;
        file.seek(SeekFrom::Start(offset))?;
        file.write_all(buf)?;
        Ok(())
    }

    fn total_blocks(&self) -> u64 {
        self.total_blocks
    }
}
```

## Designing the On-Disk Layout

Our file system ("SimpleFS") will have this layout:

```
Block 0:      Superblock (metadata about the filesystem)
Block 1-N:    Inode bitmap (which inodes are allocated)
Block N+1-M:  Block bitmap (which data blocks are allocated)
Block M+1-P:  Inode table (the actual inode structures)
Block P+1-end: Data blocks (file content)
```

Let's define the on-disk structures:

```rust
/// Superblock — always at block 0
#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct Superblock {
    pub magic: u32,            // Magic number to identify our FS
    pub version: u32,          // Format version
    pub block_size: u32,       // Block size in bytes
    pub total_blocks: u64,     // Total blocks in the device
    pub inode_count: u64,      // Total number of inodes
    pub free_blocks: u64,      // Number of unallocated data blocks
    pub free_inodes: u64,      // Number of unallocated inodes
    pub inode_bitmap_start: u64,
    pub block_bitmap_start: u64,
    pub inode_table_start: u64,
    pub data_start: u64,
    pub root_inode: u64,       // Inode number of the root directory
    _padding: [u8; 4000],      // Pad to fill the block
}

const SIMPLEFS_MAGIC: u32 = 0x53464653; // "SFFS"

/// Inode — metadata for one file or directory
#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct Inode {
    pub mode: u16,         // File type + permissions
    pub uid: u16,          // Owner user ID
    pub gid: u16,          // Owner group ID
    pub nlinks: u16,       // Number of hard links
    pub size: u64,         // File size in bytes
    pub created: u64,      // Creation timestamp
    pub modified: u64,     // Last modification timestamp
    pub direct: [u64; 12], // Direct block pointers
    pub indirect: u64,     // Single indirect block pointer
    pub double_indirect: u64, // Double indirect block pointer
    _padding: [u8; 24],
}

const INODE_SIZE: usize = core::mem::size_of::<Inode>();
const INODES_PER_BLOCK: usize = BLOCK_SIZE / INODE_SIZE;

// File type flags
const S_IFREG: u16 = 0o100000; // Regular file
const S_IFDIR: u16 = 0o040000; // Directory

/// Directory entry
#[repr(C)]
#[derive(Clone, Copy)]
pub struct DirEntry {
    pub inode: u64,        // Inode number (0 = deleted)
    pub name_len: u16,     // Length of the name
    pub entry_len: u16,    // Total length of this entry (for alignment)
    pub file_type: u8,     // File type (redundant with inode, but faster lookups)
    pub name: [u8; 251],   // File name (not null-terminated)
}

const DIR_ENTRY_SIZE: usize = core::mem::size_of::<DirEntry>();
```

The `#[repr(C)]` is critical — it ensures the struct layout matches what we write to disk. Without it, Rust could reorder or pad fields differently across compiler versions.

## The File System Implementation

```rust
pub struct SimpleFS<D: BlockDevice> {
    device: D,
    superblock: Superblock,
}

impl<D: BlockDevice> SimpleFS<D> {
    /// Format a block device with a new SimpleFS
    pub fn format(device: D, inode_count: u64) -> io::Result<Self> {
        let total_blocks = device.total_blocks();

        // Calculate layout
        let inode_bitmap_blocks = (inode_count + (BLOCK_SIZE as u64 * 8) - 1)
            / (BLOCK_SIZE as u64 * 8);
        let data_blocks = total_blocks - 1 - inode_bitmap_blocks;
        let block_bitmap_blocks = (data_blocks + (BLOCK_SIZE as u64 * 8) - 1)
            / (BLOCK_SIZE as u64 * 8);
        let inode_table_blocks = (inode_count + INODES_PER_BLOCK as u64 - 1)
            / INODES_PER_BLOCK as u64;

        let inode_bitmap_start = 1;
        let block_bitmap_start = inode_bitmap_start + inode_bitmap_blocks;
        let inode_table_start = block_bitmap_start + block_bitmap_blocks;
        let data_start = inode_table_start + inode_table_blocks;

        let mut sb = Superblock {
            magic: SIMPLEFS_MAGIC,
            version: 1,
            block_size: BLOCK_SIZE as u32,
            total_blocks,
            inode_count,
            free_blocks: total_blocks - data_start,
            free_inodes: inode_count - 1, // Root inode is pre-allocated
            inode_bitmap_start,
            block_bitmap_start,
            inode_table_start,
            data_start,
            root_inode: 0,
            _padding: [0; 4000],
        };

        // Write superblock
        let mut block = [0u8; BLOCK_SIZE];
        unsafe {
            let sb_ptr = &sb as *const Superblock as *const u8;
            core::ptr::copy_nonoverlapping(
                sb_ptr,
                block.as_mut_ptr(),
                core::mem::size_of::<Superblock>(),
            );
        }
        device.write_block(0, &block)?;

        // Zero out bitmaps
        let zero_block = [0u8; BLOCK_SIZE];
        for i in inode_bitmap_start..(inode_table_start) {
            device.write_block(i, &zero_block)?;
        }

        let mut fs = SimpleFS { device, superblock: sb };

        // Create root directory (inode 0)
        fs.allocate_inode()?; // Mark inode 0 as used
        let root_inode = Inode {
            mode: S_IFDIR | 0o755,
            uid: 0,
            gid: 0,
            nlinks: 2, // . and parent (root's parent is itself)
            size: 0,
            created: 0, // TODO: actual timestamps
            modified: 0,
            direct: [0; 12],
            indirect: 0,
            double_indirect: 0,
            _padding: [0; 24],
        };
        fs.write_inode(0, &root_inode)?;

        Ok(fs)
    }

    /// Mount an existing SimpleFS
    pub fn mount(device: D) -> io::Result<Self> {
        let mut block = [0u8; BLOCK_SIZE];
        device.read_block(0, &mut block)?;

        let superblock: Superblock = unsafe {
            core::ptr::read(block.as_ptr() as *const Superblock)
        };

        if superblock.magic != SIMPLEFS_MAGIC {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "not a SimpleFS filesystem",
            ));
        }

        Ok(SimpleFS { device, superblock })
    }

    // --- Bitmap operations ---

    fn allocate_inode(&mut self) -> io::Result<u64> {
        let mut block = [0u8; BLOCK_SIZE];

        for bitmap_block in 0..((self.superblock.inode_count + BLOCK_SIZE as u64 * 8 - 1) / (BLOCK_SIZE as u64 * 8)) {
            let block_num = self.superblock.inode_bitmap_start + bitmap_block;
            self.device.read_block(block_num, &mut block)?;

            for byte_idx in 0..BLOCK_SIZE {
                if block[byte_idx] != 0xFF {
                    // Found a byte with a free bit
                    for bit in 0..8u8 {
                        if block[byte_idx] & (1 << bit) == 0 {
                            // Mark as allocated
                            block[byte_idx] |= 1 << bit;
                            self.device.write_block(block_num, &block)?;
                            self.superblock.free_inodes -= 1;

                            let inode_num = bitmap_block * BLOCK_SIZE as u64 * 8
                                + byte_idx as u64 * 8
                                + bit as u64;
                            return Ok(inode_num);
                        }
                    }
                }
            }
        }

        Err(io::Error::new(io::ErrorKind::Other, "no free inodes"))
    }

    fn allocate_block(&mut self) -> io::Result<u64> {
        let mut block = [0u8; BLOCK_SIZE];
        let data_blocks = self.superblock.total_blocks - self.superblock.data_start;
        let bitmap_blocks = (data_blocks + BLOCK_SIZE as u64 * 8 - 1) / (BLOCK_SIZE as u64 * 8);

        for bitmap_block in 0..bitmap_blocks {
            let block_num = self.superblock.block_bitmap_start + bitmap_block;
            self.device.read_block(block_num, &mut block)?;

            for byte_idx in 0..BLOCK_SIZE {
                if block[byte_idx] != 0xFF {
                    for bit in 0..8u8 {
                        if block[byte_idx] & (1 << bit) == 0 {
                            block[byte_idx] |= 1 << bit;
                            self.device.write_block(block_num, &block)?;
                            self.superblock.free_blocks -= 1;

                            let data_block = self.superblock.data_start
                                + bitmap_block * BLOCK_SIZE as u64 * 8
                                + byte_idx as u64 * 8
                                + bit as u64;
                            return Ok(data_block);
                        }
                    }
                }
            }
        }

        Err(io::Error::new(io::ErrorKind::Other, "no free blocks"))
    }

    // --- Inode operations ---

    fn read_inode(&self, inode_num: u64) -> io::Result<Inode> {
        let block_num = self.superblock.inode_table_start
            + inode_num / INODES_PER_BLOCK as u64;
        let offset = (inode_num as usize % INODES_PER_BLOCK) * INODE_SIZE;

        let mut block = [0u8; BLOCK_SIZE];
        self.device.read_block(block_num, &mut block)?;

        let inode: Inode = unsafe {
            core::ptr::read(block.as_ptr().add(offset) as *const Inode)
        };
        Ok(inode)
    }

    fn write_inode(&self, inode_num: u64, inode: &Inode) -> io::Result<()> {
        let block_num = self.superblock.inode_table_start
            + inode_num / INODES_PER_BLOCK as u64;
        let offset = (inode_num as usize % INODES_PER_BLOCK) * INODE_SIZE;

        let mut block = [0u8; BLOCK_SIZE];
        self.device.read_block(block_num, &mut block)?;

        unsafe {
            let src = inode as *const Inode as *const u8;
            core::ptr::copy_nonoverlapping(src, block.as_mut_ptr().add(offset), INODE_SIZE);
        }

        self.device.write_block(block_num, &block)?;
        Ok(())
    }

    // --- File operations ---

    /// Get the disk block number for a given logical block of a file
    fn get_file_block(&self, inode: &Inode, logical_block: u64) -> io::Result<Option<u64>> {
        if logical_block < 12 {
            // Direct blocks
            let block = inode.direct[logical_block as usize];
            if block == 0 {
                Ok(None)
            } else {
                Ok(Some(block))
            }
        } else if logical_block < 12 + (BLOCK_SIZE as u64 / 8) {
            // Single indirect
            if inode.indirect == 0 {
                return Ok(None);
            }
            let idx = logical_block - 12;
            let mut block = [0u8; BLOCK_SIZE];
            self.device.read_block(inode.indirect, &mut block)?;
            let ptrs = unsafe {
                core::slice::from_raw_parts(
                    block.as_ptr() as *const u64,
                    BLOCK_SIZE / 8,
                )
            };
            let data_block = ptrs[idx as usize];
            if data_block == 0 { Ok(None) } else { Ok(Some(data_block)) }
        } else {
            // Double indirect — left as exercise
            Err(io::Error::new(io::ErrorKind::Other, "file too large"))
        }
    }

    /// Read file data
    pub fn read_file(&self, inode_num: u64, offset: u64, buf: &mut [u8]) -> io::Result<usize> {
        let inode = self.read_inode(inode_num)?;

        if offset >= inode.size {
            return Ok(0);
        }

        let mut bytes_read = 0usize;
        let to_read = core::cmp::min(buf.len() as u64, inode.size - offset) as usize;
        let mut pos = offset;

        while bytes_read < to_read {
            let logical_block = pos / BLOCK_SIZE as u64;
            let block_offset = (pos % BLOCK_SIZE as u64) as usize;
            let chunk = core::cmp::min(BLOCK_SIZE - block_offset, to_read - bytes_read);

            if let Some(disk_block) = self.get_file_block(&inode, logical_block)? {
                let mut block = [0u8; BLOCK_SIZE];
                self.device.read_block(disk_block, &mut block)?;
                buf[bytes_read..bytes_read + chunk]
                    .copy_from_slice(&block[block_offset..block_offset + chunk]);
            } else {
                // Sparse file — hole reads as zeros
                buf[bytes_read..bytes_read + chunk].fill(0);
            }

            bytes_read += chunk;
            pos += chunk as u64;
        }

        Ok(bytes_read)
    }

    /// Create a file in a directory
    pub fn create_file(&mut self, parent_inode: u64, name: &str) -> io::Result<u64> {
        if name.len() > 251 {
            return Err(io::Error::new(io::ErrorKind::InvalidInput, "name too long"));
        }

        // Allocate a new inode
        let new_inode_num = self.allocate_inode()?;

        let new_inode = Inode {
            mode: S_IFREG | 0o644,
            uid: 0,
            gid: 0,
            nlinks: 1,
            size: 0,
            created: 0,
            modified: 0,
            direct: [0; 12],
            indirect: 0,
            double_indirect: 0,
            _padding: [0; 24],
        };
        self.write_inode(new_inode_num, &new_inode)?;

        // Add directory entry to parent
        self.add_dir_entry(parent_inode, new_inode_num, name, 1)?;

        Ok(new_inode_num)
    }

    fn add_dir_entry(
        &mut self,
        dir_inode_num: u64,
        target_inode: u64,
        name: &str,
        file_type: u8,
    ) -> io::Result<()> {
        let mut entry = DirEntry {
            inode: target_inode,
            name_len: name.len() as u16,
            entry_len: DIR_ENTRY_SIZE as u16,
            file_type,
            name: [0u8; 251],
        };
        entry.name[..name.len()].copy_from_slice(name.as_bytes());

        let entry_bytes = unsafe {
            core::slice::from_raw_parts(
                &entry as *const DirEntry as *const u8,
                DIR_ENTRY_SIZE,
            )
        };

        // Find space in existing directory blocks or allocate new one
        let mut dir_inode = self.read_inode(dir_inode_num)?;
        let current_size = dir_inode.size;
        let block_idx = current_size / BLOCK_SIZE as u64;
        let block_offset = current_size as usize % BLOCK_SIZE;

        let disk_block = if block_offset == 0 {
            // Need a new block
            let new_block = self.allocate_block()?;
            if block_idx < 12 {
                dir_inode.direct[block_idx as usize] = new_block;
            } else {
                return Err(io::Error::new(io::ErrorKind::Other, "directory too large"));
            }
            new_block
        } else {
            self.get_file_block(&dir_inode, block_idx)?.unwrap()
        };

        let mut block = [0u8; BLOCK_SIZE];
        self.device.read_block(disk_block, &mut block)?;
        block[block_offset..block_offset + DIR_ENTRY_SIZE].copy_from_slice(entry_bytes);
        self.device.write_block(disk_block, &block)?;

        dir_inode.size = current_size + DIR_ENTRY_SIZE as u64;
        self.write_inode(dir_inode_num, &dir_inode)?;

        Ok(())
    }

    /// List directory contents
    pub fn list_dir(&self, dir_inode_num: u64) -> io::Result<Vec<(String, u64)>> {
        let inode = self.read_inode(dir_inode_num)?;
        let mut entries = Vec::new();
        let mut offset = 0u64;

        while offset < inode.size {
            let logical_block = offset / BLOCK_SIZE as u64;
            let block_offset = (offset % BLOCK_SIZE as u64) as usize;

            if let Some(disk_block) = self.get_file_block(&inode, logical_block)? {
                let mut block = [0u8; BLOCK_SIZE];
                self.device.read_block(disk_block, &mut block)?;

                let entry: DirEntry = unsafe {
                    core::ptr::read(
                        block.as_ptr().add(block_offset) as *const DirEntry
                    )
                };

                if entry.inode != 0 {
                    let name = core::str::from_utf8(
                        &entry.name[..entry.name_len as usize]
                    ).unwrap_or("<invalid>").to_string();
                    entries.push((name, entry.inode));
                }
            }

            offset += DIR_ENTRY_SIZE as u64;
        }

        Ok(entries)
    }
}
```

## Testing Our File System

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn format_and_mount() {
        let device = MemoryBlockDevice::new(1024);
        let fs = SimpleFS::format(device, 128).unwrap();
        assert_eq!(fs.superblock.magic, SIMPLEFS_MAGIC);
    }

    #[test]
    fn create_and_list_files() {
        let device = MemoryBlockDevice::new(1024);
        let mut fs = SimpleFS::format(device, 128).unwrap();

        // Create files in root directory
        fs.create_file(0, "hello.txt").unwrap();
        fs.create_file(0, "world.txt").unwrap();
        fs.create_file(0, "readme.md").unwrap();

        // List root directory
        let entries = fs.list_dir(0).unwrap();
        assert_eq!(entries.len(), 3);
        assert_eq!(entries[0].0, "hello.txt");
        assert_eq!(entries[1].0, "world.txt");
        assert_eq!(entries[2].0, "readme.md");
    }
}
```

## Making It Real with FUSE

FUSE (Filesystem in Userspace) lets you mount your file system on an actual Linux system without kernel modules:

```rust
use fuser::{
    FileAttr, FileType, Filesystem, MountOption,
    ReplyAttr, ReplyData, ReplyDirectory, ReplyEntry,
    Request,
};
use std::ffi::OsStr;
use std::time::{Duration, UNIX_EPOCH};

struct SimpleFSFuse {
    // Wrap our SimpleFS implementation
    // fs: SimpleFS<FileBlockDevice>,
}

impl Filesystem for SimpleFSFuse {
    fn lookup(
        &mut self,
        _req: &Request,
        parent: u64,
        name: &OsStr,
        reply: ReplyEntry,
    ) {
        let name = name.to_str().unwrap_or("");
        // Look up name in parent directory
        // Return inode attributes
        // ...
        reply.error(libc::ENOENT);
    }

    fn getattr(&mut self, _req: &Request, ino: u64, reply: ReplyAttr) {
        // Read inode and convert to FileAttr
        let attr = FileAttr {
            ino,
            size: 0,
            blocks: 0,
            atime: UNIX_EPOCH,
            mtime: UNIX_EPOCH,
            ctime: UNIX_EPOCH,
            crtime: UNIX_EPOCH,
            kind: FileType::RegularFile,
            perm: 0o644,
            nlink: 1,
            uid: 1000,
            gid: 1000,
            rdev: 0,
            blksize: BLOCK_SIZE as u32,
            flags: 0,
        };
        reply.attr(&Duration::from_secs(1), &attr);
    }

    fn readdir(
        &mut self,
        _req: &Request,
        ino: u64,
        _fh: u64,
        offset: i64,
        mut reply: ReplyDirectory,
    ) {
        // List directory entries starting from offset
        // For each entry: reply.add(inode, offset, kind, name)
        reply.ok();
    }
}

fn main() {
    let mountpoint = "/tmp/simplefs";
    std::fs::create_dir_all(mountpoint).ok();

    let options = vec![
        MountOption::RW,
        MountOption::FSName("simplefs".to_string()),
    ];

    fuser::mount2(SimpleFSFuse {}, mountpoint, &options)
        .expect("Failed to mount filesystem");
}
```

With FUSE, you can literally `cd /tmp/simplefs`, create files, `ls`, and everything routes through your Rust code. It's an incredibly powerful development tool.

## What Real File Systems Get Right

Our SimpleFS is missing a lot of what makes production file systems reliable:

**Journaling.** If the system crashes between writing data and updating the inode, the file system is inconsistent. Journaling (ext4) or copy-on-write (btrfs, ZFS) prevents this.

**Crash consistency.** Every write sequence must be designed so that at any point of interruption, the file system is recoverable.

**Extent-based allocation.** Instead of tracking individual blocks, track contiguous runs of blocks. A 1GB file might need only a few extent descriptors instead of millions of block pointers.

**Caching.** Reading from disk for every operation is unacceptable. The VFS layer and page cache handle this in the kernel.

But the fundamentals are here — and they're the same fundamentals that ext4, XFS, and btrfs are built on. Everything else is optimization and reliability engineering.

Next up: we're going below the file system, below the OS, and building network protocols from raw packets.
