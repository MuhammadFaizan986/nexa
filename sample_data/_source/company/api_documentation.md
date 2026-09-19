# Lumen Platform API v2 Reference

*Synthetic demo document — fictional company, not real data.*

The Lumen Platform API lets customers manage users and projects programmatically. This reference covers API version 2 as of release v2.5.0. Base URL: `https://api.lumenlabs.example/v2`

## Authentication

The API uses the OAuth 2.0 client credentials flow. Exchange your client ID and client secret for an access token:

```
POST https://api.lumenlabs.example/oauth/token
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials&client_id=...&client_secret=...&scope=users:read projects:read
```

Send the access token in the `Authorization` header of every request:

```
Authorization: Bearer <access_token>
```

Access tokens are valid for 60 minutes. Request a new token when the current one expires; the API does not issue refresh tokens for the client credentials flow. Client secrets can be rotated in the admin console under Settings > API Clients.

## Scopes

Each access token carries one or more scopes. An endpoint can only be called with a token that has the scope it requires.

| Scope | Allows |
| --- | --- |
| `users:read` | List and read users |
| `users:write` | Create, update and deactivate users |
| `projects:read` | List and read projects |
| `projects:write` | Create and update projects |
| `billing:read` | Read invoices and usage |

## Endpoints

### GET /users

Returns a paginated list of users in your organisation. Requires the `users:read` scope.

Query parameters: `limit` (default 25, maximum 100), `cursor`, `status` (`active` or `deactivated`).

### GET /users/{id}

Returns a single user. Requires the `users:read` scope.

### POST /users

Creates a user and sends them an invitation email. Requires the `users:write` scope.

### GET /projects

Returns a paginated list of projects. Requires the `projects:read` scope.

## Errors

Errors return a JSON body with an `error.code` and a human-readable `error.message`.

| HTTP status | error.code | Meaning |
| --- | --- | --- |
| 400 | `validation_error` | The request body or query parameters are invalid. |
| 401 | `token_missing`, `token_invalid`, `token_expired` | Unauthorized: the `Authorization` header is missing, the token is malformed, or the token has expired. |
| 403 | `insufficient_scope` | Forbidden: the token is valid but does not have the scope the endpoint requires. |
| 404 | `not_found` | The resource does not exist or belongs to another organisation. |
| 429 | `rate_limited` | Too many requests. Wait for the number of seconds in the `Retry-After` header. |

### Troubleshooting 401 and 403 on /users

A **401 Unauthorized** on `/users` means the API could not authenticate the request. The most common causes are a missing `Authorization: Bearer` header, a typo in the token, or a token older than 60 minutes. Request a new access token and retry. A **403 Forbidden** means authentication worked but the token was issued without the `users:read` scope; request a new token that includes `users:read`.

## Pagination

List endpoints use cursor-based pagination. Each response includes a `next_cursor` value; pass it as the `cursor` query parameter to get the next page. When `next_cursor` is `null` there are no more results. Page size is set with `limit` (default 25, maximum 100).

## Rate Limits

Each API client can make 100 requests per minute. Enterprise plan customers can make 1,000 requests per minute. Requests over the limit receive a 429 response with a `Retry-After` header giving the number of seconds to wait.

## Webhooks

Lumen can send webhooks for `user.created`, `user.deactivated` and `project.updated` events. Each webhook is signed with HMAC-SHA256 using your webhook secret, and the signature is sent in the `X-Lumen-Signature` header. Failed deliveries are retried up to 5 times with exponential backoff.

## Versioning

Version 1 of the API (`/v1`) was retired on 31 March 2026. All integrations must use `/v2`. Changes are announced in the release notes.
