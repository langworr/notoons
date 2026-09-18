# notoons
a web tool to convert notes and pdf into cbz and, eventually in the future, others formats.

## OpenID Connect login

Output directories are configured as a JSON array. Each entry needs a
nickname, which is shown in the web UI, and a path relative to the application
directory or an absolute path:

```ini
outputs_dir = [{"nickname": "Default", "path": "outputs"}, {"nickname": "Archive", "path": "outputs/archive"}]
```

The application supports the OIDC authorization-code flow with PKCE. Configure
the OIDC values in `config.txt`:

```ini
oidc_issuer_url = https://identity.example.com/realms/notoons
oidc_client_id = notoons
oidc_client_secret = your-client-secret
oidc_redirect_uri = http://127.0.0.1:8000/auth/callback
oidc_session_secret = generate-a-long-random-secret
oidc_cookie_secure = false
```

`OIDC_SESSION_SECRET` should be a long random value. Set
`oidc_cookie_secure = true` when serving over HTTPS. Environment variables
such as `NOTOONS_OIDC_CLIENT_SECRET` override values in `config.txt`.

After configuration, open `http://127.0.0.1:8000/`; unauthenticated requests
are redirected to the provider and successful callbacks return to the app.
