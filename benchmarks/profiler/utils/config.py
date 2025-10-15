# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import logging
import math
import shlex
from typing import Literal, Optional, Protocol

import yaml
from pydantic import BaseModel

from benchmarks.profiler.utils.planner_utils import build_planner_args_from_namespace
from dynamo.planner.defaults import WORKER_COMPONENT_NAMES, SubComponentType

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s", "%Y-%m-%d %H:%M:%S"
)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)


class VolumeMount(BaseModel):
    name: str = "dynamo-pvc"
    mountPoint: str = "/data"


class Container(BaseModel):
    image: Optional[str] = None
    workingDir: Optional[str] = None
    command: Optional[list[str]] = None
    args: Optional[list[str]] = None
    model_config = {"extra": "allow"}


class PodSpec(BaseModel):
    mainContainer: Optional[Container] = None
    model_config = {"extra": "allow"}


class ServiceResources(BaseModel):
    requests: Optional[dict[str, str]] = None
    limits: Optional[dict[str, str]] = None


class Service(BaseModel):
    replicas: Optional[int] = None
    resources: Optional[ServiceResources] = None
    extraPodSpec: Optional[PodSpec] = None
    subComponentType: Optional[str] = None
    model_config = {"extra": "allow"}


class Services(BaseModel):
    Frontend: Service
    model_config = {"extra": "allow"}


class PVCConfig(BaseModel):
    name: str = "dynamo-pvc"
    create: Optional[bool] = False
    model_config = {"extra": "allow"}


class Spec(BaseModel):
    services: dict[str, Service]
    pvcs: Optional[list[PVCConfig]] = None
    model_config = {"extra": "allow"}


class Metadata(BaseModel):
    name: str
    model_config = {"extra": "allow"}


class Config(BaseModel):
    metadata: Metadata
    spec: Spec
    model_config = {"extra": "allow"}


class MultinodeConfig(BaseModel):
    nodeCount: int


class DgdPlannerServiceConfig(BaseModel):
    dynamoNamespace: str = "dynamo"  # placeholder
    componentType: str = "planner"
    replicas: int = 1
    volumeMounts: list[VolumeMount] = [VolumeMount()]
    extraPodSpec: PodSpec = PodSpec(
        mainContainer=Container(
            image="my-registry/dynamo-runtime:my-tag",  # placeholder
            workingDir="/workspace/components/src/dynamo/planner",
            command=["python3", "-m", "planner_sla"],
            args=[],
        )
    )
    model_config = {"extra": "allow"}


def break_arguments(args: list[str] | None) -> list[str]:
    ans: list[str] = []
    if args is None:
        return ans
    if isinstance(args, str):
        # Use shlex.split to properly handle quoted arguments and JSON values
        ans = shlex.split(args)
    else:
        for arg in args:
            if arg is not None:
                # Use shlex.split to properly handle quoted arguments
                ans.extend(shlex.split(arg))
    return ans


def remove_valued_arguments(args: list[str], key: str) -> list[str]:
    """Remove a valued argument (e.g., --key value) from the arguments list if exists."""
    if key in args:
        idx = args.index(key)
        if idx + 1 < len(args):
            del args[idx : idx + 2]

    return args


def append_argument(args: list[str], to_append) -> list[str]:
    idx = find_arg_index(args)
    if isinstance(to_append, list):
        args[idx:idx] = to_append
    else:
        args.insert(idx, to_append)
    return args


def find_arg_index(args: list[str]) -> int:
    # find the correct index to insert an argument
    idx = len(args)

    try:
        new_idx = args.index("|")
        idx = min(idx, new_idx)
    except ValueError:
        pass

    try:
        new_idx = args.index("2>&1")
        idx = min(idx, new_idx)
    except ValueError:
        pass

    return idx


def parse_override_engine_args(args: list[str]) -> tuple[dict, list[str]]:
    """
    Parse and extract --override-engine-args from argument list.

    Returns:
        tuple: (override_dict, modified_args) where override_dict is the parsed JSON
               and modified_args is the args list with --override-engine-args removed
    """
    override_dict = {}
    try:
        idx = args.index("--override-engine-args")
        if idx + 1 < len(args):
            # Parse existing override
            override_dict = json.loads(args[idx + 1])
            # Remove the old override args
            del args[idx : idx + 2]
    except (ValueError, json.JSONDecodeError):
        pass  # No existing override or invalid JSON

    return override_dict, args


def set_multinode_config(worker_service, gpu_count: int, num_gpus_per_node: int):
    """Helper function to set multinode configuration based on GPU count and GPUs per node."""
    if gpu_count <= num_gpus_per_node:
        # Single node: remove multinode configuration if present
        if (
            hasattr(worker_service, "multinode")
            and worker_service.multinode is not None
        ):
            worker_service.multinode = None
    else:
        # Multi-node: set nodeCount = math.ceil(gpu_count / num_gpus_per_node)
        node_count = math.ceil(gpu_count / num_gpus_per_node)
        if not hasattr(worker_service, "multinode") or worker_service.multinode is None:
            # Create multinode configuration if it doesn't exist
            worker_service.multinode = MultinodeConfig(nodeCount=node_count)
        else:
            # Handle both dict (from YAML) and MultinodeConfig object cases
            if isinstance(worker_service.multinode, dict):
                worker_service.multinode["nodeCount"] = node_count
            else:
                worker_service.multinode.nodeCount = node_count


def get_service_name_by_type(
    config: Config, backend: str, sub_component_type: SubComponentType
) -> str:
    """Helper function to get service name by subComponentType.

    First tries to find service by subComponentType, then falls back to component name.

    Args:
        config: Configuration object
        backend: Backend name (e.g., "sglang", "vllm", "trtllm")
        sub_component_type: The type of sub-component to look for (PREFILL or DECODE)

    Returns:
        The service name
    """
    # Check if config has the expected structure
    if not config.spec or not config.spec.services:
        # Fall back to default name if structure is unexpected
        if sub_component_type == SubComponentType.DECODE:
            return WORKER_COMPONENT_NAMES[backend].decode_worker_k8s_name
        else:
            return WORKER_COMPONENT_NAMES[backend].prefill_worker_k8s_name

    # Look through services to find one with matching subComponentType
    services = config.spec.services
    for service_name, service_config in services.items():
        if service_config.subComponentType == sub_component_type.value:
            return service_name

    # Fall back to default component names
    if sub_component_type == SubComponentType.DECODE:
        default_name = WORKER_COMPONENT_NAMES[backend].decode_worker_k8s_name
    else:
        default_name = WORKER_COMPONENT_NAMES[backend].prefill_worker_k8s_name

    # Check if the default name exists in services
    if default_name in services:
        return default_name

    # Last resort: return the default name anyway
    return default_name


def get_worker_service_from_config(
    config: Config,
    backend: str = "sglang",
    sub_component_type: SubComponentType = SubComponentType.DECODE,
):
    """Helper function to get a worker service from config.

    First tries to find service by subComponentType, then falls back to component name.

    Args:
        config: Configuration dictionary
        backend: Backend name (e.g., "sglang", "vllm", "trtllm"). Defaults to "sglang".
        sub_component_type: The type of sub-component to look for (PREFILL or DECODE). Defaults to DECODE.

    Returns:
        The worker service from the configuration
    """
    if backend not in WORKER_COMPONENT_NAMES:
        raise ValueError(
            f"Unsupported backend: {backend}. Supported backends: {list(WORKER_COMPONENT_NAMES.keys())}"
        )

    # Get the service name using the type-aware logic
    service_name = get_service_name_by_type(config, backend, sub_component_type)

    # Get the actual service from the config
    return config.spec.services[service_name]


def setup_worker_service_resources(
    worker_service, gpu_count: int, num_gpus_per_node: Optional[int] = None
):
    """Helper function to set up worker service resources (requests and limits)."""
    # Handle multinode configuration if num_gpus_per_node is provided
    if num_gpus_per_node is not None:
        set_multinode_config(worker_service, gpu_count, num_gpus_per_node)

    # Ensure resources exists
    if worker_service.resources is None:
        worker_service.resources = ServiceResources()

    # Ensure requests exists
    if worker_service.resources.requests is None:
        worker_service.resources.requests = {}

    # Set GPU requests
    gpu_value = (
        min(gpu_count, num_gpus_per_node)
        if num_gpus_per_node is not None
        else gpu_count
    )
    worker_service.resources.requests["gpu"] = str(gpu_value)

    # Update limits if they exist
    if worker_service.resources.limits is not None:
        worker_service.resources.limits["gpu"] = str(gpu_value)


def validate_and_get_worker_args(worker_service, backend):
    """Helper function to validate worker service and get its arguments.

    Args:
        worker_service: Worker service object to validate
        backend: Backend name (e.g., "sglang", "vllm", "trtllm"). Defaults to "sglang".

    Returns:
        List of arguments from the worker service
    """
    if backend not in WORKER_COMPONENT_NAMES:
        raise ValueError(
            f"Unsupported backend: {backend}. Supported backends: {list(WORKER_COMPONENT_NAMES.keys())}"
        )

    if not worker_service.extraPodSpec or not worker_service.extraPodSpec.mainContainer:
        raise ValueError(
            f"Missing extraPodSpec or mainContainer in {backend} decode worker service '{WORKER_COMPONENT_NAMES[backend].decode_worker_k8s_name}'"
        )

    args = worker_service.extraPodSpec.mainContainer.args
    return break_arguments(args)


def set_argument_value(args: list, arg_name: str, value: str):
    """Helper function to set an argument value, adding it if not present."""
    try:
        idx = args.index(arg_name)
        args[idx + 1] = value
    except ValueError:
        args = append_argument(args, [arg_name, value])
    return args


class ConfigModifierProtocol(Protocol):
    @classmethod
    def convert_config(
        cls,
        config: dict,
        target: Literal["prefill", "decode"],
        is_moe_model: bool = False,
    ) -> dict:
        ...

    @classmethod
    def set_config_tp_size(
        cls,
        config: dict,
        tp_size: int,
        component_type: SubComponentType = SubComponentType.DECODE,
    ) -> dict:
        ...

    @classmethod
    def set_config_tep_size(
        cls,
        config: dict,
        tep_size: int,
        num_gpus_per_node: int,
        component_type: SubComponentType = SubComponentType.DECODE,
    ) -> dict:
        ...

    @classmethod
    def set_config_dep_size(
        cls,
        config: dict,
        dep_size: int,
        num_gpus_per_node: int,
        component_type: SubComponentType = SubComponentType.DECODE,
    ) -> dict:
        ...

    @classmethod
    def get_model_name(cls, config: dict) -> str:
        ...

    @classmethod
    def get_port(cls, config: dict) -> int:
        ...

    @classmethod
    def get_kv_cache_size_from_dynamo_log(
        cls, dynamo_log_fn: str, attention_dp_size: int = 1
    ) -> int:
        ...


def generate_dgd_config_with_planner(
    config_path: str,
    config_modifier,
    best_prefill_gpus: int,
    best_decode_gpus: int,
    output_dir: str,
    args,
    is_moe_model: bool = False,
    num_gpus_per_node: int = 8,
):
    """Generate DGD config with planner based on profiling results.

    Args:
        config_path: Path to the YAML config file
        config_modifier: Config modifier instance (e.g., SGLangConfigModifier)
        best_prefill_gpus: Number of GPUs for prefill engine
        best_decode_gpus: Number of GPUs for decode engine
        output_dir: Output directory for profile results
        args: Parsed arguments namespace from profile_sla
        is_moe_model: Whether this is an MoE model
        num_gpus_per_node: Number of GPUs per node (for MoE models)

    Returns:
        dict: Final DGD config with planner service configured
    """

    # Load config from file
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    if not is_moe_model:
        # dense model, use TP for both prefill and decode
        config = config_modifier.set_config_tp_size(
            config, best_prefill_gpus, SubComponentType.PREFILL
        )
        config = config_modifier.set_config_tp_size(
            config, best_decode_gpus, SubComponentType.DECODE
        )
    else:
        # MoE model, use TEP for prefill and DEP for decode
        config = config_modifier.set_config_tep_size(
            config,
            best_prefill_gpus,
            num_gpus_per_node,
            SubComponentType.PREFILL,
        )
        config = config_modifier.set_config_dep_size(
            config,
            best_decode_gpus,
            num_gpus_per_node,
            SubComponentType.DECODE,
        )
    config = Config.model_validate(config)

    # add PVC config if not present
    if not config.spec.pvcs:
        config.spec.pvcs = [PVCConfig()]

    # add the planner service
    planner_config = DgdPlannerServiceConfig()
    frontend_service = config.spec.services["Frontend"]
    planner_config.dynamoNamespace = getattr(frontend_service, "dynamoNamespace", "dynamo")  # type: ignore[attr-defined]
    if frontend_service.extraPodSpec and frontend_service.extraPodSpec.mainContainer:
        frontend_image = frontend_service.extraPodSpec.mainContainer.image
        if frontend_image and planner_config.extraPodSpec.mainContainer:
            planner_config.extraPodSpec.mainContainer.image = frontend_image

    # Build planner args dynamically from parsed arguments
    # This includes shared args (ttft, itl, backend, namespace) from profile_sla
    # and planner-specific args (with planner_ prefix)
    planner_args = build_planner_args_from_namespace(args, prefix="planner_")

    # Override profiling-specific arguments with results from profiling
    # Remove and re-add to ensure correct values from profiling context
    planner_args = [
        arg
        for arg in planner_args
        if not any(
            arg.startswith(f"--{key}=")
            for key in [
                "namespace",
                "prefill-engine-num-gpu",
                "decode-engine-num-gpu",
                "profile-results-dir",
            ]
        )
    ]

    # Add arguments determined by profiling results
    frontend_namespace = getattr(config.spec.services["Frontend"], "dynamoNamespace", "dynamo")  # type: ignore[attr-defined]
    planner_args.extend(
        [
            f"--namespace={frontend_namespace}",
            f"--prefill-engine-num-gpu={best_prefill_gpus}",
            f"--decode-engine-num-gpu={best_decode_gpus}",
            f"--profile-results-dir={output_dir}",
        ]
    )

    if (
        planner_config.extraPodSpec.mainContainer
        and planner_config.extraPodSpec.mainContainer.args is not None
    ):
        planner_config.extraPodSpec.mainContainer.args.extend(planner_args)
    # Convert planner config to dict first, then the entire config to dict
    planner_dict = planner_config.model_dump(exclude_unset=False)
    config_dict = config.model_dump(exclude_unset=False)
    config_dict["spec"]["services"]["Planner"] = planner_dict

    return config_dict
