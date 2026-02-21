from typing import Optional, List
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import json
import re

from langchain_core.messages import HumanMessage, ToolMessage, AIMessage

from main import app_api as langgraph_app, tools

# ==========================================
# FastAPI App Setup
# ==========================================

api = FastAPI(title="Flight Booking Agent API")

# Add CORS middleware for frontend
api.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store pending approvals in memory (in production, use Redis/database)
pending_approvals = {}

# ==========================================
# Request/Response Models
# ==========================================

class FlightInfo(BaseModel):
    flight_id: str
    origin: str
    destination: str
    date: str
    details: str
    source: str

class ChatRequest(BaseModel):
    message: str
    thread_id: str = "default"

class ChatResponse(BaseModel):
    response: str
    requires_approval: bool = False
    approval_id: Optional[str] = None
    tool_name: Optional[str] = None
    tool_args: Optional[dict] = None
    flights: Optional[List[FlightInfo]] = None

class ApprovalRequest(BaseModel):
    approval_id: str
    approved: bool
    thread_id: str = "default"

class ApprovalResponse(BaseModel):
    result: str

# ==========================================
# Helper Functions
# ==========================================

def find_pending_booking(messages: list):
    """Find a pending book_flight tool call that hasn't been processed."""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and hasattr(msg, 'tool_calls') and msg.tool_calls:
            for tool_call in msg.tool_calls:
                if tool_call["name"] == "book_flight":
                    # Check if this tool call already has a response
                    has_response = any(
                        isinstance(m, ToolMessage) and m.tool_call_id == tool_call["id"]
                        for m in messages
                    )
                    if not has_response:
                        return tool_call
    return None

def extract_flights_from_messages(messages: list) -> List[FlightInfo]:
    """Extract flight information from tool messages."""
    flights = []
    for msg in messages:
        if isinstance(msg, ToolMessage):
            content = msg.content
            # Handle case where content might not be a string
            if not isinstance(content, str):
                continue
            # Parse flight data from the formatted string
            if "**Flight ID:" not in content:
                continue
            flight_blocks = content.split("**Flight ID: ")
            for block in flight_blocks[1:]:  # Skip first empty block
                try:
                    lines = block.split("\n")
                    flight_id = lines[0].replace("**", "").strip()
                    
                    origin = ""
                    destination = ""
                    date = ""
                    details = ""
                    source = ""
                    
                    for line in lines[1:]:
                        if line.startswith("Route:"):
                            route = line.replace("Route:", "").strip()
                            parts = route.split("→")
                            if len(parts) == 2:
                                origin = parts[0].strip()
                                destination = parts[1].strip()
                        elif line.startswith("Date:"):
                            date = line.replace("Date:", "").strip()
                        elif line.startswith("Details:"):
                            details = line.replace("Details:", "").strip()
                        elif line.startswith("Source:"):
                            source = line.replace("Source:", "").strip()
                    
                    if flight_id and origin:
                        flights.append(FlightInfo(
                            flight_id=flight_id,
                            origin=origin,
                            destination=destination,
                            date=date,
                            details=details,
                            source=source
                        ))
                except Exception:
                    continue
    return flights

# ==========================================
# API Endpoints
# ==========================================

@api.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Send a message to the flight booking agent."""
    config = {"configurable": {"thread_id": request.thread_id}}
    inputs = {"messages": [HumanMessage(content=request.message)]}
    
    # Run the graph
    result = None
    for event in langgraph_app.stream(inputs, config=config, stream_mode="values"):
        result = event
    
    if result is None:
        return ChatResponse(response="No response from agent.")
    
    messages = result["messages"]
    last_message = messages[-1]
    
    # Extract flight information from tool messages
    flights = extract_flights_from_messages(messages)
    
    # Check for pending booking approval
    pending_booking = find_pending_booking(messages)
    if pending_booking:
        approval_id = f"{request.thread_id}_{pending_booking['id']}"
        pending_approvals[approval_id] = {
            "tool_call": pending_booking,
            "config": config
        }
        return ChatResponse(
            response="Booking requires approval.",
            requires_approval=True,
            approval_id=approval_id,
            tool_name=pending_booking["name"],
            tool_args=pending_booking["args"]
        )
    
    # Return the agent's text response
    response_content = last_message.content if hasattr(last_message, 'content') and last_message.content else str(last_message)
    return ChatResponse(response=response_content, flights=flights if flights else None)

@api.post("/approve", response_model=ApprovalResponse)
async def approve_booking(request: ApprovalRequest):
    """Approve or reject a pending booking."""
    if request.approval_id not in pending_approvals:
        raise HTTPException(status_code=404, detail="Approval request not found")
    
    approval_data = pending_approvals.pop(request.approval_id)
    tool_call = approval_data["tool_call"]
    config = approval_data["config"]
    
    if request.approved:
        # Execute the booking tool
        tools_by_name = {tool.name: tool for tool in tools}
        result = tools_by_name[tool_call["name"]].invoke(tool_call["args"])
        tool_message = ToolMessage(content=result, tool_call_id=tool_call["id"])
    else:
        tool_message = ToolMessage(content="User rejected the booking.", tool_call_id=tool_call["id"])
    
    # Continue the graph with the tool result
    inputs = {"messages": [tool_message]}
    
    final_response = None
    for event in langgraph_app.stream(inputs, config=config, stream_mode="values"):
        final_response = event
    
    if final_response:
        last_message = final_response["messages"][-1]
        content = last_message.content if hasattr(last_message, 'content') and last_message.content else str(last_message)
        return ApprovalResponse(result=content)
    
    return ApprovalResponse(result="Booking processed.")

@api.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}
