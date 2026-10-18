#!/bin/bash
set -eo pipefail
source /opt/ros/lyrical/setup.bash
xvfb-run -a -s "-screen 0 800x600x24" ros2 run turtlesim turtlesim_node &
exec ros2 launch rosbridge_server rosbridge_websocket_launch.xml address:=0.0.0.0 port:=9090
