"""LYOKO Agent Application Composition Root & Entrypoint."""

import logging

import uvicorn

from lyoko.config import settings
from lyoko.infrastructure.api.server import create_app
from lyoko.logging_config import configure_logging

configure_logging()
logger = logging.getLogger("lyoko")

app = create_app()


def main() -> None:
    """Start the LYOKO agent application server with Uvicorn."""
    logger.info(f"Starting LYOKO agent on {settings.lyoko_host}:{settings.lyoko_port}")
    uvicorn.run(app, host=settings.lyoko_host, port=settings.lyoko_port)


if __name__ == "__main__":
    main()
