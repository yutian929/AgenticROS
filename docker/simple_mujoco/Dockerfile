FROM ros:jazzy-ros-base-noble
SHELL ["/bin/bash", "-c"]
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-venv python3-colcon-common-extensions build-essential cmake \
    ros-jazzy-rosidl-default-generators ros-jazzy-sensor-msgs libosmesa6 \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv --system-site-packages /opt/body-venv && \
    /opt/body-venv/bin/pip install --no-cache-dir mujoco==3.3.7 numpy==1.26.4 pillow==11.3.0
COPY docker/interfaces /opt/driver_ws/src/rosbody_driver_interfaces
RUN source /opt/ros/jazzy/setup.bash && cd /opt/driver_ws && colcon build
COPY docker/simple_mujoco/world.py docker/simple_mujoco/driver.py docker/simple_mujoco/tabletop.xml docker/simple_mujoco/evaluator.py /opt/body/
COPY docker/shared/fastdds.xml /etc/rosbody/fastdds.xml
COPY docker/simple_mujoco/entrypoint.sh /usr/local/bin/body-entrypoint
RUN chmod +x /usr/local/bin/body-entrypoint && useradd -u 1001 -m body && \
    mkdir /observations && chown body:body /observations
ENV MUJOCO_GL=osmesa ROS_LOG_DIR=/tmp/ros_logs HOME=/tmp \
    RMW_IMPLEMENTATION=rmw_fastrtps_cpp FASTRTPS_DEFAULT_PROFILES_FILE=/etc/rosbody/fastdds.xml
USER body
WORKDIR /tmp
ENTRYPOINT ["/usr/local/bin/body-entrypoint"]
