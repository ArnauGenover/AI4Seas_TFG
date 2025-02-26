# src/ship_broker/api/routes/auth.py

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status, Form, Cookie
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
import jwt
from datetime import datetime, timedelta
from typing import Optional
import uuid
from ...core.database import User, Session as DbSession
from ...core.schemas import UserCreate
from ..dependencies import get_db
from ...config import get_settings
import logging

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter()
templates = Jinja2Templates(directory="src/ship_broker/web/templates")

SECRET_KEY = settings.SECRET_KEY
ALGORITHM = "HS256"

# Import SERVER_ID from main
try:
    from ...main import SERVER_ID
except ImportError:
    # If not available yet, create a placeholder
    SERVER_ID = None
    logger.warning("SERVER_ID not available during auth module import")

# Dependency to get current user
async def get_current_user(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("session")
    if not token:
        return None
    
    try:
        # First try JWT token (for backward compatibility)
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            return None
        
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            return user
    except jwt.PyJWTError:
        # If JWT decoding fails, try persistent session
        try:
            db_session = db.query(DbSession).filter(DbSession.id == token).first()
            
            if not db_session:
                return None
            
            # Check server ID if available
            if hasattr(db_session, 'server_id') and SERVER_ID and db_session.server_id != SERVER_ID:
                logger.info(f"Session {token} is from a different server instance. Invalidating.")
                db.delete(db_session)
                db.commit()
                return None
                
            if db_session.expires < datetime.utcnow():
                # Session expired, clean up
                db.delete(db_session)
                db.commit()
                logger.info(f"Expired session removed: {token}")
                return None
            
            # Update last activity
            db_session.last_activity = datetime.utcnow()
            db.commit()
            
            return db_session.user
        except Exception as e:
            logger.error(f"Error retrieving session: {str(e)}")
            return None
    except Exception as e:
        logger.error(f"Error in authentication: {str(e)}")
        return None

# API token endpoint for programmatic access
@router.post("/token")
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not user.verify_password(form_data.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Create JWT token for API access
    token_data = {
        "sub": str(user.id),
        "exp": datetime.utcnow() + timedelta(minutes=30)
    }
    token = jwt.encode(token_data, SECRET_KEY, algorithm=ALGORITHM)
    
    return {"access_token": token, "token_type": "bearer"}

# New endpoint to view active sessions (for profile page)
@router.get("/sessions")
async def get_user_sessions(
    request: Request,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    
    sessions = db.query(DbSession).filter(
        DbSession.user_id == current_user.id,
        DbSession.expires > datetime.utcnow()
    ).all()
    
    return {
        "sessions": [
            {
                "id": session.id,
                "created_at": session.created_at,
                "expires": session.expires,
                "last_activity": session.last_activity,
                "server_id": getattr(session, "server_id", None)
            } for session in sessions
        ]
    }

# New endpoint to terminate a specific session
@router.delete("/sessions/{session_id}")
async def terminate_session(
    session_id: str,
    current_user = Depends(get_current_user),
    current_session: Optional[str] = Cookie(None),
    db: Session = Depends(get_db)
):
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    
    # Find the session
    session = db.query(DbSession).filter(
        DbSession.id == session_id,
        DbSession.user_id == current_user.id
    ).first()
    
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )
    
    # Delete the session
    db.delete(session)
    db.commit()
    
    # If user terminated their current session, log them out
    if session_id == current_session:
        response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
        response.delete_cookie("session")
        return response
    
    return {"status": "success"}