FROM ros:jazzy-ros-base-noble
SHELL ["/bin/bash", "-c"]
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-venv python3-colcon-common-extensions build-essential cmake git ripgrep \
    ros-jazzy-rosidl-default-generators ros-jazzy-sensor-msgs ros-jazzy-geometry-msgs \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv --system-site-packages /opt/agent-venv && \
    /opt/agent-venv/bin/pip install --no-cache-dir openai-codex==0.168.0 numpy==1.26.4 pillow==11.3.0 scipy==1.16.3
COPY docker/interfaces /opt/driver_ws/src/rosbody_driver_interfaces
RUN source /opt/ros/jazzy/setup.bash && cd /opt/driver_ws && colcon build
COPY docker/agenticros/contract.md docker/agenticros/body.md /opt/rosbody/
COPY docker/agenticros/skills /opt/rosbody/skills
COPY docker/agenticros/AGENTS.md /opt/rosbody/AGENTS.md
COPY docker/shared/fastdds.xml /etc/rosbody/fastdds.xml
COPY docker/agenticros/entrypoint.sh /usr/local/bin/agent-entrypoint
COPY docker/agenticros/codex.sh /usr/local/bin/codex
COPY docker/agenticros/environment.sh /etc/profile.d/rosbody.sh
RUN chmod +x /usr/local/bin/agent-entrypoint /usr/local/bin/codex && \
    useradd -m -u 1001 -s /bin/bash agent && \
    mkdir -p /workspace /observations /home/agent/.codex && \
    chown -R agent:agent /workspace /observations /home/agent
ENV PATH="/opt/agent-venv/bin:${PATH}" CODEX_HOME=/home/agent/.codex \
    RMW_IMPLEMENTATION=rmw_fastrtps_cpp FASTRTPS_DEFAULT_PROFILES_FILE=/etc/rosbody/fastdds.xml
USER agent
WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/agent-entrypoint"]
CMD ["codex"]
