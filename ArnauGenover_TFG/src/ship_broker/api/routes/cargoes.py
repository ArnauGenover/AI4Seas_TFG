# src/ship_broker/api/routes/cargoes.py

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional, Dict
from datetime import datetime
import logging
from sqlalchemy import or_  # Add this import

from ...core.database import Cargo, Vessel
from ...core.schemas import CargoCreate, CargoResponse
from ...core.cargo_tracker import CargoTracker
from ...core.vessel_tracker import tracker
from ..dependencies import get_db
from ..routes.auth import get_current_user
from ...core.database import Cargo, Vessel, User

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/cargoes", response_model=List[CargoResponse])
@router.get("/cargoes/", response_model=List[CargoResponse])
async def get_cargoes(
    skip: int = 0,
    limit: int = 100,
    include_system: bool = True,  # New parameter
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Get all cargoes for the authenticated user with pagination"""
    try:
        if not current_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required"
            )
            
        # Base query for user-owned cargoes
        if include_system:
            # Include both user's cargoes and system cargoes (owner_id is NULL)
            logger.info(f"Including system cargoes for user {current_user.id}")
            query = db.query(Cargo).filter(
                or_(
                    Cargo.owner_id == current_user.id,
                    Cargo.owner_id == None
                )
            )
        else:
            # Only include user's own cargoes
            logger.info(f"Only querying cargoes for user {current_user.id}")
            query = db.query(Cargo).filter(
                Cargo.owner_id == current_user.id
            )
            
        cargoes = query.order_by(Cargo.created_at.desc()).offset(skip).limit(limit).all()
        logger.info(f"Found {len(cargoes)} cargoes (include_system={include_system})")
        
        # Debug log cargo details
        for c in cargoes:
            logger.info(f"Cargo: {c.cargo_type}, quantity: {c.quantity}, owner_id: {c.owner_id}")
            
        return cargoes
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting cargoes: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/cargoes", response_model=CargoResponse)
@router.post("/cargoes/", response_model=CargoResponse)
async def create_cargo(
    cargo: CargoCreate, 
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Create a new cargo for the authenticated user"""
    try:
        if not current_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required"
            )
            
        db_cargo = Cargo(
            cargo_type=cargo.cargo_type,
            quantity=cargo.quantity,
            load_port=cargo.load_port,
            discharge_port=cargo.discharge_port,
            laycan_start=cargo.laycan_start,
            laycan_end=cargo.laycan_end,
            description=cargo.description,
            rate=cargo.rate,
            owner_id=current_user.id  # Set owner to current user
        )
        db.add(db_cargo)
        db.commit()
        db.refresh(db_cargo)
        return db_cargo
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating cargo: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/my-cargoes")
async def get_my_cargoes(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get cargoes owned by the current user"""
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    
    cargoes = db.query(Cargo).filter(Cargo.owner_id == current_user.id).all()
    return cargoes

@router.get("/cargoes/match/{cargo_id}/vessels", response_model=List[Dict])
async def get_matching_vessels(
    cargo_id: int, 
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Get vessels that match a specific cargo"""
    try:
        if not current_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required"
            )
            
        # Get the cargo and verify ownership
        cargo = db.query(Cargo).filter(
            Cargo.id == cargo_id,
            Cargo.owner_id == current_user.id
        ).first()
        
        if not cargo:
            raise HTTPException(status_code=404, detail="Cargo not found")

        # Get user's vessels from the database
        db_vessels = db.query(Vessel).filter(Vessel.owner_id == current_user.id).all()
        if not db_vessels:
            return []
        
        # Get live vessels from tracker
        live_vessels = tracker.vessels_cache or {}
        
        matching_vessels = []
        
        # First, process the user's database vessels
        for vessel in db_vessels:
            # Calculate match score based on vessel properties
            score = 0.0
            reason = []

            # Type matching (20% of score)
            vessel_type = vessel.vessel_type.lower() if vessel.vessel_type else ""
            cargo_type = cargo.cargo_type.lower()
            
            if ('bulk' in vessel_type and any(t in cargo_type for t in ['coal', 'ore', 'clinker'])):
                score += 0.2
                reason.append("Vessel type matches cargo")
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
                    reason.append(f"Optimal vessel size ({utilization:.0%} utilization)")
                elif 0.3 <= utilization < 0.5:
                    score += 0.2
                    reason.append(f"Acceptable vessel size ({utilization:.0%} utilization)")
                elif utilization < 1.0:
                    score += 0.1
                    reason.append(f"Vessel has sufficient capacity")
            
            # Location matching (30% of score)
            if vessel.position and cargo.load_port:
                port_coords = tracker._get_port_coordinates(cargo.load_port)
                distance = None
                
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
                    except Exception as e:
                        logger.warning(f"Error calculating distance: {str(e)}")
            
            # Temporal matching (20% of score) - not explicitly calculated in this function
            
            if score >= 0.3:  # Only include vessels with reasonable match score
                matching_vessels.append({
                    "vessel": {
                        "id": vessel.id,
                        "name": vessel.name,
                        "type": vessel.vessel_type or "Unknown",
                        "position": vessel.position or "Unknown",
                        "dwt": vessel.dwt or 0,
                        "distance_to_load": f"{distance:.0f} nm" if distance else "Unknown",
                        "source": "user_database"
                    },
                    "score": score,
                    "reason": "; ".join(reason)
                })

        # Sort by score and return top matches
        matching_vessels.sort(key=lambda x: x['score'], reverse=True)
        return matching_vessels[:10]

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error matching vessels: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/cargoes/{cargo_id}", response_model=CargoResponse)
async def get_cargo(
    cargo_id: int, 
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Get a specific cargo"""
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required"
        )
        
    cargo = db.query(Cargo).filter(
        Cargo.id == cargo_id,
        Cargo.owner_id == current_user.id
    ).first()
    
    if cargo is None:
        raise HTTPException(status_code=404, detail="Cargo not found")
    
    return cargo