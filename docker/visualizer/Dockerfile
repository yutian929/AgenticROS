FROM ros:humble-ros-base-jammy
SHELL ["/bin/bash", "-c"]
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-numpy python3-pil ros-humble-sensor-msgs ros-humble-tf2-msgs \
    ros-humble-robot-state-publisher && rm -rf /var/lib/apt/lists/*
COPY docker/visualizer /opt/visualizer
COPY docker/shared/fastdds.xml /etc/rosbody/fastdds.xml
RUN useradd -u 1001 -m viewer
ENV ROS_LOG_DIR=/tmp/ros_logs HOME=/tmp PYTHONDONTWRITEBYTECODE=1 \
    RMW_IMPLEMENTATION=rmw_fastrtps_cpp FASTRTPS_DEFAULT_PROFILES_FILE=/etc/rosbody/fastdds.xml
USER viewer
WORKDIR /tmp
ENTRYPOINT ["/ros_entrypoint.sh", "python3", "/opt/visualizer/visualizer.py"]
