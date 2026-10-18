"""Smoke test: drive turtlesim through ros-mcp-server over MCP, then show the auth/limit gap.

Prereq: docker compose -f docker/ros2/compose.yaml up --build -d
Run:    uv run --with fastmcp python scripts/ros2_mcp_smoke.py
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from fastmcp import Client

MCP_URL = os.environ.get("ROS_MCP_URL", "http://127.0.0.1:9000/mcp")
TWIST = "geometry_msgs/msg/Twist"
POSE = "turtlesim_msgs/msg/Pose"  # Lyrical moved turtlesim msgs to turtlesim_msgs (Jazzy: turtlesim/msg/Pose)


def _payload(result: Any) -> Any:
    if getattr(result, "data", None) is not None:
        return result.data
    text = "".join(getattr(c, "text", "") for c in result.content)
    try:
        return json.loads(text)
    except ValueError:
        return text


def _pose(payload: Any) -> dict[str, float]:
    msg = payload.get("msg", payload) if isinstance(payload, dict) else {}
    return {k: round(float(msg[k]), 3) for k in ("x", "y", "theta") if k in msg}


async def main() -> int:
    # No Authorization header is sent anywhere in this script.
    async with Client(MCP_URL) as client:
        tools = sorted(t.name for t in await client.list_tools())
        print(f"tools exposed ({len(tools)}): {', '.join(tools)}")

        # Server inside compose reaches rosbridge by service name on the internal network.
        r = await client.call_tool("connect_to_robot", {"ip": "ros", "port": 9090})
        print("connect_to_robot:", _payload(r))

        async def pose() -> dict[str, float]:
            r = await client.call_tool(
                "subscribe_once", {"topic": "/turtle1/pose", "msg_type": POSE, "timeout": 5.0}
            )
            return _pose(_payload(r))

        async def twist(lin: float, ang: float) -> Any:
            msg = {"linear": {"x": lin, "y": 0.0, "z": 0.0}, "angular": {"x": 0.0, "y": 0.0, "z": ang}}
            r = await client.call_tool(
                "publish_for_durations",
                {"topic": "/turtle1/cmd_vel", "msg_type": TWIST, "messages": [msg], "durations": [1.0]},
            )
            return _payload(r)

        before = await pose()
        print("pose BEFORE:", before)
        print("move (lin=1.0, ang=0.5):", await twist(1.0, 0.5))
        await asyncio.sleep(0.5)
        after = await pose()
        print("pose AFTER: ", after)
        moved = before != after
        print("RESULT moved:", moved)

        print("\n--- gap check: no auth, no limits ---")
        r = await client.call_tool(
            "publish_once",
            {
                "topic": "/turtle1/cmd_vel",
                "msg_type": TWIST,
                "msg": {"linear": {"x": 1e9, "y": 0.0, "z": 0.0}, "angular": {"x": 0.0, "y": 0.0, "z": 0.0}},
            },
        )
        res = _payload(r)
        print("publish_once linear.x=1e9 ->", res)
        accepted = isinstance(res, dict) and "error" not in res
        print(f"GAP unauthenticated client call accepted: {accepted}")
        print(f"GAP absurd velocity (1e9 m/s) accepted, no bound enforced: {accepted}")
        return 0 if moved and accepted else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
