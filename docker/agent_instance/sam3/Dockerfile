FROM ros:humble-ros-base-jammy AS ros-dependencies
SHELL ["/bin/bash", "-c"]
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-colcon-common-extensions build-essential cmake python3-pil python3-numpy \
    ros-humble-rosidl-default-generators ros-humble-sensor-msgs \
    ca-certificates git && rm -rf /var/lib/apt/lists/*

# Keep heavyweight inference dependencies independent of ROS wrapper changes.
# Humble uses Python 3.10; SAM3 gets its own Python 3.12 worker environment.
FROM ghcr.io/astral-sh/uv:0.9.17 AS uv
FROM ros-dependencies AS inference
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/python
RUN uv python install 3.12 && uv venv --python 3.12 /opt/inference
RUN --mount=type=cache,id=agenticros-sam3-uv,target=/root/.cache/uv \
    uv pip install --python /opt/inference/bin/python \
    torch==2.7.1 torchvision==0.22.1 --index-url https://download.pytorch.org/whl/cu126
ARG SAM3_REVISION=31cb7e624e4fe95301b1be1d4e809111593d2e45
RUN --mount=type=cache,id=agenticros-sam3-uv,target=/root/.cache/uv \
    git clone https://github.com/facebookresearch/sam3.git /opt/sam3-model && \
    cd /opt/sam3-model && git checkout "$SAM3_REVISION" && \
    uv pip install --python /opt/inference/bin/python . 'setuptools<81' pillow einops scipy psutil pycocotools && \
    printf '%s\n' "$SAM3_REVISION" > /opt/sam3-model-revision

FROM ros-dependencies AS transport
COPY docker/agent_instance/sam3/interfaces /opt/perception_ws/src/agenticros_perception_interfaces
RUN source /opt/ros/humble/setup.bash && cd /opt/perception_ws && colcon build
COPY docker/agent_instance/sam3/*.py docker/agent_instance/sam3/fastdds.xml /opt/sam3/
COPY docker/agent_instance/sam3/entrypoint.sh /usr/local/bin/sam3-entrypoint
RUN chmod +x /usr/local/bin/sam3-entrypoint
ENV RMW_IMPLEMENTATION=rmw_fastrtps_cpp ROS_LOCALHOST_ONLY=0 PYTHONUNBUFFERED=1 \
    FASTRTPS_DEFAULT_PROFILES_FILE=/opt/sam3/fastdds.xml
ENTRYPOINT ["/usr/local/bin/sam3-entrypoint"]
CMD ["python3", "/opt/sam3/node.py"]
HEALTHCHECK --interval=5s --timeout=8s --start-period=600s --retries=3 CMD ["/usr/local/bin/sam3-entrypoint", "python3", "/opt/sam3/healthcheck.py"]

FROM transport AS sam3
COPY --from=inference /opt/python /opt/python
COPY --from=inference /opt/inference /opt/inference
COPY --from=inference /opt/sam3-model-revision /opt/sam3-model-revision
ENV SAM3_PYTHON=/opt/inference/bin/python
RUN /opt/inference/bin/python -c 'from sam3.model_builder import build_sam3_image_model; from sam3.model.sam3_image_processor import Sam3Processor'
