"""OpenStackDeployment (OSDPL) CRD models.

This module provides Pydantic models for the OpenStackDeployment custom resource,
which defines and manages OpenStack deployments on MOSK clusters.
"""

from __future__ import annotations

import contextlib
from enum import Enum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from mosk_mcp.adapters.crd.base import (
    KubernetesMetadata,
    KubernetesResource,
)


class OSDPLPhase(str, Enum):
    """OpenStackDeployment lifecycle phases."""

    PENDING = "Pending"
    DEPLOYING = "Deploying"
    DEPLOYED = "Deployed"
    UPDATING = "Updating"
    FAILED = "Failed"
    DELETING = "Deleting"


class NodeSelector(BaseModel):
    """Node selector for OpenStack service placement.

    Attributes:
        match_labels: Labels that nodes must match.
        match_expressions: Expression-based selectors.
    """

    model_config = ConfigDict(populate_by_name=True)

    match_labels: dict[str, str] = Field(
        default_factory=dict,
        alias="matchLabels",
        description="Labels that nodes must match",
    )
    match_expressions: list[dict[str, Any]] = Field(
        default_factory=list,
        alias="matchExpressions",
        description="Expression-based selectors",
    )

    def to_kubernetes(self) -> dict[str, Any]:
        """Convert to Kubernetes API format."""
        result: dict[str, Any] = {}
        if self.match_labels:
            result["matchLabels"] = self.match_labels
        if self.match_expressions:
            result["matchExpressions"] = self.match_expressions
        return result


class OpenStackFeatures(BaseModel):
    """Feature flags for OpenStack deployment.

    Attributes:
        ssl: Enable SSL for OpenStack services.
        neutron: Neutron-specific features.
        nova: Nova-specific features.
        services: Per-service feature configuration.
    """

    model_config = ConfigDict(populate_by_name=True)

    ssl: dict[str, Any] = Field(
        default_factory=dict,
        description="SSL configuration",
    )
    neutron: dict[str, Any] = Field(
        default_factory=dict,
        description="Neutron features",
    )
    nova: dict[str, Any] = Field(
        default_factory=dict,
        description="Nova features",
    )
    services: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Per-service features",
    )

    def to_kubernetes(self) -> dict[str, Any]:
        """Convert to Kubernetes API format."""
        result: dict[str, Any] = {}
        if self.ssl:
            result["ssl"] = self.ssl
        if self.neutron:
            result["neutron"] = self.neutron
        if self.nova:
            result["nova"] = self.nova
        if self.services:
            result["services"] = self.services
        return result


class OpenStackNetworkingSpec(BaseModel):
    """Networking configuration for OpenStack deployment.

    Attributes:
        internal_domain: Internal DNS domain.
        external_domain: External DNS domain.
        ingress: Ingress configuration.
        physical_network_mappings: Physical network to provider mapping.
    """

    model_config = ConfigDict(populate_by_name=True)

    internal_domain: str = Field(
        default="cluster.local",
        alias="internalDomain",
        description="Internal DNS domain",
    )
    external_domain: str | None = Field(
        None,
        alias="externalDomain",
        description="External DNS domain",
    )
    ingress: dict[str, Any] = Field(
        default_factory=dict,
        description="Ingress configuration",
    )
    physical_network_mappings: dict[str, str] = Field(
        default_factory=dict,
        alias="physicalNetworkMappings",
        description="Physical network mappings",
    )

    def to_kubernetes(self) -> dict[str, Any]:
        """Convert to Kubernetes API format."""
        result: dict[str, Any] = {
            "internalDomain": self.internal_domain,
        }
        if self.external_domain is not None:
            result["externalDomain"] = self.external_domain
        if self.ingress:
            result["ingress"] = self.ingress
        if self.physical_network_mappings:
            result["physicalNetworkMappings"] = self.physical_network_mappings
        return result


class ServiceConfig(BaseModel):
    """Configuration for an individual OpenStack service.

    Attributes:
        enabled: Whether the service is enabled.
        replicas: Number of replicas.
        resources: Resource requests/limits.
        config: Service-specific configuration.
    """

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = Field(True, description="Whether service is enabled")
    replicas: int | None = Field(None, description="Number of replicas")
    resources: dict[str, Any] = Field(
        default_factory=dict,
        description="Resource requests/limits",
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Service-specific configuration",
    )

    def to_kubernetes(self) -> dict[str, Any]:
        """Convert to Kubernetes API format."""
        result: dict[str, Any] = {"enabled": self.enabled}
        if self.replicas is not None:
            result["replicas"] = self.replicas
        if self.resources:
            result["resources"] = self.resources
        if self.config:
            result["config"] = self.config
        return result


class OpenStackServicesSpec(BaseModel):
    """OpenStack services configuration.

    Attributes:
        keystone: Identity service configuration.
        glance: Image service configuration.
        nova: Compute service configuration.
        neutron: Network service configuration.
        cinder: Block storage configuration.
        heat: Orchestration service configuration.
        horizon: Dashboard configuration.
        octavia: Load balancer configuration.
        manila: Shared filesystem configuration.
        barbican: Key management configuration.
    """

    model_config = ConfigDict(populate_by_name=True)

    keystone: ServiceConfig | None = Field(None, description="Identity service")
    glance: ServiceConfig | None = Field(None, description="Image service")
    nova: ServiceConfig | None = Field(None, description="Compute service")
    neutron: ServiceConfig | None = Field(None, description="Network service")
    cinder: ServiceConfig | None = Field(None, description="Block storage")
    heat: ServiceConfig | None = Field(None, description="Orchestration")
    horizon: ServiceConfig | None = Field(None, description="Dashboard")
    octavia: ServiceConfig | None = Field(None, description="Load balancer")
    manila: ServiceConfig | None = Field(None, description="Shared filesystem")
    barbican: ServiceConfig | None = Field(None, description="Key management")

    def to_kubernetes(self) -> dict[str, Any]:
        """Convert to Kubernetes API format."""
        result: dict[str, Any] = {}
        for service in [
            "keystone",
            "glance",
            "nova",
            "neutron",
            "cinder",
            "heat",
            "horizon",
            "octavia",
            "manila",
            "barbican",
        ]:
            config = getattr(self, service)
            if config is not None:
                result[service] = config.to_kubernetes()
        return result


class OpenStackDeploymentSpec(BaseModel):
    """Specification for OpenStackDeployment resource.

    Attributes:
        openstack_version: OpenStack release version.
        preset: Deployment preset (compute, compute-tf, etc.).
        size: Deployment size (small, medium, large).
        features: Feature flags.
        networking: Networking configuration.
        services: Per-service configuration.
        node_selector: Node selector for service placement.
        public_domain_name: Public domain for OpenStack endpoints.
        secrets_name: Name of secret containing credentials.
    """

    model_config = ConfigDict(populate_by_name=True)

    openstack_version: str = Field(
        ...,
        alias="openStackVersion",
        description="OpenStack release (e.g., 'yoga', 'zed', 'antelope')",
    )
    preset: Literal[
        "compute",
        "compute-tf",
        "core",
        "core-ceph",
        "full",
    ] = Field(
        default="compute",
        description="Deployment preset",
    )
    size: Literal["tiny", "small", "medium", "large", "xlarge"] = Field(
        default="medium",
        description="Deployment size",
    )
    features: OpenStackFeatures = Field(
        default_factory=OpenStackFeatures,
        description="Feature flags",
    )
    networking: OpenStackNetworkingSpec = Field(
        default_factory=OpenStackNetworkingSpec,
        description="Networking configuration",
    )
    services: OpenStackServicesSpec = Field(
        default_factory=OpenStackServicesSpec,
        description="Per-service configuration",
    )
    node_selector: NodeSelector = Field(
        default_factory=NodeSelector,
        alias="nodeSelector",
        description="Node selector for service placement",
    )
    public_domain_name: str | None = Field(
        None,
        alias="publicDomainName",
        description="Public domain for OpenStack endpoints",
    )
    secrets_name: str | None = Field(
        None,
        alias="secretsName",
        description="Name of secret containing credentials",
    )

    def to_kubernetes(self) -> dict[str, Any]:
        """Convert to Kubernetes API format."""
        result: dict[str, Any] = {
            "openStackVersion": self.openstack_version,
            "preset": self.preset,
            "size": self.size,
        }

        features = self.features.to_kubernetes()
        if features:
            result["features"] = features

        networking = self.networking.to_kubernetes()
        if networking:
            result["networking"] = networking

        services = self.services.to_kubernetes()
        if services:
            result["services"] = services

        node_selector = self.node_selector.to_kubernetes()
        if node_selector:
            result["nodeSelector"] = node_selector

        if self.public_domain_name is not None:
            result["publicDomainName"] = self.public_domain_name
        if self.secrets_name is not None:
            result["secretsName"] = self.secrets_name

        return result


class ServiceStatus(BaseModel):
    """Status of an individual OpenStack service.

    Attributes:
        ready: Whether the service is ready.
        replicas: Number of running replicas.
        available_replicas: Number of available replicas.
        message: Status message.
    """

    model_config = ConfigDict(populate_by_name=True)

    ready: bool = Field(False, description="Whether service is ready")
    replicas: int = Field(0, description="Number of running replicas")
    available_replicas: int = Field(
        0,
        alias="availableReplicas",
        description="Number of available replicas",
    )
    message: str | None = Field(None, description="Status message")


class OpenStackDeploymentStatus(BaseModel):
    """Status of OpenStackDeployment resource.

    Attributes:
        phase: Current deployment phase.
        openstack_version: Deployed OpenStack version.
        observed_generation: Last observed generation.
        services: Per-service status.
        conditions: Status conditions.
        endpoints: Service endpoints.
        health: Overall health status.
        message: Status message.
    """

    model_config = ConfigDict(populate_by_name=True)

    phase: OSDPLPhase | None = Field(None, description="Current phase")
    openstack_version: str | None = Field(
        None,
        alias="openStackVersion",
        description="Deployed OpenStack version",
    )
    observed_generation: int | None = Field(
        None,
        alias="observedGeneration",
        description="Last observed generation",
    )
    services: dict[str, ServiceStatus] = Field(
        default_factory=dict,
        description="Per-service status",
    )
    conditions: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Status conditions",
    )
    endpoints: dict[str, str] = Field(
        default_factory=dict,
        description="Service endpoints",
    )
    health: str | None = Field(None, description="Overall health status")
    message: str | None = Field(None, description="Status message")

    @property
    def is_healthy(self) -> bool:
        """Check if deployment is healthy.

        Returns:
            True if deployment is in Deployed phase and healthy.
        """
        return self.phase == OSDPLPhase.DEPLOYED and self.health == "Healthy"

    @property
    def is_ready(self) -> bool:
        """Check if deployment is ready.

        Returns:
            True if deployment is in Deployed phase.
        """
        return self.phase == OSDPLPhase.DEPLOYED


class OpenStackDeployment(KubernetesResource[OpenStackDeploymentSpec, OpenStackDeploymentStatus]):
    """OpenStackDeployment custom resource.

    Represents an OpenStack deployment managed by the Rockoon operator.

    Example:
        osdpl = OpenStackDeployment(
            metadata=KubernetesMetadata(
                name="openstack",
                namespace="openstack",
            ),
            spec=OpenStackDeploymentSpec(
                openstack_version="antelope",
                preset="compute",
                size="medium",
                features=OpenStackFeatures(
                    ssl={"public_endpoints": True},
                ),
            ),
        )
    """

    API_VERSION: ClassVar[str] = "lcm.mirantis.com/v1alpha1"
    KIND: ClassVar[str] = "OpenStackDeployment"
    PLURAL: ClassVar[str] = "openstackdeployments"
    GROUP: ClassVar[str] = "lcm.mirantis.com"

    api_version: str = Field(default="lcm.mirantis.com/v1alpha1", alias="apiVersion")
    kind: str = Field(default="OpenStackDeployment")
    spec: OpenStackDeploymentSpec
    status: OpenStackDeploymentStatus | None = None

    @property
    def is_healthy(self) -> bool:
        """Check if deployment is healthy.

        Returns:
            True if status indicates healthy deployment.
        """
        return self.status is not None and self.status.is_healthy

    @property
    def current_version(self) -> str | None:
        """Get the currently deployed version.

        Returns:
            Deployed OpenStack version or None.
        """
        if self.status:
            return self.status.openstack_version
        return None

    @property
    def target_version(self) -> str:
        """Get the target version from spec.

        Returns:
            Target OpenStack version.
        """
        return self.spec.openstack_version

    @classmethod
    def from_kubernetes(cls, data: dict[str, Any]) -> OpenStackDeployment:
        """Create from Kubernetes API response.

        Args:
            data: Resource dictionary from Kubernetes API.

        Returns:
            OpenStackDeployment instance.
        """
        spec_data = data.get("spec", {})

        # Parse features
        features_data = spec_data.get("features", {})
        features = OpenStackFeatures(
            ssl=features_data.get("ssl", {}),
            neutron=features_data.get("neutron", {}),
            nova=features_data.get("nova", {}),
            services=features_data.get("services", {}),
        )

        # Parse networking
        networking_data = spec_data.get("networking", {})
        networking = OpenStackNetworkingSpec(
            internal_domain=networking_data.get("internalDomain", "cluster.local"),
            external_domain=networking_data.get("externalDomain"),
            ingress=networking_data.get("ingress", {}),
            physical_network_mappings=networking_data.get("physicalNetworkMappings", {}),
        )

        # Parse node selector
        ns_data = spec_data.get("nodeSelector", {})
        node_selector = NodeSelector(
            match_labels=ns_data.get("matchLabels", {}),
            match_expressions=ns_data.get("matchExpressions", []),
        )

        # Parse services (simplified - full implementation would parse each service)
        services_data = spec_data.get("services", {})
        services = OpenStackServicesSpec()

        for service_name in [
            "keystone",
            "glance",
            "nova",
            "neutron",
            "cinder",
            "heat",
            "horizon",
            "octavia",
            "manila",
            "barbican",
        ]:
            if service_name in services_data:
                svc_data = services_data[service_name]
                setattr(
                    services,
                    service_name,
                    ServiceConfig(
                        enabled=svc_data.get("enabled", True),
                        replicas=svc_data.get("replicas"),
                        resources=svc_data.get("resources", {}),
                        config=svc_data.get("config", {}),
                    ),
                )

        spec = OpenStackDeploymentSpec(
            openstack_version=spec_data.get("openStackVersion", ""),
            preset=spec_data.get("preset", "compute"),
            size=spec_data.get("size", "medium"),
            features=features,
            networking=networking,
            services=services,
            node_selector=node_selector,
            public_domain_name=spec_data.get("publicDomainName"),
            secrets_name=spec_data.get("secretsName"),
        )

        status = None
        if "status" in data:
            status_data = data["status"]
            phase = None
            if "phase" in status_data:
                with contextlib.suppress(ValueError):
                    phase = OSDPLPhase(status_data["phase"])

            services_status = {}
            for name, svc_status in status_data.get("services", {}).items():
                services_status[name] = ServiceStatus(
                    ready=svc_status.get("ready", False),
                    replicas=svc_status.get("replicas", 0),
                    available_replicas=svc_status.get("availableReplicas", 0),
                    message=svc_status.get("message"),
                )

            status = OpenStackDeploymentStatus(
                phase=phase,
                openstack_version=status_data.get("openStackVersion"),
                observed_generation=status_data.get("observedGeneration"),
                services=services_status,
                conditions=status_data.get("conditions", []),
                endpoints=status_data.get("endpoints", {}),
                health=status_data.get("health"),
                message=status_data.get("message"),
            )

        return cls(
            metadata=KubernetesMetadata.from_kubernetes(data["metadata"]),
            spec=spec,
            status=status,
        )
