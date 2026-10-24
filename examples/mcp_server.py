"""Minimal stdio MCP server used by the direct host and LangChain examples.

Run directly: python -m examples.mcp_server
It is a real MCP server over stdio; no network or credentials are needed.
"""
from mcp.server.fastmcp import FastMCP
from typing import Annotated
from pydantic import Field

mcp = FastMCP("context-protocol-demo")


@mcp.tool()
def add(a: Annotated[int, Field(description="第一个整数")],
        b: Annotated[int, Field(description="第二个整数")]) -> int:
    """计算两个整数的和。"""
    return a + b


@mcp.tool()
def echo(text: Annotated[str, Field(description="需要原样返回的文本")]) -> str:
    """原样返回输入文本。"""
    return text


if __name__ == "__main__":
    mcp.run(transport="stdio")
