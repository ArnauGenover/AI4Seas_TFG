# src/ship_broker/api/routes/vessels.py

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional, Dict
from datetime import datetime
import logging
from sqlalchemy import or_  # Add this import

from ...core.database import Vessel, Cargo
from ...core.schemas import Vessel as VesselSchema
from ...core.vessel_tracker import tracker
from ..dependencies import get_db
from ..routes.auth import get_current_user
from ...core.database import Vessel, Cargo, User

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/vessels", response_model=List[VesselSchema])
@router.get("/vessels/", response_model=List[VesselSchema])
async def get_vessels(
    skip: int = 0,
    limit: int = 100,
    include_system: bool = True,  # New parameter
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Get all vessels for the authenticated user"""
    try:
        if not current_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required"
            )
        
        # Include system vessels if requested
        if include_system:
            vessels = db.query(Vessel).filter(
                or_(
                    Vessel.owner_id == current_user.id,
                    Vessel.owner_id == None
                )
            ).offset(skip).limit(limit).all()
            logger.info(f"Including system vessels for user {current_user.id}")
        else:
            # Only user's vessels
            vessels = db.query(Vessel).filter(
                Vessel.owner_id == current_user.id
            ).offset(skip).limit(limit).all()
            logger.info(f"Only querying vessels for user {current_user.id}")
        
        logger.info(f"Found {len(vessels)} vessels (include_system={include_system})")
        
        # Debug log vessel details
        for v in vessels:
            logger.info(f"Vessel: {v.name}, owner_id: {v.owner_id}")
            
        return vessels
    except Exception as e:
        logger.error(f"Error getting vessels: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/my-vessels")
async def get_my_vessels(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get vessels owned by the current user"""
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    
    vessels = db.query(Vessel).filter(Vessel.owner_id == current_user.id).all()
    return vessels

@router.get("/vessels/match/{vessel_id}/cargoes", response_model=List[Dict])
async def get_matching_cargoes(
    vessel_id: int, 
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Get cargoes that match a specific vessel"""
    try:
        if not current_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required"
            )
            
        # Get the vessel and verify ownership
        vessel = db.query(Vessel).filter(
            Vessel.id == vessel_id,
            Vessel.owner_id == current_user.id
        ).first()
        
        if not vessel:
            raise HTTPException(status_code=404, detail="Vessel not found")
            
        # Get all active cargoes for this user
        cargoes = db.query(Cargo).filter(Cargo.owner_id == current_user.id).all()
        matching_cargoes = []
        
        for cargo in cargoes:
            # Calculate match score
            score = 0.0
            reason = []
            
            # Type matching (20% of score)
            vessel_type = vessel.vessel_type.lower() if vessel.vessel_type else ""
            cargo_type = cargo.cargo_type.lower()
            
            if ('bulk' in vessel_type and any(t in cargo_type for t in ['coal', 'ore', 'clinker'])):
                score += 0.2
                reason.append("Vessel type suitable for bulk cargo")
            elif ('tanker' in vessel_type and 'oil' in cargo_type):
                score += 0.2
                reason.append("Tanker suitable for liquid cargo")
            elif any(t in vessel_type for t in ['cargo', 'general']):
                score += 0.15
                reason.append("General cargo vessel")
            
            # Size matching (30% of score)
            if vessel.dwt and cargo.quantity:
                utilization = cargo.quantity / vessel.dwt
                if 0.5 <= utilization <= 0.95:
                    score += 0.3
                    reason.append(f"Optimal cargo size ({utilization:.0%} utilization)")
                elif 0.3 <= utilization < 0.5:
                    score += 0.2
                    reason.append(f"Acceptable cargo size ({utilization:.0%} utilization)")
                
            # Location matching (30% of score)
            if vessel.position and cargo.load_port:
                port_coords = tracker._get_port_coordinates(cargo.load_port)
                if port_coords:
                    try:
                        vessel_coords = tracker._parse_position(vessel.position)
                        if vessel_coords and vessel_coords[0] is not None:
                            distance = tracker._calculate_distance(
                                port_coords['lat'],
                                port_coords['lon'],
                                vessel_coords[0],
                                vessel_coords[1]
                            )
                            if distance <= 100:
                                score += 0.3
                                reason.append(f"Close to loading port ({distance:.0f} nm)")
                            elif distance <= 300:
                                score += 0.2
                                reason.append(f"Within range of loading port ({distance:.0f} nm)")
                            elif distance <= 500:
                                score += 0.1
                                reason.append(f"Distant from loading port ({distance:.0f} nm)")
                    except Exception as e:
                        logger.warning(f"Error calculating distance: {str(e)}")
            
            # Temporal matching (20% of score) - not explicitly calculated in this function
            
            if score >= 0.3:  # Only include good matches
                matching_cargoes.append({
                    "cargo": {
                        "id": cargo.id,
                        "type": cargo.cargo_type,
                        "quantity": cargo.quantity,
                        "load_port": cargo.load_port,
                        "discharge_port": cargo.discharge_port,
                        "laycan_start": cargo.laycan_start,
                        "laycan_end": cargo.laycan_end,
                        "rate": cargo.rate
                    },
                    "score": score,
                    "reason": "; ".join(reason)
                })
        
        # Sort by score and return top matches
        matching_cargoes.sort(key=lambda x: x['score'], reverse=True)
        return matching_cargoes[:10]
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error matching cargoes: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/vessels/{vessel_id}", response_model=VesselSchema)
async def get_vessel(
    vessel_id: int, 
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Get a specific vessel"""
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required"
        )
        
    vessel = db.query(Vessel).filter(
        Vessel.id == vessel_id,
        Vessel.owner_id == current_user.id
    ).first()
    
    if vessel is None:
        raise HTTPException(status_code=404, detail="Vessel not found")
    
    return vessel