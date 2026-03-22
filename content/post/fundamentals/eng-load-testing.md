---
title: "Lesson 5: Load Testing — k6, vegeta, realistic patterns"
author: Atharva Pandey
keywords:
  - load testing
  - k6
  - vegeta
  - performance testing
  - stress testing
  - engineering practices
tags:
  - fundamentals
  - engineering practices
series: "Engineering Practices That Scale"
lesson: 5
date: '2024-07-13T00:00:00.000Z'
---

We load-tested our new checkout service before launch. 1,000 virtual users, 10 minutes, all hitting `/v1/checkout` sequentially. It passed with excellent numbers. Launch day: real traffic hit the service, and it fell over at 200 concurrent users. The problem was our test. Real users don't all call the same endpoint in sequence. They browse, add to cart, apply discount codes, fill in addresses, and then checkout — a session that touches 8 different endpoints over 4 minutes. Our test didn't model this. Our test was measuring the wrong thing.

Load testing is useful. Realistic load testing is hard.

## How It Works

**Types of Load Tests**

Different tests answer different questions:

- **Load test**: Does the system handle expected production load? Run at expected peak traffic, measure that SLOs are met.
- **Stress test**: Where does the system break? Gradually increase load past expected maximum. Find the breaking point. Understand how it fails (gracefully or catastrophically).
- **Spike test**: Can the system handle sudden traffic spikes? Jump from baseline to 10x in seconds. Models viral moments, flash sales.
- **Soak test**: Does the system hold up over time? Run at moderate load for hours. Find memory leaks, connection pool exhaustion that manifests over hours, not minutes.
- **Breakpoint test**: Automated stress test that ramps until failure to find maximum capacity.

**k6**

k6 is my primary tool. It's scripted in JavaScript, runs as a single binary, has excellent metrics export, and supports complex user flows. It's designed for modern API testing.

```javascript
// k6 script: checkout_flow.js
import http from 'k6/http';
import { sleep, check } from 'k6';
import { Rate } from 'k6/metrics';

const errorRate = new Rate('errors');

// Test configuration
export const options = {
  stages: [
    { duration: '2m', target: 20 },   // Ramp up to 20 VUs over 2 minutes
    { duration: '10m', target: 200 },  // Ramp to 200 VUs over 10 minutes
    { duration: '5m', target: 200 },   // Hold at 200 VUs
    { duration: '2m', target: 0 },     // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p95<500'],    // 95% of requests < 500ms
    http_req_failed: ['rate<0.01'],    // < 1% error rate
    errors: ['rate<0.01'],
  },
};

// Base URL from environment variable
const BASE_URL = __ENV.BASE_URL || 'https://api.example.com';

// Simulate a realistic checkout session
export default function () {
  // Step 1: Browse product
  const productRes = http.get(`${BASE_URL}/v1/products/prod-123`, {
    headers: { 'Authorization': `Bearer ${getAuthToken()}` },
  });
  check(productRes, { 'product loaded': (r) => r.status === 200 });
  sleep(randomBetween(1, 3)); // User reads product page

  // Step 2: Add to cart
  const cartRes = http.post(`${BASE_URL}/v1/cart/items`, JSON.stringify({
    product_id: 'prod-123',
    quantity: 1,
  }), {
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${getAuthToken()}` },
  });
  check(cartRes, { 'item added': (r) => r.status === 201 });
  const cartID = cartRes.json('cart_id');
  sleep(randomBetween(0.5, 2));

  // Step 3: Apply coupon (happens 30% of the time in production)
  if (Math.random() < 0.3) {
    http.post(`${BASE_URL}/v1/cart/${cartID}/coupons`, JSON.stringify({
      code: 'SAVE10',
    }), { headers: { 'Content-Type': 'application/json' } });
    sleep(0.5);
  }

  // Step 4: Checkout
  const checkoutRes = http.post(`${BASE_URL}/v1/checkout`, JSON.stringify({
    cart_id: cartID,
    payment_method_id: 'pm-test-visa',
    shipping_address: testAddress(),
  }), {
    headers: { 'Content-Type': 'application/json' },
    timeout: '10s', // checkout can take longer
  });
  const success = check(checkoutRes, {
    'checkout succeeded': (r) => r.status === 200,
  });
  if (!success) {
    errorRate.add(1);
  }

  sleep(randomBetween(1, 5)); // Wait between sessions
}

function randomBetween(min, max) {
  return Math.random() * (max - min) + min;
}

function testAddress() {
  return { street: '123 Test St', city: 'Testville', zip: '12345', country: 'US' };
}
```

Run it:

```bash
# Run against staging
BASE_URL=https://staging.api.example.com k6 run checkout_flow.js

# With output to InfluxDB for long-running tests
k6 run --out influxdb=http://localhost:8086/k6 checkout_flow.js

# With summary at end
k6 run --summary-trend-stats="avg,min,med,max,p(95),p(99)" checkout_flow.js
```

**vegeta**

vegeta is simpler — it reads a list of targets and fires them at a constant rate. Great for HTTP testing with a simple pattern and for creating precise constant-rate loads.

```bash
# Create targets file
cat > targets.txt << 'EOF'
GET https://api.example.com/v1/orders/123
Authorization: Bearer test-token

GET https://api.example.com/v1/products/456
Authorization: Bearer test-token

POST https://api.example.com/v1/cart/items
Content-Type: application/json
Authorization: Bearer test-token
@cart_item.json
EOF

# Run at 100 requests/second for 60 seconds
echo "GET https://api.example.com/v1/products/123" | \
  vegeta attack -duration=60s -rate=100 | \
  vegeta report

# Generate a histogram
echo "GET https://api.example.com/v1/products/123" | \
  vegeta attack -duration=30s -rate=500 | \
  vegeta report -type=hist[0,5ms,10ms,50ms,100ms,500ms,1s]
```

**Realistic Traffic Modeling**

The most common load testing mistake: all virtual users do the same thing. Real traffic is a distribution. Get this data from your production logs:

```bash
# Extract request distribution from nginx logs
cat /var/log/nginx/access.log | \
  awk '{print $7}' | \
  sed 's/\?.*$//' | \
  sort | uniq -c | sort -rn | head -20

# Output:
#  23847 /v1/products
#  18293 /v1/cart
#   9847 /v1/checkout
#   7234 /v1/orders
```

Use this ratio to weight your load test endpoints proportionally to actual production traffic.

## Why It Matters

Load tests catch capacity issues before users do. A service that handles 100 requests/second in normal operation might hit a connection pool limit at 150/second — and this is discoverable with a 20-minute test, not a production incident. Load testing also validates your SLOs: "our SLO is p95 < 300ms" should be verified under expected peak load before you commit to it.

The results also inform capacity planning. If your service saturates at 500 rps on a single instance, you know your horizontal scaling targets before a traffic spike.

## Production Example

A load test pipeline that runs on every major release:

```yaml
# .github/workflows/load-test.yaml
name: Load Test
on:
  workflow_dispatch:
    inputs:
      target_rps:
        description: 'Target requests per second'
        default: '200'
      duration:
        description: 'Test duration (e.g., 5m)'
        default: '5m'

jobs:
  load-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup k6
        run: |
          sudo gpg --no-default-keyring --keyring /usr/share/keyrings/k6-archive-keyring.gpg \
            --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys C5AD17C747E3415A3642D57D77C6C491D6AC1D69
          echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" \
            | sudo tee /etc/apt/sources.list.d/k6.list
          sudo apt-get update && sudo apt-get install k6

      - name: Run load test
        env:
          BASE_URL: ${{ secrets.STAGING_BASE_URL }}
          AUTH_TOKEN: ${{ secrets.LOAD_TEST_TOKEN }}
        run: |
          k6 run \
            --env BASE_URL=$BASE_URL \
            --env AUTH_TOKEN=$AUTH_TOKEN \
            --summary-trend-stats="avg,min,med,max,p(95),p(99)" \
            tests/load/checkout_flow.js

      - name: Check thresholds
        # k6 exits with non-zero if thresholds fail
        # This fails the CI pipeline if p95 > 500ms or error rate > 1%
```

Finding memory leaks with a soak test:

```javascript
// soak_test.js — run for 2 hours at moderate load
export const options = {
  stages: [
    { duration: '5m', target: 50 },   // Ramp up
    { duration: '2h', target: 50 },   // Hold for 2 hours
    { duration: '5m', target: 0 },    // Ramp down
  ],
};
```

Watch your service's memory usage during this test. If it grows monotonically over 2 hours without leveling off, you have a memory leak. Common culprits in Go: goroutine leaks, growing caches without eviction, long-lived contexts holding references.

## The Tradeoffs

**Test environment parity**: Load testing staging instead of production means your test data, network topology, and resource sizing may differ. Load testing production during off-hours with real test accounts is more accurate but riskier. At minimum, ensure staging uses the same instance types and database sizes as production.

**Test data state**: Multiple virtual users doing checkout need separate cart IDs, separate user accounts. Load tests that share state give incorrect results — the second checkout from the same cart will fail, skewing error rates. Seed realistic test data and ensure each virtual user operates on isolated resources.

**Synthetic vs real traffic**: k6 generates synthetic load. Real production traffic has patterns your synthetic test doesn't model: bot traffic, mobile clients with slow connections, retries from clients after failures. Production performance profiling (sampling real requests) catches issues synthetic tests miss.

**Infrastructure cost**: Running 500 virtual users for 30 minutes from a CI runner consumes resources. For high-load tests, provision a dedicated load generator instance. For cloud-native testing, k6 Cloud and Artillery Cloud distribute load from multiple geographic regions.

**Database load testing**: Load tests hammer databases. Ensure your test database can handle it without cross-contaminating production data or affecting production database performance if they share infrastructure.

## Key Takeaway

Load testing is only useful if it models realistic user behavior. A test that hits one endpoint with uniform load tells you nothing about how the system behaves under the complex, multi-step, probabilistic flow of actual users. Model your test on production request distributions. Use k6 for scripted, realistic user journeys and vegeta for simple constant-rate HTTP testing. Run soak tests to find memory leaks. Fail CI if performance thresholds aren't met under load. And always load test in an environment that mirrors production sizing — a test that passes on half the resources gives you false confidence.

---

**Previous:** [Lesson 4: Monitoring and Alerting](/post/fundamentals/eng-monitoring/)
**Next:** [Lesson 6: Feature Flags — Progressive rollout and kill switches](/post/fundamentals/eng-feature-flags/)
