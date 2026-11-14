FROM ros:humble-ros-base-jammy
SHELL ["/bin/bash", "-c"]
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-venv python3-colcon-common-extensions build-essential cmake git ripgrep \
    ros-humble-rosidl-default-generators ros-humble-sensor-msgs ros-humble-geometry-msgs \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv --system-site-packages /opt/agent-venv && \
    /opt/agent-venv/bin/pip install --no-cache-dir openai-codex==0.168.0 numpy==1.26.4 pillow==11.3.0 scipy==1.15.3
COPY docker/agenticros/contract.md /opt/agenticros/
COPY docker/agenticros/skills /opt/agenticros/skills
COPY docker/agenticros/AGENTS.md /opt/agenticros/AGENTS.md
COPY docker/shared/fastdds.xml /etc/agenticros/fastdds.xml
COPY docker/agenticros/entrypoint.sh /usr/local/bin/agent-entrypoint
COPY docker/agenticros/codex.sh /usr/local/bin/codex
COPY docker/agenticros/connection.py /usr/local/bin/agenticros
COPY docker/agenticros/environment.sh /etc/profile.d/agenticros.sh
RUN chmod +x /usr/local/bin/agent-entrypoint /usr/local/bin/codex /usr/local/bin/agenticros && \
    useradd -m -u 1001 -s /bin/bash agent && \
    mkdir -p /workspace /observations /home/agent/.codex && \
    chown -R agent:agent /workspace /observations /home/agent
ENV PATH="/opt/agent-venv/bin:${PATH}" CODEX_HOME=/home/agent/.codex \
    RMW_IMPLEMENTATION=rmw_fastrtps_cpp FASTRTPS_DEFAULT_PROFILES_FILE=/etc/agenticros/fastdds.xml
USER agent
WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/agent-entrypoint"]
CMD ["agenticros", "start"]
