---
title: "Lesson 3: Cryptography — ring, RustCrypto, and sodiumoxide"
author: Atharva Pandey
series: "Rust Security in Production"
lesson: 3
keywords: [Rust, rust, security, cryptography, ring, RustCrypto, sodiumoxide, hashing, encryption, signatures]
tags: [Rust tutorial, rust, security]
date: '2025-05-05T11:47:00.000Z'
---

I'm going to say something controversial: most developers should never write cryptographic code. Not because they're not smart enough — because the field is absurdly hostile to even tiny mistakes. A single branch in your constant-time comparison function leaks timing information. A reused nonce in AES-GCM completely destroys confidentiality. An ECDSA implementation with a biased random number generator leaks your private key after enough signatures.

But you still need to *use* cryptography. Every production system needs hashing, encryption, signatures, or key derivation at some point. The trick is picking the right library, using it correctly, and understanding just enough of the theory to avoid the common footguns.

## The Three Rust Crypto Ecosystems

There are three major crypto ecosystems in Rust, and they serve different purposes.

### ring — The Fortress

`ring` is a Rust wrapper around BoringSSL's crypto primitives, maintained by Brian Smith. It's opinionated, battle-tested, and intentionally limited in scope. If it supports what you need, use it.

```toml
[dependencies]
ring = "0.17"
```

**Strengths:** Fast, audited, constant-time, used by rustls (which powers most Rust TLS). Minimal API surface means fewer ways to screw up.

**Limitations:** Doesn't support everything. No AES-CBC (intentionally — CBC is a footgun). No RSA encryption (only signing). If you need something ring doesn't offer, that's by design.

### RustCrypto — The Swiss Army Knife

RustCrypto is a collection of pure-Rust implementations. Each algorithm is its own crate: `aes`, `sha2`, `chacha20poly1305`, `ed25519-dalek`, etc.

```toml
[dependencies]
sha2 = "0.10"
aes-gcm = "0.10"
argon2 = "0.5"
ed25519-dalek = "2"
rand = "0.8"
```

**Strengths:** Pure Rust (no C dependencies), broad algorithm support, good for `no_std` environments, active community.

**Limitations:** Not all crates have been formally audited. Quality varies across the dozens of crates. You need to know which algorithm to pick.

### sodiumoxide — The NaCl Wrapper

`sodiumoxide` wraps libsodium, which is the portable version of Dan Bernstein's NaCl library. It's the "just give me safe defaults" option.

```toml
[dependencies]
sodiumoxide = "0.2"
```

**Strengths:** Excellent defaults, hard to misuse, well-audited underlying library.

**Limitations:** Requires linking to libsodium (C library). The Rust crate hasn't seen as much maintenance recently — check the repo before committing.

## Password Hashing — Get This Right

If you store passwords, you need a proper password hashing function. Not SHA-256. Not even SHA-256 with a salt. You need argon2, bcrypt, or scrypt — algorithms specifically designed to be slow and memory-hard.

### Argon2 (Recommended)

```rust
use argon2::{
    password_hash::{rand_core::OsRng, PasswordHash, PasswordHasher, PasswordVerifier, SaltString},
    Argon2,
};

fn hash_password(password: &str) -> Result<String, argon2::password_hash::Error> {
    let salt = SaltString::generate(&mut OsRng);
    let argon2 = Argon2::default(); // Argon2id with sensible defaults

    let hash = argon2.hash_password(password.as_bytes(), &salt)?;
    Ok(hash.to_string())
}

fn verify_password(password: &str, hash: &str) -> Result<bool, argon2::password_hash::Error> {
    let parsed_hash = PasswordHash::new(hash)?;
    Ok(Argon2::default()
        .verify_password(password.as_bytes(), &parsed_hash)
        .is_ok())
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let password = "hunter2";

    let hash = hash_password(password)?;
    println!("Hash: {}", hash);

    assert!(verify_password(password, &hash)?);
    assert!(!verify_password("wrong_password", &hash)?);

    println!("Password hashing works correctly");
    Ok(())
}
```

The `Argon2::default()` uses Argon2id, which is the recommended variant. It combines resistance against both side-channel attacks (Argon2i) and GPU attacks (Argon2d). The salt is generated from a cryptographic RNG. The output is a PHC string format that embeds the parameters, so you can upgrade them later without breaking existing hashes.

### What NOT To Do

```rust
// DO NOT DO THIS. Seriously.
use sha2::{Sha256, Digest};

fn terrible_hash(password: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(password.as_bytes());
    format!("{:x}", hasher.finalize())
}
// This is crackable on a single GPU in minutes for most passwords.
// SHA-256 is a fast hash — that's the OPPOSITE of what you want for passwords.
```

## Symmetric Encryption — AES-GCM and ChaCha20-Poly1305

For encrypting data, you want authenticated encryption (AEAD). This means your ciphertext includes a tag that detects tampering. The two standard choices are AES-256-GCM and ChaCha20-Poly1305.

### AES-256-GCM with RustCrypto

```rust
use aes_gcm::{
    aead::{Aead, KeyInit, OsRng},
    Aes256Gcm, Nonce,
};
use rand::RngCore;

struct Encryptor {
    cipher: Aes256Gcm,
}

impl Encryptor {
    fn new(key: &[u8; 32]) -> Self {
        let cipher = Aes256Gcm::new(key.into());
        Encryptor { cipher }
    }

    fn encrypt(&self, plaintext: &[u8]) -> Result<Vec<u8>, aes_gcm::Error> {
        // CRITICAL: Never reuse a nonce with the same key.
        // For AES-GCM, nonce reuse completely breaks security.
        let mut nonce_bytes = [0u8; 12];
        rand::thread_rng().fill_bytes(&mut nonce_bytes);
        let nonce = Nonce::from_slice(&nonce_bytes);

        let ciphertext = self.cipher.encrypt(nonce, plaintext)?;

        // Prepend the nonce so we can decrypt later
        let mut result = Vec::with_capacity(12 + ciphertext.len());
        result.extend_from_slice(&nonce_bytes);
        result.extend_from_slice(&ciphertext);
        Ok(result)
    }

    fn decrypt(&self, data: &[u8]) -> Result<Vec<u8>, aes_gcm::Error> {
        if data.len() < 12 {
            return Err(aes_gcm::Error);
        }

        let (nonce_bytes, ciphertext) = data.split_at(12);
        let nonce = Nonce::from_slice(nonce_bytes);

        self.cipher.decrypt(nonce, ciphertext)
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    // In production, derive this from a KMS or key derivation function
    let mut key = [0u8; 32];
    rand::thread_rng().fill_bytes(&mut key);

    let encryptor = Encryptor::new(&key);

    let secret = b"credit card: 4111-1111-1111-1111";
    let encrypted = encryptor.encrypt(secret).expect("encryption failed");
    let decrypted = encryptor.decrypt(&encrypted).expect("decryption failed");

    assert_eq!(secret.as_slice(), decrypted.as_slice());
    println!("Encryption round-trip successful");

    // Verify tamper detection works
    let mut tampered = encrypted.clone();
    tampered[20] ^= 0xFF; // flip some bits in the ciphertext
    assert!(encryptor.decrypt(&tampered).is_err());
    println!("Tamper detection works");

    Ok(())
}
```

The biggest footgun here is nonce reuse. With AES-GCM, if you encrypt two different plaintexts with the same key and nonce, an attacker can XOR the ciphertexts and recover both plaintexts. Game over.

For high-volume encryption (more than 2^32 messages with the same key), consider AES-256-GCM-SIV or XChaCha20-Poly1305, which have larger nonce spaces and are more forgiving of nonce management.

### ChaCha20-Poly1305 with ring

```rust
use ring::aead::{self, LessSafeKey, UnboundKey, Nonce, Aad, CHACHA20_POLY1305};
use ring::rand::{SystemRandom, SecureRandom};

fn encrypt_with_ring(
    key_bytes: &[u8; 32],
    plaintext: &[u8],
) -> Result<Vec<u8>, ring::error::Unspecified> {
    let rng = SystemRandom::new();

    let unbound_key = UnboundKey::new(&CHACHA20_POLY1305, key_bytes)?;
    let key = LessSafeKey::new(unbound_key);

    let mut nonce_bytes = [0u8; 12];
    rng.fill(&mut nonce_bytes)?;
    let nonce = Nonce::assume_unique_for_key(nonce_bytes);

    // ring encrypts in place, so we need to copy the plaintext
    // and leave room for the tag
    let mut in_out = plaintext.to_vec();
    key.seal_in_place_append_tag(nonce, Aad::empty(), &mut in_out)?;

    // Prepend nonce
    let mut result = Vec::with_capacity(12 + in_out.len());
    result.extend_from_slice(&nonce_bytes);
    result.extend_from_slice(&in_out);
    Ok(result)
}

fn decrypt_with_ring(
    key_bytes: &[u8; 32],
    data: &[u8],
) -> Result<Vec<u8>, ring::error::Unspecified> {
    if data.len() < 12 {
        return Err(ring::error::Unspecified);
    }

    let (nonce_bytes, ciphertext) = data.split_at(12);
    let nonce = Nonce::assume_unique_for_key(
        nonce_bytes.try_into().map_err(|_| ring::error::Unspecified)?,
    );

    let unbound_key = UnboundKey::new(&CHACHA20_POLY1305, key_bytes)?;
    let key = LessSafeKey::new(unbound_key);

    let mut in_out = ciphertext.to_vec();
    let plaintext = key.open_in_place(nonce, Aad::empty(), &mut in_out)?;
    Ok(plaintext.to_vec())
}
```

Notice how ring's API uses `assume_unique_for_key` — that name is a reminder that nonce uniqueness is YOUR responsibility.

## Digital Signatures — Ed25519

For signing and verifying data (API requests, JWTs, software updates), Ed25519 is the standard recommendation. It's fast, has small keys and signatures, and is resistant to many implementation pitfalls.

```rust
use ed25519_dalek::{Signer, SigningKey, Verifier, VerifyingKey};
use rand::rngs::OsRng;

fn signature_example() -> Result<(), Box<dyn std::error::Error>> {
    // Generate a keypair
    let signing_key = SigningKey::generate(&mut OsRng);
    let verifying_key: VerifyingKey = signing_key.verifying_key();

    // Sign a message
    let message = b"transfer $1000 to account XYZ";
    let signature = signing_key.sign(message);

    // Verify the signature
    verifying_key.verify(message, &signature)?;
    println!("Signature valid");

    // Verify tampered message fails
    let tampered = b"transfer $9999 to account XYZ";
    assert!(verifying_key.verify(tampered, &signature).is_err());
    println!("Tamper detection works");

    // Serialize keys for storage
    let secret_bytes: [u8; 32] = signing_key.to_bytes();
    let public_bytes: [u8; 32] = verifying_key.to_bytes();
    println!("Secret key: {} bytes", secret_bytes.len());
    println!("Public key: {} bytes", public_bytes.len());

    // Deserialize
    let restored_signing = SigningKey::from_bytes(&secret_bytes);
    let restored_verifying = VerifyingKey::from_bytes(&public_bytes)?;

    // Verify with restored keys
    restored_verifying.verify(message, &signature)?;
    println!("Restored key verification works");

    Ok(())
}
```

## Key Derivation — HKDF

When you have a master key and need to derive multiple purpose-specific keys, use HKDF:

```rust
use ring::hkdf;

fn derive_keys(master_key: &[u8]) -> Result<([u8; 32], [u8; 32]), ring::error::Unspecified> {
    let salt = hkdf::Salt::new(hkdf::HKDF_SHA256, b"my-app-salt-v1");
    let prk = salt.extract(master_key);

    // Derive an encryption key
    let mut encryption_key = [0u8; 32];
    let okm = prk.expand(&[b"encryption-key-v1"], My32ByteLen)?;
    okm.fill(&mut encryption_key)?;

    // Derive a signing key
    let mut signing_key = [0u8; 32];
    let okm = prk.expand(&[b"signing-key-v1"], My32ByteLen)?;
    okm.fill(&mut signing_key)?;

    Ok((encryption_key, signing_key))
}

// ring requires you to define a type for the output length
struct My32ByteLen;

impl hkdf::KeyType for My32ByteLen {
    fn len(&self) -> usize {
        32
    }
}
```

The `info` parameter (e.g., `b"encryption-key-v1"`) is critical — it ensures that even if the same master key is used, different purposes get different derived keys. Version your info strings so you can rotate key derivation parameters without rotating the master key.

## Common Mistakes I've Seen in Production

**1. Using `rand::thread_rng()` for key generation.** ThreadRng is a good CSPRNG, but use `OsRng` for key material. It reads directly from the OS entropy source.

```rust
use rand::rngs::OsRng;
use rand::RngCore;

fn generate_key() -> [u8; 32] {
    let mut key = [0u8; 32];
    OsRng.fill_bytes(&mut key);
    key
}
```

**2. Comparing MACs with `==`.** This is vulnerable to timing attacks. Use constant-time comparison:

```rust
use ring::constant_time;

fn verify_mac(computed: &[u8], received: &[u8]) -> bool {
    constant_time::verify_slices_are_equal(computed, received).is_ok()
}
```

**3. Hardcoding keys in source code.** Don't do it. Load keys from environment variables, a vault, or a KMS at startup. We'll cover this more in the next lesson on secret management.

**4. Not zeroing key material.** When you're done with a key, you need to zero the memory. Otherwise it sits in RAM, gets paged to disk, or shows up in a core dump. The next lesson covers `zeroize` for this.

## Which Library Should You Pick?

Here's my decision tree:

- **TLS/HTTPS?** Use `rustls` (built on `ring`). Don't roll your own.
- **Password hashing?** Use the `argon2` crate from RustCrypto.
- **Symmetric encryption?** Use `ring` for ChaCha20-Poly1305 or `aes-gcm` from RustCrypto.
- **Signing?** Use `ed25519-dalek` from RustCrypto or `ring`'s Ed25519 support.
- **Everything else?** Check if `ring` supports it first. If not, use RustCrypto. Use `sodiumoxide` if you want a batteries-included API and don't mind the C dependency.

Don't mix and match without reason. Pick one ecosystem for each purpose and stick with it. Key management gets hairy fast when you have three different serialization formats for the same key.

## The Bottom Line

Cryptography in Rust benefits from the same memory safety guarantees as everything else — no buffer overflows in your crypto code, no use-after-free on key material (unless you use `unsafe`). But the algorithm-level pitfalls remain: nonce reuse, timing leaks, weak key derivation, improper MAC verification.

Use established libraries. Use their defaults. Read the docs for every function you call — especially the safety requirements around nonces, key sizes, and initialization vectors. And when in doubt, ask someone who does this for a living to review your crypto code.

The memory is safe. Make sure the cryptography is too.
