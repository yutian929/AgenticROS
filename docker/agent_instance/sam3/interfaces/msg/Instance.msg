uint32 instance_id
float32 score
# Original image pixels: x_min, y_min, x_max, y_max; x right, y down.
float32[4] box_xyxy
# Original image resolution, mono8: background 0, foreground 255.
sensor_msgs/Image mask
