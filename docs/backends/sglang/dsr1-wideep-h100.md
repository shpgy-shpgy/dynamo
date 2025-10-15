<!--
SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Running DeepSeek-R1 Disaggregated with WideEP on H100s

Dynamo supports SGLang's implementation of wide expert parallelism and large scale P/D for DeepSeek-R1! You can read their blog post [here](https://lmsys.org/blog/2025-05-05-large-scale-ep/) for more details. We provide a Dockerfile for this in `container/Dockerfile.sglang-wideep` and a sample configuration that demonstrates WideEP and P/D  disaggregation. To run the exact configuration shown in the blog post, you can view the commands created by the SGLang team [here](https://github.com/sgl-project/sglang/issues/6017). In this example, we will run 1 prefill worker on 4 H100 nodes and 1 decode worker on 4 H100 nodes (64 total GPUs).

## Instructions

1. Build the Dynamo container using the latest published dynamo version and stable sglang version. If you want to build from a local dynamo repo, you can add `--build-arg BRANCH_TYPE=local` to the build command. If you want to build from a remote dynamo repo, you can add `--build-arg BRANCH_TYPE=remote` to the build command. If you want to use a specific tag for the default sglang version, you can add `--build-arg SGLANG_IMAGE_TAG=<tag>` to the build command.

> [!Note]
> Please ensure that you are building this on an AMD64 (x86_64) machine. The correct SGLang image will be selected automatically via the multi-arch manifest.

```bash
cd $DYNAMO_ROOT
docker build \
  -f container/Dockerfile.sglang-wideep \
  -t dynamo-wideep \
  --no-cache \
  .
```

2. You can run this container on each 8xH100 node using the following command.

> [!IMPORTANT]
> We recommend downloading DeepSeek-R1 and then mounting it to the container. You can find the model [here](https://huggingface.co/deepseek-ai/DeepSeek-R1)

```bash
docker run \
    --gpus all \
    -it \
    --rm \
    --network host \
    --volume /PATH_TO_DSR1_MODEL/:/model/ \
    --shm-size=10G \
    --ulimit memlock=-1 \
    --ulimit stack=67108864 \
    --ulimit nofile=65536:65536 \
    --cap-add CAP_SYS_PTRACE \
    --ipc host \
    dynamo-wideep:latest
```

In each container, you should be in the `/sgl-workspace/dynamo/components/backends/sglang` directory.

3. Run the ingress and prefill worker

```bash
# run ingress
python3 -m dynamo.frontend --http-port=8000 &
# run prefill worker
python3 -m dynamo.sglang \
  --model-path /model/ \
  --served-model-name deepseek-ai/DeepSeek-R1 \
  --skip-tokenizer-init \
  --disaggregation-mode prefill \
  --disaggregation-transfer-backend nixl \
  --disaggregation-bootstrap-port 30001 \
  --dist-init-addr ${HEAD_PREFILL_NODE_IP}:29500 \
  --nnodes 4 \
  --node-rank 0 \
  --tp-size 32 \
  --dp-size 32 \
  --enable-dp-attention \
  --decode-log-interval 1000 \
  --moe-a2a-backend deepep \
  --load-balance-method round_robin \
  --page-size 1 \
  --trust-remote-code \
  --moe-dense-tp-size 1 \
  --enable-dp-lm-head \
  --disable-radix-cache \
  --watchdog-timeout 1000000 \
  --enable-two-batch-overlap \
  --deepep-mode normal \
  --mem-fraction-static 0.85 \
  --deepep-config /configs/deepep.json \
  --ep-num-redundant-experts 32 \
  --ep-dispatch-algorithm dynamic \
  --eplb-algorithm deepseek
```

On the other prefill node (since this example has 4 total prefill nodes), run the same command but change `--node-rank` to 1,2, and 3

4. Run the decode worker on the head decode node

```bash
python3 -m dynamo.sglang \
  --model-path /model/ \
  --served-model-name deepseek-ai/DeepSeek-R1 \
  --skip-tokenizer-init \
  --disaggregation-mode decode \
  --disaggregation-transfer-backend nixl \
  --disaggregation-bootstrap-port 30001 \
  --dist-init-addr ${HEAD_DECODE_NODE_IP}:29500 \
  --nnodes 4 \
  --node-rank 0 \
  --tp-size 32 \
  --dp-size 32 \
  --enable-dp-attention \
  --decode-log-interval 1000 \
  --moe-a2a-backend deepep \
  --prefill-round-robin-balance \
  --page-size 1 \
  --trust-remote-code \
  --moe-dense-tp-size 1 \
  --enable-dp-lm-head \
  --disable-radix-cache \
  --watchdog-timeout 1000000 \
  --enable-two-batch-overlap \
  --deepep-mode low_latency \
  --mem-fraction-static 0.835 \
  --ep-num-redundant-experts 32 \
  --cuda-graph-bs 128
```

On the other decode nodes (this example has 4 total decode nodes), run the same command but change `--node-rank` to 1, 2, and 3
