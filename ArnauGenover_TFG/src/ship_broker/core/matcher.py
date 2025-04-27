# src/ship_broker/core/matcher.py
from typing import List, Dict
from sqlalchemy.orm import Session
from .vessel_tracker import VesselTracker
from .database import Cargo
import logging

logger = logging.getLogger(__name__)

class CargoMatcher:
    def __init__(self, db: Session, vessel_tracker: VesselTracker):
        self.db = db
        self.vessel_tracker = vessel_tracker

    def find_vessels_for_cargo(self, cargo_id: int) -> List[Dict]:
        try:
            # Get cargo details
            cargo = self.db.query(Cargo).filter(Cargo.id == cargo_id).first()
            if not cargo:
                return []

            # Get vessels in loading port
            available_vessels = self.vessel_tracker.get_vessels_in_port(cargo.load_port)
            
            # Filter suitable vessels
            matches = []
            for vessel in available_vessels:
                if self._is_vessel_suitable(vessel, cargo):
                    match_score = self._calculate_match_score(vessel, cargo)
                    matches.append({
                        'vessel': vessel,
                        'score': match_score,
                        'reason': self._get_match_reason(vessel, cargo)
                    })

            return sorted(matches, key=lambda x: x['score'], reverse=True)
        except Exception as e:
            logger.error(f"Error finding vessels for cargo {cargo_id}: {str(e)}")
            return []

    def _is_vessel_suitable(self, vessel: Dict, cargo: Cargo) -> bool:
        if not vessel.get('dwt'):
            return False
        
        # Check if vessel size is appropriate for cargo
        cargo_size = cargo.quantity or 0
        return (vessel['dwt'] * 0.95) >= cargo_size >= (vessel['dwt'] * 0.3)

    def _calculate_match_score(self, vessel: Dict, cargo: Cargo) -> float:
        score = 0.0
        
        # Size compatibility (30% of score)
        if cargo.quantity:
            utilization = cargo.quantity / vessel['dwt']
            score += min(1.0, utilization) * 0.3

        # Location score (30% of score)
        if vessel['position'] and cargo.load_port:
            score += 0.3

        # Type compatibility (20% of score)
        # (Would need vessel type information to implement)
            
        # Timing score (20% of score)
        if vessel.get('eta') and cargo.laycan_start:
            # Implementation would go here
            score += 0.2

        return min(1.0, score)

    def _get_match_reason(self, vessel: Dict, cargo: Cargo) -> str:
        reasons = []
        if vessel['dwt'] >= (cargo.quantity or 0):
            reasons.append(f"Sufficient capacity ({vessel['dwt']} DWT)")
        if vessel['position']:
            reasons.append(f"Currently in/near {vessel['position']}")
        return "; ".join(reasons)