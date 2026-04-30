# Rate limits

Every key uses a sliding 60-second window. When exceeded the API returns `429 Too Many Requests`.

| Plan | Rpm | Calls/mo | Batch receivers |
|---|---:|---:|---:|
| Free | 10 | 200 | — |
| Starter | 30 | 3,000 | 100 |
| Pro | 100 | 25,000 | 500 |
| Business | 300 | 150,000 | 2,000 |
| Enterprise | 1,000 | custom | 10,000 |
| Ultra | 5,000 | custom | 50,000 |

## Response headers

- `X-RateLimit-Limit` — current rpm ceiling
- `X-RateLimit-Remaining` — calls still available in the window
- `Retry-After` — present on 429 responses

## Demo keys

Additional hard cap of **6 rpm** regardless of the nominal tier of the key.
