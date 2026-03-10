<!--
SPDX-FileCopyrightText: 2026 Mappalink

SPDX-License-Identifier: MIT
-->

# InOrbit MiR Connector

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)

InOrbit Edge connector for MiR

## Overview

This repository contains the [InOrbit](https://inorbit.ai/) Edge Connector for [MiR](https://www.mobile-industrial-robots.com/) robots. It runs one process per robot and supports multi-robot fleet management through a single fleet configuration file.

This integration requires the Connector to be configured following the instructions below.

## API Documentation

This connector implementation is based on the official publicly available MiR API specifications available on the MiR support portal. For detailed API documentation and reference materials, please refer to:

- [REST API Documentation](https://www.mobile-industrial-robots.com/support/) (requires a free account login)

## Features

- Multi-robot fleet management through a single connector instance
- Real-time robot monitoring (pose, battery, state, velocity, etc.)
- Automatic retry logic with exponential backoff for API calls
- Background polling architecture for efficient data fetching
- Annotation synchronization for waypoint positions between MiR and InOrbit
- Built on top of the [`inorbit-connector-python`](https://github.com/inorbit-ai/inorbit-connector-python) framework

## Requirements

- Python 3.12 or later
- InOrbit account [(it's free to sign up!)](https://control.inorbit.ai/)
- Access to a MiR server with API credentials
- Network connectivity between the connector host and MiR server

## Setup

1. Create a Python virtual environment in the host machine and install the connector.

```shell
# Using uv (recommended)
uv sync
```

> [!TIP]
> Installing the `colorlog` package is optional. If available, it will be used to colorize the logs.

```shell
uv pip install colorlog
```

2. Configure the Connector:

- Copy `config/fleet.example.yaml` to `config/my_fleet.yaml` and configure your robot fleet. Each robot needs an InOrbit `robot_id` and the corresponding MiR `fleet_robot_id`.

- Optionally, configure the MiR connector-specific settings via environment variables. Copy `config/example.env` to `config/.env` and fill in the values. Any `connector_config` fields can be set using the `INORBIT_MIR_` prefix (e.g., `INORBIT_MIR_FLEET_HOST`, `INORBIT_MIR_FLEET_USERNAME`). Environment variables are used as fallbacks when fields are missing from the YAML configuration. See `config/example.env` for reference.

- Set the `INORBIT_API_KEY` environment variable. You can get the API key for your account from InOrbit's [Developer Console](https://developer.inorbit.ai/docs#configuring-environment-variables).

```bash
export INORBIT_API_KEY=your-api-key-here
# Or place the value in the config/.env file
```

## Deployment

Once all dependencies are installed and the configuration is complete, the Connector can be run as a command.

```bash
source config/.env && mir-connector -c config/my_fleet.yaml -id my_robot
```

You can validate your fleet config without starting the connector:

```bash
mir-connector -c config/my_fleet.yaml --validate
```

### Docker

The Connector can be run as a containerized application using Docker Compose:

1. Copy `docker/docker-compose.example.yaml` to `docker/docker-compose.yaml`
2. Copy `config/example.env` to `config/.env` and fill in your credentials
3. Update volume paths in `docker-compose.yaml` to point to your configuration files
4. Run: `docker compose -f docker/docker-compose.yaml up -d`

The Docker Compose setup supports environment variable configuration via `config/.env` and allows running multiple connector instances. See `docker/docker-compose.example.yaml` for detailed configuration options.

## Contributing

Any contribution that you make to this repository will be under the MIT license, as dictated by that [license](https://opensource.org/licenses/MIT).

Please refer to the [CONTRIBUTING.md](CONTRIBUTING.md) file for information on how to contribute to this project.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

## Support

- **Documentation**: [InOrbit Developer Docs](https://developer.inorbit.ai/)
- **Issues**: [GitHub Issues](https://github.com/mappalink/inorbit-mir-connector/issues)
- **Email**: info@mappalink.com

---

**Powered by InOrbit** | [www.inorbit.ai](https://www.inorbit.ai)
