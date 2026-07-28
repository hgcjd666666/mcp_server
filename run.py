import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_server.main import mcp

port = int(os.environ.get("PORT", "2749"))
mcp.run(transport="http", host="127.0.0.1", port=port, path="/mcp")
