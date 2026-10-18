# ROS 2 Lyrical (Ubuntu 26.04, official multi-arch image incl. arm64):
# headless turtlesim under xvfb plus rosbridge_server on websocket 9090.
FROM ros:lyrical-ros-base

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ros-lyrical-turtlesim ros-lyrical-rosbridge-server ros-lyrical-rosapi \
      xvfb xauth libgl1 \
 && rm -rf /var/lib/apt/lists/*

COPY start.sh /usr/local/bin/start.sh
RUN chmod 0755 /usr/local/bin/start.sh \
 && useradd --create-home --shell /usr/sbin/nologin rosuser
USER rosuser
ENV QT_QPA_PLATFORM=xcb HOME=/home/rosuser
EXPOSE 9090
ENTRYPOINT ["/usr/local/bin/start.sh"]
