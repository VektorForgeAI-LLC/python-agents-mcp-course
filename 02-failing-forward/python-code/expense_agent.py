"""
Expense Agent - Demonstrating Response-as-Instruction

This agent has a MINIMAL system prompt. It doesn't know about "Failing Forward"
or any special error handling patterns. It's just an expense assistant.

The key insight: the agent successfully navigates complex workflows because
the TOOL RESPONSES guide it. The tools return structured responses with
next_action, next_action_params, and hints that teach the agent what to do.

This demonstrates that well-designed tool responses can guide any reasonable
LLM without requiring special instructions about the pattern.

Run with: python expense_agent.py "Submit a $150 dinner with client from last week"
"""

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from dotenv import load_dotenv
from mcp import types
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
import anthropic

# Load environment variables
load_dotenv()

# Verify API key
if not os.environ.get("ANTHROPIC_API_KEY"):
    print("Error: ANTHROPIC_API_KEY not found. Set it in .env file or environment.")
    sys.exit(1)

anthropic_client = anthropic.Anthropic()
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")


# ============================================================================
# Types
# ============================================================================


@dataclass
class ToolResult:
    """Parsed tool result structure."""

    status: str
    message: str
    error: str | None = None
    next_action: str | None = None
    next_action_params: dict[str, Any] | None = None
    hint: str | None = None
    tell_user: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolResult":
        return cls(
            status=data.get("status", ""),
            message=data.get("message", ""),
            error=data.get("error"),
            next_action=data.get("next_action"),
            next_action_params=data.get("next_action_params"),
            hint=data.get("hint"),
            tell_user=data.get("tell_user"),
        )


# ============================================================================
# Helper Functions
# ============================================================================


def mcp_tools_to_anthropic(mcp_tools: list[types.Tool]) -> list[dict[str, Any]]:
    """Convert MCP tools to Anthropic tool format."""
    return [
        {
            "name": tool.name,
            "description": tool.description or f"Tool: {tool.name}",
            "input_schema": tool.inputSchema if tool.inputSchema else {"type": "object", "properties": {}},
        }
        for tool in mcp_tools
    ]


def get_result_text(result: types.CallToolResult) -> str:
    """Extract text from MCP tool result."""
    if not result.content:
        return "(no output)"
    parts = []
    for content in result.content:
        if isinstance(content, types.TextContent):
            parts.append(content.text)
        else:
            parts.append(json.dumps(content.model_dump() if hasattr(content, 'model_dump') else str(content)))
    return "\n".join(parts)


def parse_tool_result(result_text: str) -> ToolResult | None:
    """Parse JSON tool result text into ToolResult."""
    try:
        data = json.loads(result_text)
        return ToolResult.from_dict(data)
    except (json.JSONDecodeError, KeyError):
        return None


def get_output_text(response: anthropic.types.Message) -> str:
    """Extract the text blocks from a Claude response."""
    return "\n".join(block.text for block in response.content if block.type == "text")


def get_tool_uses(response: anthropic.types.Message) -> list[anthropic.types.ToolUseBlock]:
    """Get tool_use blocks from a Claude response."""
    return [block for block in response.content if block.type == "tool_use"]


# ============================================================================
# Main Agent Loop
# ============================================================================


async def run_agent(user_message: str) -> None:
    """Run the expense agent with the given user message."""
    print("\n" + "=" * 60)
    print("FAILING FORWARD EXPENSE AGENT")
    print("=" * 60)
    print(f"\nUser request: {user_message}\n")

    # Connect to the expense server
    print("Connecting to Expense Server...\n")

    server_params = StdioServerParameters(
        command="python",
        args=["expense_server.py"],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools_result = await session.list_tools()
            mcp_tools = tools_result.tools
            tools = mcp_tools_to_anthropic(mcp_tools)
            print(f"Discovered {len(tools)} tools: {', '.join(t.name for t in mcp_tools)}\n")

            # MINIMAL system prompt - the agent knows NOTHING about "Failing Forward"
            # or any special error handling patterns. It's just an expense assistant.
            # The tool responses will guide it through any issues.
            today = datetime.now().strftime("%Y-%m-%d")
            system_instructions = f"""You are an expense submission assistant that helps users submit business expenses.

Today's date is {today}.

Be helpful and guide the user through the expense submission process."""

            # The messages array holds the conversation context
            messages: list[anthropic.types.MessageParam] = [
                {
                    "role": "user",
                    "content": user_message,
                }
            ]

            # Agent loop
            iteration = 0
            max_iterations = 10

            while iteration < max_iterations:
                iteration += 1
                print(f"\n--- Iteration {iteration} ---")

                # Call Claude
                response = anthropic_client.messages.create(
                    model=MODEL,
                    max_tokens=16000,
                    system=system_instructions,
                    messages=messages,
                    tools=tools,
                )

                # Keep the full assistant turn (including tool_use blocks) in history
                messages.append({"role": "assistant", "content": response.content})

                # Check if the model wants to call tools
                tool_uses = get_tool_uses(response)
                if tool_uses:
                    tool_results: list[dict[str, Any]] = []

                    for tool_use in tool_uses:
                        args = tool_use.input
                        print(f"\nCalling: {tool_use.name}")
                        print(f"Arguments: {json.dumps(args, indent=2)}")

                        result = await session.call_tool(
                            name=tool_use.name,
                            arguments=args,
                        )

                        result_text = get_result_text(result)
                        parsed = parse_tool_result(result_text)

                        # Show the result
                        print("\nResult:")
                        if parsed:
                            print(f"  Status: {parsed.status}")
                            if parsed.error:
                                print(f"  Error: {parsed.error}")
                            print(f"  Message: {parsed.message}")
                            if parsed.next_action:
                                print(f"  Next Action: {parsed.next_action}")
                                if parsed.next_action_params:
                                    print(f"  Next Action Params: {json.dumps(parsed.next_action_params, indent=4)}")
                            if parsed.hint:
                                print(f"  Hint: {parsed.hint}")
                        else:
                            print(f"  {result_text[:200]}...")

                        # Collect the tool output for the next iteration
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tool_use.id,
                            "content": result_text,
                        })

                    # All tool results go back together in a single user message
                    messages.append({"role": "user", "content": tool_results})
                else:
                    # Agent finished - show final response
                    output_text = get_output_text(response)
                    print("\n" + "=" * 60)
                    print("FINAL RESPONSE")
                    print("=" * 60)
                    print(f"\n{output_text}\n")
                    break

            if iteration >= max_iterations:
                print("\n(Reached maximum iterations)")


# ============================================================================
# Entry Point
# ============================================================================


def main() -> None:
    """Entry point for the expense agent."""
    user_message = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "Submit a $150 dinner expense with a client from last Tuesday"
    )
    asyncio.run(run_agent(user_message))


if __name__ == "__main__":
    main()
