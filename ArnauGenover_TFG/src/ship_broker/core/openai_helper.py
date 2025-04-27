# src/ship_broker/core/openai_helper.py
from openai import OpenAI
from typing import Dict, Optional, List
import json
from datetime import datetime, date
import logging
from ..config import settings

logger = logging.getLogger(__name__)

class OpenAIHelper:
    def __init__(self):
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = "gpt-3.5-turbo"  # better this model for testing purposes
        # paid version self.model = "gpt-4-turbo-preview"
        self.current_year = datetime.now().year

    # Update extract_info in src/ship_broker/core/openai_helper.py

    def extract_info(self, content: str) -> Dict:
        """
        Extract both vessel and cargo information from email content using OpenAI.
        Returns a dictionary with 'vessels' and 'cargoes' lists.
        Falls back to an empty result if the API quota is exceeded.
        """
        try:
            system_prompt = self.get_system_prompt()
            prompt = f"""You are a shipping expert. Extract all vessel and cargo information from this email.
            Pay special attention to:
            1. If email mentions "PROPOSE SUITABLE CARGOES" or similar, treat vessels mentioned as cargo opportunities
            2. Look for quantity information (DWT, MTS, DWCC)
            3. Look for location and date information after "OPEN"
            4. Identify vessel equipment like cranes, capacity, etc.
            5. For dates, if only day-month is provided (e.g., "07-11"), assume it's in the near future
            
            Return ONLY a JSON object with this structure:
            {{
                "vessels": [
                    {{
                        "name": "vessel name",
                        "dwt": numeric value only,
                        "position": "current position",
                        "vessel_type": "type of vessel",
                        "eta": "DD-MM",
                        "open_date": "DD-MM",
                        "description": "additional details including equipment"
                    }}
                ],
                "cargoes": [
                    {{
                        "cargo_type": "type of cargo or vessel name if it's a cargo opportunity",
                        "quantity": numeric value only in MT,
                        "load_port": "loading port name or open position",
                        "discharge_port": "discharge port if mentioned",
                        "laycan_start": "DD-MM",
                        "laycan_end": "DD-MM",
                        "rate": "rate if mentioned",
                        "description": "full original text and details"
                    }}
                ]
            }}

            Email content:
            {content}
            """

            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={ "type": "json_object" }
                )
                
                try:
                    # Extract the JSON content from the response
                    result = json.loads(response.choices[0].message.content)
                    
                    # Process and standardize the results
                    processed_result = {
                        'vessels': [],
                        'cargoes': []
                    }

                    # Process vessels
                    for vessel in result.get('vessels', []):
                        if vessel.get('name'):
                            processed_result['vessels'].append({
                                'name': vessel.get('name'),
                                'dwt': vessel.get('dwt'),
                                'position': vessel.get('position'),
                                'vessel_type': vessel.get('vessel_type'),
                                'eta': self.standardize_date(vessel.get('eta')),
                                'open_date': self.standardize_date(vessel.get('open_date')),
                                'description': vessel.get('description')
                            })

                    # Process cargoes
                    for cargo in result.get('cargoes', []):
                        if cargo.get('cargo_type'):
                            processed_result['cargoes'].append({
                                'cargo_type': cargo.get('cargo_type'),
                                'quantity': cargo.get('quantity'),
                                'load_port': cargo.get('load_port'),
                                'discharge_port': cargo.get('discharge_port'),
                                'laycan_start': self.standardize_date(cargo.get('laycan_start')),
                                'laycan_end': self.standardize_date(cargo.get('laycan_end')),
                                'rate': cargo.get('rate'),
                                'description': cargo.get('description')
                            })

                    return processed_result

                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse JSON from OpenAI response: {str(e)}")
                    logger.error(f"Raw response was: {response.choices[0].message.content}")
                    return {'vessels': [], 'cargoes': []}

            except Exception as e:
                if "429" in str(e) or "quota" in str(e).lower():
                    logger.warning("OpenAI quota exceeded - falling back to regex parsing")
                else:
                    logger.error(f"Error using OpenAI: {str(e)}")
                return {'vessels': [], 'cargoes': []}

        except Exception as e:
            logger.error(f"Error in extract_info: {str(e)}")
            return {'vessels': [], 'cargoes': []}


    def standardize_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """Convert various date formats to datetime objects with smart future date handling."""
        if not date_str:
            return None

        try:
            # If it's already a full date, try to parse it directly
            if isinstance(date_str, str) and len(date_str.split('-')) == 3:
                return datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            pass

        try:
            # Handle DD-MM format
            if isinstance(date_str, str) and len(date_str.replace('/', '-').split('-')) == 2:
                day, month = map(int, date_str.replace('/', '-').split('-'))
                # Try current year first
                try:
                    target_date = date(self.current_year, month, day)
                    # If the date would be in the past, use next year
                    if target_date < date.today():
                        target_date = date(self.current_year + 1, month, day)
                    return datetime.combine(target_date, datetime.min.time())
                except ValueError:
                    logger.warning(f"Invalid day-month combination: {date_str}")
                    return None

            # For other formats, ask OpenAI to extract the day and month
            prompt = f"""
            Extract only the day and month numbers from this text: "{date_str}"
            Return ONLY in DD-MM format, nothing else.
            Example: for "early Nov" return "01-11"
                    for "mid Dec" return "15-12"
                    for "end of Jan" return "31-01"
            If the text is too vague, return the first day of the mentioned month.
            """
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "Extract dates in DD-MM format only"},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=10
            )
            
            extracted_date = response.choices[0].message.content.strip()
            
            try:
                day, month = map(int, extracted_date.split('-'))
                target_date = date(self.current_year, month, day)
                if target_date < date.today():
                    target_date = date(self.current_year + 1, month, day)
                return datetime.combine(target_date, datetime.min.time())
            except ValueError:
                logger.warning(f"Could not parse extracted date: {extracted_date}")
                return None
            
        except Exception as e:
            logger.warning(f"Date standardization warning: {str(e)}")
            return None

        return None

    def get_system_prompt(self) -> str:
        """Return the system prompt for vessel and cargo extraction."""
        return """You are a shipping expert that extracts and structures vessel and cargo information from emails.
        Pay special attention to:
        1. Rate information in vessel offers (e.g., "USD 42.50/MT" or "Freight Rate: USD 38.00/MT")
        2. If email mentions "PROPOSE SUITABLE CARGOES" or similar, treat vessels mentioned as cargo opportunities
        3. Look for quantity information (DWT, MTS, DWCC)
        4. Look for location and date information after "OPEN"
        5. Look for freight rates and ensure they're in USD/MT format
        6. Identify vessel equipment like cranes, capacity, etc.
        7. For dates, if only day-month is provided (e.g., "07-11"), assume it's in the near future
        
        Return ONLY a JSON object with this structure:
        {
            "vessels": [
                {
                    "name": "vessel name",
                    "dwt": numeric value only,
                    "position": "current position",
                    "vessel_type": "type of vessel",
                    "eta": "DD-MM",
                    "open_date": "DD-MM",
                    "description": "additional details including equipment and rates"
                }
            ],
            "cargoes": [
                {
                    "cargo_type": "type of cargo or vessel name if it's a cargo opportunity",
                    "quantity": numeric value only in MT,
                    "load_port": "loading port name or open position",
                    "discharge_port": "discharge port if mentioned",
                    "laycan_start": "DD-MM",
                    "laycan_end": "DD-MM",
                    "rate": "rate if mentioned in USD/MT format",
                    "description": "full original text and details"
                }
            ]
        }"""