import os
import json
import asyncio
import logging
from typing import Dict, List, Optional
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, 
    ConversationHandler, MessageHandler, filters, ContextTypes
)

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration - Fixed tokens and settings
TELEGRAM_BOT_TOKEN = "8125768320:AAGj35QQfAM0mvxjNDP_PlbrjQaPzNTZYkY"
CLOUDFLARE_API_TOKEN = "kLC0cg3GbAPQ5L-gDrKzxQii88h7dbV-zE1q2a3I"  # Fixed the missing quote
CLOUDFLARE_API_BASE = "https://api.cloudflare.com/client/v4"
OWNER_ID = 7187126565
OWNER_EMAIL = "nihalchakradhri9@gmail.com"
MAX_DOMAINS_PER_USER = 15
PORT = int(os.environ.get("PORT", 8080))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://subdomain-behq.onrender.com")

# Email configuration (optional - for notifications)
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

# Conversation states
WAITING_FOR_SUBDOMAIN_NAME, WAITING_FOR_IP_ADDRESS, WAITING_FOR_MODIFY_IP = range(3)

class CloudflareBot:
    def __init__(self):
        self.approved_users = set()
        self.user_domains = {}  # {user_id: {domain_name: [record_data]}}
        self.available_domains = []
        self.zone_ids = {}  # {domain: zone_id}
        self.user_records = {}  # {user_id: {record_id: {domain, name, type, content}}}
        
    async def send_email_notification(self, subject: str, body: str):
        """Send email notification to owner"""
        try:
            msg = MIMEMultipart()
            msg['From'] = OWNER_EMAIL
            msg['To'] = OWNER_EMAIL
            msg['Subject'] = subject
            
            msg.attach(MIMEText(body, 'plain'))
            
            # Note: You'll need to set up app password for Gmail
            # This is optional and requires email setup
            logger.info(f"Email notification: {subject}")
            
        except Exception as e:
            logger.error(f"Failed to send email: {e}")
        
    async def fetch_domains(self):
        """Fetch available domains from Cloudflare with retry logic"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                headers = {
                    "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
                    "Content-Type": "application/json"
                }
                
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                    async with session.get(f"{CLOUDFLARE_API_BASE}/zones", headers=headers) as response:
                        data = await response.json()
                        
                        if data.get("success"):
                            self.available_domains = []
                            self.zone_ids = {}
                            
                            for zone in data["result"]:
                                domain_name = zone["name"]
                                zone_id = zone["id"]
                                self.available_domains.append(domain_name)
                                self.zone_ids[domain_name] = zone_id
                                
                            logger.info(f"Fetched {len(self.available_domains)} domains")
                            return True
                        else:
                            logger.error(f"Failed to fetch domains: {data}")
                            
            except Exception as e:
                logger.error(f"Error fetching domains (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff
                    
        return False
    
    async def create_dns_record(self, domain: str, name: str, content: str, record_type: str = "A"):
        """Create DNS record in Cloudflare with enhanced error handling"""
        try:
            zone_id = self.zone_ids.get(domain)
            if not zone_id:
                return False, "Domain not found", None
            
            headers = {
                "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
                "Content-Type": "application/json"
            }
            
            # Determine full record name
            full_name = f"{name}.{domain}" if name != "@" else domain
            
            record_data = {
                "type": record_type,
                "name": full_name,
                "content": content,
                "ttl": 3600,  # 1 hour
                "proxied": False
            }
            
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.post(
                    f"{CLOUDFLARE_API_BASE}/zones/{zone_id}/dns_records",
                    headers=headers,
                    json=record_data
                ) as response:
                    data = await response.json()
                    
                    if data.get("success"):
                        record_id = data["result"]["id"]
                        record_info = {
                            "id": record_id,
                            "domain": domain,
                            "name": full_name,
                            "type": record_type,
                            "content": content
                        }
                        return True, "Record created successfully", record_info
                    else:
                        error_msg = data.get("errors", [{}])[0].get("message", "Unknown error")
                        return False, error_msg, None
                        
        except asyncio.TimeoutError:
            return False, "Request timeout", None
        except Exception as e:
            logger.error(f"Error creating DNS record: {e}")
            return False, str(e), None
    
    async def delete_dns_record(self, domain: str, record_id: str):
        """Delete DNS record from Cloudflare"""
        try:
            zone_id = self.zone_ids.get(domain)
            if not zone_id:
                return False, "Domain not found"
            
            headers = {
                "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
                "Content-Type": "application/json"
            }
            
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.delete(
                    f"{CLOUDFLARE_API_BASE}/zones/{zone_id}/dns_records/{record_id}",
                    headers=headers
                ) as response:
                    data = await response.json()
                    
                    return data.get("success", False), data
                    
        except Exception as e:
            logger.error(f"Error deleting DNS record: {e}")
            return False, str(e)
    
    async def update_dns_record(self, domain: str, record_id: str, new_content: str):
        """Update DNS record content"""
        try:
            zone_id = self.zone_ids.get(domain)
            if not zone_id:
                return False, "Domain not found"
            
            headers = {
                "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
                "Content-Type": "application/json"
            }
            
            # First get the current record to preserve other fields
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(
                    f"{CLOUDFLARE_API_BASE}/zones/{zone_id}/dns_records/{record_id}",
                    headers=headers
                ) as response:
                    data = await response.json()
                    
                    if not data.get("success"):
                        return False, "Record not found"
                    
                    current_record = data["result"]
                    
                # Update the record
                update_data = {
                    "type": current_record["type"],
                    "name": current_record["name"],
                    "content": new_content,
                    "ttl": current_record["ttl"],
                    "proxied": current_record["proxied"]
                }
                
                async with session.put(
                    f"{CLOUDFLARE_API_BASE}/zones/{zone_id}/dns_records/{record_id}",
                    headers=headers,
                    json=update_data
                ) as response:
                    data = await response.json()
                    
                    return data.get("success", False), data
                    
        except Exception as e:
            logger.error(f"Error updating DNS record: {e}")
            return False, str(e)
    
    def add_user_record(self, user_id: int, record_info: dict):
        """Add record to user's tracking"""
        if user_id not in self.user_records:
            self.user_records[user_id] = {}
        
        record_id = record_info["id"]
        self.user_records[user_id][record_id] = {
            "domain": record_info["domain"],
            "name": record_info["name"],
            "type": record_info["type"],
            "content": record_info["content"]
        }
    
    def remove_user_record(self, user_id: int, record_id: str):
        """Remove record from user's tracking"""
        if user_id in self.user_records and record_id in self.user_records[user_id]:
            del self.user_records[user_id][record_id]
    
    def get_user_records(self, user_id: int):
        """Get all records for a user"""
        return self.user_records.get(user_id, {})
    
    def user_owns_record(self, user_id: int, record_id: str):
        """Check if user owns a specific record"""
        return user_id in self.user_records and record_id in self.user_records[user_id]

# Initialize bot instance
bot_instance = CloudflareBot()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"
    
    # Auto-approve owner
    if user_id == OWNER_ID:
        bot_instance.approved_users.add(user_id)
    
    if user_id not in bot_instance.approved_users:
        await update.message.reply_text(
            f"❌ You are not approved to use this bot.\n"
            f"Your User ID: {user_id}\n"
            f"Contact the administrator for approval."
        )
        # Notify owner of new user
        try:
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=f"🔔 New user requested access:\n"
                     f"User ID: {user_id}\n"
                     f"Username: @{username}\n"
                     f"Use /approve {user_id} to approve"
            )
        except:
            pass
        return
    
    keyboard = [
        [InlineKeyboardButton("➕ Add Domain", callback_data="add_domain")],
        [InlineKeyboardButton("🗑️ Remove Domain", callback_data="remove_domain")],
        [InlineKeyboardButton("✏️ Modify Domain", callback_data="modify_domain")],
        [InlineKeyboardButton("📋 My Domains", callback_data="list_domains")]
    ]
    
    if user_id == OWNER_ID:
        keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"🌐 Welcome to Cloudflare DNS Manager Bot!\n\n"
        f"👋 Hello @{username}!\n"
        f"Choose an option below:",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard button presses"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    if user_id not in bot_instance.approved_users and user_id != OWNER_ID:
        await query.edit_message_text("❌ You are not approved to use this bot.")
        return
    
    if data == "add_domain":
        await show_domain_selection(query, context, "add")
    elif data == "remove_domain":
        await show_user_domains_for_removal(query, context)
    elif data == "modify_domain":
        await show_user_domains_for_modification(query, context)
    elif data == "list_domains":
        await show_user_domains(query, context)
    elif data == "admin_panel" and user_id == OWNER_ID:
        await show_admin_panel(query, context)
    elif data == "back_to_main":
        await show_main_menu(query, context)
    elif data.startswith("select_domain_"):
        domain = data.split("select_domain_")[1]
        context.user_data["selected_domain"] = domain
        await query.edit_message_text(
            f"🌐 Selected domain: **{domain}**\n\n"
            f"Please enter the subdomain name:\n"
            f"• Use @ for root domain\n"
            f"• Use * for wildcard\n"
            f"• Or enter any subdomain name (e.g., www, api, blog)"
        )
        return WAITING_FOR_SUBDOMAIN_NAME
    elif data.startswith("remove_record_"):
        await handle_remove_record(query, context)
    elif data.startswith("modify_record_"):
        await handle_modify_record(query, context)
    elif data.startswith("type_"):
        await handle_record_type_selection(query, context)

async def show_main_menu(query, context):
    """Show main menu"""
    user_id = query.from_user.id
    username = query.from_user.username or "Unknown"
    
    keyboard = [
        [InlineKeyboardButton("➕ Add Domain", callback_data="add_domain")],
        [InlineKeyboardButton("🗑️ Remove Domain", callback_data="remove_domain")],
        [InlineKeyboardButton("✏️ Modify Domain", callback_data="modify_domain")],
        [InlineKeyboardButton("📋 My Domains", callback_data="list_domains")]
    ]
    
    if user_id == OWNER_ID:
        keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"🌐 Cloudflare DNS Manager Bot\n\n"
        f"👋 Hello @{username}!\n"
        f"Choose an option below:",
        reply_markup=reply_markup
    )

async def show_domain_selection(query, context, action):
    """Show available domains for selection"""
    success = await bot_instance.fetch_domains()
    
    if not success or not bot_instance.available_domains:
        keyboard = [[InlineKeyboardButton("🔄 Retry", callback_data="add_domain")],
                   [InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "❌ Failed to fetch domains from Cloudflare.\n"
            "This might be due to:\n"
            "• Invalid API token\n"
            "• Network connectivity issues\n"
            "• Cloudflare API being down",
            reply_markup=reply_markup
        )
        return
    
    keyboard = []
    for domain in bot_instance.available_domains:
        keyboard.append([InlineKeyboardButton(f"🌐 {domain}", callback_data=f"select_domain_{domain}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"🌐 Select a domain to add subdomain:\n\n"
        f"Available domains: {len(bot_instance.available_domains)}",
        reply_markup=reply_markup
    )

async def show_user_domains(query, context):
    """Show domains created by user"""
    user_id = query.from_user.id
    user_records = bot_instance.get_user_records(user_id)
    
    if not user_records:
        keyboard = [[InlineKeyboardButton("➕ Add First Domain", callback_data="add_domain")],
                   [InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "📋 You haven't created any domains yet.\n"
            "Click 'Add First Domain' to get started!",
            reply_markup=reply_markup
        )
        return
    
    message = f"📋 **Your DNS Records** ({len(user_records)} total):\n\n"
    
    for record_id, record_info in user_records.items():
        message += (f"🌐 **{record_info['name']}**\n"
                   f"   Type: {record_info['type']}\n"
                   f"   Points to: {record_info['content']}\n"
                   f"   Domain: {record_info['domain']}\n\n")
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup)

async def show_user_domains_for_removal(query, context):
    """Show user domains with remove buttons"""
    user_id = query.from_user.id
    user_records = bot_instance.get_user_records(user_id)
    
    if not user_records:
        await query.edit_message_text("📋 You haven't created any domains yet.")
        return
    
    keyboard = []
    for record_id, record_info in user_records.items():
        button_text = f"🗑️ {record_info['name']} ({record_info['type']})"
        callback_data = f"remove_record_{record_id}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "🗑️ Select a record to remove:",
        reply_markup=reply_markup
    )

async def show_user_domains_for_modification(query, context):
    """Show user domains with modify buttons"""
    user_id = query.from_user.id
    user_records = bot_instance.get_user_records(user_id)
    
    if not user_records:
        await query.edit_message_text("📋 You haven't created any domains yet.")
        return
    
    keyboard = []
    for record_id, record_info in user_records.items():
        button_text = f"✏️ {record_info['name']} ({record_info['type']})"
        callback_data = f"modify_record_{record_id}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "✏️ Select a record to modify:",
        reply_markup=reply_markup
    )

async def show_admin_panel(query, context):
    """Show admin panel for owner"""
    total_users = len(bot_instance.approved_users)
    total_records = sum(len(records) for records in bot_instance.user_records.values())
    
    keyboard = [
        [InlineKeyboardButton("📊 Detailed Stats", callback_data="detailed_stats")],
        [InlineKeyboardButton("👥 List Users", callback_data="list_users")],
        [InlineKeyboardButton("🔄 Refresh Domains", callback_data="refresh_domains")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"👑 **Admin Panel**\n\n"
        f"📊 **Quick Stats:**\n"
        f"• Approved Users: {total_users}\n"
        f"• Total Records: {total_records}\n"
        f"• Available Domains: {len(bot_instance.available_domains)}\n\n"
        f"**Commands:**\n"
        f"• /approve <user_id> - Approve user\n"
        f"• /unapprove <user_id> - Remove approval\n"
        f"• /broadcast <message> - Send message to all users",
        reply_markup=reply_markup
    )

async def handle_remove_record(query, context):
    """Handle record removal"""
    user_id = query.from_user.id
    record_id = query.data.split("remove_record_")[1]
    
    # Check if user owns this record
    if not bot_instance.user_owns_record(user_id, record_id):
        await query.edit_message_text("❌ You don't own this record.")
        return
    
    record_info = bot_instance.user_records[user_id][record_id]
    success, result = await bot_instance.delete_dns_record(record_info["domain"], record_id)
    
    if success:
        # Remove from user's records
        bot_instance.remove_user_record(user_id, record_id)
        
        await query.edit_message_text(
            f"✅ **Record deleted successfully!**\n\n"
            f"🗑️ Deleted: {record_info['name']}\n"
            f"📍 Was pointing to: {record_info['content']}"
        )
        
        # Send notification
        await bot_instance.send_email_notification(
            "DNS Record Deleted",
            f"User {user_id} deleted DNS record: {record_info['name']}"
        )
    else:
        await query.edit_message_text(f"❌ Failed to delete record: {result}")

async def handle_modify_record(query, context):
    """Handle record modification"""
    user_id = query.from_user.id
    record_id = query.data.split("modify_record_")[1]
    
    # Check if user owns this record
    if not bot_instance.user_owns_record(user_id, record_id):
        await query.edit_message_text("❌ You don't own this record.")
        return
    
    record_info = bot_instance.user_records[user_id][record_id]
    context.user_data["modify_record_id"] = record_id
    context.user_data["modify_record_info"] = record_info
    
    await query.edit_message_text(
        f"✏️ **Modifying Record**\n\n"
        f"🌐 Domain: {record_info['name']}\n"
        f"📍 Current IP: {record_info['content']}\n\n"
        f"Enter the new IP address:"
    )
    return WAITING_FOR_MODIFY_IP

async def handle_record_type_selection(query, context):
    """Handle record type selection"""
    record_type = query.data.split("type_")[1]
    context.user_data["record_type"] = record_type
    
    content_type = {
        "A": "IPv4 address (e.g., 192.168.1.1)",
        "AAAA": "IPv6 address (e.g., 2001:db8::1)",
        "CNAME": "target domain (e.g., example.com)"
    }.get(record_type, "content")
    
    await query.edit_message_text(f"📝 Enter the {content_type}:")
    return WAITING_FOR_IP_ADDRESS

async def subdomain_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle subdomain name input"""
    subdomain_name = update.message.text.strip()
    
    # Validate subdomain name
    if not subdomain_name or len(subdomain_name) > 63:
        await update.message.reply_text(
            "❌ Invalid subdomain name. Please enter a valid name (1-63 characters)."
        )
        return WAITING_FOR_SUBDOMAIN_NAME
    
    context.user_data["subdomain_name"] = subdomain_name
    
    # Show record type selection
    keyboard = [
        [InlineKeyboardButton("🌐 A Record (IPv4)", callback_data="type_A")],
        [InlineKeyboardButton("🌍 AAAA Record (IPv6)", callback_data="type_AAAA")],
        [InlineKeyboardButton("🔗 CNAME Record", callback_data="type_CNAME")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"📝 **Subdomain:** {subdomain_name}\n\n"
        f"Select record type:",
        reply_markup=reply_markup
    )

async def ip_address_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle IP address/content input for new records"""
    user_id = update.effective_user.id
    content = update.message.text.strip()
    
    domain = context.user_data.get("selected_domain")
    subdomain_name = context.user_data.get("subdomain_name")
    record_type = context.user_data.get("record_type", "A")
    
    if not domain or not subdomain_name:
        await update.message.reply_text("❌ Missing information. Please start over with /start")
        context.user_data.clear()
        return ConversationHandler.END
    
    # Check user's domain limit
    user_record_count = len(bot_instance.get_user_records(user_id))
    if user_record_count >= MAX_DOMAINS_PER_USER:
        await update.message.reply_text(
            f"❌ You've reached the maximum limit of {MAX_DOMAINS_PER_USER} records.\n"
            f"Please remove some records before adding new ones."
        )
        context.user_data.clear()
        return ConversationHandler.END
    
    # Create the DNS record
    success, message, record_info = await bot_instance.create_dns_record(
        domain, subdomain_name, content, record_type
    )
    
    if success:
        # Track user's record
        bot_instance.add_user_record(user_id, record_info)
        
        full_domain = record_info["name"]
        await update.message.reply_text(
            f"✅ **DNS Record Created Successfully!**\n\n"
            f"🌐 **Domain:** {full_domain}\n"
            f"📍 **Points to:** {content}\n"
            f"🔧 **Type:** {record_type}\n"
            f"⏱️ **TTL:** 1 hour\n"
            f"🚫 **Cloudflare Proxy:** Disabled\n\n"
            f"🎉 Your domain is now live and should propagate within a few minutes!"
        )
        
        # Send notification to owner
        await bot_instance.send_email_notification(
            "New DNS Record Created",
            f"User {user_id} created DNS record: {full_domain} -> {content}"
        )
        
    else:
        await update.message.reply_text(f"❌ **Failed to create DNS record**\n\nError: {message}")
    
    # Clear user data
    context.user_data.clear()
    return ConversationHandler.END

async def modify_ip_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle IP modification"""
    user_id = update.effective_user.id
    new_content = update.message.text.strip()
    
    record_id = context.user_data.get("modify_record_id")
    record_info = context.user_data.get("modify_record_info")
    
    if not record_id or not record_info:
        await update.message.reply_text("❌ Missing information. Please start over.")
        context.user_data.clear()
        return ConversationHandler.END
    
    # Update the DNS record
    success, result = await bot_instance.update_dns_record(
        record_info["domain"], record_id, new_content
    )
    
    if success:
        # Update user's record tracking
        bot_instance.user_records[user_id][record_id]["content"] = new_content
        
        await update.message.reply_text(
            f"✅ **Record Updated Successfully!**\n\n"
            f"🌐 **Domain:** {record_info['name']}\n"
            f"📍 **Old IP:** {record_info['content']}\n"
            f"📍 **New IP:** {new_content}\n\n"
            f"🎉 Changes should propagate within a few minutes!"
        )
        
        # Send notification
        await bot_instance.send_email_notification(
            "DNS Record Modified",
            f"User {user_id} modified DNS record: {record_info['name']} from {record_info['content']} to {new_content}"
        )
    else:
        await update.message.reply_text(f"❌ **Failed to update record**\n\nError: {result}")
    
    # Clear user data
    context.user_data.clear()
    return ConversationHandler.END

async def approve_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Approve user command"""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Only the owner can approve users.")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Please provide a user ID: /approve <user_id>")
        return
    
    try:
        user_id = int(context.args[0])
        bot_instance.approved_users.add(user_id)
        await update.message.reply_text(f"✅ User {user_id} has been approved.")
        
        # Try to notify the user
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="🎉 Congratulations! You have been approved to use the Cloudflare DNS Manager Bot.\n\nUse /start to begin managing your DNS records."
            )
        except:
            pass
            
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")

async def unapprove_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unapprove user command"""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Only the owner can unapprove users.")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Please provide a user ID: /unapprove <user_id>")
        return
    
    try:
        user_id = int(context.args[0])
        bot_instance.approved_users.discard(user_id)
        await update.message.reply_text(f"✅ User {user_id} has been unapproved.")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel conversation"""
    await update.message.reply_text("❌ Operation cancelled. Use /start to return to the main menu.")
    context.user_data.clear()
    return ConversationHandler.END

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command"""
    help_text = """
🌐 **Cloudflare DNS Manager Bot Help**

**Available Commands:**
• /start - Start the bot and show main menu
• /help - Show this help message
• /cancel - Cancel current operation

**Features:**
• ➕ Add DNS records (A, AAAA, CNAME)
• 🗑️ Remove your DNS records
• ✏️ Modify existing records
• 📋 View all your records

**Record Types:**
• **A Record** - Points to IPv4 address
• **AAAA Record** - Points to IPv6 address  
• **CNAME Record** - Points to another domain

**Limits:**
• Maximum {MAX_DOMAINS_PER_USER} records per user
• Records are tracked per user
• Only approved users can use the bot

**Support:**
Contact the administrator for approval or issues.
    """.format(MAX_DOMAINS_PER_USER=MAX_DOMAINS_PER_USER)
    
    await update.message.reply_text(help_text)

def main():
    """Main function to run the bot"""
    try:
        # Create application
        application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
        
        # Conversation handler for domain creation
        conv_handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(button_handler, pattern="^select_domain_")],
            states={
                WAITING_FOR_SUBDOMAIN_NAME: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, subdomain_name_handler)
                ],
                WAITING_FOR_IP_ADDRESS: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, ip_address_handler),
                    CallbackQueryHandler(handle_record_type_selection, pattern="^type_")
                ],
                WAITING_FOR_MODIFY_IP: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, modify_ip_handler)
                ]
            },
            fallbacks=[CommandHandler("cancel", cancel)],
            allow_reentry=True
        )
        
        # Add handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("approve", approve_user))
        application.add_handler(CommandHandler("unapprove", unapprove_user))
        application.add_handler(conv_handler)
        application.add_handler(CallbackQueryHandler(button_handler))
        
        logger.info("Bot starting up...")
        
        # Start the bot with webhook
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_BOT_TOKEN}"
        )
        
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        raise

if __name__ == "__main__":
    main()
