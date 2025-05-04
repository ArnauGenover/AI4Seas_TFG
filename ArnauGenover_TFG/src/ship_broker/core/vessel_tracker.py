# src/ship_broker/core/vessel_tracker.py

from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
import logging
import asyncio
import websockets
import json
import math
from dotenv import load_dotenv
import os

# Configure logging
logging.getLogger('websockets.client').setLevel(logging.WARNING)
logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)  # Set to WARNING to reduce output

class VesselTracker:
    def __init__(self):
        load_dotenv()
        self.api_key = os.getenv('AISSTREAM_API_KEY')
        self.vessels_cache = {}
        self.last_update = datetime.now()
        self._running = False

    async def start_tracking(self):
        """Start vessel tracking"""
        if self._running:
            return
                
        self._running = True
        max_retries = 3  # Limit retries when the service is unavailable
        retry_count = 0
        
        while self._running:
            try:
                await self.connect_ais_stream()
                # Reset retry count after successful connection
                retry_count = 0
            except Exception as e:
                retry_count += 1
                if retry_count >= max_retries:
                    logger.error(f"AIS stream connection failed after {max_retries} attempts. Stopping retries.")
                    self._running = False
                    return
                    
                logger.error(f"AIS stream connection error: {str(e)}")
                # Exponential backoff with a cap
                wait_time = min(30, 2 ** retry_count)
                logger.info(f"Waiting {wait_time} seconds before reconnecting...")
                await asyncio.sleep(wait_time)

        
    async def stop_tracking(self):
        """Stop vessel tracking"""
        self._running = False
        
    async def connect_ais_stream(self):
        """Connect to AISStream WebSocket"""
        if not self.api_key:
            logger.error("No API key found in environment variables")
            return

        url = "wss://stream.aisstream.io/v0/stream"
        
        try:
            async with websockets.connect(url) as websocket:
                logger.info("Connected to AISStream")
                
                subscribe_message = {
                    "APIKey": self.api_key,
                    "BoundingBoxes": [[
                        [-90, -180],  # [lat, lon] of first corner
                        [90, 180]     # [lat, lon] of second corner
                    ]],
                    "FilterMessageTypes": ["PositionReport"]
                }
                
                await websocket.send(json.dumps(subscribe_message))
                
                while self._running:
                    try:
                        message = await asyncio.wait_for(websocket.recv(), timeout=30)
                        message_data = json.loads(message)
                        await self._process_ais_message(message_data)
                    except asyncio.TimeoutError:
                        try:
                            pong = await websocket.ping()
                            await asyncio.wait_for(pong, timeout=10)
                        except Exception as e:
                            logger.error(f"Ping failed: {str(e)}")
                            break
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to decode message: {str(e)}")
                        continue
                    except Exception as e:
                        if "no close frame received or sent" in str(e):
                            break
                        logger.error(f"Error processing message: {str(e)}")
                        continue
                        
        except Exception as e:
            logger.error(f"WebSocket connection error: {str(e)}")
            raise

    async def _process_ais_message(self, message: Dict):
        """Process incoming AIS message"""
        try:
            position_report = message.get('Message', {}).get('PositionReport')
            if not position_report:
                return
                
            lat = position_report.get('Latitude')
            lon = position_report.get('Longitude')
            mmsi = str(position_report.get('UserID', ''))
            
            if not all([lat, lon, mmsi]):
                return
                
            vessel = {
                'mmsi': mmsi,
                'name': position_report.get('ShipName', f'VESSEL_{mmsi[-6:]}'),
                'type': self._get_vessel_type(position_report.get('ShipType', 0)),
                'length': position_report.get('Length', 0),
                'width': position_report.get('Width', 0),
                'draught': position_report.get('Draught', 0),
                'position': self._get_position_string(lat, lon),
                'lat': lat,
                'lon': lon,
                'speed': f"{position_report.get('Sog', 0):.1f} kn",
                'course': position_report.get('Cog', 0),
                'heading': position_report.get('TrueHeading', 0),
                'destination': position_report.get('Destination', 'Unknown'),
                'eta': position_report.get('Eta', 'Unknown'),
                'status': self._get_status_description(position_report.get('NavigationalStatus', 15)),
                'last_update': datetime.now().isoformat(),
                'is_mock': False
            }
            
            self.vessels_cache[mmsi] = vessel
            self.last_update = datetime.now()
            
        except Exception as e:
            logger.error(f"Error processing AIS message: {str(e)}")

    def _get_vessel_type(self, type_code: int) -> str:
        """Get human-readable vessel type from AIS type code"""
        vessel_types = {
            60: "Passenger",
            70: "Cargo",
            80: "Tanker",
            30: "Fishing",
            31: "Towing",
            32: "Towing Long/Wide",
            33: "Dredging",
            34: "Diving",
            35: "Military",
            36: "Sailing",
            37: "Pleasure",
            40: "High Speed Craft",
            50: "Pilot",
            51: "Search and Rescue",
            52: "Tug",
            53: "Port Tender",
            54: "Anti-Pollution",
            55: "Law Enforcement"
        }
        
        base_type = type_code - (type_code % 10)
        return vessel_types.get(base_type, "Unknown")
    
    def _get_position_string(self, lat: float, lon: float) -> str:
        """Convert coordinates to readable position"""
        return f"LAT: {lat:.4f}, LON: {lon:.4f}"
    
    def _calculate_distance(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate distance between two points in nautical miles"""
        R = 3440.065  # Earth's radius in nautical miles
        
        lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        
        a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
        c = 2 * math.asin(math.sqrt(a))
        return R * c
    
    def _parse_position(self, position: str) -> Tuple[Optional[float], Optional[float]]:
        """Parse position string to coordinates"""
        try:
            lat = float(position.split('LAT: ')[1].split(',')[0])
            lon = float(position.split('LON: ')[1])
            return lat, lon
        except:
            return None, None

    def _get_status_description(self, status_code: int) -> str:
        """Get human-readable vessel status description"""
        status_codes = {
            0: "Under way using engine",
            1: "At anchor",
            2: "Not under command",
            3: "Restricted maneuverability",
            4: "Constrained by draught",
            5: "Moored",
            6: "Aground",
            7: "Engaged in fishing",
            8: "Under way sailing",
            9: "Reserved for HSC",
            10: "Reserved for WIG",
            11: "Power-driven vessel towing astern",
            12: "Power-driven vessel pushing ahead/towing alongside",
            13: "Reserved",
            14: "AIS-SART (active)",
            15: "Not defined"
        }
        return status_codes.get(status_code, "Unknown status")
    
    def get_vessels_in_port(self, port_name: str) -> List[Dict]:
        """Get vessels currently in or near a specific port"""
        try:
            route_parts = port_name.split('→') if '→' in port_name else [port_name]
            target_port = route_parts[0].strip()
            
            if ',' in target_port:
                port, region = [p.strip() for p in target_port.split(',', 1)]
            else:
                port = target_port
            
            port_coords = self._get_port_coordinates(target_port)
            if not port_coords:
                return []
            
            if not self.vessels_cache or \
               datetime.now() - self.last_update > timedelta(minutes=30):
                return []
            
            nearby_vessels = []
            search_radius = 75
            
            for vessel in self.vessels_cache.values():
                try:
                    vessel_lat = vessel.get('lat')
                    vessel_lon = vessel.get('lon')
                    
                    if vessel_lat is None or vessel_lon is None:
                        continue
                        
                    distance = self._calculate_distance(
                        port_coords['lat'],
                        port_coords['lon'],
                        vessel_lat,
                        vessel_lon
                    )
                    
                    if distance <= search_radius:
                        vessel_copy = vessel.copy()
                        vessel_copy.update({
                            'near_port': port,
                            'distance_to_port': f"{distance:.1f} nm",
                            'in_port': distance <= 2.0,
                            'matched_route': port_name,
                            'last_seen': datetime.fromisoformat(vessel['last_update']).strftime('%Y-%m-%d %H:%M:%S')
                        })
                        nearby_vessels.append(vessel_copy)
                        
                except Exception as e:
                    continue
            
            if nearby_vessels:
                nearby_vessels.sort(key=lambda x: float(x['distance_to_port'].split()[0]))
                return nearby_vessels
            
            if len(self.vessels_cache) > 0:
                return list(self.vessels_cache.values())[:3]
            return []
                
        except Exception as e:
            logger.error(f"Error getting vessels: {str(e)}")
            return []
    
    def _get_port_coordinates(self, port_name: str) -> Optional[Dict]:
        """Get port coordinates from name"""
        ports_db = {
            "DAMPIER, AUSTRALIA": {"lat": -20.6167, "lon": 116.7167},
            "NEWCASTLE, AUSTRALIA": {"lat": -32.9167, "lon": 151.7833},
            "SINGAPORE": {"lat": 1.2833, "lon": 103.8500},
            "TIANJIN, CHINA": {"lat": 39.0000, "lon": 117.7167},
            "MUNDRA, INDIA": {"lat": 22.8333, "lon": 69.7167},
            "CHITTAGONG, BANGLADESH": {"lat": 22.3419, "lon": 91.8132},
            "ANTWERP, BELGIUM": {"lat": 51.2194, "lon": 4.4025},
            "HOUSTON, USA": {"lat": 29.7604, "lon": -95.3698}
        }
        
        return ports_db.get(port_name.upper())

    def _get_mock_data(self, port_name: str) -> List[Dict]:
        """Return mock data when no real data is available"""
        mock_vessels = []
        vessel_types = ["CARGO", "TANKER", "CONTAINER"]
        
        for i in range(3):
            mock_vessels.append({
                'name': f"MOCK VESSEL {i+1}",
                'mmsi': f"999{str(i).zfill(6)}",
                'type': vessel_types[i % len(vessel_types)],
                'dwt': 50000 + (i * 10000),
                'position': port_name,
                'speed': "0 kn",
                'destination': port_name,
                'eta': "In Port",
                'status': "Moored",
                'is_mock': True,
                'in_port': True,
                'distance_to_port': "0.0 nm",
                'last_update': datetime.now().isoformat()
            })
        
        return mock_vessels

# Global tracker instance
tracker = VesselTracker()