import asyncio
import smtplib
from email.message import EmailMessage
from app.config import settings

def _send(address:str, subject:str, body:str):
    msg=EmailMessage()
    msg['From']=settings.mail_from
    msg['To']=address
    msg['Subject']=subject
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host,settings.smtp_port,timeout=5) as smtp:
        smtp.send_message(msg)

async def send_mail(address:str, subject:str, body:str):
    await asyncio.to_thread(_send,address,subject,body)
