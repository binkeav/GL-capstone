from __future__ import annotations

import logging
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_FILE = ROOT / "logs" / "invoice_processing.log"


def configure_logging(log_file: str | Path | None = None) -> Path:
    path = Path(log_file or os.getenv("INVOICE_PROCESSING_LOG_FILE", DEFAULT_LOG_FILE))
    if not path.is_absolute():
        path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    os.environ["INVOICE_PROCESSING_LOG_FILE"] = str(path)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    if not any(
        isinstance(handler, logging.FileHandler)
        and Path(getattr(handler, "baseFilename", "")) == path
        for handler in root_logger.handlers
    ):
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s [%(process)d:%(threadName)s] %(message)s"
            )
        )
        root_logger.addHandler(handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)
    return path
