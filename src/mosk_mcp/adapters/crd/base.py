"""Base classes for Kubernetes Custom Resource Definitions.

This module provides the foundation for all Kubernetes resource models,
implementing standard metadata structures and serialization patterns.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field


class OwnerReference(BaseModel):
    """Kubernetes owner reference for resource ownership tracking.

    Attributes:
        api_version: API version of the referent.
        kind: Kind of the referent.
        name: Name of the referent.
        uid: UID of the referent.
        controller: If true, this reference points to the managing controller.
        block_owner_deletion: If true, blocks deletion of dependent.
    """

    model_config = ConfigDict(populate_by_name=True)

    api_version: str = Field(..., alias="apiVersion")
    kind: str
    name: str
    uid: str
    controller: bool | None = None
    block_owner_deletion: bool | None = Field(None, alias="blockOwnerDeletion")

    def to_kubernetes(self) -> dict[str, Any]:
        """Convert to Kubernetes API format.

        Returns:
            Dictionary suitable for Kubernetes API.
        """
        result: dict[str, Any] = {
            "apiVersion": self.api_version,
            "kind": self.kind,
            "name": self.name,
            "uid": self.uid,
        }
        if self.controller is not None:
            result["controller"] = self.controller
        if self.block_owner_deletion is not None:
            result["blockOwnerDeletion"] = self.block_owner_deletion
        return result


class KubernetesMetadata(BaseModel):
    """Standard Kubernetes metadata for all resources.

    Attributes:
        name: Name of the resource.
        namespace: Namespace of the resource (None for cluster-scoped).
        labels: Labels attached to the resource.
        annotations: Annotations attached to the resource.
        uid: Unique identifier assigned by Kubernetes.
        resource_version: Version for optimistic concurrency.
        generation: Generation of the resource.
        creation_timestamp: When the resource was created.
        deletion_timestamp: When the resource was deleted (if any).
        finalizers: List of finalizers.
        owner_references: List of owner references.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str
    namespace: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    uid: str | None = None
    resource_version: str | None = Field(None, alias="resourceVersion")
    generation: int | None = None
    creation_timestamp: datetime | None = Field(None, alias="creationTimestamp")
    deletion_timestamp: datetime | None = Field(None, alias="deletionTimestamp")
    finalizers: list[str] = Field(default_factory=list)
    owner_references: list[OwnerReference] = Field(default_factory=list, alias="ownerReferences")

    def to_kubernetes(self) -> dict[str, Any]:
        """Convert to Kubernetes API format.

        Returns:
            Dictionary suitable for Kubernetes API.
        """
        result: dict[str, Any] = {"name": self.name}

        if self.namespace is not None:
            result["namespace"] = self.namespace
        if self.labels:
            result["labels"] = self.labels
        if self.annotations:
            result["annotations"] = self.annotations
        if self.uid is not None:
            result["uid"] = self.uid
        if self.resource_version is not None:
            result["resourceVersion"] = self.resource_version
        if self.generation is not None:
            result["generation"] = self.generation
        if self.creation_timestamp is not None:
            result["creationTimestamp"] = self.creation_timestamp.isoformat()
        if self.deletion_timestamp is not None:
            result["deletionTimestamp"] = self.deletion_timestamp.isoformat()
        if self.finalizers:
            result["finalizers"] = self.finalizers
        if self.owner_references:
            result["ownerReferences"] = [ref.to_kubernetes() for ref in self.owner_references]

        return result

    @classmethod
    def from_kubernetes(cls, data: dict[str, Any]) -> KubernetesMetadata:
        """Create from Kubernetes API response.

        Args:
            data: Metadata dictionary from Kubernetes API.

        Returns:
            KubernetesMetadata instance.
        """
        owner_refs = []
        if "ownerReferences" in data:
            owner_refs = [
                OwnerReference(
                    api_version=ref["apiVersion"],
                    kind=ref["kind"],
                    name=ref["name"],
                    uid=ref["uid"],
                    controller=ref.get("controller"),
                    block_owner_deletion=ref.get("blockOwnerDeletion"),
                )
                for ref in data["ownerReferences"]
            ]

        return cls(
            name=data["name"],
            namespace=data.get("namespace"),
            labels=data.get("labels", {}),
            annotations=data.get("annotations", {}),
            uid=data.get("uid"),
            resource_version=data.get("resourceVersion"),
            generation=data.get("generation"),
            creation_timestamp=data.get("creationTimestamp"),
            deletion_timestamp=data.get("deletionTimestamp"),
            finalizers=data.get("finalizers", []),
            owner_references=owner_refs,
        )


SpecT = TypeVar("SpecT", bound=BaseModel)
StatusT = TypeVar("StatusT", bound="BaseModel | None")


class KubernetesResource(BaseModel, Generic[SpecT, StatusT]):
    """Base class for all Kubernetes custom resources.

    This generic base class provides common functionality for all CRDs,
    including serialization to/from Kubernetes API format.

    Attributes:
        api_version: API version (e.g., 'kaas.mirantis.com/v1alpha1').
        kind: Resource kind (e.g., 'Machine').
        metadata: Standard Kubernetes metadata.
        spec: Resource specification (type varies by resource).
        status: Resource status (type varies by resource).
    """

    model_config = ConfigDict(populate_by_name=True)

    # Class-level constants to be overridden by subclasses
    API_VERSION: ClassVar[str] = "v1"
    KIND: ClassVar[str] = "Resource"
    PLURAL: ClassVar[str] = "resources"
    GROUP: ClassVar[str] = ""

    api_version: str = Field(default="v1", alias="apiVersion")
    kind: str = Field(default="Resource")
    metadata: KubernetesMetadata
    spec: SpecT | None = None
    status: StatusT | None = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Set default values for api_version and kind from class constants."""
        super().__init_subclass__(**kwargs)

    def model_post_init(self, __context: Any) -> None:
        """Set api_version and kind from class constants if not provided."""
        if self.api_version == "v1" and hasattr(self.__class__, "API_VERSION"):
            object.__setattr__(self, "api_version", self.__class__.API_VERSION)
        if self.kind == "Resource" and hasattr(self.__class__, "KIND"):
            object.__setattr__(self, "kind", self.__class__.KIND)

    def to_kubernetes(self) -> dict[str, Any]:
        """Convert to Kubernetes API format.

        Returns:
            Dictionary suitable for Kubernetes API.
        """
        result: dict[str, Any] = {
            "apiVersion": self.api_version,
            "kind": self.kind,
            "metadata": self.metadata.to_kubernetes(),
        }

        if self.spec is not None:
            # Check if spec has a to_kubernetes method
            if hasattr(self.spec, "to_kubernetes"):
                result["spec"] = self.spec.to_kubernetes()
            else:
                result["spec"] = self.spec.model_dump(by_alias=True, exclude_none=True)

        if self.status is not None:
            if hasattr(self.status, "to_kubernetes"):
                result["status"] = self.status.to_kubernetes()
            else:
                result["status"] = self.status.model_dump(by_alias=True, exclude_none=True)

        return result

    def to_yaml_dict(self) -> dict[str, Any]:
        """Convert to dictionary suitable for YAML generation.

        This is useful for generating YAML manifests that users can apply.
        It excludes server-managed fields like uid, resourceVersion, etc.

        Returns:
            Dictionary suitable for YAML output.
        """
        result: dict[str, Any] = {
            "apiVersion": self.api_version,
            "kind": self.kind,
            "metadata": {
                "name": self.metadata.name,
            },
        }

        if self.metadata.namespace is not None:
            result["metadata"]["namespace"] = self.metadata.namespace
        if self.metadata.labels:
            result["metadata"]["labels"] = self.metadata.labels
        if self.metadata.annotations:
            result["metadata"]["annotations"] = self.metadata.annotations

        if self.spec is not None:
            if hasattr(self.spec, "to_kubernetes"):
                result["spec"] = self.spec.to_kubernetes()
            else:
                result["spec"] = self.spec.model_dump(by_alias=True, exclude_none=True)

        return result

    @property
    def full_name(self) -> str:
        """Get the fully qualified name (namespace/name or just name).

        Returns:
            Full resource name.
        """
        if self.metadata.namespace:
            return f"{self.metadata.namespace}/{self.metadata.name}"
        return self.metadata.name

    @property
    def resource_ref(self) -> str:
        """Get a reference string for this resource.

        Returns:
            Reference in format 'kind/name' or 'kind/namespace/name'.
        """
        if self.metadata.namespace:
            return f"{self.kind}/{self.metadata.namespace}/{self.metadata.name}"
        return f"{self.kind}/{self.metadata.name}"


ResourceT = TypeVar("ResourceT", bound=KubernetesResource[Any, Any])


class KubernetesResourceList(BaseModel, Generic[ResourceT]):
    """List of Kubernetes resources.

    Attributes:
        api_version: API version for the list.
        kind: Kind of the list (e.g., 'MachineList').
        metadata: List metadata (contains continue token for pagination).
        items: List of resources.
    """

    model_config = ConfigDict(populate_by_name=True)

    api_version: str = Field(..., alias="apiVersion")
    kind: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    items: list[ResourceT]

    @property
    def continue_token(self) -> str | None:
        """Get the continue token for pagination.

        Returns:
            Continue token or None if no more pages.
        """
        return self.metadata.get("continue")

    def __len__(self) -> int:
        """Return number of items in the list."""
        return len(self.items)

    def __iter__(self):  # type: ignore[no-untyped-def]
        """Iterate over items."""
        return iter(self.items)
