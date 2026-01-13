#!/bin/bash
set -e

maturin build
pip uninstall ai_dynamo_runtime -y
pip install target/wheels/ai_dynamo_runtime*.whl
