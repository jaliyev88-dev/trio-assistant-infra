import os

from fastmcp import FastMCP

from email_tools import register_email_tools

TENANT = os.environ["TENANT_NAME"]
PORT = int(os.environ["MCP_PORT"])
TOKEN = os.environ["MCP_TOKEN"]
DATA_ROOT = os.environ["DATA_ROOT"]

mcp = FastMCP(f"{TENANT.capitalize()} Tools")


def ping() -> str:
    return f"{TENANT} OK"


mcp.tool(name=f"{TENANT}_ping")(ping)

register_email_tools(mcp, TENANT, DATA_ROOT)


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host="127.0.0.1",
        port=PORT,
        path=f"/mcp/{TOKEN}",
    )
