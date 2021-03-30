from __future__ import annotations

import os
import tempfile

from types import SimpleNamespace
from os import path
from pydoc import locate
from typing import Any, Dict, Optional, Tuple

from ..conf import settings
from ..errors import UndefinedCustomImplementation
from ..utils import logger
from .constants import (
    ENV_K8S_SERVICE_HOST,
    DefaultCondaFile,
    DefaultEnvFilename,
    DefaultModelFilename,
    ModelDataType,
)
from .types import PredictMethodSignature, LoadMethodSignature
from .loader import (
    load_custom,
    save_custom,
    save_environment,
)
from .metadata import (
    ModelDataArgs,
    ModelDetails,
    ModelFramework,
    RuntimeOptions,
)
from .remote import Remote
from .runtime import ModelSpec, Runtime
from .args import infer_args, process_datatypes
from .protocol import Protocol
from ..kfserving import KFServingV2Protocol


class BaseModel:
    def __init__(
        self,
        name: str,
        user_func: PredictMethodSignature = None,
        load_func: LoadMethodSignature = None,
        local_folder: str = None,
        uri: str = None,
        platform: ModelFramework = None,
        inputs: ModelDataType = None,
        outputs: ModelDataType = None,
        conda_env: str = None,
        protocol: Protocol = None,
        runtime_options: RuntimeOptions = RuntimeOptions(),
    ):
        if protocol is None:
            protocol = KFServingV2Protocol()
        self._name = name
        self._user_func = user_func
        self._load_func = load_func
        self.conda_env_name = conda_env

        if uri is None:
            uri = ""

        local_folder = self._get_local_folder(local_folder)
        input_args, output_args = self._get_args(inputs, outputs)

        self.details = ModelDetails(
            name=name,
            local_folder=local_folder,
            uri=uri,
            platform=platform,
            inputs=input_args,
            outputs=output_args,
        )

        self.protocol = protocol
        self.model_spec = ModelSpec(
            model_details=self.details,
            protocol=self.protocol,
            runtime_options=runtime_options,
        )

        self.use_remote: bool = False
        self.runtime_options_override: Optional[RuntimeOptions] = None

        # ctx represents internal context shared (optionally) between different
        # methods of the model (e.g. predict, loader, etc.)
        self.ctx = SimpleNamespace()

    def set_remote(self, val: bool):
        self.use_remote = val

    def set_runtime_options_override(self, runtime_options: RuntimeOptions):
        self.runtime_options_override = runtime_options

    def _get_args(
        self, inputs: ModelDataType = None, outputs: ModelDataType = None
    ) -> Tuple[ModelDataArgs, ModelDataArgs]:
        if isinstance(inputs, ModelDataArgs) and isinstance(outputs, ModelDataArgs):
            return inputs, outputs

        if inputs or outputs:
            return process_datatypes(inputs, outputs)

        if self._user_func is not None:
            return infer_args(self._user_func)

        return ModelDataArgs(args=[]), ModelDataArgs(args=[])

    def _get_local_folder(self, local_folder: str = None) -> Optional[str]:
        if not local_folder:
            # TODO: Should we do cleanup at some point?
            local_folder = tempfile.mkdtemp()

        return local_folder

    @classmethod
    def load(cls, folder: str) -> "BaseModel":
        file_path_pkl = os.path.join(folder, DefaultModelFilename)
        return load_custom(file_path_pkl)

    def save(self, save_env=True):
        logger.info("Saving environment")
        if not self._user_func:
            # Nothing to save
            return

        file_path_pkl = os.path.join(self.details.local_folder, DefaultModelFilename)
        logger.info("Saving tempo model to %s", file_path_pkl)
        save_custom(self, file_path_pkl)

        if save_env:
            file_path_env = os.path.join(self.details.local_folder, DefaultEnvFilename)
            conda_env_file_path = path.join(self.details.local_folder, DefaultCondaFile)
            if not path.exists(conda_env_file_path):
                conda_env_file_path = None

            save_environment(
                conda_pack_file_path=file_path_env,
                conda_env_file_path=conda_env_file_path,
                env_name=self.conda_env_name,
            )

    def request(self, req: Dict) -> Dict:

        if self._user_func is None:
            raise UndefinedCustomImplementation(self.details.name)

        req_converted = self.protocol.from_protocol_request(req, self.details.inputs)
        if type(req_converted) == dict:
            response = self(**req_converted)
        elif type(req_converted) == list or type(req_converted) == tuple:
            response = self(*req_converted)
        else:
            response = self(req_converted)

        if type(response) == dict:
            response_converted = self.protocol.to_protocol_response(
                self.details, **response
            )
        elif type(response) == list or type(response) == tuple:
            response_converted = self.protocol.to_protocol_response(
                self.details, *response
            )
        else:
            response_converted = self.protocol.to_protocol_response(
                self.details, response
            )

        return response_converted

    def _get_model_spec(self) -> ModelSpec:
        if self.runtime_options_override:
            return ModelSpec(
                model_details=self.details,
                protocol=self.protocol,
                runtime_options=self.runtime_options_override,
            )
        else:
            return self.model_spec

    def _create_remote(self, model_spec: ModelSpec) -> Remote:
        cls_path = model_spec.runtime_options.runtime
        if cls_path is None:
            if settings.use_kubernetes or os.getenv(ENV_K8S_SERVICE_HOST):
                cls_path = model_spec.runtime_options.k8s_options.defaultRuntime
            else:
                cls_path = model_spec.runtime_options.docker_options.defaultRuntime
        logger.debug("Using remote class %s", cls_path)
        cls: Any = locate(cls_path)
        return cls()

    def remote(self, *args, **kwargs):
        remoter = self._create_remote(self._get_model_spec())
        return remoter.remote(self._get_model_spec(), *args, **kwargs)

    def wait_ready(self, runtime: Runtime, timeout_secs=None):
        return runtime.wait_ready_spec(
            self._get_model_spec(), timeout_secs=timeout_secs
        )

    def get_endpoint(self, runtime: Runtime):
        return runtime.get_endpoint_spec(self._get_model_spec())

    def to_k8s_yaml(self, runtime: Runtime) -> str:
        """
        Get k8s yaml
        """

        return runtime.to_k8s_yaml_spec(self._get_model_spec())

    def deploy(self, runtime: Runtime):
        # self.set_runtime(runtime)
        runtime.deploy_spec(self._get_model_spec())

    def undeploy(self, runtime: Runtime):
        # self.unset_runtime()
        logger.info("Undeploying %s", self.details.name)
        runtime.undeploy_spec(self._get_model_spec())

    def get_tempo(self) -> BaseModel:
        return self

    def __call__(self, *args, **kwargs) -> Any:
        if self._user_func is None:
            return self.remote(*args, **kwargs)

        if self.use_remote:
            return self.remote(*args, **kwargs)

        return self._user_func(*args, **kwargs)
