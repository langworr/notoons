"""Route registration for the Notoons Falcon application.

This module keeps the application's URL configuration in one place.  It
creates the resource objects used by Falcon and associates them with the
public web page and the conversion-job API endpoints.  Resource classes own
request handling; this module only defines which resource receives each
request path.
"""

import falcon.asgi
from ..auth import OIDCClient
from .routes.auth import AuthResource
from .routes.pages import HomeResource, ConfigResource
from .routes.jobs import JobsResource, JobDetailResource, DownloadResource


def add_routes(app: falcon.asgi.App, oidc: OIDCClient) -> None:
    """Register all page and conversion API routes on a Falcon application.

    The same :class:`JobsResource` instance handles both ``/jobs`` and its
    backwards-compatible ``/process`` alias.  This ensures that listing and
    creating jobs use the same resource implementation regardless of which
    endpoint a client calls.

    Registered routes are:

    ``GET /``
        Serves the application's HTML interface.
    ``GET /config``
        Returns the named output directories and configured log/temp paths.
    ``GET /auth/login``
        Starts the OIDC authorization-code flow.
    ``GET /auth/callback``
        Validates the provider response and creates the local session.
    ``GET /auth/logout``
        Clears the local session cookie.
    ``GET /auth/me``
        Returns the authenticated user's basic claims.
    ``GET /jobs``
        Returns the current conversion-job summaries.
    ``POST /jobs`` and ``POST /process``
        Accepts a multipart PDF upload and queues a conversion job.  The
        request may include ``mode`` and ``dpi`` form fields.
    ``GET /jobs/{job_id}``
        Returns the status and details of one conversion job.
    ``DELETE /jobs/{job_id}``
        Removes a job and its generated or temporary files.
    ``GET /download/{job_id}``
        Streams the completed CBZ archive for a conversion job.

    Args:
        app: The Falcon ASGI application that will receive the route
            registrations.  The application is modified in place.
        oidc: Configured OIDC client shared by the auth resource and
            authentication middleware.

    Raises:
        AttributeError: If ``app`` does not provide Falcon's
            :meth:`falcon.asgi.App.add_route` interface.
    """
    auth_resource = AuthResource(oidc)
    jobs_resource = JobsResource()

    # Rotte per Frontend HTML
    app.add_route("/", HomeResource())
    app.add_route("/config", ConfigResource())
    app.add_route("/auth/login", auth_resource)
    app.add_route("/auth/callback", auth_resource, suffix="callback")
    app.add_route("/auth/logout", auth_resource, suffix="logout")
    app.add_route("/auth/me", auth_resource, suffix="me")

    # Rotte API REST dei Job
    app.add_route("/jobs", jobs_resource)
    app.add_route("/process", jobs_resource)  # Alias
    app.add_route("/jobs/{job_id}", JobDetailResource())
    app.add_route("/download/{job_id}", DownloadResource())
