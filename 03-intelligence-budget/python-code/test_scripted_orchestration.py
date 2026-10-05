"""
Test Scripted Orchestration

This test verifies that the execute_workflow tool allows agents to write
and execute Python scripts that orchestrate multiple tool calls.
"""

import asyncio
import json
import os
import sys
from typing import List, Dict, Any

from dotenv import load_dotenv
import anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Load environment variables
load_dotenv()

# Verify API key
if not os.getenv("ANTHROPIC_API_KEY"):
    print("Error: ANTHROPIC_API_KEY not found in .env file", file=sys.stderr)
    sys.exit(1)

anthropic_client = anthropic.Anthropic()
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")


def get_result_text(result: Any) -> str:
    """Extract text from MCP tool result."""
    if not result.content:
        return "(no output)"
    return "\n".join(
        c.text if hasattr(c, 'text') else json.dumps(c)
        for c in result.content
    )


def parse_result(result: Any) -> Dict[str, Any]:
    """Parse MCP tool result as JSON."""
    try:
        return json.loads(get_result_text(result))
    except json.JSONDecodeError:
        return {"message": get_result_text(result)}


def mcp_tools_to_anthropic(mcp_tools: List[Any]) -> List[Dict[str, Any]]:
    """Convert MCP tools to Anthropic tool format."""
    return [
        {
            "name": t.name,
            "description": t.description or f"Tool: {t.name}",
            "input_schema": t.inputSchema if t.inputSchema else {"type": "object", "properties": {}},
        }
        for t in mcp_tools
    ]


async def test_scripted_orchestration():
    """Test that agent can write and execute workflow scripts."""

    print("\n" + "="*80)
    print("Testing Scripted Orchestration")
    print("="*80)

    # Connect to server
    server_params = StdioServerParameters(
        command="python",
        args=["scripted_orchestration_server.py"],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Get available tools
            tools_list = await session.list_tools()
            tools = mcp_tools_to_anthropic(tools_list.tools)

            print(f"\n✓ Connected to server")
            print(f"✓ Available tools: {[t['name'] for t in tools]}")

            # Test 1: Simple script - get expense statistics
            print("\n" + "-"*80)
            print("Test 1: Agent writes script to analyze expenses")
            print("-"*80)

            system_prompt = """You are an expense analysis assistant. You have access to tools that let you:
1. Execute Python workflow scripts that orchestrate multiple operations
2. Get example scripts to learn from

When asked to analyze expenses, write a Python script that:
- Uses await tools.get_expense_stats() to get statistics
- Processes the data to answer the user's question
- Returns a useful summary

The script will be executed via the execute_workflow tool."""
            messages = [
                {
                    "role": "user",
                    "content": "Can you analyze our expenses and tell me how much we're spending by category?"
                }
            ]

            print("\nUser: Can you analyze our expenses and tell me how much we're spending by category?")

            # Agent loop
            for iteration in range(5):
                print(f"\n[Iteration {iteration + 1}]")

                response = anthropic_client.messages.create(
                    model=MODEL,
                    max_tokens=16000,
                    system=system_prompt,
                    messages=messages,
                    tools=tools,
                )
                messages.append({"role": "assistant", "content": response.content})

                # Check if done
                tool_uses = [block for block in response.content if block.type == "tool_use"]
                if not tool_uses:
                    text = "\n".join(block.text for block in response.content if block.type == "text")
                    print(f"\nAssistant: {text}")
                    break

                # Execute tool calls
                tool_results = []
                for tool_use in tool_uses:
                    func_name = tool_use.name
                    args = tool_use.input

                    print(f"\nCalling: {func_name}")
                    if func_name == "execute_workflow":
                        code = args.get("code", "")
                        print(f"Script (preview):\n{code[:200]}...")

                    # Call MCP tool
                    result = await session.call_tool(func_name, args)
                    result_text = get_result_text(result)

                    # Collect tool result
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": result_text,
                    })

                    # Parse and display result
                    parsed = parse_result(result)
                    if parsed.get("status") == "success":
                        print(f"✓ Script executed successfully")
                        if "result" in parsed:
                            print(f"Result: {json.dumps(parsed['result'], indent=2)[:300]}...")
                    else:
                        print(f"✗ Error: {parsed.get('message', 'Unknown error')}")

                # All tool results go back together in a single user message
                messages.append({"role": "user", "content": tool_results})

            # Test 2: More complex script - batch operations
            print("\n" + "-"*80)
            print("Test 2: Agent writes script to submit multiple expenses")
            print("-"*80)

            system_prompt = """You are an expense submission assistant. When submitting multiple expenses,
you MUST use the execute_workflow tool with a Python script that efficiently batches the operations.

Write a script that:
1. Creates a list to collect results
2. Calls await tools.create_expense(amount, category, description) for each expense
3. Returns a summary dict with {"submitted": count, "expenses": [list of expense ids]}
   (create_expense returns a dict whose "id" key holds the expense id)

DO NOT use the submit_expense tool multiple times. Use execute_workflow with a single script instead.

Example pattern:
```python
results = []
results.append(await tools.create_expense(12.0, "meals", "Coffee meeting"))
results.append(await tools.create_expense(8.0, "supplies", "Office supplies"))
return {"submitted": len(results), "expenses": [r["id"] for r in results]}
```"""
            messages = [
                {
                    "role": "user",
                    "content": """Submit these three expenses for me:
1. $12 for coffee meeting (meals)
2. $8 for office supplies (supplies)
3. $15 for taxi to client (travel)"""
                }
            ]

            print("\nUser: Submit these three expenses for me...")

            # Agent loop
            for iteration in range(5):
                print(f"\n[Iteration {iteration + 1}]")

                response = anthropic_client.messages.create(
                    model=MODEL,
                    max_tokens=16000,
                    system=system_prompt,
                    messages=messages,
                    tools=tools,
                )
                messages.append({"role": "assistant", "content": response.content})

                # Check if done
                tool_uses = [block for block in response.content if block.type == "tool_use"]
                if not tool_uses:
                    text = "\n".join(block.text for block in response.content if block.type == "text")
                    print(f"\nAssistant: {text}")
                    break

                # Execute tool calls
                tool_results = []
                for tool_use in tool_uses:
                    func_name = tool_use.name
                    args = tool_use.input

                    print(f"\nCalling: {func_name}")
                    if func_name == "execute_workflow":
                        code = args.get("code", "")
                        lines = code.split('\n')
                        print(f"Script ({len(lines)} lines)")
                        # Check if it creates multiple expenses
                        if code.count('create_expense') > 1:
                            print(f"✓ Script will create multiple expenses in one execution")

                    # Call MCP tool
                    result = await session.call_tool(func_name, args)
                    result_text = get_result_text(result)

                    # Collect tool result
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": result_text,
                    })

                    # Parse and display result
                    parsed = parse_result(result)
                    if parsed.get("status") == "success":
                        print(f"✓ Script executed successfully")
                        result_data = parsed.get("result", {})
                        if isinstance(result_data, dict) and "submitted" in result_data:
                            print(f"✓ Submitted {result_data['submitted']} expenses")
                    else:
                        print(f"✗ Error: {parsed.get('message', 'Unknown error')}")

                # All tool results go back together in a single user message
                messages.append({"role": "user", "content": tool_results})

    print("\n" + "="*80)
    print("✓ Scripted Orchestration tests completed!")
    print("="*80)


if __name__ == "__main__":
    asyncio.run(test_scripted_orchestration())
