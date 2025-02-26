# src/ship_broker/core/email_service.py

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import logging
import random
import string
from datetime import datetime, timedelta
from ..config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

def generate_verification_code(length=6):
    """Generate a random verification code"""
    return ''.join(random.choices(string.digits, k=length))

def send_verification_email(user_email, verification_code):
    """Send verification email to user"""
    try:
        message = MIMEMultipart("alternative")
        message["Subject"] = "Ship Broker - Verify Your Email"
        
        # Set the From field to hide your real email address
        message["From"] = "Ship Broker <no-reply@shipbroker.com>"
        
        # Make it clear this is a no-reply address
        message["Reply-To"] = "no-reply@shipbroker.com"
        message["To"] = user_email
        
        
        # Create the plain-text and HTML version of your message
        text = f"""
        Hello,
        
        Thank you for registering with Ship Broker.
        Your verification code is: {verification_code}
        
        This code will expire in 30 minutes.
        
        Best regards,
        Ship Broker Team
        """
        
        html = f"""
        <html>
          <body>
            <h2>Welcome to Ship Broker!</h2>
            <p>Thank you for registering with our service.</p>
            <p>Your verification code is: <strong>{verification_code}</strong></p>
            <p>This code will expire in 30 minutes.</p>
            <br>
            <p>Best regards,</p>
            <p>Ship Broker Team</p>
          </body>
        </html>
        """
        
        # Turn these into plain/html MIMEText objects
        part1 = MIMEText(text, "plain")
        part2 = MIMEText(html, "html")
        
        # Add HTML/plain-text parts to MIMEMultipart message
        message.attach(part1)
        message.attach(part2)
        
        # Send email
        with smtplib.SMTP_SSL(settings.SMTP_SERVER, settings.SMTP_PORT) as server:
            server.login(settings.EMAIL_ADDRESS, settings.EMAIL_PASSWORD)
            server.sendmail(
                settings.EMAIL_ADDRESS, user_email, message.as_string()
            )
        
        logger.info(f"Verification email sent to {user_email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send verification email: {str(e)}")
        return False