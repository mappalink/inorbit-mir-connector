# SPDX-FileCopyrightText: 2026 Mappalink
#
# SPDX-License-Identifier: MIT

"""Entry point for the InOrbit MiR Connector."""

import argparse
import logging
import signal
import sys
from typing import NoReturn

from pydantic import ValidationError

from mir_connector.src.connector import MirConnector
from mir_connector.src.config.models import ConnectorConfig
from mir_connector.src.config.fleet_config_loader import get_robot_config


def setup_logging():
    """Configure logging with appropriate levels and formatting."""
    log_level = logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s (%(filename)s:%(lineno)d)",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("inorbit_edge.robot").setLevel(logging.INFO)
    logging.getLogger("RobotSession").setLevel(logging.INFO)
    return logging.getLogger(__name__)


LOGGER = setup_logging()


class CustomParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        sys.stderr.write(f"error: {message}\n")
        self.print_help()
        sys.exit(2)


def start() -> None:
    """Main entry point. Parses args, loads config, starts connector."""
    parser = CustomParser(prog="mir-connector")
    parser.add_argument(
        "-c", "--config", type=str, required=True, help="Path to fleet YAML config"
    )
    parser.add_argument(
        "-id", "--robot_id", type=str, required=True, help="Robot ID from fleet YAML"
    )
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )
    args = parser.parse_args()

    if args.log_level:
        logging.getLogger().setLevel(getattr(logging, args.log_level.upper()))

    try:
        config = ConnectorConfig(**get_robot_config(args.config, args.robot_id))
    except FileNotFoundError:
        LOGGER.error(f"Configuration file not found: {args.config}")
        sys.exit(1)
    except IndexError as e:
        LOGGER.error(str(e))
        sys.exit(1)
    except ValidationError as e:
        LOGGER.error(f"Config validation failed:\n{e}")
        sys.exit(1)

    connector = MirConnector(args.robot_id, config)
    LOGGER.info(f"Starting connector for {args.robot_id}")
    connector.start()
    signal.signal(signal.SIGINT, lambda sig, frame: connector.stop())
    connector.join()


if __name__ == "__main__":
    start()
