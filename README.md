# Orbit

## Quick Start

### Requirements

- Python 3.10+
- Node.js 18+
- npm

### Install

Clone the repository, enter the project root, and run:

```bash
./scripts/install-env.sh
```

This installs both environments:

- Backend dependencies in a Python virtual environment at `./Orbit`
- Frontend dependencies with npm in `./frontend`

### Common Options

Install and build the frontend:

```bash
./scripts/install-env.sh --build-frontend
```

Install dependencies and run database setup/migrations:

```bash
./scripts/install-env.sh --migrate
```

Install only one side of the project:

```bash
./scripts/install-env.sh --backend-only
./scripts/install-env.sh --frontend-only
```

Use a custom virtual environment path:

```bash
./scripts/install-env.sh --venv /path/to/orbit-venv
```

The default `./Orbit` virtual environment is recommended because the existing backend start script expects it there.
