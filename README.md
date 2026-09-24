# Notoons

Notoons is a small web application for converting PDF notes, handouts, and
slides into CBZ archives. Uploads are processed asynchronously, with progress
and logs available while a conversion is running.

## Features

- PDF to CBZ conversion with automatic layout detection.
- Explicit page mode (`1 page = 1 image`) or grid/slide extraction mode.
- Configurable rendering DPI.
- Multiple named output directories.
- In-memory job tracking with downloadable results and cleanup.
- OpenID Connect login using the authorization-code flow with PKCE.
- Falcon ASGI application served by Uvicorn.

## Requirements

- Python 3.11 or newer.
- A configured OpenID Connect provider.
- Windows users can use the included `env\dev` virtual environment and
	`run.bat`; the commands below also work from any activated virtualenv.

## Installation

Create and activate a virtual environment, then install the runtime and test
dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r env/prod.txt
python -m pip install pytest
```

The checked-in development environment already contains these packages. If
you use it directly on Windows, activate it with:

```powershell
.\env\dev\Scripts\Activate.ps1
```

## Configuration

The packaged application reads `notoons/config/config.txt`. It falls back to
the repository `config.txt` when the packaged file is unavailable. Relative
paths are resolved from the application directory and required directories
are created at startup.

Copy the example values in the configuration file and replace all placeholder
OIDC values before starting the app:

```ini
outputs_dir = [{"nickname": "Default", "path": "outputs"}, {"nickname": "Archive", "path": "outputs/archive"}]
logs_dir = logs
temp_dir = temp

oidc_issuer_url = https://identity.example.com/realms/notoons
oidc_client_id = notoons
oidc_client_secret = CHANGE_ME
oidc_redirect_uri = http://127.0.0.1:8000/auth/callback
oidc_post_logout_redirect_uri = http://127.0.0.1:8000/auth/logout
oidc_session_secret = CHANGE_ME_TO_A_LONG_RANDOM_VALUE
oidc_scopes = openid profile email
oidc_cookie_secure = false
```

Register the redirect URI with the identity provider. Use a long random value
for `oidc_session_secret`, and set `oidc_cookie_secure = true` when the app is
served over HTTPS.

`outputs_dir` accepts a JSON array of objects. Each object requires a unique
`nickname` and a relative or absolute `path`. A legacy single path such as
`outputs_dir = outputs` is also accepted.

Environment variables override file values. The preferred names are:

| Setting | Environment variable |
| --- | --- |
| `outputs_dir` | `NOTOONS_OUTPUTS_DIR` |
| `logs_dir` | `NOTOONS_LOGS_DIR` |
| `temp_dir` | `NOTOONS_TEMP_DIR` |
| `oidc_issuer_url` | `NOTOONS_OIDC_ISSUER_URL` |
| `oidc_client_id` | `NOTOONS_OIDC_CLIENT_ID` |
| `oidc_client_secret` | `NOTOONS_OIDC_CLIENT_SECRET` |
| `oidc_redirect_uri` | `NOTOONS_OIDC_REDIRECT_URI` |
| `oidc_post_logout_redirect_uri` | `NOTOONS_OIDC_POST_LOGOUT_REDIRECT_URI` |
| `oidc_session_secret` | `NOTOONS_OIDC_SESSION_SECRET` |
| `oidc_scopes` | `NOTOONS_OIDC_SCOPES` |
| `oidc_cookie_secure` | `NOTOONS_OIDC_COOKIE_SECURE` |

The legacy `OUTPUTS_DIR`, `LOGS_DIR`, and `TEMP_DIR` names remain supported.

## Running

Start the development server with the included launcher:

```powershell
.\run.bat
```

Or run Uvicorn directly:

```powershell
python -m uvicorn notoons.app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open <http://127.0.0.1:8000/> in a browser. Unauthenticated requests are sent
to the configured OIDC provider. The server listens on localhost by default;
configure a reverse proxy and HTTPS before exposing it outside the local
machine.

## Conversion workflow

1. Sign in through the OIDC provider.
2. Select or drag a PDF into the upload area.
3. Choose an extraction mode, DPI, and output directory.
4. Monitor the asynchronous job until it completes.
5. Download the generated CBZ archive.

The supported extraction modes are:

- `auto`: detect page layouts and grids automatically.
- `pages`: render one CBZ image per PDF page.
- `slides`: force grid slicing for handouts or slide sheets.

Temporary uploaded PDFs are removed after processing. Completed jobs and their
files are retained in process-local state until deleted or until the two-hour
job cache expires.

## HTTP API

All application endpoints require authentication unless stated otherwise.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Main web interface |
| `GET` | `/config` | Active output, log, and temp directories |
| `GET` | `/jobs` | List retained jobs |
| `POST` | `/jobs` | Queue a multipart PDF conversion |
| `GET` | `/jobs/{job_id}` | Read job status, progress, and logs |
| `DELETE` | `/jobs/{job_id}` | Delete a job and its generated file |
| `GET` | `/download/{job_id}` | Download a completed CBZ |
| `GET` | `/auth/login` | Begin OIDC login |
| `GET` | `/auth/callback` | Handle the OIDC callback |
| `GET` | `/auth/logout` | Clear the local session |

`POST /jobs` expects a multipart form with a required `file` field and these
optional fields:

```text
mode=auto|pages|slides
dpi=200
output_dir=Default
```

The upload response contains a `job_id`. Poll `GET /jobs/{job_id}` until its
status is `completed` or `failed`, then use the returned `download_url`.

## Testing

Run the test suite from the repository root:

```powershell
python -m pytest
```

The tests cover conversion lifecycle and cleanup, named output directories,
authentication redirects, and logout behavior.

## Project layout

```text
notoons/
	app/                 Falcon application, routes, auth, and state
	config/config.txt   packaged runtime configuration
	app/static/          browser assets
	app/templates/       HTML templates
initial_assets/        default image assets
outputs/               generated CBZ archives
logs/                  conversion logs
temp/                  temporary upload files
run.bat                Windows development launcher
```

## License

See [LICENSE](LICENSE).
