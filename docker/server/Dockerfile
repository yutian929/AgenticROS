FROM ros:humble-ros-base-jammy
COPY docker/server/registry.py docker/shared/lan_profile.py /opt/agenticros-server/
RUN useradd -u 1001 -m registry
USER registry
ENV ROS_LOG_DIR=/tmp/ros_logs RMW_IMPLEMENTATION=rmw_fastrtps_cpp
CMD ["python3", "/opt/agenticros-server/registry.py"]
