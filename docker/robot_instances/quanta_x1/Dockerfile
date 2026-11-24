FROM ros:jazzy-ros-base-noble@sha256:5147d3ae250ce754afd399f6bfec15b33dd4e473ca357e30432a3182031bfad6 AS ros-base
SHELL ["/bin/bash", "-c"]
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-colcon-common-extensions ros-jazzy-rosidl-default-generators \
    ros-jazzy-sensor-msgs ros-jazzy-tf2-ros python3-venv build-essential \
    libgl1 libegl1 libglib2.0-0 libxrender1 libxt6 libxi6 libxrandr2 libxkbcommon0 \
    libvulkan1 libgomp1 libsm6 libxext6 libnss3 libasound2t64 \
    && rm -rf /var/lib/apt/lists/*

# Download only at build time. The archive stays in a BuildKit cache, not the image.
FROM ros-base AS assets
COPY docker/robot_instances/quanta_x1/prepare_assets.py /opt/prepare_assets.py
RUN --mount=type=cache,target=/var/cache/quanta \
    python3 /opt/prepare_assets.py --output /assets --cache /var/cache/quanta

FROM ros-base AS transport
COPY docker/robot_instances/robot_docs/quanta_x1/interfaces /opt/driver_ws/src/quanta_driver_interfaces
RUN source /opt/ros/jazzy/setup.bash && cd /opt/driver_ws && colcon build
COPY docker/robot_instances/quanta_x1/scanner_device.py docker/robot_instances/quanta_x1/scanner_scene.py \
    docker/robot_instances/quanta_x1/control.py docker/robot_instances/quanta_x1/driver.py \
    docker/robot_instances/quanta_x1/cameras.py docker/robot_instances/quanta_x1/simulation.py \
    docker/robot_instances/quanta_x1/entrypoint.sh docker/robot_instances/quanta_x1/healthcheck.py \
    docker/robot_instances/quanta_x1/UPSTREAM.json docker/robot_instances/quanta_x1/LICENSE.model /opt/body/
COPY docker/robot_instances/quanta_x1/scanner_asset/scanner.usdc docker/robot_instances/quanta_x1/scanner_asset/albedo.png /opt/body/scanner_asset/
COPY docker/robot_instances/common/robot_identity.py /opt/body/
RUN useradd -u 1001 -m body
ENV HOME=/tmp ROS_LOG_DIR=/tmp/ros_logs ROS_LOCALHOST_ONLY=0 RMW_IMPLEMENTATION=rmw_fastrtps_cpp \
    FASTDDS_BUILTIN_TRANSPORTS=UDPv4
USER body
WORKDIR /tmp

FROM ros-base AS dependencies
RUN python3 -m venv --system-site-packages /opt/sim && \
    /opt/sim/bin/pip install --upgrade pip && \
    /opt/sim/bin/pip install --no-cache-dir 'isaacsim[all,extscache]==6.0.1.0' --extra-index-url https://pypi.nvidia.com
# Isaac's core package supplies its pinned torch dependency. This body uses
# CPU PhysX/NumPy; RTX camera rendering uses Isaac's native GPU runtime.

FROM dependencies AS runtime
COPY --from=assets /assets /assets
COPY docker/robot_instances/robot_docs/quanta_x1/interfaces /opt/driver_ws/src/quanta_driver_interfaces
RUN source /opt/ros/jazzy/setup.bash && cd /opt/driver_ws && colcon build
COPY docker/robot_instances/quanta_x1/scanner_device.py docker/robot_instances/quanta_x1/scanner_scene.py \
    docker/robot_instances/quanta_x1/control.py docker/robot_instances/quanta_x1/driver.py \
    docker/robot_instances/quanta_x1/cameras.py docker/robot_instances/quanta_x1/simulation.py \
    docker/robot_instances/quanta_x1/entrypoint.sh docker/robot_instances/quanta_x1/healthcheck.py \
    docker/robot_instances/quanta_x1/UPSTREAM.json docker/robot_instances/quanta_x1/LICENSE.model /opt/body/
COPY docker/robot_instances/quanta_x1/scanner_asset/scanner.usdc docker/robot_instances/quanta_x1/scanner_asset/albedo.png /opt/body/scanner_asset/
COPY docker/robot_instances/common/robot_identity.py /opt/body/
RUN useradd -u 1001 -m body
ENV HOME=/tmp ROS_LOG_DIR=/tmp/ros_logs ROS_LOCALHOST_ONLY=0 \
    RMW_IMPLEMENTATION=rmw_fastrtps_cpp PYTHONUNBUFFERED=1 \
    FASTDDS_BUILTIN_TRANSPORTS=UDPv4 \
    OMNI_KIT_ACCEPT_EULA=YES NVIDIA_DRIVER_CAPABILITIES=graphics,compute,utility
USER body
WORKDIR /tmp
ENTRYPOINT ["bash", "/opt/body/entrypoint.sh"]
HEALTHCHECK --interval=5s --timeout=3s --start-period=300s --retries=6 CMD ["python3", "/opt/body/healthcheck.py"]
