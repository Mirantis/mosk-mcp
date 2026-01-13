"""RabbitMQ messaging operations tools for MOSK MCP Server.

This package provides MCP tools for monitoring and diagnosing RabbitMQ
messaging clusters in MOSK environments, including:

- Cluster health and status monitoring
- Queue listing with backlog analysis
- Connection pool monitoring
- Comprehensive diagnostics

Tools are categorized by safety level:
- Read-only: get_rabbitmq_status, list_rabbitmq_queues,
  get_rabbitmq_connections, diagnose_rabbitmq_issue

All RabbitMQ operations are READ-ONLY and do not modify cluster state.
MOSK typically has two RabbitMQ instances:
- main: openstack-rabbitmq-rabbitmq-0 (used by most OpenStack services)
- neutron: openstack-neutron-rabbitmq-rabbitmq-0 (Neutron-specific)

Example:
    >>> from mosk_mcp.tools.messaging_operations import (
    ...     get_rabbitmq_status,
    ...     list_rabbitmq_queues,
    ... )
    >>>
    >>> # Get cluster status
    >>> status = await get_rabbitmq_status(k8s_adapter)
    >>> print(f"Health: {status.health}")
    >>>
    >>> # List queues for Nova
    >>> queues = await list_rabbitmq_queues(k8s_adapter, vhost="nova")
    >>> for q in queues.queues:
    ...     print(f"{q.name}: {q.messages} messages")
"""

from __future__ import annotations

from mosk_mcp.tools.messaging_operations.diagnose_rabbitmq_issue import (
    diagnose_rabbitmq_issue,
)
from mosk_mcp.tools.messaging_operations.get_rabbitmq_connections import (
    get_rabbitmq_connections,
)
from mosk_mcp.tools.messaging_operations.get_rabbitmq_status import (
    get_rabbitmq_status,
)
from mosk_mcp.tools.messaging_operations.list_rabbitmq_queues import (
    list_rabbitmq_queues,
)
from mosk_mcp.tools.messaging_operations.models import (
    # Enums
    AlarmType,
    ConnectionsByUserSummary,
    ConnectionState,
    # get_rabbitmq_connections models
    DiagnoseRabbitMQIssueInput,
    DiagnoseRabbitMQIssueOutput,
    GetRabbitMQConnectionsInput,
    GetRabbitMQConnectionsOutput,
    # get_rabbitmq_status models
    GetRabbitMQStatusInput,
    GetRabbitMQStatusOutput,
    # list_rabbitmq_queues models
    ListRabbitMQQueuesInput,
    ListRabbitMQQueuesOutput,
    QueuesByVhostSummary,
    # diagnose_rabbitmq_issue models
    RabbitMQConnectionInfo,
    RabbitMQDiagnosticCheck,
    RabbitMQHealthLevel,
    RabbitMQInstanceDiagnosis,
    RabbitMQNodeInfo,
    RabbitMQQueueInfo,
)
from mosk_mcp.tools.messaging_operations.rabbitmq_client import (
    RabbitMQClient,
)


__all__ = [
    "AlarmType",
    "ConnectionState",
    "ConnectionsByUserSummary",
    "DiagnoseRabbitMQIssueInput",
    "DiagnoseRabbitMQIssueOutput",
    "GetRabbitMQConnectionsInput",
    "GetRabbitMQConnectionsOutput",
    "GetRabbitMQStatusInput",
    "GetRabbitMQStatusOutput",
    "ListRabbitMQQueuesInput",
    "ListRabbitMQQueuesOutput",
    "QueuesByVhostSummary",
    "RabbitMQClient",
    "RabbitMQConnectionInfo",
    "RabbitMQDiagnosticCheck",
    "RabbitMQHealthLevel",
    "RabbitMQInstanceDiagnosis",
    "RabbitMQNodeInfo",
    "RabbitMQQueueInfo",
    "diagnose_rabbitmq_issue",
    "get_rabbitmq_connections",
    "get_rabbitmq_status",
    "list_rabbitmq_queues",
]
