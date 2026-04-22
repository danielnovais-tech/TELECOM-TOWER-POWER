# Authentication

Every request needs the `X-API-Key` header. Keys are created in the [portal](https://app.telecomtowerpower.com.br/portal) once a plan is active.

## Demo keys

Public keys rotated monthly — great for evaluating the API before subscribing.

- Prefix: `demo_try_…`
- Rate: **6 rpm** (hard cap regardless of tier claimed)
- No PDF, no AI
- Responses carry an `X-Demo-Key: true` header

## Production keys

- Prefix: `ttp_` followed by 32 random characters (`secrets.token_urlsafe(32)`)
- Delivered by email at checkout
- Persistent: they do not rotate automatically — rotate manually in the portal if compromised

## Best practices

- **Never** ship a key in frontend code. Use a backend proxy.
- Enterprise plans support IP allowlists.
- Watch the `X-RateLimit-Remaining` header to avoid `429` responses.
