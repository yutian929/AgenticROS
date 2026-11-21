FROM ros:humble-ros-base-jammy

# 必改：与复制后的型号目录名一致。
ARG ROBOT_TYPE=my_robot

# 按需修改：在这里安装厂商 SDK / ROS 驱动依赖，构建自定义 ROS 包。
COPY docker/robot_instances/${ROBOT_TYPE}/entrypoint.sh \
     docker/robot_instances/${ROBOT_TYPE}/driver.py \
     docker/robot_instances/${ROBOT_TYPE}/healthcheck.py /opt/robot/
COPY docker/robot_instances/common/robot_identity.py /opt/robot/

# 与 Agent 保持相同 UID，避免同机 DDS 共享内存权限问题。
RUN useradd -u 1001 -m robot
ENV HOME=/tmp ROS_LOG_DIR=/tmp/ros_logs \
    RMW_IMPLEMENTATION=rmw_fastrtps_cpp ROS_LOCALHOST_ONLY=0
USER robot
WORKDIR /tmp
HEALTHCHECK --interval=3s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python3", "/opt/robot/healthcheck.py"]
ENTRYPOINT ["bash", "/opt/robot/entrypoint.sh"]
