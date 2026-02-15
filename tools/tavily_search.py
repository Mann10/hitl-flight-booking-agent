import os
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
        
        Args:
            origin: Departure city or airport code
            destination: Arrival city or airport code
            date: Travel date (YYYY-MM-DD or natural language)
            max_results: Maximum number of results to return (default: 5)
        
        Returns:
            List of flight dictionaries with airline, times, and price
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
        
        flights = self._parse_results(response)
        return flights[:max_results]
    
    def _parse_results(self, response: Dict) -> List[Dict]:
        """Parse Tavily response into structured flight data."""
        flights = []
        
        for result in response.get("results", []):
            flight = self._extract_flight_info(result)
            if flight:
                flights.append(flight)
        
        return flights
    
    def _extract_flight_info(self, result: Dict) -> Optional[Dict]:
        """Extract flight information from a search result."""
        content = result.get("content", "")
        url = result.get("url", "")
        
        flight = {
            "source": url,
            "details": content[:500]
        }
        
        return flight


def format_flights_for_display(flights: List[Dict]) -> str:
    """Format flight results for display to user."""
    if isinstance(flights, dict) and "error" in flights:
        return f"Error: {flights['error']}"
    
    if not flights:
        return "No flights found for your search criteria."
    
    output = []
    for i, flight in enumerate(flights, 1):
        output.append(f"\n{i}. Flight Option:")
        output.append(f"   Source: {flight.get('source', 'Unknown')}")
        output.append(f"   Details: {flight.get('details', 'No details available')}")
    
    return "\n".join(output)