import os
import hashlib
from typing import List, Dict, Optional
from tavily import TavilyClient


class TavilyFlightSearch:
    """Service for searching flights using Tavily API."""
    
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("TAVILY_API_KEY")
        if not self.api_key:
            raise ValueError(
                "TAVILY_API_KEY not found. Set it in your .env file or environment."
            )
        self.client = TavilyClient(api_key=self.api_key)
    
    def search(
        self, 
        origin: str, 
        destination: str, 
        date: str,
        max_results: int = 5
    ) -> List[Dict]:
        """
        Search for flights between origin and destination on a specific date.
        """
        query = f"flights from {origin} to {destination} on {date}"
        
        try:
            response = self.client.search(
                query=query,
                search_depth="advanced",
                max_results=max_results * 2,
                include_raw_content=False
            )
        except Exception as e:
            return {"error": f"Search failed: {str(e)}"}
        
        flights = self._parse_results(response, origin, destination, date)
        return flights[:max_results]
    
    def _parse_results(self, response: Dict, origin: str, destination: str, date: str) -> List[Dict]:
        """Parse Tavily response into structured flight data."""
        flights = []
        
        for i, result in enumerate(response.get("results", []), 1):
            flight = self._extract_flight_info(result, i, origin, destination, date)
            if flight:
                flights.append(flight)
        
        return flights
    
    def _extract_flight_info(self, result: Dict, index: int, origin: str, destination: str, date: str) -> Optional[Dict]:
        """Extract flight information from a search result."""
        content = result.get("content", "")
        url = result.get("url", "")
        
        # Generate a unique flight ID based on route, date, and index
        flight_id = f"FL{index}-{origin[:3].upper()}-{destination[:3].upper()}-{date}"
        
        flight = {
            "flight_id": flight_id,
            "origin": origin,
            "destination": destination,
            "date": date,
            "source": url,
            "details": content[:300]
        }
        
        return flight


def format_flights_for_display(flights: List[Dict]) -> str:
    """Format flight results for display to user."""
    if isinstance(flights, dict) and "error" in flights:
        return f"Error: {flights['error']}"
    
    if not flights:
        return "No flights found for your search criteria."
    
    output = []
    for flight in flights:
        output.append(f"\n**Flight ID: {flight.get('flight_id')}**")
        output.append(f"Route: {flight.get('origin')} → {flight.get('destination')}")
        output.append(f"Date: {flight.get('date')}")
        output.append(f"Details: {flight.get('details', 'No details available')[:200]}")
        output.append(f"Source: {flight.get('source', 'Unknown')}")
        output.append("-" * 40)
    
    return "\n".join(output)