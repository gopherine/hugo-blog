---
title: "Lesson 11: Design a Notification System — Push vs Pull, Priority, Dedup"
author: "Atharva Pandey"
series: "System Design from First Principles"
lesson: 11
tags:
  - fundamentals
  - system design
keywords:
  - notification system design
  - push notifications
  - APNs
  - FCM
  - deduplication
  - priority queues
  - system design
date: "2024-09-21T00:00:00.000Z"
---

Notifications are the feature that can make or break user retention — and also destroy it. Done right, they bring users back at exactly the right moment. Done wrong, they're spam that drives uninstalls. The system design challenge isn't just the technical plumbing (though that's interesting). It's building infrastructure that's fast for critical alerts, reliable for important messages, and smart enough to not overwhelm users with low-priority noise.

## The Core Concept

A notification system has to handle multiple channels with wildly different characteristics:
- **Push notifications** (APNs for iOS, FCM for Android, Web Push): delivered to mobile/web clients when the app is not open. Near-instant, but delivery is not guaranteed.
- **In-app notifications**: shown inside the app when the user is active. Fetched via polling or WebSocket.
- **Email**: high deliverability, but asynchronous. Users expect inbox delivery, not real-time.
- **SMS**: expensive, but highest engagement rate. Used for critical alerts (OTPs, fraud alerts).

Each channel has different latency, cost, and delivery guarantee characteristics. A well-designed notification system routes each notification to the appropriate channel based on user preferences, notification priority, and device state.

**Push vs. Pull for in-app notifications**

For notifications displayed inside the app:
- **Pull**: the app polls `GET /notifications?since=lastTimestamp` periodically. Simple but adds latency and wastes bandwidth on empty responses.
- **Push**: the server sends new notifications over an existing WebSocket connection. Near-instant delivery for active users.

For mobile push when the app is in the background: the server sends a push notification via APNs/FCM, which wakes the app or shows a system notification.

## How to Design It

**System architecture**

```
[Event Sources] → [Notification Service] → [Kafka queue] → [Channel Workers]
  - Post liked                                                - Push worker (APNs/FCM)
  - Comment received                                         - Email worker (SendGrid)
  - Follow received                                          - SMS worker (Twilio)
  - Payment processed                                        - In-app worker
```

Multiple services generate notification events (post liked, order shipped, fraud alert). Rather than each service having notification-sending logic, they publish events to a central notification service. This prevents coupling between your business logic services and the notification infrastructure.

The notification service:
1. Receives the event
2. Looks up user preferences (do they have push enabled? Email digest or instant?)
3. Looks up device tokens for the user
4. Enqueues delivery jobs to the appropriate channel queue
5. Channel workers process the queue and call APNs/FCM/SendGrid

**Priority queues**

Not all notifications are equal. An OTP for login, a fraud alert, or a payment confirmation needs to arrive within seconds. A "someone liked your post" notification can wait. Using a single queue treats both equally.

Use separate queues by priority:
- **Critical** (OTP, fraud, payment): high-priority Kafka topic, small consumer group running continuously, SLA of < 5 seconds
- **High** (direct messages, mentions): medium priority, SLA of < 30 seconds
- **Normal** (likes, follows, recommendations): low priority, can batch, SLA of < 5 minutes

```go
type NotificationPriority int

const (
    PriorityCritical NotificationPriority = iota
    PriorityHigh
    PriorityNormal
)

func (ns *NotificationService) Enqueue(n Notification) error {
    topic := topicForPriority(n.Priority) // "notifications.critical", etc.
    return ns.kafka.Publish(topic, n)
}
```

**Deduplication**

At-least-once delivery means the same notification could be delivered multiple times. If "order shipped" sends two emails, users get frustrated. Deduplication is essential.

Each notification has a unique idempotency key: typically a hash of `(event_type, entity_id, user_id, timestamp_bucket)`. Before sending, check if this key has been processed recently.

```go
func (w *PushWorker) Process(ctx context.Context, n Notification) error {
    dedupKey := fmt.Sprintf("notif:dedup:%s", n.IdempotencyKey)

    // SET with NX (set if not exists) + TTL
    set, err := w.redis.SetNX(ctx, dedupKey, "1", 24*time.Hour).Result()
    if err != nil {
        return err
    }
    if !set {
        // Already processed — skip
        return nil
    }

    return w.sendPush(ctx, n)
}
```

The TTL ensures the dedup key doesn't accumulate forever. 24 hours is a reasonable window — after that, a duplicate would be a genuinely new notification.

**Device token management**

For push notifications, you need the device token (a string APNs/FCM gives you when the user grants notification permission). These change when users reinstall the app. APNs/FCM tell you when a token is invalid (they return specific error codes). Your system needs to:
- Accept device token registration from the client
- Associate multiple tokens with a user (phone + tablet + web)
- Remove invalid tokens promptly (APNs will stop delivering if you keep sending to invalid tokens)

```sql
CREATE TABLE device_tokens (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL,
    token       VARCHAR(512) NOT NULL UNIQUE,
    platform    VARCHAR(10) NOT NULL, -- 'ios', 'android', 'web'
    is_active   BOOLEAN DEFAULT TRUE,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
```

**Notification preferences**

Users should control what they receive and how. A preferences store (per user) tracks:
- Which categories are enabled per channel (push/email/SMS)
- Quiet hours (don't send push between 11pm and 8am in my timezone)
- Digest mode (batch daily email rather than instant)

Notification workers check preferences before sending. Quiet hours require knowing the user's timezone — store this with the user profile.

**Batching and digests**

For low-priority notifications, batching improves user experience (one email with "5 people liked your post" is better than 5 separate emails) and reduces cost. A digest worker collects notifications over a window, groups by user and type, and sends a combined notification.

## Real-World Example

Airbnb's notification system handles booking confirmations, host messages, reminders, and promotional campaigns — each with different priority and channel requirements. They built a centralized "Notification Platform" that all teams use. Key features: preference management, A/B testing of notification copy, delivery rate tracking, and a suppression list (users who've opted out).

Stripe's critical path notifications (payment failed, fraud alert) go through a high-priority pipeline separate from marketing notifications. Missing a "payment failed" notification for an enterprise customer is a serious business problem. Sending a marketing email late is not.

LinkedIn sends billions of email notifications monthly. Their email pipeline is a separate microservice cluster, and they run sophisticated engagement models to decide which notifications to batch, skip, or send immediately based on the user's engagement history.

## Interview Tips

"How do you handle a user not receiving a notification?" Build an observability layer: log the notification event, enqueue timestamp, worker pickup time, channel API response, and delivery status. When a user complains, you can trace the notification's journey.

"What if APNs is down?" Retry with exponential backoff. APNs is a third-party service — it goes down occasionally. Your queue absorbs the backlog. Critical notifications should have a fallback channel (SMS if push fails after N retries).

"How do you prevent notification spam?" Rate limit notifications per user per time window. Prefer batching for the same notification type. Track unsubscribe rates and auto-suppress users with very high unsubscribe rates from low-priority campaigns.

"How do you scale to 1 billion users?" The architecture scales horizontally: more Kafka partitions, more channel workers. The bottleneck is often the third-party channel APIs (APNs/FCM have rate limits). You need multiple APNs connections and careful batching to stay within limits.

## Key Takeaway

Notification systems span multiple channels with different latency, cost, and delivery guarantees. Route by priority to separate queues to guarantee SLAs for critical alerts without sacrificing throughput for lower-priority messages. Deduplication via idempotency keys in Redis prevents double-delivery under at-least-once semantics. Device token lifecycle management (registration, invalidation, multi-device) is an operational concern that's easy to overlook. User preference management and batching are product concerns with significant infrastructure implications. Design the end-to-end observability story so you can debug delivery failures — because your users will report them.

---

**Previous:** [Lesson 10: Design a News Feed](/post/fundamentals/sd-news-feed/)
**Next:** [Lesson 12: Design a Search Engine — Inverted Index and Ranking](/post/fundamentals/sd-search-engine/)
