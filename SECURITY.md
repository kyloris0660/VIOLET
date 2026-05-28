# Security

V.I.O.L.E.T. is designed as a **single-user, local-first** application for managing personal anime/illustration collections. It includes authentication and access controls inherited from Blombooru, but is not hardened for public internet exposure.

## Deployment Considerations

### Local Development (Primary)

V.I.O.L.E.T. is primarily developed and used as a local Windows application. In this mode, it listens on `localhost:8000` and is not exposed to the network by default.

### Reverse Proxy

If you expose your instance via a reverse proxy (Nginx, Caddy, Traefik, etc.):

- **Set `X-Forwarded-For` correctly.** V.I.O.L.E.T. uses this header for login rate limiting. Your proxy should completely overwrite (not just append to) this header with the real client IP.
- **Use HTTPS.** The authentication cookie is set with the `Secure` flag only when the request scheme is HTTPS.

### API Keys

Three ways to pass API keys:

1. **`Authorization: Bearer blom_...`** header (Recommended)
2. **HTTP Basic Auth** with the API key as the password (Danbooru client compatibility)
3. **`?api_key=blom_...`** query parameter (Danbooru client compatibility)

> [!IMPORTANT]
> Prefer method 1 or 2. Method 3 exposes your API key in server logs, browser history, proxy logs, and HTTP Referer headers.

API keys are stored as SHA-256 hashes in the database. Only the key prefix is stored in plaintext for UI identification.

### Authentication Modes

- **`REQUIRE_AUTH=true`**: All routes require authentication.
- **`REQUIRE_AUTH=false`** (Default): Web UI and API are open; administrative actions still require admin authentication.

### Admin Mode

The `admin_mode` cookie is a UI safety toggle, not a security mechanism. Actual admin endpoint authentication is enforced via JWT token validation.

### Debug Mode

Debug mode (`BLOMBOORU_DEBUG=true`) exposes full exception messages including stack traces and file paths. Do not run debug mode on a publicly accessible instance.

### Secrets

Never commit `.env`, API keys, database credentials, LLM API keys, or model files. The `.gitignore` is configured to exclude these.

### External Provider Privacy

External provider calls and uploads are disabled unless a phase explicitly approves the provider policy, privacy eligibility, budget/rate-limit plan, cache/audit behavior, and run scope. Originals, local paths, filenames, source labels, iCloud/source paths, and unknown/non-anime/unapproved illustration content must not be sent to external providers by default. Derived/resized/stripped inputs may be used only after provider-specific approval.

Provider evidence can support candidates, but confirmed entity assignment remains manual or explicitly policy-approved. Low-confidence provider matches must not be used as weak character recognition truth.

## Reporting Vulnerabilities

If you discover a security vulnerability, please open a GitHub issue at https://github.com/kyloris0660/VIOLET/issues or contact the maintainer directly.
