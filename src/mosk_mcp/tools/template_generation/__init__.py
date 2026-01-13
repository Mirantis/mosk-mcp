"""Template generation tools for MOSK MCP Server.

This module provides tools for generating Kubernetes custom resource templates
for MOSK clusters. All tools are READ_ONLY safety level and generate templates
without modifying cluster state.

Tools:
    - generate_bmhi: Generate BareMetalHostInventory CR for hardware discovery
    - generate_bmhp: Generate BareMetalHostProfile CR with RAID/partition config
    - generate_machine: Generate Machine CR with role labels and network references
    - generate_l2template: Generate L2Template CR for network segments
    - generate_osdpl_patch: Generate OSDPL configuration patch with diff preview
    - validate_template: Validate generated template against cluster state

Example usage:
    >>> from mosk_mcp.tools.template_generation import generate_machine
    >>> output = await generate_machine(
    ...     name="compute-01",
    ...     role="compute",
    ...     bmhp_ref="compute-standard-profile",
    ... )
    >>> print(output.template.content)
"""

from __future__ import annotations

from mosk_mcp.tools.template_generation.base import (
    BaseTemplateGenerator,
    DiffOutput,
    OutputFormat,
    TemplateOutput,
)
from mosk_mcp.tools.template_generation.bmhi import (
    BMHIGenerator,
    GenerateBMHIInput,
    GenerateBMHIOutput,
    generate_bmhi,
    get_bmhi_generator,
)
from mosk_mcp.tools.template_generation.bmhp import (
    BMHPGenerator,
    DiskConfig,
    GenerateBMHPInput,
    GenerateBMHPOutput,
    PartitionConfig,
    RaidConfig,
    generate_bmhp,
    get_bmhp_generator,
)
from mosk_mcp.tools.template_generation.l2template import (
    GenerateL2TemplateInput,
    GenerateL2TemplateOutput,
    L2TemplateGenerator,
    generate_l2template,
    get_l2template_generator,
)
from mosk_mcp.tools.template_generation.machine import (
    GenerateMachineInput,
    GenerateMachineOutput,
    MachineGenerator,
    generate_machine,
    get_machine_generator,
)
from mosk_mcp.tools.template_generation.node_templates import (
    GenerateNodeTemplatesInput,
    GenerateNodeTemplatesOutput,
    NodeTemplateGenerator,
    generate_node_templates,
    get_node_template_generator,
)
from mosk_mcp.tools.template_generation.osdpl import (
    GenerateOSDPLPatchInput,
    GenerateOSDPLPatchOutput,
    OSDPLChange,
    OSDPLPatchGenerator,
    PatchOperation,
    generate_osdpl_patch,
    get_osdpl_patch_generator,
)
from mosk_mcp.tools.template_generation.validator import (
    TemplateValidator,
    ValidateTemplateInput,
    ValidateTemplateOutput,
    ValidationIssue,
    get_template_validator,
    validate_template,
)


__all__ = [
    "BMHIGenerator",
    "BMHPGenerator",
    "BaseTemplateGenerator",
    "DiffOutput",
    "DiskConfig",
    "GenerateBMHIInput",
    "GenerateBMHIOutput",
    "GenerateBMHPInput",
    "GenerateBMHPOutput",
    "GenerateL2TemplateInput",
    "GenerateL2TemplateOutput",
    "GenerateMachineInput",
    "GenerateMachineOutput",
    "GenerateNodeTemplatesInput",
    "GenerateNodeTemplatesOutput",
    "GenerateOSDPLPatchInput",
    "GenerateOSDPLPatchOutput",
    "L2TemplateGenerator",
    "MachineGenerator",
    "NodeTemplateGenerator",
    "OSDPLChange",
    "OSDPLPatchGenerator",
    "OutputFormat",
    "PartitionConfig",
    "PatchOperation",
    "RaidConfig",
    "TemplateOutput",
    "TemplateValidator",
    "ValidateTemplateInput",
    "ValidateTemplateOutput",
    "ValidationIssue",
    "generate_bmhi",
    "generate_bmhp",
    "generate_l2template",
    "generate_machine",
    "generate_node_templates",
    "generate_osdpl_patch",
    "get_bmhi_generator",
    "get_bmhp_generator",
    "get_l2template_generator",
    "get_machine_generator",
    "get_node_template_generator",
    "get_osdpl_patch_generator",
    "get_template_validator",
    "validate_template",
]
