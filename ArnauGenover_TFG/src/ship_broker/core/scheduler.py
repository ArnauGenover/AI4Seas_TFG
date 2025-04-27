# src/ship_broker/core/scheduler.py

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import logging
from datetime import datetime, timedelta
from typing import List
import asyncio  

from ..config import settings
from .email_parser import EmailParser
from .auction_service import AuctionService
from .database import SessionLocal, User

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()

def cleanup_sessions():
    """Clean up expired sessions"""
    try:
        db = SessionLocal()
        cutoff = datetime.utcnow() - timedelta(days=7)
        
        expired_sessions = db.query(db.session.Session).filter(
            db.session.Session.expires < cutoff
        ).delete()
        
        db.commit()
        logger.info(f"Cleaned up {expired_sessions} expired sessions")
    except Exception as e:
        logger.error(f"Session cleanup failed: {str(e)}")
    finally:
        db.close()

def update_auction_prices():
    """Update auction prices based on time elapsed"""
    try:
        db = SessionLocal()
        auction_service = AuctionService(db)
        auction_service.update_auction_prices()
    except Exception as e:
        logger.error(f"Auction price update failed: {str(e)}")
    finally:
        db.close()

def get_users_with_email_config() -> List[User]:
    """Get all users with email configuration"""
    db = SessionLocal()
    try:
        users = db.query(User).filter(
            User.is_active == True,
            User.email_address.isnot(None),
            User.encrypted_email_password.isnot(None)
        ).all()
        return users
    except Exception as e:
        logger.error(f"Error getting users with email config: {str(e)}")
        return []
    finally:
        db.close()

def process_all_user_emails():
    """Process emails for all configured users"""
    logger.info("Starting scheduled email processing for all users")
    
    users = get_users_with_email_config()
    logger.info(f"Found {len(users)} users with email configuration")
    
    for user in users:
        try:
            db = SessionLocal()
            
            # Get decrypted email password
            email_password = user.get_email_password()
            if not email_password:
                logger.error(f"Invalid email password encryption for user {user.id}")
                continue
            
            # Process emails for this user
            parser = EmailParser(
                email_address=user.email_address,
                password=email_password,
                db=db,
                imap_server=user.imap_server,
                user_id=user.id
            )
            
            # Get and process emails
            emails = parser.get_emails(days=1)  # Get last day's emails
            logger.info(f"Found {len(emails)} new emails for user {user.id}")
            
            for email_data in emails:
                try:
                    cargoes, vessels = parser.process_and_store_email(email_data)
                    logger.info(f"Processed email for user {user.id}: found {len(vessels)} vessels and {len(cargoes)} cargoes")
                except Exception as e:
                    logger.error(f"Error processing email for user {user.id}: {str(e)}")
            
        except Exception as e:
            logger.error(f"Error processing emails for user {user.id}: {str(e)}")
        finally:
            db.close()
    
    logger.info("Completed scheduled email processing for all users")

# Initialize scheduler jobs
# Check emails every 5 minutes (configurable in settings)
scheduler.add_job(
    process_all_user_emails,
    IntervalTrigger(minutes=settings.EMAIL_CHECK_INTERVAL),
    id='process_emails',
    name='Process all user emails',
    replace_existing=True
)

# Update auction prices every hour
scheduler.add_job(
    update_auction_prices,
    IntervalTrigger(hours=1),
    id='update_prices',
    name='Update auction prices',
    replace_existing=True
)

# Clean up expired sessions daily
scheduler.add_job(
    cleanup_sessions,
    IntervalTrigger(days=1),
    id='cleanup_sessions',
    name='Clean up expired sessions',
    replace_existing=True
)

# Add start_scheduler function for compatibility with main.py
async def start_scheduler():
    """Start the background scheduler"""
    scheduler.start()
    logger.info("Background scheduler started")
    
    # Keep the task alive
    while True:
        await asyncio.sleep(60)  # Keep the task alive