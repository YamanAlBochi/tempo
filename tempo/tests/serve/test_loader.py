import pytest
import os

from tempo.serve.loader import (
    _to_rclone,
    _get_environment,
    _add_required_deps,
    _get_pip_deps,
    _pack_environment,
)
from tempo.serve.constants import MLServerEnvDeps, DefaultEnvFilename


@pytest.mark.parametrize(
    "path, expected",
    [
        ("gs://mybucket/sklearn/iris", "gcs://mybucket/sklearn/iris"),
        ("s3://mybucket/sklear/iris", "s3://mybucket/sklear/iris"),
    ],
)
def test_rclone_conversion(path, expected):
    assert _to_rclone(path) == expected


def test_pack_environment(tmp_path):
    env = {"dependencies": [{"pip": MLServerEnvDeps}]}

    env_path = os.path.join(tmp_path, DefaultEnvFilename)
    _pack_environment(env=env, file_path=env_path)

    assert os.path.isfile(env_path)


def test_get_environment():
    env = _get_environment(env_name="base")

    assert env["name"] == "base"


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"foo": "bar"},
        {"foo": "bar", "dependencies": ["foo==1.0"]},
        {"foo": "bar", "dependencies": ["foo==1.0", {"pip": ["foo-pip==1.0"]}]},
    ],
)
def test_add_required_deps(env):
    env_with_deps = _add_required_deps(env=env)

    pip_deps = _get_pip_deps(env_with_deps["dependencies"])
    for dep in MLServerEnvDeps:
        assert dep in pip_deps["pip"]
