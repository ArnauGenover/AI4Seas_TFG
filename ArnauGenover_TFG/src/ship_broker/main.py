# src/ship_broker/main.py

import os
import uuid
import jwt
import asyncio
import logging
import imaplib
import secrets
import signal
import sys
import re

from datetime import datetime, timedelta
from typing import Optional

from fastapi import (
    FastAPI, Request, Depends, Form, status, Cookie, HTTPException
)
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .core.database import Base, engine, Session as DbSession, User
from .core.scheduler import start_scheduler
from .core.vessel_tracker import tracker
from .core.email_service import generate_verification_code, send_verification_email
from .api.routes import (
    vessels, cargoes, email_processing, test, matching, auctions, auth
)
from .api.dependencies import get_db

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

# Generate a unique server ID for this server instance
SERVER_ID = str(uuid.uuid4())
logger.info(f"Server started with ID: {SERVER_ID}")

# Create database tables
Base.metadata.create_all(bind=engine)

# Get settings
settings = get_settings()

# Initialize FastAPI app first
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Ship broker application for managing vessels and cargoes",
    docs_url="/api/docs",
    redoc_url="/api/redoc"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Get the current file's directory
current_dir = os.path.dirname(os.path.abspath(__file__))

# Mount static files
static_dir = os.path.join(current_dir, "web", "static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir, exist_ok=True)
    logger.info(f"Created static directory: {static_dir}")

# Create necessary subdirectories
for subdir in ['css', 'js', 'img']:
    subdir_path = os.path.join(static_dir, subdir)
    if not os.path.exists(subdir_path):
        os.makedirs(subdir_path, exist_ok=True)
        logger.info(f"Created static subdirectory: {subdir_path}")

app.mount("/static", StaticFiles(directory=static_dir), name="static")
logger.info(f"Static files mounted from: {static_dir}")

# Initialize templates
templates_dir = os.path.join(current_dir, "web", "templates")
if not os.path.exists(templates_dir):
    os.makedirs(templates_dir, exist_ok=True)
    logger.info(f"Created templates directory: {templates_dir}")

templates = Jinja2Templates(directory=templates_dir)

# Custom dependency to get current user with server ID check
async def get_current_user_with_server_check(
    request: Request, 
    session: Optional[str] = Cookie(None),
    db: Session = Depends(get_db)
):
    """Get current user with server ID check"""
    if not session:
        return None
    
    try:
        # First try JWT token (for backward compatibility)
        payload = jwt.decode(session, settings.SECRET_KEY, algorithms=["HS256"])
        user_id = payload.get("sub")
        if user_id is None:
            return None
        
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            return user
    except jwt.PyJWTError:
        # If JWT decoding fails, try persistent session
        try:
            db_session = db.query(DbSession).filter(DbSession.id == session).first()
            
            if not db_session:
                return None
                
            # Check if session has a server_id and if it matches current server
            if hasattr(db_session, 'server_id') and db_session.server_id != SERVER_ID:
                logger.info(f"Session {session} is from a different server instance. Invalidating.")
                db.delete(db_session)
                db.commit()
                return None
                
            if db_session.expires < datetime.utcnow():
                # Session expired, clean up
                db.delete(db_session)
                db.commit()
                logger.info(f"Expired session removed: {session}")
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

# Include API routers with consistent prefixes
app.include_router(
    vessels.router,
    prefix="/api/v1",
    tags=["vessels"]
)

app.include_router(
    cargoes.router,
    prefix="/api/v1",
    tags=["cargoes"]
)

app.include_router(
    email_processing.router,
    prefix="/api/v1",
    tags=["email"]
)

app.include_router(
    matching.router,
    prefix="/api/v1",
    tags=["matching"]
)

app.include_router(
    test.router,
    prefix="/api/v1",
    tags=["test"]
)

app.include_router(
    auctions.router,
    prefix="/api/v1",
    tags=["auctions"]
)

app.include_router(
    auth.router,
    prefix="/api/v1",
    tags=["auth"]
)

@app.on_event("startup")
async def startup_event():
    """Start background tasks when the application starts"""
    try:
        logger.info("Starting background tasks...")
        
        # Start the scheduler
        asyncio.create_task(start_scheduler())
        
        # Start AIS stream
        asyncio.create_task(tracker.start_tracking())
        
        logger.info("Background tasks started successfully")
    except Exception as e:
        logger.error(f"Error starting background tasks: {str(e)}")

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup when the application shuts down"""
    try:
        # Stop AIS stream
        await tracker.stop_tracking()
        
        # Clear all active sessions
        db = SessionLocal()
        try:
            # Delete all active sessions to force logout
            deleted_count = db.query(DbSession).delete()
            db.commit()
            logger.info(f"Cleared {deleted_count} active sessions during shutdown")
        except Exception as e:
            logger.error(f"Error clearing sessions during shutdown: {str(e)}")
            db.rollback()
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Error during shutdown: {str(e)}")

@app.get("/")
async def home(request: Request, current_user=Depends(get_current_user_with_server_check)):
    """Render home page"""
    try:
        return templates.TemplateResponse("index.html", {
            "request": request,
            "current_user": current_user
        })
    except Exception as e:
        logger.error(f"Error rendering home page: {str(e)}")
        return {"error": "Error rendering page"}

@app.get("/vessels")
async def vessels_page(request: Request, current_user=Depends(get_current_user_with_server_check)):
    """Render vessels page"""
    try:
        return templates.TemplateResponse("vessels.html", {
            "request": request,
            "current_user": current_user
        })
    except Exception as e:
        logger.error(f"Error rendering vessels page: {str(e)}")
        return {"error": "Error rendering page"}

@app.get("/cargoes")
async def cargoes_page(request: Request, current_user=Depends(get_current_user_with_server_check)):
    """Render cargoes page"""
    try:
        return templates.TemplateResponse("cargoes.html", {
            "request": request,
            "current_user": current_user
        })
    except Exception as e:
        logger.error(f"Error rendering cargoes page: {str(e)}")
        return {"error": "Error rendering page"}

@app.get("/auctions")
async def auctions_page(request: Request, current_user=Depends(get_current_user_with_server_check)):
    """Render auctions page"""
    try:
        return templates.TemplateResponse("auctions.html", {
            "request": request,
            "current_user": current_user
        })
    except Exception as e:
        logger.error(f"Error rendering auctions page: {str(e)}")
        return {"error": "Error rendering page"}

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        return {
            "status": "healthy",
            "version": settings.VERSION,
            "database": "connected" if engine else "disconnected",
            "ais_stream": "connected" if tracker.api_key else "disconnected",
            "server_id": SERVER_ID
        }
    except Exception as e:
        logger.error(f"Error in health check: {str(e)}")
        return {
            "status": "unhealthy",
            "error": str(e)
        }


@app.get("/login")
async def login_page(request: Request):
    """Render login page"""
    try:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "current_user": None
        })
    except Exception as e:
        logger.error(f"Error rendering login page: {str(e)}")
        return {"error": "Error rendering page"}

@app.post("/login")
async def login_form(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    remember_me: bool = Form(False),
    db: Session = Depends(get_db)
):
    """Process login form submission"""
    try:
        user = db.query(User).filter(User.email == username).first()
        if not user or not user.verify_password(password):
            return templates.TemplateResponse(
                "login.html",
                {"request": request, "error": "Invalid email or password", "current_user": None}
            )
        
        # Check if user is verified
        if hasattr(user, 'is_verified') and not user.is_verified:
            # Generate new verification code
            verification_code = generate_verification_code()
            expires = datetime.utcnow() + timedelta(minutes=30)
            
            user.verification_code = verification_code
            user.verification_code_expires = expires
            db.commit()
            
            # Send verification email
            send_verification_email(user.email, verification_code)
            
            return templates.TemplateResponse(
                "login.html",
                {"request": request, "error": "Your account is not verified. A new verification code has been sent to your email.", "current_user": None}
            )
        
        # Set session duration based on remember_me
        session_duration = 30 if remember_me else 1  # 30 days or 1 day
        
        # Create persistent session
        session_id = str(uuid.uuid4())
        expires = datetime.utcnow() + timedelta(days=session_duration)
        
        try:
            # Add server_id to session
            db_session = DbSession(
                id=session_id,
                user_id=user.id,
                expires=expires,
                server_id=SERVER_ID  # Store the current server ID
            )
        except Exception as e:
            # If server_id column doesn't exist yet, create session without it
            logger.warning(f"Could not add server_id to session: {str(e)}")
            db_session = DbSession(
                id=session_id,
                user_id=user.id,
                expires=expires
            )
            
        db.add(db_session)
        db.commit()
        
        response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
        response.set_cookie(
            key="session",
            value=session_id,
            httponly=True,
            max_age=session_duration * 24 * 60 * 60,  # Convert days to seconds
            expires=session_duration * 24 * 60 * 60,
            secure=settings.COOKIE_SECURE if hasattr(settings, 'COOKIE_SECURE') else False,
            samesite="lax"
        )
        
        return response
    except Exception as e:
        logger.error(f"Error processing login: {str(e)}")
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Login failed. Please try again.", "current_user": None}
        )


@app.get("/register")
async def register_page(request: Request):
    """Render registration page"""
    try:
        return templates.TemplateResponse("register.html", {
            "request": request,
            "current_user": None
        })
    except Exception as e:
        logger.error(f"Error rendering register page: {str(e)}")
        return {"error": "Error rendering page"}



@app.post("/register")
async def register_form(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    email_confirm: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    db: Session = Depends(get_db)
):
    """Process registration form submission"""
    try:
        # Validate email confirmation
        if email != email_confirm:
            return templates.TemplateResponse(
                "register.html",
                {"request": request, "error": "Email addresses do not match", "current_user": None}
            )
            
        # Validate password confirmation
        if password != password_confirm:
            return templates.TemplateResponse(
                "register.html",
                {"request": request, "error": "Passwords do not match", "current_user": None}
            )
        
        # Validate password strength
        if len(password) < 8:
            return templates.TemplateResponse(
                "register.html",
                {"request": request, "error": "Password must be at least 8 characters long", "current_user": None}
            )
            
        # Check for at least one letter
        if not re.search(r'[a-zA-Z]', password):
            return templates.TemplateResponse(
                "register.html",
                {"request": request, "error": "Password must contain at least one letter", "current_user": None}
            )
            
        # Check for at least one number
        if not re.search(r'[0-9]', password):
            return templates.TemplateResponse(
                "register.html",
                {"request": request, "error": "Password must contain at least one number", "current_user": None}
            )
            
        # Check for at least one special character
        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
            return templates.TemplateResponse(
                "register.html",
                {"request": request, "error": "Password must contain at least one special character", "current_user": None}
            )
        
        # Check for existing email
        existing_email = db.query(User).filter(User.email == email).first()
        # Check for existing username
        existing_username = db.query(User).filter(User.username == username).first()

        errors = []
        if existing_email:
            errors.append("Email is already registered")
        if existing_username:
            errors.append("Username is already taken")

        if errors:
            return templates.TemplateResponse(
                "register.html",
                {"request": request, "error": " and ".join(errors), "current_user": None}
            )
        
        # Generate verification code
        verification_code = generate_verification_code()
        expires = datetime.utcnow() + timedelta(minutes=30)
        
        # Create new user with verification details
        user = User(
            email=email,
            username=username,
            hashed_password=User.get_password_hash(password),
            verification_code=verification_code,
            verification_code_expires=expires,
            is_active=False,
            is_verified=False
        )
        db.add(user)
        db.commit()
        
        # Send verification email
        send_verification_email(email, verification_code)
        
        # Redirect to verification page
        return RedirectResponse(
            url=f"/verify?email={email}", 
            status_code=status.HTTP_302_FOUND
        )
    except Exception as e:
        logger.error(f"Error processing registration: {str(e)}")
        db.rollback()
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": f"Registration failed: {str(e)}", "current_user": None}
        )

@app.get("/verify")
async def verify_page(request: Request, email: str):
    """Show verification page"""
    return templates.TemplateResponse(
        "verify.html", 
        {"request": request, "email": email, "current_user": None}
    )

@app.post("/verify")
async def verify_email(
    request: Request,
    code: str = Form(...),
    email: str = Form(...),
    db: Session = Depends(get_db)
):
    """Process verification code"""
    try:
        user = db.query(User).filter(User.email == email).first()
        
        if not user:
            return templates.TemplateResponse(
                "verify.html",
                {"request": request, "email": email, "error": "User not found.", "current_user": None}
            )
        
        if user.is_verified:
            # If already verified, redirect to dashboard
            return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
        
        if datetime.utcnow() > user.verification_code_expires:
            return templates.TemplateResponse(
                "verify.html",
                {"request": request, "email": email, "error": "Verification code has expired. Please request a new one.", "current_user": None}
            )
        
        if user.verification_code != code:
            return templates.TemplateResponse(
                "verify.html",
                {"request": request, "email": email, "error": "Invalid verification code.", "current_user": None}
            )
        
        # Code is valid, activate user
        user.is_verified = True
        user.is_active = True
        user.verification_code = None
        db.commit()
        
        # Create a session with server ID
        session_id = str(uuid.uuid4())
        expires = datetime.utcnow() + timedelta(days=1)  # 1 day by default
        
        try:
            db_session = DbSession(
                id=session_id,
                user_id=user.id,
                expires=expires,
                server_id=SERVER_ID
            )
        except Exception as e:
            # If server_id column doesn't exist yet, create session without it
            logger.warning(f"Could not add server_id to session: {str(e)}")
            db_session = DbSession(
                id=session_id,
                user_id=user.id,
                expires=expires
            )
            
        db.add(db_session)
        db.commit()
        
        # Redirect to dashboard with cookie
        response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
        response.set_cookie(
            key="session",
            value=session_id,
            httponly=True,
            max_age=86400,  # 24 hours
            expires=86400,
        )
        return response
    except Exception as e:
        logger.error(f"Error verifying email: {str(e)}")
        return templates.TemplateResponse(
            "verify.html",
            {"request": request, "email": email, "error": f"Verification failed: {str(e)}", "current_user": None}
        )



@app.get("/resend-verification")
async def resend_verification(
    request: Request,
    email: str,
    db: Session = Depends(get_db)
):
    """Resend verification code"""
    try:
        user = db.query(User).filter(User.email == email).first()
        
        if not user:
            return templates.TemplateResponse(
                "verify.html",
                {"request": request, "email": email, "error": "User not found.", "current_user": None}
            )
        
        if user.is_verified:
            return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
        
        # Generate new verification code
        verification_code = generate_verification_code()
        expires = datetime.utcnow() + timedelta(minutes=30)
        
        user.verification_code = verification_code
        user.verification_code_expires = expires
        db.commit()
        
        # Send new verification email
        send_verification_email(email, verification_code)
        
        return templates.TemplateResponse(
            "verify.html",
            {
                "request": request, 
                "email": email, 
                "success": "A new verification code has been sent to your email.",
                "current_user": None
            }
        )
    except Exception as e:
        logger.error(f"Error resending verification: {str(e)}")
        return templates.TemplateResponse(
            "verify.html",
            {"request": request, "email": email, "error": f"Failed to resend verification code: {str(e)}", "current_user": None}
        )

@app.get("/profile")
async def profile_page(
    request: Request, 
    current_user: User = Depends(get_current_user_with_server_check),
    message: Optional[str] = None,
    message_type: Optional[str] = None
):
    """Render profile page"""
    if not current_user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    
    return templates.TemplateResponse("profile.html", {
        "request": request,
        "current_user": current_user,
        "message": message,
        "message_type": message_type
    })


@app.post("/profile/update-user")
async def update_user_profile(
    request: Request,
    new_password: str = Form(""),
    confirm_password: str = Form(""),
    current_user: User = Depends(get_current_user_with_server_check),
    db: Session = Depends(get_db)
):
    """Update user profile"""
    if not current_user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    
    message = None
    message_type = "error"
    
    if new_password:
        if new_password != confirm_password:
            message = "Passwords do not match"
        else:
            current_user.hashed_password = User.get_password_hash(new_password)
            db.commit()
            message = "Password updated successfully"
            message_type = "success"
    
    return templates.TemplateResponse("profile.html", {
        "request": request,
        "current_user": current_user,
        "message": message,
        "message_type": message_type
    })

@app.post("/profile/update-email-settings")
async def update_email_settings(
    request: Request,
    email_address: str = Form(...),
    email_password: str = Form(""),
    imap_server: str = Form(...),
    smtp_server: str = Form(...),
    smtp_port: int = Form(...),
    current_user: User = Depends(get_current_user_with_server_check),
    db: Session = Depends(get_db)
):
    """Update email settings"""
    if not current_user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    
    try:
        current_user.email_address = email_address
        if email_password:  # Only update password if provided
            current_user.set_email_password(email_password)
        current_user.imap_server = imap_server
        current_user.smtp_server = smtp_server
        current_user.smtp_port = smtp_port
        
        db.commit()
        
        return templates.TemplateResponse("profile.html", {
            "request": request,
            "current_user": current_user,
            "message": "Email settings updated successfully",
            "message_type": "success"
        })
    except Exception as e:
        logger.error(f"Error updating email settings: {str(e)}")
        return templates.TemplateResponse("profile.html", {
            "request": request,
            "current_user": current_user,
            "message": f"Error updating email settings: {str(e)}",
            "message_type": "error"
        })

@app.post("/test-email-connection")
async def test_email_connection(
    request: Request,
    email_address: str = Form(...),
    email_password: str = Form(...),
    imap_server: str = Form(...),
    current_user: User = Depends(get_current_user_with_server_check)
):
    """Test connection to email server"""
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required"
        )
    
    try:
        # Try to connect to IMAP server
        mail = imaplib.IMAP4_SSL(imap_server)
        mail.login(email_address, email_password)
        mail.logout()
        
        return {"status": "success", "message": "Connection successful!"}
    except Exception as e:
        return {"status": "error", "message": f"Connection failed: {str(e)}"}


@app.get("/logout")
async def logout(
    request: Request,
    session: Optional[str] = Cookie(None),
    db: Session = Depends(get_db)
):
    """Log the user out by clearing the session"""
    try:
        # Delete session from database if it exists
        if session:
            db_session = db.query(DbSession).filter(DbSession.id == session).first()
            if db_session:
                db.delete(db_session)
                db.commit()
                logger.info(f"Session {session} deleted successfully")
            else:
                logger.warning(f"Session {session} not found in database")
        
        # Create response with deleted cookie
        response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
        response.delete_cookie(key="session")
        
        return response
    except Exception as e:
        logger.error(f"Error during logout: {str(e)}")
        # Even if there's an error, try to delete the cookie
        response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
        response.delete_cookie(key="session")
        return response


@app.get("/settings")
async def settings_page(
    request: Request, 
    current_user: User = Depends(get_current_user_with_server_check),
    message: Optional[str] = None,
    message_type: Optional[str] = None
):
    """Render settings page"""
    if not current_user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "current_user": current_user,
        "message": message,
        "message_type": message_type
    })

@app.post("/settings/notifications")
async def update_notification_settings(
    request: Request,
    notify_matches: bool = Form(False),
    notify_auctions: bool = Form(False),
    notify_bids: bool = Form(False),
    digest_frequency: str = Form("realtime"),
    current_user: User = Depends(get_current_user_with_server_check),
    db: Session = Depends(get_db)
):
    """Update notification settings"""
    if not current_user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    
    try:
        # Update user's notification preferences
        if hasattr(current_user, 'notify_matches'):
            current_user.notify_matches = notify_matches
            current_user.notify_auctions = notify_auctions
            current_user.notify_bids = notify_bids
            current_user.digest_frequency = digest_frequency
            
            db.commit()
            
            return templates.TemplateResponse("settings.html", {
                "request": request,
                "current_user": current_user,
                "message": "Notification settings updated successfully",
                "message_type": "success"
            })
        else:
            return templates.TemplateResponse("settings.html", {
                "request": request,
                "current_user": current_user,
                "message": "Settings fields not available yet. Please run the database migration first.",
                "message_type": "error"
            })
    except Exception as e:
        logger.error(f"Error updating notification settings: {str(e)}")
        return templates.TemplateResponse("settings.html", {
            "request": request,
            "current_user": current_user,
            "message": f"Error updating notification settings: {str(e)}",
            "message_type": "error"
        })

@app.post("/settings/display")
async def update_display_settings(
    request: Request,
    show_ais_data: bool = Form(False),
    show_vessel_history: bool = Form(False),
    default_latitude: float = Form(0),
    default_longitude: float = Form(0),
    default_zoom: int = Form(3),
    date_format: str = Form("iso"),
    current_user: User = Depends(get_current_user_with_server_check),
    db: Session = Depends(get_db)
):
    """Update display settings"""
    if not current_user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    
    try:
        # Update user's display preferences
        if hasattr(current_user, 'show_ais_data'):
            current_user.show_ais_data = show_ais_data
            current_user.show_vessel_history = show_vessel_history
            current_user.default_latitude = default_latitude
            current_user.default_longitude = default_longitude
            current_user.default_zoom = default_zoom
            current_user.date_format = date_format
            
            db.commit()
            
            return templates.TemplateResponse("settings.html", {
                "request": request,
                "current_user": current_user,
                "message": "Display settings updated successfully",
                "message_type": "success"
            })
        else:
            return templates.TemplateResponse("settings.html", {
                "request": request,
                "current_user": current_user,
                "message": "Settings fields not available yet. Please run the database migration first.",
                "message_type": "error"
            })
    except Exception as e:
        logger.error(f"Error updating display settings: {str(e)}")
        return templates.TemplateResponse("settings.html", {
            "request": request,
            "current_user": current_user,
            "message": f"Error updating display settings: {str(e)}",
            "message_type": "error"
        })

@app.post("/settings/generate-api-key")
async def generate_api_key(
    request: Request,
    current_user: User = Depends(get_current_user_with_server_check),
    db: Session = Depends(get_db)
):
    """Generate a new API key for the user"""
    if not current_user:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    
    try:
        # Generate a new API key
        if hasattr(current_user, 'api_key'):
            api_key = f"sb_{uuid.uuid4().hex}_{secrets.token_hex(16)}"
            current_user.api_key = api_key
            
            db.commit()
            
            return templates.TemplateResponse("settings.html", {
                "request": request,
                "current_user": current_user,
                "message": "API key generated successfully",
                "message_type": "success"
            })
        else:
            return templates.TemplateResponse("settings.html", {
                "request": request,
                "current_user": current_user,
                "message": "API key field not available yet. Please run the database migration first.",
                "message_type": "error"
            })
    except Exception as e:
        logger.error(f"Error generating API key: {str(e)}")
        return templates.TemplateResponse("settings.html", {
            "request": request,
            "current_user": current_user,
            "message": f"Error generating API key: {str(e)}",
            "message_type": "error"
        })


