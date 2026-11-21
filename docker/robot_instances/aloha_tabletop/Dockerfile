FROM ros:humble-ros-base-jammy
SHELL ["/bin/bash", "-c"]
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-venv python3-colcon-common-extensions build-essential cmake \
    ros-humble-rosidl-default-generators ros-humble-sensor-msgs ros-humble-tf2-ros \
    ros-humble-rviz2 ros-humble-robot-state-publisher \
    libosmesa6 libgl1-mesa-dri xvfb x11-utils x11vnc novnc websockify openbox \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv --system-site-packages /opt/body-venv && \
    /opt/body-venv/bin/pip install --no-cache-dir mujoco==3.3.7 numpy==1.26.4 pillow==11.3.0
COPY docker/robot_instances/robot_docs/aloha_tabletop/interfaces /opt/driver_ws/src/aloha_driver_interfaces
RUN source /opt/ros/humble/setup.bash && cd /opt/driver_ws && colcon build
COPY docker/robot_instances/aloha_tabletop/ /opt/body/
COPY docker/robot_instances/common/robot_identity.py /opt/body/
RUN /opt/body-venv/bin/python /opt/body/prepare_scene.py
RUN useradd -u 1001 -m body
ENV MUJOCO_GL=osmesa ROS_LOG_DIR=/tmp/ros_logs HOME=/tmp LIBGL_ALWAYS_SOFTWARE=1 \
    RMW_IMPLEMENTATION=rmw_fastrtps_cpp ROS_LOCALHOST_ONLY=0
USER body
WORKDIR /tmp
ENTRYPOINT ["bash", "/opt/body/entrypoint.sh"]

HEALTHCHECK --interval=3s --timeout=4s --start-period=15s --retries=15 CMD ["/opt/body-venv/bin/python", "/opt/body/healthcheck.py"]
