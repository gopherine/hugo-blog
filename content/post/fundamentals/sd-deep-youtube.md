---
title: "Lesson 1: Design YouTube — Video upload, transcoding, streaming at scale"
author: "Atharva Pandey"
series: "System Design Deep Dives"
lesson: 1
tags:
  - fundamentals
  - system design
  - interviews
keywords:
  - YouTube system design
  - video transcoding pipeline
  - adaptive bitrate streaming
  - CDN video delivery
  - distributed video processing
date: "2024-04-09T00:00:00.000Z"
---

YouTube serves over 500 hours of video uploaded every minute and delivers billions of views per day. When I first studied this problem seriously, I made the mistake of treating it as a simple "upload file, store it, serve it" exercise. It isn't. The interesting engineering is in what happens between the moment a creator hits upload and the moment a viewer's video starts playing seamlessly on a 3G connection in rural India. That gap is where the system design lives.

## Requirements

Before drawing boxes, nail down the scope.

**Functional requirements**
- Users can upload videos up to 4K resolution, maximum 12 hours long
- Uploaded videos are transcoded into multiple resolutions (360p, 480p, 720p, 1080p, 4K) and formats (H.264, H.265, VP9)
- Videos are served with adaptive bitrate streaming — quality adjusts to available bandwidth
- Users can search, view, like, comment, and subscribe
- Creators see view counts and basic analytics

**Non-functional requirements**
- Upload availability: 99.9% (a failed upload is lost work for a creator)
- Playback availability: 99.99% (viewers will leave in seconds if buffering)
- Eventual consistency is acceptable for like counts and view counts
- Video playback should start within 2 seconds on a stable connection
- Support for 1 billion daily active users

**Scale estimates**
- 500 hours of video uploaded per minute → ~30,000 hours per hour
- Average video: 1 hour long, 2 GB in raw format → ~60 TB of raw uploads per hour
- After transcoding into 5 resolutions, storage multiplies roughly 4x → ~240 TB/hour stored
- Read-to-write ratio: heavily skewed toward reads; most videos are watched far more than uploaded

## High-Level Design

The system splits naturally into three planes: the **upload pipeline**, the **storage layer**, and the **serving layer**.

```
Creator → Upload Service → Message Queue → Transcoding Workers
                                                    ↓
                                         Distributed Object Store
                                                    ↓
                                              CDN Edge Nodes
                                                    ↓
                                              Viewer Client
```

**Upload flow**

A creator's client uploads the raw video directly to an object store (think S3 or GCS) using a pre-signed URL issued by the Upload Service. This is important: the application server never touches the video bytes. Direct-to-object-store uploads remove the app tier as a bottleneck and avoid re-transmitting gigabytes through your datacenter.

Once the upload completes, the object store emits an event (or the client notifies the Upload Service, which publishes a message) to a durable message queue. The message contains the video ID, raw object path, creator ID, and metadata.

**Transcoding pipeline**

Transcoding workers pull jobs from the queue. Each worker picks up one video and fans it out into parallel encoding jobs — one per output format and resolution. This fan-out is crucial: encoding a single 4K video to all target formats sequentially would take hours. In parallel, it takes minutes.

```go
type TranscodeJob struct {
    VideoID    string
    InputPath  string
    OutputPath string
    Resolution string // "360p", "720p", "1080p", "4k"
    Codec      string // "h264", "vp9", "h265"
}

func fanOutTranscoding(raw RawUpload, queue JobQueue) error {
    profiles := []struct{ res, codec string }{
        {"360p", "h264"},
        {"480p", "h264"},
        {"720p", "h264"},
        {"1080p", "h264"},
        {"1080p", "vp9"},
        {"4k", "h265"},
    }
    for _, p := range profiles {
        job := TranscodeJob{
            VideoID:    raw.VideoID,
            InputPath:  raw.ObjectPath,
            OutputPath: fmt.Sprintf("transcoded/%s/%s_%s", raw.VideoID, p.res, p.codec),
            Resolution: p.res,
            Codec:      p.codec,
        }
        if err := queue.Publish(job); err != nil {
            return fmt.Errorf("publishing transcode job: %w", err)
        }
    }
    return nil
}
```

When all encoding jobs for a video complete, a coordinator marks the video as "ready" in the metadata database and makes it publicly visible.

## Deep Dive

**Adaptive Bitrate Streaming (ABR)**

Transcoding produces individual video files, but that's not what gets served to clients. The output gets packaged into streaming segments — typically 2–10 second chunks — and a manifest file (`.m3u8` for HLS, `.mpd` for DASH) that lists all available quality levels and the segment URLs for each.

The client player downloads the manifest first. Then it monitors its download speed and buffer depth in real time, continuously selecting the next segment from the most appropriate quality tier. If your connection drops, you step down to 360p and playback continues. If bandwidth recovers, you step back up to 1080p. The player manages this autonomously, segment by segment.

This means storage isn't just one file per resolution. It's thousands of small segment files per resolution. A 2-hour video at 6 quality levels, segmented into 4-second chunks, generates 6 × 1800 = 10,800 segment files plus manifests. This is why CDN is not optional — it's load-bearing.

**CDN Architecture**

YouTube uses a tiered CDN. Edge nodes (PoPs, Points of Presence) are geographically distributed close to viewers. When a viewer requests a segment, the edge node checks its cache. On a cache miss, it fetches from a regional origin node. If the regional node misses, it fetches from the central object store.

Popular videos — the top 1% by views — account for roughly 95% of traffic. These are pre-warmed into CDN edges proactively. Tail videos (uploaded by small creators, rarely watched) are served from origin on demand and may not get CDN-cached at all. This asymmetry matters for cache sizing: you don't need to cache everything, just the right things.

**Metadata Storage**

Video metadata (title, description, creator, tags, view count, like count) lives in a relational or document database, sharded by video ID. View counts and like counts are eventually consistent — they're updated via aggregation pipelines from event logs, not direct increments on the primary row. This is deliberate: incrementing a counter in a relational database for every view at YouTube scale would destroy write throughput.

## Scaling Challenges

**The thundering herd problem**

When a major creator publishes a new video to 50 million subscribers, millions of viewers hit the CDN simultaneously for the first few minutes before the video is widely cached. Edge nodes that haven't cached the video yet all miss and flood the origin simultaneously. Mitigation strategies include: pre-warming the CDN before making the video publicly visible, request coalescing at the CDN edge (multiple concurrent misses for the same object trigger only one origin fetch), and origin rate limiting with a local queue.

**Transcoding latency vs. availability**

A creator expects their video to be available shortly after upload. But full transcoding of a 4K, 2-hour video across all profiles can take 30+ minutes even with parallelism. YouTube solves this with **progressive availability**: they make the video available in lower resolutions first (360p might be ready in 3 minutes), then higher resolutions appear as they complete. The manifest is updated incrementally.

**Resumable uploads**

Mobile creators upload on unreliable connections. A 2 GB upload that fails at 90% shouldn't require starting over. The upload service issues a resumable upload session token. The client tracks the last confirmed byte and can resume from that offset. Object stores like GCS support this natively via their resumable upload API.

**Storage costs**

Storing every resolution permanently is expensive. YouTube doesn't keep the raw upload file after transcoding completes. They also down-tier old, rarely watched videos to cheaper cold storage. For videos with fewer than a threshold of views per quarter, lower-resolution versions may be kept while very high resolution versions are deleted and regenerated on demand if views spike.

## Interview Tips

The interviewer is watching for a few specific things in a YouTube design:

**Don't conflate upload and streaming.** These are separate systems with different requirements. Upload is write-heavy, high latency tolerant, needs durability. Streaming is read-heavy, latency sensitive, needs edge distribution. Treat them separately.

**Explain why the app server doesn't touch video bytes.** Pre-signed URLs and direct-to-object-store uploads are the correct answer. If you route GBs through your application tier, you'll be asked why immediately.

**Mention transcoding fan-out explicitly.** The interviewer wants to know you understand that sequential multi-profile encoding is impractical and that parallelism is the solution.

**Talk about CDN tiering.** "Put it on a CDN" is table stakes. Explaining that you pre-warm popular content, use regional origin tiers, and don't cache tail content shows depth.

**Acknowledge consistency trade-offs.** View counts don't need to be strongly consistent. Like counts can lag. Explaining why you'd use event aggregation rather than direct counter increments shows that you understand the cost of consistency at scale.

## Key Takeaway

YouTube is a master class in separating planes of a system. The upload pipeline, the transcoding pipeline, and the serving pipeline are loosely coupled via message queues and object storage. Each can scale independently. The hardest part isn't storing videos — it's the transcoding fan-out that produces thousands of segments per video, and the CDN architecture that delivers the right segment to the right viewer in under 100ms regardless of where they are in the world. Every design decision points back to one constraint: the bottleneck is always bandwidth, and you solve bandwidth problems by moving data closer to the consumer.

---

**Up next:** [Lesson 2: Design Uber — Real-time matching, geospatial indexing, surge pricing](/post/fundamentals/sd-deep-uber/)
