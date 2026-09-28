"""A stand-in mobile-mcp server for test_mcp_client.py. The first element-list call hangs forever (the flag file
records that it already hung), so the client has to time out, kill this process, and start a fresh one."""

import json
import os
import sys
import time
from pathlib import Path

from mcp.server.mcpserver import MCPServer

FLAG = Path(sys.argv[1])
server = MCPServer("hanging-mobile-mcp")


@server.tool()
def mobile_list_available_devices() -> str:
    return json.dumps({"devices": [{"id": "fake-1", "platform": "android"}]})


@server.tool()
def mobile_list_elements_on_screen(device: str, format: str = "json") -> str:
    if not FLAG.exists():
        FLAG.write_text(str(os.getpid()))
        time.sleep(3600)
    return "Found these elements on screen: " + json.dumps(
        [{"ref": "@e1", "type": "android.widget.TextView", "text": f"pid {os.getpid()}",
          "coordinates": {"x": 10, "y": 200, "width": 100, "height": 50}}])


@server.tool()
def mobile_click_on_screen_at_coordinates(device: str, x: int = 0, y: int = 0) -> str:
    time.sleep(3600)
    return "never"


if __name__ == "__main__":
    server.run("stdio")
