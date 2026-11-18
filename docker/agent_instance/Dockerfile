FROM ros:humble-ros-base-jammy AS agent
SHELL ["/bin/bash", "-c"]
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-venv python3-colcon-common-extensions build-essential cmake git ripgrep \
    ros-humble-rosidl-default-generators ros-humble-sensor-msgs ros-humble-geometry-msgs \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv --system-site-packages /opt/agent-venv && \
    /opt/agent-venv/bin/pip install --no-cache-dir openai-codex==0.168.0 numpy==1.26.4 pillow==11.3.0 scipy==1.15.3
COPY docker/agent_instance/agent/contract.md /opt/agenticros/
COPY docker/agent_instance/agent/skills /opt/agenticros/skills
COPY docker/agent_instance/agent/agents /opt/agenticros/agents
COPY docker/agent_instance/agent/AGENTS.md /opt/agenticros/AGENTS.md
COPY docker/shared/lan_profile.py /opt/agenticros/lan_profile.py
COPY docker/shared/fastdds.xml /etc/agenticros/fastdds.xml
COPY docker/agent_instance/agent/entrypoint.sh /usr/local/bin/agent-entrypoint
COPY docker/agent_instance/agent/codex.sh /usr/local/bin/codex
COPY docker/agent_instance/agent/connection.py /usr/local/bin/agenticros
COPY docker/agent_instance/agent/environment.sh /etc/profile.d/agenticros.sh
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

# Generic LAN instance: communication, interactive brain, and visualization.
FROM agent AS instance
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    ros-humble-rviz2 ros-humble-compressed-image-transport python3-pyqt5 python3-yaml \
    libgl1-mesa-dri xvfb x11-utils x11vnc novnc websockify openbox \
    && rm -rf /var/lib/apt/lists/*
COPY docker/agent_instance/registry/registry.py docker/shared/lan_profile.py /opt/agenticros-server/
COPY docker/agent_instance/viewer/*.py docker/agent_instance/viewer/*.sh docker/agent_instance/viewer/openbox.xml /opt/viewer/
COPY docker/shared/lan_profile.py /opt/viewer/lan_profile.py
COPY docker/agent_instance/runtime.py /opt/instance/
RUN mkdir /viewer-auth && chown agent:agent /viewer-auth && chmod 700 /viewer-auth && \
    ln -s vnc.html /usr/share/novnc/index.html
ENV XDG_RUNTIME_DIR=/tmp/runtime-viewer MESA_SHADER_CACHE_DIR=/tmp/mesa-cache \
    QT_X11_NO_MITSHM=1 LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe
USER agent
CMD ["python3", "/opt/instance/runtime.py", "serve"]
