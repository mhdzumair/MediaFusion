import logging

from db.config import settings
from utils.exception_tracker import install_exception_handler

_LOGGING_CONFIGURED = False


def configure_logging() -> None:
    """Configure root logging for API and service modules."""
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return

    logging.basicConfig(
        format="%(levelname)s::%(asctime)s::%(pathname)s::%(lineno)d - %(message)s",
        datefmt="%d-%b-%y %H:%M:%S",
        level=settings.logging_level.upper(),
    )

    _LOGGING_CONFIGURED = True


# Install once when API package loads so app and workers share exception tracking.
configure_logging()
install_exception_handler()
