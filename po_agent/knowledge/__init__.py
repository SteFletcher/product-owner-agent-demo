"""Enterprise knowledge behind MCP: a server over YAML catalogues, and a client protocol the
graph depends on. Replace the server, keep the tool names, and the graph does not change."""
from .client import Knowledge, MCPKnowledge, StaticKnowledge, open_knowledge

__all__ = ["Knowledge", "MCPKnowledge", "StaticKnowledge", "open_knowledge"]
