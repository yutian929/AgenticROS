FROM ros:humble-ros-base-jammy AS dependencies
SHELL ["/bin/bash", "-c"]
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-venv python3-colcon-common-extensions build-essential cmake git ripgrep \
    ros-humble-rosidl-default-generators ros-humble-sensor-msgs ros-humble-geometry-msgs \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv --system-site-packages /opt/agent-venv && \
    /opt/agent-venv/bin/pip install --no-cache-dir openai-codex==0.168.0 numpy==1.26.4 pillow==11.3.0 scipy==1.15.3

FROM dependencies AS server
COPY docker/agent_instance/agent/contract.md /opt/agenticros/
COPY docker/agent_instance/agent/skills /opt/agenticros/skills
COPY docker/agent_instance/agent/agents /opt/agenticros/agents
COPY docker/agent_instance/agent/AGENTS.md /opt/agenticros/AGENTS.md
COPY docker/agent_instance/agent/rules.py docker/agent_instance/agent/entrypoint.sh /opt/agenticros/
COPY docker/agent_instance/agent/entrypoint.sh /usr/local/bin/agent-entrypoint
COPY docker/agent_instance/agent/codex.sh /usr/local/bin/codex
COPY docker/agent_instance/agent/connection.py /usr/local/bin/agenticros
COPY docker/agent_instance/agent/environment.sh /etc/profile.d/agenticros.sh
RUN chmod +x /usr/local/bin/agent-entrypoint /usr/local/bin/codex /usr/local/bin/agenticros && \
    useradd -m -u 1001 -s /bin/bash agent && \
    mkdir -p /workspace /home/agent/.codex && \
    chown -R agent:agent /workspace /home/agent
ENV PATH="/opt/agent-venv/bin:${PATH}" CODEX_HOME=/home/agent/.codex \
    RMW_IMPLEMENTATION=rmw_fastrtps_cpp ROS_LOCALHOST_ONLY=0
WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/agent-entrypoint"]


COPY docker/agent_instance/agent/runtime.py docker/agent_instance/agent/ros_client.py /opt/instance/
COPY docker/agent_instance/agent/ros-command.sh /usr/local/bin/agenticros-ros
RUN chmod +x /usr/local/bin/agenticros-ros
USER agent
CMD ["python3", "/opt/instance/runtime.py", "serve"]
