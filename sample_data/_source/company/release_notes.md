# Lumen Platform Release Notes

*Synthetic demo document — fictional company, not real data.*

Release history for the Lumen Platform and API version 2, newest first.

## v2.5.0 (12 August 2026)

### Added
- Webhooks for `user.created`, `user.deactivated` and `project.updated` events, signed with HMAC-SHA256 in the `X-Lumen-Signature` header.
- Failed webhook deliveries are retried up to 5 times with exponential backoff.
- New `projects:write` scope for creating and updating projects through the API.

### Fixed
- `GET /users?status=deactivated` no longer returns users who were reactivated.

## v2.4.0 (3 June 2026)

### Changed
- Access token lifetime increased from 30 minutes to 60 minutes.
- Responses with HTTP status 429 now include a `Retry-After` header giving the number of seconds to wait.
- Enterprise plan rate limit increased from 500 to 1,000 requests per minute.

### Deprecated
- The `/v2/reports` endpoint is deprecated and will be removed in v3.0. Use the billing export in the admin console instead.

## v2.3.0 (15 March 2026)

### Changed
- Cursor-based pagination replaces offset pagination on `/users` and `/projects`. The `offset` parameter is no longer supported; use `cursor` and `next_cursor`.
- Maximum page size increased from 50 to 100 results.

### Security
- Client secrets can now be rotated without downtime; the old secret keeps working for 24 hours after rotation.

## v2.2.0 (20 January 2026)

### Added
- Scopes: `users:read`, `users:write` and `projects:read`. Requests made with a token that lacks the required scope now return 403 `insufficient_scope`.

### Announced
- Version 1 of the API (`/v1`) will be retired on 31 March 2026. All customers must migrate to `/v2` before that date.
