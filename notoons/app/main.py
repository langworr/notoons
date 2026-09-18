"""Application factory and ASGI entry point for Notoons.

This module assembles the Falcon ASGI application used by the Notoons web
tool.  Importing it removes stale files from the configured temporary upload
directory, while :func:`create_app` configures multipart PDF uploads, static
assets, and application routes.
"""

import os
import falcon.asgi
from falcon.media.multipart import MultipartFormHandler

from .config import BASE_DIR
from .auth import OIDCClient
from .middleware import AuthenticationMiddleware
from .state import cleanup_temp_dir
from .api.router import add_routes

# Pulisce eventuali file temporanei pendenti all'avvio
cleanup_temp_dir()


def create_app() -> falcon.asgi.App:
    """Create and configure a Falcon ASGI application instance.

    The returned application is configured with:

    * a multipart form handler that buffers request parts up to 1 GiB and
        multipart headers up to 64 KiB, allowing large PDF uploads;
    * the ``/static`` route mapped to the application's static asset
        directory; and
    * all page and conversion-job routes registered by
        :func:`notoons.app.api.router.add_routes`.

    A new Falcon application is created on every call.  Temporary-file
    cleanup is performed once at module import time rather than on every
    factory call.

    Returns:
        A fully configured :class:`falcon.asgi.App` ready to be served by an
        ASGI server such as Uvicorn.

    Raises:
        OSError: If Falcon or the application setup cannot access a required
            static-file directory or configured runtime directory.
        RuntimeError: If Falcon rejects one of the application or multipart
            configuration settings.
    """
    app_ = falcon.asgi.App()
    oidc = OIDCClient()
    app_.add_middleware(AuthenticationMiddleware(oidc))

    # Configurazione Parser Multipart (fino ad 1GB per caricamento PDF)
    multipart_handler = MultipartFormHandler()
    multipart_handler.parse_options.max_body_part_buffer_size = 1024 * 1024 * 1024
    multipart_handler.parse_options.max_body_part_headers_size = 64 * 1024
    app_.req_options.media_handlers["multipart/form-data"] = multipart_handler

    # Mappatura della cartella di file statici
    static_dir = os.path.join(BASE_DIR, "app", "static")
    app_.add_static_route("/static", static_dir)

    # Registra tutte le rotte dell'app
    add_routes(app_, oidc)

    return app_


app = create_app()
"""The default ASGI application exported for servers such as Uvicorn."""
