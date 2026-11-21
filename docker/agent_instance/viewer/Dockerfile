FROM ros:humble-ros-base-jammy
RUN apt-get update && apt-get install -y --no-install-recommends \
    ros-humble-rviz2 ros-humble-compressed-image-transport python3-pyqt5 python3-yaml \
    libgl1-mesa-dri xvfb x11-utils x11vnc novnc websockify openbox \
    && rm -rf /var/lib/apt/lists/*
COPY docker/agent_instance/viewer/*.py docker/agent_instance/viewer/*.sh docker/agent_instance/viewer/*.rviz docker/agent_instance/viewer/openbox.xml /opt/viewer/
RUN useradd -m -u 1001 viewer && mkdir /viewer-auth && \
    chown viewer:viewer /viewer-auth && chmod 700 /viewer-auth && \
    ln -s vnc.html /usr/share/novnc/index.html
ENV RMW_IMPLEMENTATION=rmw_fastrtps_cpp ROS_LOCALHOST_ONLY=0 ROS_LOG_DIR=/tmp/ros_logs \
    HOME=/tmp XDG_RUNTIME_DIR=/tmp/runtime-viewer MESA_SHADER_CACHE_DIR=/tmp/mesa-cache \
    QT_X11_NO_MITSHM=1 LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe \
    AGENTICROS_VIEWER_PORT=8080 AGENTICROS_VNC_PORT=5900 AGENTICROS_VIEWER_BIND=127.0.0.1
USER viewer
ENTRYPOINT ["bash", "/opt/viewer/entrypoint.sh"]
