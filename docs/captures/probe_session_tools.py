"""Print the tool list the Claude Agent SDK hands a session under a2acode's options.

a2acode drops the SDK's init SystemMessage on the floor (events_from_message only
maps Assistant/User/Result), so the only way to see which tools a run actually has
is to open a session directly with the same options.
"""

import asyncio
import json
import os
import sys

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient


async def main() -> int:
    cwd = os.path.expanduser("~/scratch/demo-app")
    # Mirror ClaudeBackend._options: empty setting_sources, no allowed_tools.
    options = ClaudeAgentOptions(cwd=cwd, setting_sources=[])
    async with ClaudeSDKClient(options=options) as client:
        await client.query("hi")
        async for message in client.receive_response():
            data = getattr(message, "data", None)
            if isinstance(data, dict) and "tools" in data:
                tools = data["tools"]
                print(json.dumps({"n": len(tools), "tools": sorted(tools)}, indent=2))
                return 0
            if type(message).__name__ == "ResultMessage":
                break
    print("no init message with a tool list seen", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
