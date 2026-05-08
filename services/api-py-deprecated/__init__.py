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
        format="%(levelname)s::%(asctime)s::%(pathname)s::%(lineno)d [%(request_id)s] - %(message)s",
        datefmt="%d-%b-%y %H:%M:%S",
        level=settings.logging_level.upper(),
    )

    # Inject request_id into EVERY LogRecord at creation time.
    #
    # Python's Logger.callHandlers() traverses the logger hierarchy calling
    # hdlr.emit() directly — it does NOT call parent logger's handle(), so
    # filters on the root logger are never invoked for propagated records from
    # child loggers (APScheduler, SQLAlchemy, etc.).  setLogRecordFactory is
    # the only reliable hook that runs for every record, everywhere.
    from utils.request_context import REQUEST_ID_VAR  # noqa: PLC0415

    _orig_factory = logging.getLogRecordFactory()

    def _request_id_record_factory(*args, **kwargs):
        record = _orig_factory(*args, **kwargs)
        record.request_id = REQUEST_ID_VAR.get()
        return record

    logging.setLogRecordFactory(_request_id_record_factory)

    _LOGGING_CONFIGURED = True


# Install once when API package loads so app and workers share exception tracking.
configure_logging()
install_exception_handler()
