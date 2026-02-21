import json
import os
from typing import Annotated
from typing_extensions import TypedDict

from dotenv import load_dotenv

from langchain_ollama import ChatOllama
from langchain_core.tools import tool
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage

from langgraph.graph import StateGraph, MessagesState, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver

from tools.tavily_search import TavilyFlightSearch, format_flights_for_display
from prompts import FLIGHT_ASSISTANT_PROMPT

# Load environment variables
load_dotenv()

# ==========================================
# 1. Define Tools (The "Hands")
# ==========================================

@tool
def search_flights(origin: str, destination: str, date: str):
    """
    Search for available flights between an origin and destination for a specific date.
    Returns top 5 flight options with airline, times, and prices.
    
    Args:
        origin: Departure city or airport code
        destination: Arrival city or airport code  
        date: Travel date in YYYY-MM-DD format
    """
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return "Error: TAVILY_API_KEY not found. Please set the API key in .env file."
    
    try:
        searcher = TavilyFlightSearch(api_key)
        flights = searcher.search(origin, destination, date)
        
        if not flights:
            return f"No flights found for {origin} to {destination} on {date}."
        
        return format_flights_for_display(flights)
    except Exception as e:
        return f"Error searching flights: {str(e)}"

@tool
def book_flight(flight_id: str, user_name: str):
    """
    Book a specific flight using its ID.
    
    IMPORTANT: This action requires explicit human approval before execution.
    """
    # In a real system, this would call an API
    return f"Successfully booked flight {flight_id} for {user_name}."

# ==========================================
# 2. Setup Model (The "Brain")
# ==========================================

# Initialize local Ollama model
# Explicitly set base_url to localhost since OLLAMA_HOST=0.0.0.0 is invalid for client connections
llm = ChatOllama(model="ministral-3:latest", temperature=0, base_url="http://localhost:11434")

# Bind tools to the model
tools = [search_flights, book_flight]
llm_with_tools = llm.bind_tools(tools)

# ==========================================
# 3. Define Agent Logic (The Graph)
# ==========================================

# Define the state
class AgentState(TypedDict):
    messages: Annotated[list, add_messages]

def agent_node(state: AgentState):
    """The main agent logic that decides what to do next."""
    system_prompt = SystemMessage(content=FLIGHT_ASSISTANT_PROMPT)
    
    # LOG: State coming into agent node
    print("\n" + "="*60)
    print("📥 [AGENT NODE] State received:")
    print(f"   Total messages in state: {len(state['messages'])}")
    for i, msg in enumerate(state["messages"]):
        msg_type = type(msg).__name__
        content_preview = str(msg.content)[:80] + "..." if len(str(msg.content)) > 80 else str(msg.content)
        print(f"   [{i}] {msg_type}: {content_preview}")
        if hasattr(msg, 'tool_calls') and msg.tool_calls:
            print(f"       └── Tool calls: {msg.tool_calls}")
    
    response = llm_with_tools.invoke([system_prompt] + state["messages"])
    
    # LOG: What the model is returning
    print("\n📤 [AGENT NODE] Model response:")
    print(f"   Content: {response.content[:100] + '...' if response.content and len(response.content) > 100 else response.content}")
    print(f"   Tool calls: {response.tool_calls if hasattr(response, 'tool_calls') else 'None'}")
    print("="*60 + "\n")
    
    return {"messages": [response]}

def should_continue(state: AgentState):
    """Determine if we should continue or end."""
    messages = state["messages"]
    last_message = messages[-1]
    
    # If there is no tool call, we finish
    if not last_message.tool_calls:
        return END
    else:
        return "tools"

# ==========================================
# 4. Human-in-the-Loop Logic (The POC Core)
# ==========================================

# Create a custom tool node that handles logic and interruption
class HumanApprovalToolNode:
    def __init__(self, tools, interactive: bool = True):
        self.tools_by_name = {tool.name: tool for tool in tools}
        self.interactive = interactive

    def __call__(self, state: AgentState):
        messages = state["messages"]
        last_message = messages[-1]
        
        # LOG: State coming into tool node
        print("\n" + "-"*60)
        print("🔧 [TOOL NODE] State received:")
        print(f"   Total messages in state: {len(messages)}")
        print(f"   Tool calls to execute: {last_message.tool_calls}")
        
        new_messages = []
        
        for tool_call in last_message.tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]
            
            # === THE APPROVAL GATE ===
            if tool_name == "book_flight":
                if self.interactive:
                    print("\n⚠️  ACTION REQUIRED: The agent wants to book a flight.")
                    print(f"   Flight ID: {tool_args.get('flight_id')}")
                    print(f"   Passenger: {tool_args.get('user_name')}")
                    
                    user_input = input("   Do you approve this booking? (yes/no): ").strip().lower()
                    
                    if user_input == "yes":
                        # Execute the tool
                        result = self.tools_by_name[tool_name].invoke(tool_args)
                        new_messages.append(
                            ToolMessage(content=result, tool_call_id=tool_call["id"])
                        )
                    else:
                        # Reject the tool call
                        print("   ❌ Booking rejected by user.")
                        new_messages.append(
                            ToolMessage(content="User rejected the booking.", tool_call_id=tool_call["id"])
                        )
                else:
                    # Non-interactive mode: don't process booking, let API handle it
                    # Don't add any ToolMessage so the tool call remains pending
                    pass
            else:
                # For non-sensitive tools (search), just run them
                result = self.tools_by_name[tool_name].invoke(tool_args)
                print(f"\n🔍 [TOOL EXECUTED] {tool_name}({tool_args})")
                print(f"   Result: {result[:100] + '...' if len(str(result)) > 100 else result}")
                new_messages.append(
                    ToolMessage(content=str(result), tool_call_id=tool_call["id"])
                )
        
        # LOG: What messages are being added to state
        print(f"\n📤 [TOOL NODE] Returning {len(new_messages)} new message(s)")
        print("-"*60 + "\n")
                
        return {"messages": new_messages}

# Interactive tool node for CLI
tool_node = HumanApprovalToolNode(tools, interactive=True)

# Non-interactive tool node for API
tool_node_api = HumanApprovalToolNode(tools, interactive=False)

# Recompile graph with custom tool node (CLI version)
workflow = StateGraph(AgentState)
workflow.add_node("agent", agent_node)
workflow.add_node("tools", tool_node)
workflow.set_entry_point("agent")
workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
workflow.add_edge("tools", "agent")

# Add memory for conversation history
checkpointer = MemorySaver()
app = workflow.compile(checkpointer=checkpointer)

# API version with non-interactive tool node
workflow_api = StateGraph(AgentState)
workflow_api.add_node("agent", agent_node)
workflow_api.add_node("tools", tool_node_api)
workflow_api.set_entry_point("agent")
workflow_api.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
workflow_api.add_edge("tools", "agent")

checkpointer_api = MemorySaver()
app_api = workflow_api.compile(checkpointer=checkpointer_api)

# ==========================================
# 5. Run the Chat Loop (CLI)
# ==========================================

def run_chat():
    print("🤖 Flight Booking POC Ready (Model: ministral-3:latest)")
    print("Type 'quit' to exit.")
    print("-" * 30)
    
    thread_id = "session-1"
    config = {"configurable": {"thread_id": thread_id}}
    
    while True:
        user_input = input("\n👤 You: ")
        
        if user_input.lower() in ["quit", "exit", "q"]:
            print("👋 Goodbye!")
            break
            
        # Send message to agent
        inputs = {"messages": [HumanMessage(content=user_input)]}
        
        # LOG: New user input being added
        print(f"\n💬 [USER INPUT] Adding to state: '{user_input}'")
        
        # Stream the response
        step_count = 0
        for event in app.stream(inputs, config=config, stream_mode="values"):
            step_count += 1
            last_message = event["messages"][-1]
            
            # LOG: State after each graph step
            print(f"\n📊 [STATE UPDATE] After step {step_count}:")
            print(f"   Total messages in state: {len(event['messages'])}")
            
            # Only print the final text response, not the tool logic
            if hasattr(last_message, 'content') and last_message.content and not isinstance(last_message, ToolMessage):
                # Skip printing the system prompt echo if it appears
                if "SystemMessage" not in str(type(last_message)):
                    print(f"🤖 Agent: {last_message.content}")

if __name__ == "__main__":
    run_chat()