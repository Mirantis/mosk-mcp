"""Combined node template generation tool.

This module provides the generate_node_templates tool that generates all
resources needed to add a new node to a MOSK cluster:
- BMC Credentials Secret
- BareMetalHostInventory (BMHi)
- Machine CR

All templates use clear placeholders that must be replaced before applying.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from mosk_mcp.observability.logging import get_logger
from mosk_mcp.tools.template_generation.base import OutputFormat


class GenerationMode(str, Enum):
    """Mode for template generation.

    - sample: Generate templates with placeholders for exploration/documentation
    - interactive: Return list of required fields that must be provided (for LLM to ask user)
    - production: Validate all mandatory fields, fail with clear errors if missing
    """

    SAMPLE = "sample"
    INTERACTIVE = "interactive"
    PRODUCTION = "production"


# Field requirement definitions
MANDATORY_FIELDS = {
    "node_name": "Unique node identifier (e.g., compute-04)",
    "boot_mac_address": "Primary boot NIC MAC address for PXE boot (e.g., aa:bb:cc:dd:ee:ff)",
    "bmc_address": "BMC/IPMI address for hardware management (e.g., 192.168.100.10 or ipmi://192.168.100.10)",
    "namespace": "Kubernetes namespace where resources will be created",
}

RECOMMENDED_FIELDS = {
    "cluster_name": "Target cluster name (can be looked up: kubectl get clusters -n <namespace>)",
    "bmhp_name": "BareMetalHostProfile name (can be looked up: kubectl get bmhp -n <namespace>)",
    "l2_template_label": "L2Template selector label (can be looked up: kubectl get l2templates -n <namespace> --show-labels)",
}

CREDENTIAL_FIELDS = {
    "bmc_username": "BMC/IPMI username for authentication",
    "bmc_password": "BMC/IPMI password for authentication",
}

OPTIONAL_FIELDS = {
    "additional_node_labels": "Custom node labels beyond role defaults (e.g., rack-id, node-type)",
    "region": "Region label value (default: region-one)",
}

logger = get_logger(__name__)


# Allowed node labels in MOSK clusters
ALLOWED_NODE_LABELS: list[dict[str, str]] = [
    {"displayName": "Stacklight", "key": "stacklight", "value": "enabled"},
    {
        "displayName": "OpenStack control plane",
        "key": "openstack-control-plane",
        "value": "enabled",
    },
    {"displayName": "OpenStack compute", "key": "openstack-compute-node", "value": "enabled"},
    {"displayName": "OpenStack gateway", "key": "openstack-gateway", "value": "enabled"},
    {"displayName": "Open vSwitch", "key": "openvswitch", "value": "enabled"},
    {"displayName": "Tungsten Fabric Analytics", "key": "tfanalytics", "value": "enabled"},
    {"displayName": "Tungsten Fabric Config", "key": "tfconfig", "value": "enabled"},
    {"displayName": "Tungsten Fabric Control", "key": "tfcontrol", "value": "enabled"},
    {"displayName": "Tungsten Fabric web UI", "key": "tfwebui", "value": "enabled"},
    {"displayName": "Tungsten Fabric Config database", "key": "tfconfigdb", "value": "enabled"},
    {
        "displayName": "Tungsten Fabric Analytics database",
        "key": "tfanalyticsdb",
        "value": "enabled",
    },
    {"displayName": "Tungsten Fabric vRouter", "key": "tfvrouter", "value": "enabled"},
    {"displayName": "Node Type", "key": "node-type", "value": "<custom>"},
    {"displayName": "Node Rack ID", "key": "rack-id", "value": "<custom>"},
]

# Build lookup structures for efficient validation
ALLOWED_LABEL_KEYS: set[str] = {label["key"] for label in ALLOWED_NODE_LABELS}
ALLOWED_LABEL_VALUES: dict[str, str] = {
    label["key"]: label["value"] for label in ALLOWED_NODE_LABELS
}


def validate_node_labels(
    labels: list[dict[str, str]] | None,
) -> tuple[list[dict[str, str]], list[str]]:
    """Validate node labels against the allowed list.

    Args:
        labels: List of labels to validate, each with 'key' and 'value'.

    Returns:
        Tuple of (valid_labels, errors).
        - valid_labels: Labels that passed validation.
        - errors: List of error messages for invalid labels.
    """
    if not labels:
        return [], []

    valid_labels: list[dict[str, str]] = []
    errors: list[str] = []

    for label in labels:
        key = label.get("key", "")
        value = label.get("value", "")

        # Check if key is in allowed list (case-sensitive)
        if key not in ALLOWED_LABEL_KEYS:
            # Check if this might be a case mismatch
            key_lower = key.lower()
            matching_keys = [k for k in ALLOWED_LABEL_KEYS if k.lower() == key_lower]
            if matching_keys:
                errors.append(
                    f"Invalid label key '{key}': Label keys are case-sensitive. "
                    f"Did you mean '{matching_keys[0]}'?"
                )
            else:
                errors.append(
                    f"Invalid label key '{key}': Not a supported MOSK node label. "
                    f"Supported labels: {', '.join(sorted(ALLOWED_LABEL_KEYS))}"
                )
            continue

        # Check if value is valid for this key
        expected_value = ALLOWED_LABEL_VALUES[key]
        if expected_value not in ("<custom>", value):
            errors.append(
                f"Invalid value '{value}' for label '{key}': "
                f"This label only supports the value '{expected_value}'"
            )
            continue

        valid_labels.append(label)

    return valid_labels, errors


# Node role configuration with correct labels from real MOSK clusters
NODE_ROLE_CONFIG: dict[str, dict[str, Any]] = {
    "compute": {
        "description": "OpenStack compute/hypervisor node",
        "machine_labels": {
            "hostlabel.bm.kaas.mirantis.com/worker": "worker",
        },
        "node_labels": [
            {"key": "openstack-compute-node", "value": "enabled"},
            {"key": "openvswitch", "value": "enabled"},
        ],
        "bmhp_suggestion": "worker-nova-cmp",
        "requires_rack_id": False,
    },
    "control": {
        "description": "OpenStack control plane node",
        "machine_labels": {
            "cluster.sigs.k8s.io/control-plane": "controlplane",
            "hostlabel.bm.kaas.mirantis.com/controlplane": "controlplane",
            "hostlabel.bm.kaas.mirantis.com/worker": "worker",
        },
        "node_labels": [
            {"key": "openstack-control-plane", "value": "enabled"},
            {"key": "openstack-gateway", "value": "enabled"},
            {"key": "openstack-compute-node", "value": "enabled"},
            {"key": "openvswitch", "value": "enabled"},
            {"key": "stacklight", "value": "enabled"},
        ],
        "bmhp_suggestion": "controller",
        "requires_rack_id": False,
    },
    "storage": {
        "description": "Ceph storage node (OSD) - rack-id label recommended for CRUSH rules",
        "machine_labels": {
            "hostlabel.bm.kaas.mirantis.com/worker": "worker",
        },
        "node_labels": [
            {"key": "role", "value": "ceph-osd-node"},
        ],
        "bmhp_suggestion": "storage-osd",
        "requires_rack_id": True,
    },
    "gateway": {
        "description": "Network gateway node",
        "machine_labels": {
            "hostlabel.bm.kaas.mirantis.com/worker": "worker",
        },
        "node_labels": [
            {"key": "openstack-gateway", "value": "enabled"},
            {"key": "openvswitch", "value": "enabled"},
        ],
        "bmhp_suggestion": "gateway",
        "requires_rack_id": False,
    },
    "generic": {
        "description": "Generic worker node (update labels as needed)",
        "machine_labels": {
            "hostlabel.bm.kaas.mirantis.com/worker": "worker",
        },
        "node_labels": [],
        "bmhp_suggestion": "worker",
        "requires_rack_id": False,
    },
}


class GenerateNodeTemplatesInput(BaseModel):
    """Input parameters for generating node templates.

    The 'mode' parameter controls validation behavior:
    - sample: All parameters optional, generates placeholders (for exploration)
    - interactive: Returns list of missing required fields (for LLM to ask user)
    - production: Validates mandatory fields, fails if missing

    MANDATORY fields (required for production mode):
    - node_name: Unique node identifier
    - boot_mac_address: Primary boot NIC MAC for PXE
    - bmc_address: BMC/IPMI address
    - namespace: Must be explicitly specified (no silent default)

    RECOMMENDED fields (can be looked up from cluster):
    - cluster_name, bmhp_name, l2_template_label

    CREDENTIALS (always shown as placeholders for security):
    - bmc_username, bmc_password
    """

    mode: GenerationMode = Field(
        default=GenerationMode.INTERACTIVE,
        description=(
            "Generation mode: 'sample' for placeholder templates, "
            "'interactive' to get list of required fields, "
            "'production' to validate and generate final templates"
        ),
    )
    node_name: str | None = Field(
        default=None,
        description="MANDATORY: Unique node identifier (e.g., compute-04)",
    )
    role: Literal["compute", "control", "storage", "gateway", "generic"] = Field(
        default="generic",
        description="Node role: compute, control, storage, gateway, or generic",
    )
    namespace: str | None = Field(
        default=None,
        description="MANDATORY: Kubernetes namespace for all resources. Must be explicitly provided.",
    )
    cluster_name: str | None = Field(
        default=None,
        description="RECOMMENDED: Cluster name (e.g., mos). Can lookup: kubectl get clusters -n <namespace>",
    )
    region: str = Field(
        default="region-one",
        description="Region label value",
    )
    bmhp_name: str | None = Field(
        default=None,
        description="RECOMMENDED: BareMetalHostProfile name. Can lookup: kubectl get bmhp -n <namespace>",
    )
    l2_template_label: str | None = Field(
        default=None,
        description="RECOMMENDED: L2Template selector label. Can lookup: kubectl get l2templates -n <namespace> --show-labels",
    )
    bmc_address: str | None = Field(
        default=None,
        description="MANDATORY: BMC/IPMI address (e.g., 192.168.100.10 or ipmi://192.168.100.10)",
    )
    boot_mac_address: str | None = Field(
        default=None,
        description="MANDATORY: Primary boot NIC MAC address for PXE boot (e.g., aa:bb:cc:dd:ee:ff)",
    )
    bmc_username: str | None = Field(
        default=None,
        description="BMC/IPMI username. If not provided, uses placeholder <BMC_USERNAME>",
    )
    bmc_password: str | None = Field(
        default=None,
        description="BMC/IPMI password. If not provided, uses placeholder <BMC_PASSWORD>",
    )
    rack_id: str | None = Field(
        default=None,
        description="Rack ID for Ceph storage nodes (used in CRUSH rules). MANDATORY for storage role.",
    )
    additional_node_labels: list[dict[str, str]] | None = Field(
        default=None,
        description="Additional node labels beyond role defaults",
    )
    output_format: OutputFormat = Field(
        default=OutputFormat.YAML,
        description="Output format (yaml or json)",
    )


class FieldRequirement(BaseModel):
    """A required or recommended field."""

    field_name: str = Field(..., description="Parameter name")
    description: str = Field(..., description="What this field is for")
    example: str | None = Field(default=None, description="Example value")
    lookup_command: str | None = Field(default=None, description="Command to find valid values")


class GenerateNodeTemplatesOutput(BaseModel):
    """Output from generate_node_templates tool.

    The 'status' field indicates the result:
    - success: Templates generated successfully (production mode with all fields, or sample mode)
    - missing_required: Missing mandatory fields (interactive/production mode)
    - validation_error: Invalid field values
    """

    status: Literal["success", "missing_required", "validation_error"] = Field(
        ..., description="Generation status"
    )
    mode: str = Field(..., description="Generation mode used")
    templates: str | None = Field(
        default=None, description="Complete templates with all resources (only if status=success)"
    )
    resources_included: list[str] | None = Field(
        default=None, description="List of resources in output"
    )
    placeholders_to_replace: list[str] | None = Field(
        default=None, description="Placeholders that must be replaced before applying"
    )
    apply_order: list[str] | None = Field(default=None, description="Order to apply resources")
    role_info: str = Field(..., description="Information about the selected role")

    # Validation feedback fields
    missing_mandatory: list[FieldRequirement] | None = Field(
        default=None,
        description="Mandatory fields that are missing (must be provided)",
    )
    missing_recommended: list[FieldRequirement] | None = Field(
        default=None,
        description="Recommended fields that are missing (can be looked up)",
    )
    optional_fields: list[FieldRequirement] | None = Field(
        default=None,
        description="Optional fields that can be provided for customization",
    )
    warnings: list[str] | None = Field(
        default=None,
        description="Warnings about the generated templates",
    )
    message: str | None = Field(
        default=None,
        description="Human-readable message explaining the status",
    )


class NodeTemplateGenerator:
    """Generator for complete node templates."""

    def _generate_header(
        self,
        role: str,
        node_name: str | None,
        placeholders: list[str],
    ) -> str:
        """Generate the template header with instructions."""
        role_config = NODE_ROLE_CONFIG.get(role, NODE_ROLE_CONFIG["generic"])
        role_desc = role_config["description"]

        name_display = node_name if node_name else "<NODE_NAME>"

        header = f"""# =============================================================================
# MOSK Node Templates - {role.title()} Node
# Generated for: {name_display}
# Role: {role_desc}
# =============================================================================
#
# REQUIRED - Replace these placeholders before applying:
"""
        for ph in placeholders:
            header += f"#   {ph}\n"

        header += """#
# APPLY ORDER:
#   1. kubectl apply -f <filename> (Secret first)
#   2. kubectl apply -f <filename> (BMHi second)
#   3. kubectl apply -f <filename> (Machine last)
#
# Or apply all at once: kubectl apply -f <filename>
# =============================================================================
"""
        return header

    def _generate_secret(
        self,
        node_name: str,
        namespace: str,
        bmc_username: str | None = None,
        bmc_password: str | None = None,
    ) -> dict[str, Any]:
        """Generate BMC credentials Secret."""
        secret_name = f"{node_name}-bmc-secret"

        return {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": secret_name,
                "namespace": namespace,
                "labels": {
                    "environment.metal3.io": "baremetal",
                },
            },
            "type": "Opaque",
            "stringData": {
                "username": bmc_username or "<BMC_USERNAME>",
                "password": bmc_password or "<BMC_PASSWORD>",
            },
        }

    def _generate_bmhi(
        self,
        node_name: str,
        namespace: str,
        bmc_address: str | None,
        boot_mac_address: str | None,
    ) -> dict[str, Any]:
        """Generate BareMetalHostInventory CR."""
        secret_name = f"{node_name}-bmc-secret"

        # Strip protocol prefixes from BMC address if present
        clean_bmc_address: str | None = bmc_address
        if bmc_address:
            for prefix in ("ipmi://", "redfish://", "ilo://", "idrac://", "https://", "http://"):
                if bmc_address.lower().startswith(prefix):
                    clean_bmc_address = bmc_address[len(prefix) :]
                    break

        # Convert MAC address to lowercase
        clean_mac_address: str | None = boot_mac_address.lower() if boot_mac_address else None

        return {
            "apiVersion": "kaas.mirantis.com/v1alpha1",
            "kind": "BareMetalHostInventory",
            "metadata": {
                "name": node_name,
                "namespace": namespace,
                "labels": {
                    "kaas.mirantis.com/baremetalhost-id": node_name,
                    "kaas.mirantis.com/provider": "baremetal",
                },
                "annotations": {
                    "inspect.metal3.io/hardwaredetails-storage-sort-term": "hctl ASC, wwn ASC, by_id ASC, name ASC",
                },
            },
            "spec": {
                "bootMACAddress": clean_mac_address or "<BOOT_MAC_ADDRESS>",
                "bmc": {
                    "address": clean_bmc_address or "<BMC_ADDRESS>",
                    "bmhCredentialsName": secret_name,
                },
                "online": True,
            },
        }

    def _generate_machine(
        self,
        node_name: str,
        namespace: str,
        role: str,
        cluster_name: str | None,
        region: str,
        bmhp_name: str | None,
        l2_template_label: str | None,
        rack_id: str | None,
        additional_node_labels: list[dict[str, str]] | None,
    ) -> dict[str, Any]:
        """Generate Machine CR."""
        role_config = NODE_ROLE_CONFIG.get(role, NODE_ROLE_CONFIG["generic"])

        # Build machine labels
        labels: dict[str, str] = {
            "cluster.sigs.k8s.io/cluster-name": cluster_name or "<CLUSTER_NAME>",
            "kaas.mirantis.com/provider": "baremetal",
            "kaas.mirantis.com/region": region,
        }
        # Add role-specific machine labels
        labels.update(role_config["machine_labels"])

        # Build node labels
        node_labels = list(role_config["node_labels"])

        # For storage nodes, add rack-id label (required for Ceph CRUSH rules)
        if role == "storage":
            rack_id_value = rack_id or "<RACK_ID>"
            node_labels.append({"key": "rack-id", "value": rack_id_value})

        if additional_node_labels:
            node_labels.extend(additional_node_labels)

        # Build providerSpec
        bmhp_ref = bmhp_name or "<BMHP_NAME>"
        provider_spec: dict[str, Any] = {
            "apiVersion": "baremetal.k8s.io/v1alpha1",
            "kind": "BareMetalMachineProviderSpec",
            "bareMetalHostProfile": {
                "name": bmhp_ref,
                "namespace": namespace,
            },
            "hostSelector": {
                "matchLabels": {
                    "kaas.mirantis.com/baremetalhost-id": node_name,
                },
            },
        }

        # Add l2TemplateSelector if provided or as placeholder
        if l2_template_label:
            provider_spec["l2TemplateSelector"] = {"label": l2_template_label}
        else:
            provider_spec["l2TemplateSelector"] = {"label": "<L2TEMPLATE_LABEL>"}

        # Add nodeLabels if any
        if node_labels:
            provider_spec["nodeLabels"] = node_labels

        return {
            "apiVersion": "cluster.k8s.io/v1alpha1",
            "kind": "Machine",
            "metadata": {
                "name": node_name,
                "namespace": namespace,
                "labels": labels,
            },
            "spec": {
                "providerSpec": {
                    "value": provider_spec,
                },
            },
        }

    def _to_yaml(self, obj: dict[str, Any]) -> str:
        """Convert dict to YAML string."""
        import yaml

        return yaml.dump(obj, default_flow_style=False, sort_keys=False, allow_unicode=True)

    def _add_comments_to_yaml(
        self,
        yaml_str: str,
        role: str,
        _bmhp_name: str | None,  # Reserved for future use
        namespace: str,
        use_namespace_placeholder: bool = False,
    ) -> str:
        """Add helpful comments to YAML output."""
        role_config = NODE_ROLE_CONFIG.get(role, NODE_ROLE_CONFIG["generic"])
        bmhp_suggestion = role_config["bmhp_suggestion"]

        # Use placeholder in lookup commands if namespace is a placeholder
        ns_in_cmd = "<NAMESPACE>" if use_namespace_placeholder else namespace

        # Add comment for BMHP placeholder
        if "<BMHP_NAME>" in yaml_str:
            yaml_str = yaml_str.replace(
                "name: <BMHP_NAME>",
                f"name: <BMHP_NAME>  # Suggested: {bmhp_suggestion} (check: kubectl get bmhp -n {ns_in_cmd})",
            )

        # Add comment for L2Template placeholder
        if "<L2TEMPLATE_LABEL>" in yaml_str:
            yaml_str = yaml_str.replace(
                "label: <L2TEMPLATE_LABEL>",
                f"label: <L2TEMPLATE_LABEL>  # Check: kubectl get l2templates -n {ns_in_cmd} --show-labels",
            )

        # Add comment for cluster name placeholder
        if "<CLUSTER_NAME>" in yaml_str:
            yaml_str = yaml_str.replace(
                "cluster.sigs.k8s.io/cluster-name: <CLUSTER_NAME>",
                f"cluster.sigs.k8s.io/cluster-name: <CLUSTER_NAME>  # Check: kubectl get clusters -n {ns_in_cmd}",
            )

        return yaml_str

    def _add_namespace_comment(
        self, yaml_str: str, namespace: str, use_placeholder: bool = False
    ) -> str:
        """Add comment to namespace field."""
        if use_placeholder:
            # Namespace is already <NAMESPACE>, just add a helpful comment
            yaml_str = yaml_str.replace(
                "namespace: <NAMESPACE>\n",
                "namespace: <NAMESPACE>  # Check: kubectl get namespaces\n",
            )
        else:
            # Add verification comment to actual namespace
            yaml_str = yaml_str.replace(
                f"namespace: {namespace}\n",
                f"namespace: {namespace}  # VERIFY: Ensure this matches your target namespace\n",
                1,  # Only replace first occurrence (in metadata)
            )
        return yaml_str

    def _validate_fields(
        self, input_data: GenerateNodeTemplatesInput
    ) -> tuple[
        list[FieldRequirement],
        list[FieldRequirement],
        list[FieldRequirement],
        list[str],
        list[str],
        list[dict[str, str]],
    ]:
        """Validate input fields and return missing mandatory, recommended, optional, warnings, validation errors, and validated labels."""
        missing_mandatory: list[FieldRequirement] = []
        missing_recommended: list[FieldRequirement] = []
        optional_fields: list[FieldRequirement] = []
        warnings: list[str] = []
        validation_errors: list[str] = []
        validated_labels: list[dict[str, str]] = []

        # Check mandatory fields
        if not input_data.node_name:
            missing_mandatory.append(
                FieldRequirement(
                    field_name="node_name",
                    description="Unique node identifier",
                    example="compute-04",
                )
            )
        if not input_data.boot_mac_address:
            missing_mandatory.append(
                FieldRequirement(
                    field_name="boot_mac_address",
                    description="Primary boot NIC MAC address for PXE boot",
                    example="aa:bb:cc:dd:ee:ff",
                )
            )
        if not input_data.bmc_address:
            missing_mandatory.append(
                FieldRequirement(
                    field_name="bmc_address",
                    description="BMC/IPMI address for hardware management",
                    example="192.168.100.10 or ipmi://192.168.100.10",
                )
            )
        if not input_data.namespace:
            missing_mandatory.append(
                FieldRequirement(
                    field_name="namespace",
                    description="Kubernetes namespace where resources will be created",
                    example="default",
                    lookup_command="kubectl get namespaces",
                )
            )

        # Check rack_id for storage nodes
        if input_data.role == "storage" and not input_data.rack_id:
            missing_mandatory.append(
                FieldRequirement(
                    field_name="rack_id",
                    description="Rack identifier for Ceph CRUSH rules (required for storage nodes)",
                    example="rack1",
                )
            )

        # Check recommended fields
        if not input_data.cluster_name:
            missing_recommended.append(
                FieldRequirement(
                    field_name="cluster_name",
                    description="Target cluster name",
                    example="mos",
                    lookup_command="kubectl get clusters -n <namespace>",
                )
            )
        if not input_data.bmhp_name:
            role_config = NODE_ROLE_CONFIG.get(input_data.role, NODE_ROLE_CONFIG["generic"])
            missing_recommended.append(
                FieldRequirement(
                    field_name="bmhp_name",
                    description="BareMetalHostProfile name",
                    example=role_config["bmhp_suggestion"],
                    lookup_command="kubectl get bmhp -n <namespace>",
                )
            )
        if not input_data.l2_template_label:
            missing_recommended.append(
                FieldRequirement(
                    field_name="l2_template_label",
                    description="L2Template selector label for network configuration",
                    example="default",
                    lookup_command="kubectl get l2templates -n <namespace> --show-labels",
                )
            )

        # Optional fields - always show these as available options
        role_config = NODE_ROLE_CONFIG.get(input_data.role, NODE_ROLE_CONFIG["generic"])
        default_labels = [
            f"{lbl['key']}={lbl['value']}" for lbl in role_config.get("node_labels", [])
        ]
        # Build supported labels info for documentation
        supported_labels_list = ", ".join(sorted(ALLOWED_LABEL_KEYS))
        optional_fields.append(
            FieldRequirement(
                field_name="additional_node_labels",
                description=(
                    f"Custom node labels beyond role defaults. Role '{input_data.role}' already includes: "
                    f"{', '.join(default_labels) if default_labels else 'none'}. "
                    f"Supported labels (case-sensitive): {supported_labels_list}"
                ),
                example='[{"key": "node-type", "value": "sriov"}]',
            )
        )

        # Credential warnings
        if not input_data.bmc_username or not input_data.bmc_password:
            warnings.append(
                "BMC credentials (bmc_username, bmc_password) not provided. "
                "Templates will use placeholders <BMC_USERNAME> and <BMC_PASSWORD> that must be replaced."
            )

        # Validate additional node labels
        if input_data.additional_node_labels:
            validated_labels, label_errors = validate_node_labels(input_data.additional_node_labels)
            validation_errors.extend(label_errors)

        return (
            missing_mandatory,
            missing_recommended,
            optional_fields,
            warnings,
            validation_errors,
            validated_labels,
        )

    def generate(self, input_data: GenerateNodeTemplatesInput) -> GenerateNodeTemplatesOutput:
        """Generate complete node templates with validation based on mode."""
        logger.info(
            "generating_node_templates",
            node_name=input_data.node_name,
            role=input_data.role,
            mode=input_data.mode.value,
        )

        role_config = NODE_ROLE_CONFIG.get(input_data.role, NODE_ROLE_CONFIG["generic"])
        role_info = f"{input_data.role}: {role_config['description']}"

        # Validate fields
        (
            missing_mandatory,
            missing_recommended,
            optional_fields,
            warnings,
            validation_errors,
            validated_labels,
        ) = self._validate_fields(input_data)

        # Handle validation errors first - these block generation in all modes except SAMPLE
        if validation_errors and input_data.mode != GenerationMode.SAMPLE:
            supported_labels_info = "\n".join(
                f"  - {label['key']}: {label['displayName']} (value: {label['value']})"
                for label in ALLOWED_NODE_LABELS
            )
            message_parts = ["Validation errors found in additional_node_labels:"]
            for error in validation_errors:
                message_parts.append(f"  • {error}")
            message_parts.append("\nSupported node labels:")
            message_parts.append(supported_labels_info)

            return GenerateNodeTemplatesOutput(
                status="validation_error",
                mode=input_data.mode.value,
                role_info=role_info,
                missing_mandatory=missing_mandatory if missing_mandatory else None,
                missing_recommended=missing_recommended if missing_recommended else None,
                optional_fields=optional_fields if optional_fields else None,
                warnings=warnings if warnings else None,
                message="\n".join(message_parts),
            )

        # Handle INTERACTIVE mode - return field requirements without generating
        if input_data.mode == GenerationMode.INTERACTIVE and missing_mandatory:
            message_parts = ["Please provide the following MANDATORY information:"]
            for field in missing_mandatory:
                example = f" (e.g., {field.example})" if field.example else ""
                lookup = f" [Lookup: {field.lookup_command}]" if field.lookup_command else ""
                message_parts.append(
                    f"  - {field.field_name}: {field.description}{example}{lookup}"
                )

            if missing_recommended:
                message_parts.append(
                    "\nThe following RECOMMENDED fields can be looked up from your cluster:"
                )
                for field in missing_recommended:
                    lookup = f" [Lookup: {field.lookup_command}]" if field.lookup_command else ""
                    message_parts.append(f"  - {field.field_name}: {field.description}{lookup}")

            if optional_fields:
                message_parts.append("\nOPTIONAL fields for customization:")
                for field in optional_fields:
                    example = f" (e.g., {field.example})" if field.example else ""
                    message_parts.append(f"  - {field.field_name}: {field.description}{example}")

            return GenerateNodeTemplatesOutput(
                status="missing_required",
                mode=input_data.mode.value,
                role_info=role_info,
                missing_mandatory=missing_mandatory,
                missing_recommended=missing_recommended,
                optional_fields=optional_fields if optional_fields else None,
                message="\n".join(message_parts),
            )

        # Handle PRODUCTION mode - fail if mandatory fields missing
        if input_data.mode == GenerationMode.PRODUCTION and missing_mandatory:
            field_names = [f.field_name for f in missing_mandatory]
            return GenerateNodeTemplatesOutput(
                status="missing_required",
                mode=input_data.mode.value,
                role_info=role_info,
                missing_mandatory=missing_mandatory,
                missing_recommended=missing_recommended,
                optional_fields=optional_fields if optional_fields else None,
                warnings=warnings if warnings else None,
                message=f"Cannot generate production templates. Missing mandatory fields: {', '.join(field_names)}",
            )

        # SAMPLE mode or PRODUCTION/INTERACTIVE with all mandatory fields - generate templates
        # Determine if namespace should be a placeholder
        use_namespace_placeholder = (
            input_data.mode == GenerationMode.SAMPLE and not input_data.namespace
        )
        # Use <NAMESPACE> placeholder directly instead of "default" in sample mode
        namespace = (
            "<NAMESPACE>" if use_namespace_placeholder else (input_data.namespace or "default")
        )
        node_name = input_data.node_name or "<NODE_NAME>"

        # Collect placeholders that need to be replaced
        placeholders: list[str] = []

        if not input_data.node_name:
            placeholders.append("<NODE_NAME>: Unique node identifier (e.g., compute-04)")
        if use_namespace_placeholder:
            placeholders.append("<NAMESPACE>: Kubernetes namespace (e.g., default, lab)")
        if not input_data.bmc_address:
            placeholders.append("<BMC_ADDRESS>: BMC/IPMI IP address (e.g., 192.168.100.10)")
        if not input_data.boot_mac_address:
            placeholders.append(
                "<BOOT_MAC_ADDRESS>: Primary boot NIC MAC (e.g., aa:bb:cc:dd:ee:ff)"
            )
        if not input_data.bmc_username:
            placeholders.append("<BMC_USERNAME>: BMC credentials username")
        if not input_data.bmc_password:
            placeholders.append("<BMC_PASSWORD>: BMC credentials password")
        if not input_data.cluster_name:
            placeholders.append("<CLUSTER_NAME>: Target cluster name (e.g., mos)")
        if not input_data.bmhp_name:
            placeholders.append("<BMHP_NAME>: BareMetalHostProfile name")
        if not input_data.l2_template_label:
            placeholders.append("<L2TEMPLATE_LABEL>: L2Template selector label")
        if input_data.role == "storage" and not input_data.rack_id:
            placeholders.append(
                "<RACK_ID>: Rack identifier for Ceph CRUSH rules (e.g., rack1, rack2)"
            )

        # In SAMPLE mode, add validation errors as warnings (labels were filtered out)
        if validation_errors and input_data.mode == GenerationMode.SAMPLE:
            for error in validation_errors:
                warnings.append(f"Label not added to template: {error}")

        # Generate resources
        secret = self._generate_secret(
            node_name, namespace, input_data.bmc_username, input_data.bmc_password
        )
        bmhi = self._generate_bmhi(
            node_name,
            namespace,
            input_data.bmc_address,
            input_data.boot_mac_address,
        )
        machine = self._generate_machine(
            node_name,
            namespace,
            input_data.role,
            input_data.cluster_name,
            input_data.region,
            input_data.bmhp_name,
            input_data.l2_template_label,
            input_data.rack_id,
            validated_labels,  # Use only validated labels, not raw input
        )

        # Generate header
        header = self._generate_header(input_data.role, input_data.node_name, placeholders)

        # Convert to YAML and add comments
        secret_yaml = self._to_yaml(secret)
        bmhi_yaml = self._to_yaml(bmhi)
        machine_yaml = self._to_yaml(machine)

        # Add namespace comment/placeholder to all resources
        secret_yaml = self._add_namespace_comment(secret_yaml, namespace, use_namespace_placeholder)
        bmhi_yaml = self._add_namespace_comment(bmhi_yaml, namespace, use_namespace_placeholder)
        machine_yaml = self._add_namespace_comment(
            machine_yaml, namespace, use_namespace_placeholder
        )

        # Add helpful comments for placeholders
        machine_yaml = self._add_comments_to_yaml(
            machine_yaml,
            input_data.role,
            input_data.bmhp_name,
            namespace,
            use_namespace_placeholder,
        )

        # Combine all templates
        templates = header + "\n"
        templates += "---\n# 1. BMC Credentials Secret\n"
        templates += secret_yaml
        templates += "\n---\n# 2. BareMetalHostInventory\n"
        templates += bmhi_yaml
        templates += "\n---\n# 3. Machine CR\n"
        templates += machine_yaml

        logger.info(
            "generated_node_templates",
            node_name=node_name,
            role=input_data.role,
            mode=input_data.mode.value,
            placeholders_count=len(placeholders),
        )

        return GenerateNodeTemplatesOutput(
            status="success",
            mode=input_data.mode.value,
            templates=templates,
            resources_included=["Secret", "BareMetalHostInventory", "Machine"],
            placeholders_to_replace=placeholders if placeholders else None,
            apply_order=[
                "1. Secret (BMC credentials)",
                "2. BareMetalHostInventory (hardware registration)",
                "3. Machine (triggers provisioning)",
            ],
            role_info=role_info,
            missing_recommended=missing_recommended if missing_recommended else None,
            optional_fields=optional_fields if optional_fields else None,
            warnings=warnings if warnings else None,
            message="Templates generated successfully."
            + (
                f" Note: {len(placeholders)} placeholders need to be replaced before applying."
                if placeholders
                else ""
            ),
        )


# Singleton instance
_generator: NodeTemplateGenerator | None = None


def get_node_template_generator() -> NodeTemplateGenerator:
    """Get the singleton generator instance."""
    global _generator
    if _generator is None:
        _generator = NodeTemplateGenerator()
    return _generator


async def generate_node_templates(
    mode: Literal["sample", "interactive", "production"] = "interactive",
    node_name: str | None = None,
    role: Literal["compute", "control", "storage", "gateway", "generic"] = "generic",
    namespace: str | None = None,
    cluster_name: str | None = None,
    region: str = "region-one",
    bmhp_name: str | None = None,
    l2_template_label: str | None = None,
    bmc_address: str | None = None,
    boot_mac_address: str | None = None,
    bmc_username: str | None = None,
    bmc_password: str | None = None,
    rack_id: str | None = None,
    additional_node_labels: list[dict[str, str]] | None = None,
    output_format: OutputFormat = OutputFormat.YAML,
) -> GenerateNodeTemplatesOutput:
    """Generate complete templates for adding a new node to MOSK cluster.

    IMPORTANT: This tool uses three modes to guide proper template generation:

    MODES:
    - 'interactive' (default): Returns list of MANDATORY fields that must be provided.
      Use this first to discover what information is needed from the user.
    - 'sample': Generates templates with placeholders for exploration/documentation.
      Use when user explicitly asks for sample/example templates.
    - 'production': Validates all mandatory fields and fails if any are missing.
      Use when user has provided all required information.

    MANDATORY FIELDS (required for production mode):
    - node_name: Unique node identifier (e.g., compute-04)
    - boot_mac_address: Primary boot NIC MAC for PXE boot (e.g., aa:bb:cc:dd:ee:ff)
    - bmc_address: BMC/IPMI address (e.g., 192.168.100.10 or ipmi://192.168.100.10)
    - namespace: Kubernetes namespace (MUST be explicitly provided, no silent defaults)
    - rack_id: Required ONLY for storage role nodes

    RECOMMENDED FIELDS (can be looked up from cluster):
    - cluster_name: kubectl get clusters -n <namespace>
    - bmhp_name: kubectl get bmhp -n <namespace>
    - l2_template_label: kubectl get l2templates -n <namespace> --show-labels

    WORKFLOW FOR LLM:
    1. User asks to add a node → Call with mode='interactive' (default) to discover required fields
    2. Tool returns missing_mandatory list → ASK USER for those fields (do NOT guess values)
    3. User provides values → Call with mode='production' and all user-provided values
    4. If user explicitly asks for "sample" or "example" template → Use mode='sample'

    MODE SELECTION GUIDE:
    - User says "add node with IPMI 10.0.0.11" → mode='interactive' (incomplete info, need to ask)
    - User says "show me a sample template" → mode='sample' (explicit sample request)
    - User provided all mandatory fields → mode='production' (ready to generate)

    This tool generates:
    - BMC Credentials Secret
    - BareMetalHostInventory (BMHi)
    - Machine CR

    Args:
        mode: Generation mode - 'interactive' (default), 'sample', or 'production'.
        node_name: MANDATORY - Unique node identifier (e.g., compute-04).
        role: Node role determining labels:
            - compute: OpenStack compute/hypervisor node
            - control: OpenStack control plane node
            - storage: Ceph storage node (OSD) - requires rack_id
            - gateway: Network gateway node
            - generic: Basic worker (customize labels as needed)
        namespace: MANDATORY - Kubernetes namespace. Must be explicitly provided.
        cluster_name: RECOMMENDED - Cluster name. Can lookup: kubectl get clusters -n <namespace>
        region: Region label value (default: region-one).
        bmhp_name: RECOMMENDED - BareMetalHostProfile name. Can lookup: kubectl get bmhp -n <namespace>
        l2_template_label: RECOMMENDED - L2Template selector. Can lookup: kubectl get l2templates -n <namespace>
        bmc_address: MANDATORY - BMC/IPMI address for hardware management.
        boot_mac_address: MANDATORY - Primary boot NIC MAC address for PXE boot.
        bmc_username: BMC/IPMI username. If not provided, uses placeholder.
        bmc_password: BMC/IPMI password. If not provided, uses placeholder.
        rack_id: MANDATORY for storage role - Rack ID for Ceph CRUSH rules.
        additional_node_labels: Extra node labels beyond role defaults.
        output_format: Output format (currently only yaml supported).

    Returns:
        GenerateNodeTemplatesOutput containing:
        - status: 'success', 'missing_required', or 'validation_error'
        - mode: The generation mode used
        - templates: Complete YAML (only if status='success')
        - missing_mandatory: List of mandatory fields that need to be provided
        - missing_recommended: List of recommended fields that can be looked up
        - message: Human-readable explanation of the result
        - warnings: Any warnings about the templates

    Example - Interactive workflow:
        # Step 1: Discover required fields
        >>> output = await generate_node_templates(role="compute", bmc_address="10.0.0.11")
        >>> print(output.message)
        # "Please provide: node_name, boot_mac_address, namespace..."

        # Step 2: Generate with all required fields
        >>> output = await generate_node_templates(
        ...     mode="production",
        ...     node_name="compute-04",
        ...     role="compute",
        ...     namespace="default",
        ...     bmc_address="10.0.0.11",
        ...     boot_mac_address="aa:bb:cc:dd:ee:ff",
        ...     cluster_name="mos",
        ... )
        >>> print(output.templates)
    """
    generator = get_node_template_generator()

    # Convert string mode to enum
    mode_enum = GenerationMode(mode)

    input_data = GenerateNodeTemplatesInput(
        mode=mode_enum,
        node_name=node_name,
        role=role,
        namespace=namespace,
        cluster_name=cluster_name,
        region=region,
        bmhp_name=bmhp_name,
        l2_template_label=l2_template_label,
        bmc_address=bmc_address,
        boot_mac_address=boot_mac_address,
        bmc_username=bmc_username,
        bmc_password=bmc_password,
        rack_id=rack_id,
        additional_node_labels=additional_node_labels,
        output_format=output_format,
    )

    return generator.generate(input_data)
