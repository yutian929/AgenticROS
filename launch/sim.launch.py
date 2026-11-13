"""Launch the simulation body server in the active Python/ROS environment."""
import sys
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess
from launch.substitutions import LaunchConfiguration


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("seed", default_value="0"),
        DeclareLaunchArgument("out", default_value="runs/server"),
        ExecuteProcess(cmd=[sys.executable, "-m", "rosbody.server", "--seed",
                            LaunchConfiguration("seed"), "--out", LaunchConfiguration("out")],
                       output="screen"),
    ])
