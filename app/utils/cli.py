"""CLI utilities for service entry points."""

from __future__ import annotations

import argparse
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


def setup_logging(service: str, log_level: str = "INFO") -> None:
    """Setup logging for a service."""
    from app.logger import setup_service_logging
    import logging as std_logging

    setup_service_logging(
        service,
        getattr(std_logging, log_level.upper(), std_logging.INFO),
    )


def parse_base_args(
    description: str,
    add_template: bool = True,
    add_poll: bool = False,
    add_batch: bool = False,
    add_startup_delay: bool = False,
) -> argparse.Namespace:
    """Parse common command line arguments for services."""
    parser = argparse.ArgumentParser(description=description)

    if add_template:
        parser.add_argument(
            "--template", "-t",
            help="Template name to process (default: all)",
        )

    if add_poll:
        parser.add_argument(
            "--poll",
            type=int,
            default=10,
            help="Poll interval in seconds (default: 10)",
        )

    if add_batch:
        parser.add_argument(
            "--batch",
            type=int,
            default=50,
            help="Batch size for processing (default: 50)",
        )

    if add_startup_delay:
        parser.add_argument(
            "--startup-delay",
            type=int,
            help="Startup delay in seconds before connecting to Kafka",
        )

    return parser.parse_args()


def run_service(
    main_func: Callable[..., None],
    service_name: str,
    **kwargs: Any,
) -> None:
    """Run a service with proper error handling and logging."""
    try:
        logger.info("=== %s Service Starting ===", service_name.upper())
        main_func(**kwargs)
    except KeyboardInterrupt:
        logger.info("%s Service interrupted", service_name.upper())
    except Exception as e:
        logger.exception(" %s Service failed: %s", service_name.upper(), e)
        raise
    finally:
        logger.info("=== %s Service Stopped ===", service_name.upper())
