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
COPY docker/robot_instances/cartesian_gripper/interfaces /opt/driver_ws/src/agenticros_driver_interfaces
RUN source /opt/ros/humble/setup.bash && cd /opt/driver_ws && colcon build
COPY docker/robot_instances/cartesian_gripper/world.py docker/robot_instances/cartesian_gripper/driver.py docker/robot_instances/cartesian_gripper/tabletop.xml docker/robot_instances/cartesian_gripper/evaluator.py docker/robot_instances/cartesian_gripper/sensors.py docker/robot_instances/cartesian_gripper/registration.py docker/shared/lan_profile.py /opt/body/
COPY docker/robot_instances/cartesian_gripper/desktop.py docker/robot_instances/cartesian_gripper/healthcheck.py docker/robot_instances/cartesian_gripper/robot.urdf docker/robot_instances/cartesian_gripper/observation.rviz docker/robot_instances/cartesian_gripper/openbox.xml /opt/agenticros/
COPY docker/robot_instances/cartesian_gripper/interfaces /opt/device-public/interfaces
COPY docker/robot_instances/cartesian_gripper/body.md /opt/device-public/body.md
COPY docker/shared/fastdds.xml /etc/agenticros/fastdds.xml
COPY docker/robot_instances/cartesian_gripper/entrypoint.sh /usr/local/bin/body-entrypoint
RUN chmod +x /usr/local/bin/body-entrypoint && useradd -u 1001 -m body && \
    mkdir /observations /viewer-auth && chown body:body /observations /viewer-auth && \
    chmod 700 /viewer-auth && ln -s vnc.html /usr/share/novnc/index.html
ENV MUJOCO_GL=osmesa ROS_LOG_DIR=/tmp/ros_logs HOME=/tmp \
    DISPLAY=:99 XDG_RUNTIME_DIR=/tmp/runtime-body QT_X11_NO_MITSHM=1 \
    LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe \
    RMW_IMPLEMENTATION=rmw_fastrtps_cpp FASTRTPS_DEFAULT_PROFILES_FILE=/etc/agenticros/fastdds.xml
USER body
WORKDIR /tmp
ENTRYPOINT ["/usr/local/bin/body-entrypoint"]
