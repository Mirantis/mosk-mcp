# Contributing to MOSK MCP Server

Thank you for your interest in contributing to the MOSK MCP Server! This document provides guidelines and best practices for contributing to this project.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Architecture Overview](#architecture-overview)
- [Adding New Tools](#adding-new-tools)
- [Code Standards](#code-standards)
- [Testing Guidelines](#testing-guidelines)
- [Pull Request Process](#pull-request-process)
- [MCP Protocol Compliance](#mcp-protocol-compliance)

---

## Code of Conduct

- Be respectful and inclusive
- Focus on constructive feedback
- Follow the project's coding standards
- Test your changes thoroughly

---

## Getting Started

### Prerequisites

- Python 3.11+
- Git
- Access to a MOSK cluster (for integration testing)
- Docker (optional, for containerized testing)

### Fork and Clone

```bash
# Fork the repository on GitHub
git clone https://github.com/Mirantis/mosk-mcp.git
cd mosk-mcp
```

---

## Development Setup

### 1. Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 2. Install Dependencies

```bash
# Install package with dev dependencies
pip install -e ".[dev]"

# Install pre-commit hooks
pre-commit install
```

### 3. Verify Installation

```bash
# Run tests
pytest tests/unit/ -v

# Check code quality
ruff check src tests
ruff format --check src tests
mypy src
```

---

## Architecture Overview

The MOSK MCP Server follows a layered architecture:

```
src/mosk_mcp/
├── core/               # Server setup, config, exceptions
├── auth/               # OAuth 2.0 Device Flow, RBAC, sessions
├── adapters/           # Kubernetes, Ceph, OpenStack clients
├── tools/              # Tool implementations (by category)
├── registration/       # Tool registration with FastMCP
├── infrastructure/     # Rate limiting, caching, circuit breakers
├── observability/      # Logging, metrics, audit, health
├── privacy/            # Data redaction for cloud LLMs
└── cluster/            # Multi-cluster management
```

### Key Design Patterns

1. **Context Getter Pattern**: Tools receive a function to get the current context, avoiding stale references
2. **Adapter Abstraction**: All external services accessed through adapter interfaces
3. **Pydantic Models**: All inputs/outputs use Pydantic for validation and serialization
4. **Safety Levels**: Tools classified as READ_ONLY, NON_DESTRUCTIVE, or PRIVILEGED

---

## Adding New Tools

This is the most common contribution. Follow this pattern:

### Step 1: Create Tool Implementation

Create a new file in `src/mosk_mcp/tools/<category>/`:

```python
# src/mosk_mcp/tools/my_category/my_new_tool.py
"""My new tool implementation."""

from pydantic import BaseModel, Field

from mosk_mcp.adapters.kubernetes import KubernetesAdapter
from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.common.errors import tool_handler

logger = get_logger(__name__)


class MyToolInput(BaseModel):
    """Input parameters for my_tool."""

    name: str = Field(..., description="Resource name to query")
    namespace: str = Field(default="default", description="Kubernetes namespace")


class MyToolOutput(BaseModel):
    """Output from my_tool."""

    status: str = Field(..., description="Operation status")
    data: dict = Field(default_factory=dict, description="Result data")


@tool_handler(tool_name="my_tool")
async def my_tool(
    adapter: KubernetesAdapter,
    input_data: MyToolInput,
) -> MyToolOutput:
    """Execute my tool operation.

    Args:
        adapter: Kubernetes adapter for cluster access.
        input_data: Validated input parameters.

    Returns:
        MyToolOutput with operation results.

    Raises:
        ToolExecutionError: If the operation fails.
    """
    logger.info("my_tool_started", name=input_data.name)

    # Implementation here
    result = await adapter.get("ConfigMap", input_data.name, input_data.namespace)

    return MyToolOutput(
        status="success",
        data=result,
    )
```

### Step 2: Register the Tool

Add registration in `src/mosk_mcp/registration/tools/<category>.py`:

```python
from mosk_mcp.tools.my_category.my_new_tool import (
    MyToolInput,
    MyToolOutput,
    my_tool,
)


def register_my_category_tools(
    mcp: FastMCP,
    settings: Settings,
    context_getter: Callable[[], SSOServerContext | None],
) -> None:
    """Register my category tools."""
    get_mosk_adapter, get_mcc_adapter = create_adapter_getters(context_getter)

    @mcp.tool(
        name="my_tool",
        description="Short description of what the tool does",
    )
    async def my_tool_handler(
        name: str = Field(..., description="Resource name"),
        namespace: str = Field(default="default", description="Namespace"),
    ) -> MyToolOutput:
        """Long description with usage details."""
        async with with_logging_context("my_tool"):
            adapter = await get_mosk_adapter()
            return await my_tool(
                adapter=adapter,
                input_data=MyToolInput(name=name, namespace=namespace),
            )
```

### Step 3: Add to Server Registration

Update `src/mosk_mcp/core/server.py` to include your registration function:

```python
from mosk_mcp.registration.tools.my_category import register_my_category_tools

def _register_tools(...):
    # ... existing registrations ...
    register_my_category_tools(mcp, settings, context_getter)
```

### Step 4: Add Tests

Create `tests/unit/tools/my_category/test_my_new_tool.py`:

```python
"""Tests for my_new_tool."""

import pytest
from unittest.mock import AsyncMock

from mosk_mcp.tools.my_category.my_new_tool import (
    MyToolInput,
    my_tool,
)


class TestMyTool:
    """Test cases for my_tool."""

    @pytest.fixture
    def mock_adapter(self) -> AsyncMock:
        """Create mock Kubernetes adapter."""
        adapter = AsyncMock()
        adapter.get.return_value = {"data": "test"}
        return adapter

    async def test_my_tool_success(self, mock_adapter: AsyncMock) -> None:
        """Test successful tool execution."""
        input_data = MyToolInput(name="test-config", namespace="default")

        result = await my_tool(adapter=mock_adapter, input_data=input_data)

        assert result.status == "success"
        mock_adapter.get.assert_called_once_with("ConfigMap", "test-config", "default")

    async def test_my_tool_not_found(self, mock_adapter: AsyncMock) -> None:
        """Test handling of missing resource."""
        mock_adapter.get.return_value = None
        input_data = MyToolInput(name="missing", namespace="default")

        result = await my_tool(adapter=mock_adapter, input_data=input_data)

        assert result.status == "not_found"
```

---

## Code Standards

### Python Style

- **Python 3.11+** features encouraged (match statements, type unions, etc.)
- **100 character line limit**
- **Double quotes** for strings
- **Type hints required** on all public functions
- **Docstrings required** on all public functions (Google style)

### Linting and Formatting

```bash
# Format code
ruff format src tests

# Check for issues
ruff check src tests

# Type checking
mypy src
```

### Configuration

All linting rules are configured in `pyproject.toml`. The project uses:

- **ruff**: Fast Python linter and formatter
- **mypy**: Static type checking (strict mode)
- **pytest**: Testing framework

### Naming Conventions

| Type | Convention | Example |
|------|------------|---------|
| Files | snake_case | `get_ceph_status.py` |
| Classes | PascalCase | `CephStatusOutput` |
| Functions | snake_case | `get_ceph_status` |
| Constants | UPPER_SNAKE | `DEFAULT_TIMEOUT` |
| Tool names | snake_case | `get_ceph_status` |

### Import Order

1. Standard library
2. Third-party packages
3. Local imports (relative)

ruff handles import sorting automatically.

---

## Testing Guidelines

### Test Structure

```
tests/
├── unit/                    # Fast, isolated tests
│   ├── tools/               # Tool-specific tests
│   ├── adapters/            # Adapter tests
│   ├── auth/                # Auth tests
│   └── core/                # Core component tests
├── integration/             # Tests requiring cluster
└── conftest.py              # Shared fixtures
```

### Running Tests

```bash
# All unit tests
pytest tests/unit/ -v

# Specific test file
pytest tests/unit/tools/ceph_operations/test_get_ceph_status.py -v

# With coverage
pytest tests/unit/ --cov=mosk_mcp --cov-report=html

# Integration tests (requires cluster)
pytest tests/integration/ -v --mosk-cluster=https://mcc.example.com
```

### Test Requirements

- **Minimum 80% coverage** (enforced via pytest)
- **All new tools must have tests**
- **Test happy path, error cases, and edge cases**
- **Use AsyncMock for async functions**
- **Use fixtures for common setup**

### Fixture Patterns

```python
@pytest.fixture
def mock_context() -> AsyncMock:
    """Create mock SSOServerContext."""
    context = AsyncMock()
    context.get_mosk_adapter.return_value = AsyncMock(spec=KubernetesAdapter)
    return context


@pytest.fixture
def admin_user() -> UserContext:
    """Create admin user context for privileged operations."""
    return UserContext(
        user_id="test-admin",
        role=Role.ADMINISTRATOR,
        permissions=frozenset(Permission),
    )
```

---

## Pull Request Process

### 1. Create Feature Branch

```bash
git checkout -b feature/my-new-tool
```

### 2. Make Changes

- Follow code standards
- Add tests
- Update documentation if needed

### 3. Run Quality Checks

```bash
# Format and lint
ruff format src tests
ruff check src tests --fix

# Type check
mypy src

# Run tests
pytest tests/unit/ -v
```

### 4. Commit Changes

```bash
git add .
git commit -m "feat: add my_tool for querying resources

- Add tool implementation in tools/my_category/
- Add registration in registration/tools/
- Add unit tests with 95% coverage
- Update README with tool documentation"
```

### Commit Message Format

```
<type>: <short description>

<optional body with details>

<optional footer>
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation
- `refactor`: Code refactoring
- `test`: Adding tests
- `chore`: Maintenance

### 5. Create Pull Request

- Fill out the PR template
- Link related issues
- Request review from maintainers
- Ensure CI passes

---

## MCP Protocol Compliance

This project implements the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/). When contributing, ensure:

### Tool Requirements

1. **Clear descriptions**: Tool descriptions should explain what the tool does and when to use it
2. **Input validation**: Use Pydantic models with Field descriptions
3. **Structured output**: Return Pydantic models, not raw dicts
4. **Error handling**: Use `ToolExecutionError` with appropriate error codes
5. **Idempotency**: Read-only tools should be safe to call multiple times

### Safety Levels

| Level | Description | CRQ Required |
|-------|-------------|--------------|
| READ_ONLY | No state changes | No |
| NON_DESTRUCTIVE | Reversible changes | No |
| PRIVILEGED | Destructive/high-impact | Yes |

Privileged tools must:
- Use the `@require_crq` decorator
- Default to `dry_run=True`
- Log all operations for audit

### Response Format

```python
class MyToolOutput(BaseModel):
    """Standard tool response."""

    status: str = Field(..., description="success, error, not_found, etc.")
    message: str = Field(default="", description="Human-readable message")
    data: dict = Field(default_factory=dict, description="Tool-specific data")
    # Optional metadata
    timestamp: str = Field(default_factory=utc_timestamp)
    request_id: str = Field(default="")
```

---

## Getting Help

- **Issues**: Open a GitHub issue for bugs or feature requests
- **Discussions**: Use GitHub Discussions for questions
- **Documentation**: See [ARCHITECTURE.md](ARCHITECTURE.md) for technical details

---

## Recognition

Contributors will be recognized in:
- Release notes
- CONTRIBUTORS.md (for significant contributions)

Thank you for contributing to MOSK MCP Server!
