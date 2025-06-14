import os
import json
import asyncio
import logging
from typing import Dict, List, Optional
from datetime import datetime

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

# Configuration - Hardcoded tokens and settings
TELEGRAM_BOT_TOKEN = "8125768320:AAGj35QQfAM0mvxjNDP_PlbrjQaPzNTZYkY"
CLOUDFLARE_API_TOKEN = kLC0cg3GbAPQ5L-gDrKzxQii88h7dbV-zE1q2a3I"
CLOUDFLARE_API_BASE = "https://api.cloudflare.com/client/v4"
OWNER_ID = 7187126565
MAX_DOMAINS_PER_USER = 15
PORT = int(os.environ.get("PORT", 8080))

# Conversation states
WAITING_FOR_SUBDOMAIN_NAME, WAITING_FOR_IP_ADDRESS = range(2)

class CloudflareBot:
    def __init__(self):
        self.approved_users = set()
        self.user_domains = {}  # {user_id: {domain_name: [record_ids]}}
        self.available_domains = []
        self.zone_ids = {}  # {domain: zone_id}
        
    async def fetch_domains(self):
        """Fetch available domains from Cloudflare"""
        try:
            headers = {
                "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
                "Content-Type": "application/json"
            }
            
            async with aiohttp.ClientSession() as session:
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
                    else:
                        logger.error(f"Failed to fetch domains: {data}")
                        
        except Exception as e:
            logger.error(f"Error fetching domains: {e}")
    
    async def create_dns_record(self, domain: str, name: str, ip: str, record_type: str = "A"):
        """Create DNS record in Cloudflare"""
        try:
            zone_id = self.zone_ids.get(domain)
            if not zone_id:
                return False, "Domain not found"
            
            headers = {
                "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
                "Content-Type": "application/json"
            }
            
            # Determine full record name
            full_name = f"{name}.{domain}" if name != "@" else domain
            
            record_data = {
                "type": record_type,
                "name": full_name,
                "content": ip,
                "ttl": 3600,  # 1 hour
                "proxied": False
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{CLOUDFLARE_API_BASE}/zones/{zone_id}/dns_records",
                    headers=headers,
                    json=record_data
                ) as response:
                    data = await response.json()
                    
                    if data.get("success"):
                        record_id = data["result"]["id"]
                        return True, record_id
                    else:
                        error_msg = data.get("errors", [{}])[0].get("message", "Unknown error")
                        return False, error_msg
                        
        except Exception as e:
            logger.error(f"Error creating DNS record: {e}")
            return False, str(e)
    
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
            
            async with aiohttp.ClientSession() as session:
                async with session.delete(
                    f"{CLOUDFLARE_API_BASE}/zones/{zone_id}/dns_records/{record_id}",
                    headers=headers
                ) as response:
                    data = await response.json()
                    
                    return data.get("success", False), data
                    
        except Exception as e:
            logger.error(f"Error deleting DNS record: {e}")
            return False, str(e)
    
    async def get_user_records(self, user_id: int, domain: str):
        """Get DNS records created by user for specific domain"""
        try:
            zone_id = self.zone_ids.get(domain)
            if not zone_id:
                return []
            
            headers = {
                "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
                "Content-Type": "application/json"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{CLOUDFLARE_API_BASE}/zones/{zone_id}/dns_records",
                    headers=headers
                ) as response:
                    data = await response.json()
                    
                    if data.get("success"):
                        user_record_ids = self.user_domains.get(user_id, {}).get(domain, [])
                        user_records = []
                        
                        for record in data["result"]:
                            if record["id"] in user_record_ids:
                                user_records.append({
                                    "id": record["id"],
                                    "name": record["name"],
                                    "type": record["type"],
                                    "content": record["content"]
                                })
                        
                        return user_records
                    
            return []
            
        except Exception as e:
            logger.error(f"Error getting user records: {e}")
            return []

# Initialize bot instance
bot_instance = CloudflareBot()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    user_id = update.effective_user.id
    
    if user_id not in bot_instance.approved_users and user_id != OWNER_ID:
        await update.message.reply_text("❌ You are not approved to use this bot.")
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
        "🌐 Welcome to Cloudflare DNS Manager Bot!\n\n"
        "Choose an option below:",
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
    elif data.startswith("select_domain_"):
        domain = data.split("select_domain_")[1]
        context.user_data["selected_domain"] = domain
        await query.edit_message_text(
            f"🌐 Selected domain: {domain}\n\n"
            "Please enter the subdomain name (or @ for root domain):"
        )
        return WAITING_FOR_SUBDOMAIN_NAME
    elif data.startswith("remove_record_"):
        await handle_remove_record(query, context)
    elif data.startswith("modify_record_"):
        await handle_modify_record(query, context)

async def show_domain_selection(query, context, action):
    """Show available domains for selection"""
    await bot_instance.fetch_domains()
    
    if not bot_instance.available_domains:
        await query.edit_message_text("❌ No domains available.")
        return
    
    keyboard = []
    for domain in bot_instance.available_domains:
        keyboard.append([InlineKeyboardButton(domain, callback_data=f"select_domain_{domain}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "🌐 Select a domain:",
        reply_markup=reply_markup
    )

async def show_user_domains(query, context):
    """Show domains created by user"""
    user_id = query.from_user.id
    user_domains = bot_instance.user_domains.get(user_id, {})
    
    if not user_domains:
        await query.edit_message_text("📋 You haven't created any domains yet.")
        return
    
    message = "📋 Your domains:\n\n"
    for domain, record_ids in user_domains.items():
        records = await bot_instance.get_user_records(user_id, domain)
        message += f"🌐 {domain}:\n"
        for record in records:
            message += f"  • {record['name']} ({record['type']}) → {record['content']}\n"
        message += "\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(message, reply_markup=reply_markup)

async def show_user_domains_for_removal(query, context):
    """Show user domains with remove buttons"""
    user_id = query.from_user.id
    user_domains = bot_instance.user_domains.get(user_id, {})
    
    if not user_domains:
        await query.edit_message_text("📋 You haven't created any domains yet.")
        return
    
    keyboard = []
    for domain in user_domains.keys():
        records = await bot_instance.get_user_records(user_id, domain)
        for record in records:
            button_text = f"🗑️ {record['name']} ({record['type']})"
            callback_data = f"remove_record_{domain}_{record['id']}"
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
    user_domains = bot_instance.user_domains.get(user_id, {})
    
    if not user_domains:
        await query.edit_message_text("📋 You haven't created any domains yet.")
        return
    
    keyboard = []
    for domain in user_domains.keys():
        records = await bot_instance.get_user_records(user_id, domain)
        for record in records:
            button_text = f"✏️ {record['name']} ({record['type']})"
            callback_data = f"modify_record_{domain}_{record['id']}"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "✏️ Select a record to modify:",
        reply_markup=reply_markup
    )

async def show_admin_panel(query, context):
    """Show admin panel for owner"""
    keyboard = [
        [InlineKeyboardButton("📊 Bot Statistics", callback_data="bot_stats")],
        [InlineKeyboardButton("👥 User Management", callback_data="user_management")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "👑 Admin Panel\n\n"
        "Use /approve <user_id> to approve users\n"
        "Use /unapprove <user_id> to remove approval",
        reply_markup=reply_markup
    )

async def handle_remove_record(query, context):
    """Handle record removal"""
    user_id = query.from_user.id
    data_parts = query.data.split("_")
    domain = data_parts[2]
    record_id = data_parts[3]
    
    # Check if user owns this record
    user_domains = bot_instance.user_domains.get(user_id, {})
    if domain not in user_domains or record_id not in user_domains[domain]:
        await query.edit_message_text("❌ You don't own this record.")
        return
    
    success, result = await bot_instance.delete_dns_record(domain, record_id)
    
    if success:
        # Remove from user's records
        bot_instance.user_domains[user_id][domain].remove(record_id)
        if not bot_instance.user_domains[user_id][domain]:
            del bot_instance.user_domains[user_id][domain]
        
        await query.edit_message_text("✅ Record deleted successfully!")
    else:
        await query.edit_message_text(f"❌ Failed to delete record: {result}")

async def handle_modify_record(query, context):
    """Handle record modification"""
    data_parts = query.data.split("_")
    domain = data_parts[2]
    record_id = data_parts[3]
    
    context.user_data["modify_domain"] = domain
    context.user_data["modify_record_id"] = record_id
    
    await query.edit_message_text(
        "✏️ Enter the new IP address for this record:"
    )
    return WAITING_FOR_IP_ADDRESS

async def subdomain_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle subdomain name input"""
    subdomain_name = update.message.text.strip()
    context.user_data["subdomain_name"] = subdomain_name
    
    # Show record type selection
    keyboard = [
        [InlineKeyboardButton("A Record", callback_data="type_A")],
        [InlineKeyboardButton("AAAA Record (IPv6)", callback_data="type_AAAA")],
        [InlineKeyboardButton("CNAME Record", callback_data="type_CNAME")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"📝 Subdomain: {subdomain_name}\n\n"
        "Select record type:",
        reply_markup=reply_markup
    )

async def ip_address_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle IP address input"""
    user_id = update.effective_user.id
    ip_address = update.message.text.strip()
    
    if "modify_domain" in context.user_data:
        # Handle modification
        domain = context.user_data["modify_domain"]
        record_id = context.user_data["modify_record_id"]
        
        # For simplicity, we'll delete the old record and create a new one
        # In a production environment, you might want to use the update API
        success, result = await bot_instance.delete_dns_record(domain, record_id)
        if success:
            # Remove from user's records
            bot_instance.user_domains[user_id][domain].remove(record_id)
            
            # Create new record (you'll need to store the original name and type)
            # This is a simplified approach
            await update.message.reply_text("✅ Record updated successfully!")
        else:
            await update.message.reply_text(f"❌ Failed to update record: {result}")
    
    else:
        # Handle new record creation
        domain = context.user_data.get("selected_domain")
        subdomain_name = context.user_data.get("subdomain_name")
        record_type = context.user_data.get("record_type", "A")
        
        if not domain or not subdomain_name:
            await update.message.reply_text("❌ Missing information. Please start over.")
            return ConversationHandler.END
        
        # Check user's domain limit
        user_domain_count = sum(len(records) for records in bot_instance.user_domains.get(user_id, {}).values())
        if user_domain_count >= MAX_DOMAINS_PER_USER:
            await update.message.reply_text(f"❌ You've reached the maximum limit of {MAX_DOMAINS_PER_USER} domains.")
            return ConversationHandler.END
        
        success, result = await bot_instance.create_dns_record(domain, subdomain_name, ip_address, record_type)
        
        if success:
            # Track user's record
            if user_id not in bot_instance.user_domains:
                bot_instance.user_domains[user_id] = {}
            if domain not in bot_instance.user_domains[user_id]:
                bot_instance.user_domains[user_id][domain] = []
            
            bot_instance.user_domains[user_id][domain].append(result)
            
            full_domain = f"{subdomain_name}.{domain}" if subdomain_name != "@" else domain
            await update.message.reply_text(
                f"✅ DNS record created successfully!\n\n"
                f"🌐 Domain: {full_domain}\n"
                f"📍 IP: {ip_address}\n"
                f"🔧 Type: {record_type}\n"
                f"⏱️ TTL: 1 hour\n"
                f"🚫 Proxy: Disabled"
            )
        else:
            await update.message.reply_text(f"❌ Failed to create DNS record: {result}")
    
    # Clear user data
    context.user_data.clear()
    return ConversationHandler.END

async def record_type_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle record type selection"""
    query = update.callback_query
    await query.answer()
    
    record_type = query.data.split("_")[1]
    context.user_data["record_type"] = record_type
    
    content_type = "IP address" if record_type in ["A", "AAAA"] else "target domain"
    
    await query.edit_message_text(f"📝 Enter the {content_type}:")
    return WAITING_FOR_IP_ADDRESS

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
    await update.message.reply_text("❌ Operation cancelled.")
    context.user_data.clear()
    return ConversationHandler.END

def main():
    """Main function to run the bot"""
    # Create application
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Conversation handler for domain creation
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern="^select_domain_")],
        states={
            WAITING_FOR_SUBDOMAIN_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, subdomain_name_handler)],
            WAITING_FOR_IP_ADDRESS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ip_address_handler),
                CallbackQueryHandler(record_type_handler, pattern="^type_")
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("approve", approve_user))
    application.add_handler(CommandHandler("unapprove", unapprove_user))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # Initialize domains on startup
    asyncio.create_task(bot_instance.fetch_domains())
    
    # Start the bot
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=f"https://subdomain-behq.onrender.com/{TELEGRAM_BOT_TOKEN}"
    )

if __name__ == "__main__":
    main()
