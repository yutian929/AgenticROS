# Source from bash inside `pixi run` after `pixi run build`.
# colcon's generated prefix scripts invoke an unquoted Python executable; this
# small single-package overlay also works when the checkout path contains spaces.
_rosbody_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
_rosbody_prefix="$_rosbody_root/install/rosbody_interfaces"
_rosbody_python_version="$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [ ! -d "$_rosbody_prefix/lib/python$_rosbody_python_version/site-packages" ]; then
  echo "ROSBody interfaces missing: run pixi run build first" >&2
  return 1
fi
export ROS_DISTRO=jazzy ROS_VERSION=2 ROS_PYTHON_VERSION=3
export AMENT_PREFIX_PATH="$_rosbody_prefix${AMENT_PREFIX_PATH:+:$AMENT_PREFIX_PATH}"
export CMAKE_PREFIX_PATH="$_rosbody_prefix${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
export PYTHONPATH="$_rosbody_prefix/lib/python$_rosbody_python_version/site-packages${PYTHONPATH:+:$PYTHONPATH}"
export DYLD_LIBRARY_PATH="$_rosbody_prefix/lib${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
export LD_LIBRARY_PATH="$_rosbody_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
unset _rosbody_root _rosbody_prefix _rosbody_python_version
