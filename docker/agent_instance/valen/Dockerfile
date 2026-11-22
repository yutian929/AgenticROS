FROM ros:humble-ros-base-jammy AS ros-dependencies
SHELL ["/bin/bash", "-c"]
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-colcon-common-extensions build-essential cmake python3-pil python3-numpy \
    ros-humble-rosidl-default-generators ros-humble-sensor-msgs ca-certificates \
    && rm -rf /var/lib/apt/lists/*

FROM ghcr.io/astral-sh/uv:0.9.17 AS uv
FROM ros-dependencies AS inference
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_HTTP_TIMEOUT=120
RUN uv python install 3.12 && uv venv --python 3.12 /opt/inference
RUN --mount=type=cache,id=agenticros-valen-uv,target=/root/.cache/uv \
    uv pip install --python /opt/inference/bin/python \
    'torch @ https://download.pytorch.org/whl/cu126/torch-2.8.0%2Bcu126-cp312-cp312-manylinux_2_28_x86_64.whl' \
    'torchvision @ https://download.pytorch.org/whl/cu126/torchvision-0.23.0%2Bcu126-cp312-cp312-manylinux_2_28_x86_64.whl' \
    --index-url https://pypi.org/simple
RUN --mount=type=cache,id=agenticros-valen-uv,target=/root/.cache/uv \
    uv pip install --python /opt/inference/bin/python \
    transformers==5.14.1 accelerate==1.16.0 pillow==12.0.0

FROM ros-dependencies AS weights
COPY docker/agent_instance/valen/download_model.py docker/agent_instance/valen/model.json /opt/download/
RUN --mount=type=cache,id=agenticros-valen-model,target=/var/cache/valen,sharing=locked \
    python3 /opt/download/download_model.py --cache /var/cache/valen --output /models

FROM ros-dependencies AS transport
COPY docker/agent_instance/valen/interfaces /opt/decision_ws/src/agenticros_decision_interfaces
RUN source /opt/ros/humble/setup.bash && cd /opt/decision_ws && colcon build
COPY docker/agent_instance/valen/core.py docker/agent_instance/valen/healthcheck.py docker/agent_instance/valen/node.py docker/agent_instance/valen/worker.py docker/agent_instance/valen/model.json docker/agent_instance/valen/fastdds.xml /opt/valen/
COPY docker/agent_instance/valen/entrypoint.sh /usr/local/bin/valen-entrypoint
RUN chmod +x /usr/local/bin/valen-entrypoint
ENV RMW_IMPLEMENTATION=rmw_fastrtps_cpp ROS_LOCALHOST_ONLY=0 PYTHONUNBUFFERED=1 \
    FASTRTPS_DEFAULT_PROFILES_FILE=/opt/valen/fastdds.xml
ENTRYPOINT ["/usr/local/bin/valen-entrypoint"]
CMD ["python3", "/opt/valen/node.py"]
HEALTHCHECK --interval=5s --timeout=8s --start-period=600s --retries=3 CMD ["/usr/local/bin/valen-entrypoint", "python3", "/opt/valen/healthcheck.py"]

FROM transport AS valen
COPY --from=weights /models /models
COPY --from=inference /opt/python /opt/python
COPY --from=inference /opt/inference /opt/inference
ENV VALEN_PYTHON=/opt/inference/bin/python VALEN_MODEL=/models/Valen-Decision-1.5-4B HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
RUN /opt/inference/bin/python -c 'from transformers.models.qwen3_5.modeling_qwen3_5 import Qwen3_5Model; import torchvision'
