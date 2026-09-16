"""Falcon resources for the HTML page and runtime configuration endpoints.

The resources in this module serve the browser-facing application shell and
expose the directories selected by the Notoons configuration loader.  Static
assets such as CSS and JavaScript are registered separately by the application
factory.
"""

import os
import falcon
import falcon.asgi
from ...config import CONFIG, BASE_DIR

TEMPLATES_DIR = os.path.join(BASE_DIR, "app", "templates")
"""Absolute path to the directory containing the HTML templates."""


class HomeResource:
    """Serve the main browser interface from the HTML template directory."""

    async def on_get(self, _req: falcon.asgi.Request, resp: falcon.asgi.Response) -> None:
        """Return the application's main ``index.html`` page.

        The template is read as UTF-8 text and assigned to the Falcon
        response with an HTML content type.  The handler does not process
        request parameters; client-side JavaScript uses the separate API
        routes for conversion-job operations.

        Args:
            _req: Falcon request object, unused by this handler.
            resp: Falcon response populated with the rendered HTML text.

        Returns:
            ``None``.  Falcon sends the template as an HTML response.

        Raises:
            OSError: If ``index.html`` cannot be opened or read.
            UnicodeError: If the template is not valid UTF-8.
        """
        html_path = os.path.join(TEMPLATES_DIR, "index.html")
        resp.content_type = falcon.MEDIA_HTML
        with open(html_path, "r", encoding="utf-8") as f:
            resp.text = f.read()


class ConfigResource:
    """Expose the active runtime directory configuration through HTTP."""

    async def on_get(self, _req: falcon.asgi.Request, resp: falcon.asgi.Response) -> None:
        """Return the configured output, log, and temporary directories.

        The response reflects the process-wide values loaded into
        :data:`notoons.app.config.CONFIG`.  Paths are returned as JSON fields
        named ``outputs_dir``, ``logs_dir``, and ``temp_dir``.

        Args:
            _req: Falcon request object, unused by this handler.
            resp: Falcon response populated with the configuration mapping.

        Returns:
            ``None``.  The response status is ``200 OK``.
        """
        resp.status = falcon.HTTP_200
        resp.media = {
            "outputs_dir": CONFIG["outputs_dir"],
            "logs_dir": CONFIG["logs_dir"],
            "temp_dir": CONFIG["temp_dir"],
        }
