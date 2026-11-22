cmake_minimum_required(VERSION 3.16)
project(agenticros_perception_interfaces)
find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(std_msgs REQUIRED)
find_package(builtin_interfaces REQUIRED)
rosidl_generate_interfaces(${PROJECT_NAME}
  "action/Segment.action" "msg/Instance.msg" "msg/Segmentation.msg"
  DEPENDENCIES sensor_msgs std_msgs builtin_interfaces)
ament_export_dependencies(rosidl_default_runtime)
ament_package()
