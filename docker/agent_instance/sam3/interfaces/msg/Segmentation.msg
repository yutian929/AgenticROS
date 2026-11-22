std_msgs/Header header
string session_id
string goal_id
string image_topic
uint32 width
uint32 height
# The exact selected input, so consumers can inspect masks against the same frame.
sensor_msgs/Image source_image
agenticros_perception_interfaces/Instance[] instances
