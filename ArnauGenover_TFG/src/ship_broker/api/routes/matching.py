# src/ship_broker/api/routes/matching.py

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict
import logging
from datetime import datetime

from ...core.vessel_tracker import VesselTracker
from ...core.cargo_tracker import CargoTracker
from ...core.database import Vessel, Cargo
from ...config import Settings, get_settings
from ..dependencies import get_db

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/match/cargo/{cargo_id}/vessels")
async def find_vessels_for_cargo(
    cargo_id: int,
    db: Session = Depends(get_db)
):
    """Find available vessels for a specific cargo"""
    try:
        cargo = db.query(Cargo).filter(Cargo.id == cargo_id).first()
        if not cargo:
            raise HTTPException(status_code=404, detail="Cargo not found")

        if not cargo.load_port:
            raise HTTPException(status_code=400, detail="Cargo has no loading port specified")

        # Initialize vessel tracker without API key
        tracker = VesselTracker()
        
        # Get vessels in loading port
        vessels = tracker.get_vessels_in_port(cargo.load_port)
        
        # Calculate match scores
        matches = []
        for vessel in vessels:
            if _is_vessel_suitable(vessel, cargo):
                score = _calculate_match_score(vessel, cargo)
                matches.append({
                    "vessel": vessel,
                    "score": score,
                    "reason": _get_match_reason(vessel, cargo)
                })
        
        return sorted(matches, key=lambda x: x['score'], reverse=True)

    except Exception as e:
        logger.error(f"Error finding vessels: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/match/vessel/{vessel_id}/cargoes")
async def find_cargoes_for_vessel(
    vessel_id: int,
    db: Session = Depends(get_db)
):
    """Find available cargoes for a specific vessel"""
    try:
        vessel = db.query(Vessel).filter(Vessel.id == vessel_id).first()
        if not vessel:
            raise HTTPException(status_code=404, detail="Vessel not found")

        tracker = CargoTracker()
        
        vessel_data = {
            'position': vessel.position,
            'type': vessel.vessel_type,
            'dwt': vessel.dwt
        }
        
        cargoes = tracker.get_cargoes_for_vessel(vessel_data)
        return cargoes

    except Exception as e:
        logger.error(f"Error finding cargoes: {str(e)}")
        return []  # Return empty list instead of raising error

def _is_vessel_suitable(vessel: Dict, cargo: Cargo) -> bool:
    """Check if vessel is suitable for cargo"""
    try:
        if not vessel.get('dwt') or not cargo.quantity:
            return False
        
        return (vessel['dwt'] * 0.95) >= cargo.quantity >= (vessel['dwt'] * 0.3)
    except Exception as e:
        logger.error(f"Error checking vessel suitability: {str(e)}")
        return False

def _calculate_match_score(vessel: Dict, cargo: Cargo) -> float:
    """Calculate match score between vessel and cargo"""
    try:
        score = 0.0
        
        # Size compatibility (30% of score)
        if cargo.quantity and vessel.get('dwt'):
            utilization = cargo.quantity / vessel['dwt']
            score += min(1.0, utilization) * 0.3

        # Location score (30% of score)
        if vessel.get('position') and cargo.load_port:
            if vessel['position'].upper() == cargo.load_port.upper():
                score += 0.3
            # Could add partial scores for nearby ports

        # Timing score (20% of score)
        if vessel.get('eta') and cargo.laycan_start:
            try:
                eta = datetime.fromisoformat(vessel['eta'])
                laycan = datetime.fromisoformat(cargo.laycan_start)
                if abs((eta - laycan).days) <= 3:  # Within 3 days
                    score += 0.2
                elif abs((eta - laycan).days) <= 7:  # Within a week
                    score += 0.1
            except Exception:
                pass

        # Type compatibility (20% of score) - not implemented in this function
        # but could be added for completeness

        return min(1.0, score)
    except Exception as e:
        logger.error(f"Error calculating match score: {str(e)}")
        return 0.0

def _get_match_reason(vessel: Dict, cargo: Cargo) -> str:
    """Get human-readable reason for match"""
    try:
        reasons = []
        
        if vessel.get('dwt') and cargo.quantity:
            utilization = (cargo.quantity / vessel['dwt']) * 100
            reasons.append(f"Vessel capacity utilization: {utilization:.1f}%")
        
        if vessel.get('position'):
            reasons.append(f"Currently in/near {vessel['position']}")
            
        if vessel.get('eta') and cargo.laycan_start:
            reasons.append(f"Available within laycan period")
            
        return "; ".join(reasons) if reasons else "No specific matching criteria"
    except Exception as e:
        logger.error(f"Error generating match reason: {str(e)}")
        return "Error calculating match details"