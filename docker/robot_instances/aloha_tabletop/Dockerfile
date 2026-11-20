FROM ros:humble-ros-base-jammy AS fastdds
RUN apt-get -o Dir::Etc::sourceparts=- -o Dir::Etc::sourcelist=/etc/apt/sources.list update && \
    apt-get install -y --no-install-recommends build-essential cmake curl patch libasio-dev libtinyxml2-dev libssl-dev libsqlite3-dev && \
    rm -rf /var/lib/apt/lists/*
COPY docker/shared/fastdds /build/fastdds
RUN bash /build/fastdds/build.sh

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
COPY docker/robot_instances/aloha_tabletop/interfaces /opt/driver_ws/src/aloha_driver_interfaces
RUN source /opt/ros/humble/setup.bash && cd /opt/driver_ws && colcon build
COPY docker/robot_instances/aloha_tabletop/ /opt/body/
COPY docker/shared/lan_profile.py docker/shared/robot_network.py /opt/body/
RUN /opt/body-venv/bin/python /opt/body/prepare_scene.py
COPY docker/robot_instances/aloha_tabletop/interfaces /opt/device-public/interfaces
COPY docker/robot_instances/aloha_tabletop/body.md /opt/device-public/body.md
COPY docker/shared/fastdds.xml /etc/agenticros/fastdds.xml
RUN useradd -u 1001 -m body && mkdir /observations && chown body:body /observations
ENV MUJOCO_GL=osmesa ROS_LOG_DIR=/tmp/ros_logs HOME=/tmp LIBGL_ALWAYS_SOFTWARE=1 \
    RMW_IMPLEMENTATION=rmw_fastrtps_cpp FASTRTPS_DEFAULT_PROFILES_FILE=/etc/agenticros/fastdds.xml
COPY --from=fastdds /opt/fastdds/lib/libfastrtps.so.2.6.12 /opt/ros/humble/lib/
COPY --from=fastdds /opt/fastdds/share/agenticros /usr/share/agenticros/fastdds
COPY --from=fastdds /opt/fastdds/share/fastrtps/LICENSE /usr/share/agenticros/fastdds/LICENSE
RUN ln -sf libfastrtps.so.2.6.12 /opt/ros/humble/lib/libfastrtps.so.2.6 && \
    ln -sf libfastrtps.so.2.6 /opt/ros/humble/lib/libfastrtps.so
USER body
WORKDIR /tmp
ENTRYPOINT ["bash", "/opt/body/entrypoint.sh"]
