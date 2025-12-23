#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Environment variables with defaults
export MODEL_PATH="/data/models/Qwen3-VL-32B-Instruct-FP8-Dynamic/"
export SERVED_MODEL_NAME="Qwen3-VL-32B"
export DISAGGREGATION_STRATEGY=${DISAGGREGATION_STRATEGY:-"decode_first"}
export PREFILL_ENGINE_ARGS=${PREFILL_ENGINE_ARGS:-"engine_configs/prefill.yaml"}
export DECODE_ENGINE_ARGS=${DECODE_ENGINE_ARGS:-"engine_configs/decode.yaml"}
export ENCODE_ENGINE_ARGS=${ENCODE_ENGINE_ARGS:-"engine_configs/encode.yaml"}
export PREFILL_CUDA_VISIBLE_DEVICES=${PREFILL_CUDA_VISIBLE_DEVICES:-"0,1"}
export DECODE_CUDA_VISIBLE_DEVICES=${DECODE_CUDA_VISIBLE_DEVICES:-"2,3"}
export ENCODE_CUDA_VISIBLE_DEVICES=${ENCODE_CUDA_VISIBLE_DEVICES:-"0"}
export ENCODE_ENDPOINT=${ENCODE_ENDPOINT:-"dyn://dynamo.tensorrt_llm_encode.generate"}
export MODALITY=${MODALITY:-"multimodal"}
export ALLOWED_LOCAL_MEDIA_PATH=${ALLOWED_LOCAL_MEDIA_PATH:-"/tmp"}
export MAX_FILE_SIZE_MB=${MAX_FILE_SIZE_MB:-50}

# Setup cleanup trap
cleanup() {
    echo "Cleaning up background processes..."
    kill $DYNAMO_PID $PREFILL_PID $DECODE_PID $ENCODE_PID 2>/dev/null || true
    wait $DYNAMO_PID $PREFILL_PID $DECODE_PID $ENCODE_PID 2>/dev/null || true
    echo "Cleanup complete."
}
trap cleanup EXIT INT TERM

# run clear_namespace
python3 utils/clear_namespace.py --namespace dynamo

# run frontend
<<<<<<< Updated upstream
python3 -m dynamo.frontend --http-port 8000 &
=======
DYN_LOG=debug DYN_REQUEST_PLANE=tcp python3 -m dynamo.frontend --http-port 8000 &
>>>>>>> Stashed changes
DYNAMO_PID=$!

# run encode worker
DYN_LOG=debug DYN_REQUEST_PLANE=tcp DYN_TCP_RPC_PORT=10001 DYN_SYSTEM_PORT=8101 CUDA_VISIBLE_DEVICES=0,1 python3 -m dynamo.trtllm \
  --model-path "$MODEL_PATH" \
  --served-model-name "$SERVED_MODEL_NAME" \
  --extra-engine-args "/data/luyufan/workspace/dynamo/examples/backends/trtllm/engine_configs/encode.yaml" \
  --modality "multimodal" \
  --allowed-local-media-path "/tmp" \
  --max-file-size-mb "50" \
  --disaggregation-mode encode &
ENCODE_PID=$!

# run prefill worker
DYN_LOG=info DYN_REQUEST_PLANE=tcp DYN_TCP_RPC_PORT=10002 DYN_SYSTEM_PORT=8102 CUDA_VISIBLE_DEVICES=0,1 python3 -m dynamo.trtllm \
  --model-path "$MODEL_PATH" \
  --served-model-name "$SERVED_MODEL_NAME" \
  --extra-engine-args "/data/luyufan/workspace/dynamo/examples/backends/trtllm/engine_configs/prefill.yaml" \
  --modality "multimodal" \
  --disaggregation-mode prefill \
  --encode-endpoint "dyn://dynamo.tensorrt_llm_encode.generate" &
PREFILL_PID=$!

# run decode worker
DYN_LOG=info DYN_REQUEST_PLANE=tcp DYN_TCP_RPC_PORT=10003 DYN_SYSTEM_PORT=8103 CUDA_VISIBLE_DEVICES=2,3 python3 -m dynamo.trtllm \
  --model-path "$MODEL_PATH" \
  --served-model-name "$SERVED_MODEL_NAME" \
  --extra-engine-args "/data/luyufan/workspace/dynamo/examples/backends/trtllm/engine_configs/decode.yaml" \
  --modality "multimodal" \
  --disaggregation-mode decode &
DECODE_PID=$!

wait $DYNAMO_PID
