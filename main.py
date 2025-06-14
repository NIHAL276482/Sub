import os
import json
import asyncio
import logging
from typing import Dict, List, Optional
from datetime import datetime

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Constants
OWNER_ID = 7187126565
CLOUDFLARE_EMAIL = "nihalchakradhri9@gmail.com"
CLOUDFLARE_TOKEN = "kLC0cg3GbAPQ5L-gDrKzxQii88h7dbV-zE1q2a3I"
BOT_TOKEN = "8125768320:AAGj35QQfAM0mvxjNDP_PlbrjQaPzNTZYkY"  # Replace with your actual bot token
PORT = int(os.environ.get('PORT', 8080))

# Conversation states
WAITING_FOR_SUBDOMAIN, WAITING_FOR_IP, WAITING_FOR_EDIT_IP = range(3)

# Data storage (in production, use a proper database)
approved_users = set()
user_domains = {}  # {user_id: {domain: [subdomains]}}
domain_owners = {}  # {full_domain: user_id}

class CloudflareAPI:
    def __init__(self, email: str, token: str):
        self.email = email
        self.token = token
        self.base_url = "https://api.cloudflare.com/client/v4"
        self.headers = {
            "X-Auth-Email": email,
            "X-Auth-Key": token,
            "Content-Type": "application/json"
        }
    
    def get_zones(self) -> List[Dict]:
        """Fetch all zones (domains) from Cloudflare"""
        try:
            response = requests.get(f"{self.base_url}/zones", headers=self.headers)
            response.raise_for_status()
            data = response.json()
            return data.get('result', [])
        except Exception as e:
            logger.error(f"Error fetching zones: {e}")
            return []
    
    def create_dns_record(self, zone_id: str, record_type: str, name: str, content: str) -> bool:
        """Create a DNS record"""
        try:
            payload = {
                "type": record_type,
                "name": name,
                "content": content,
                "ttl": 3600,  # 1 hour
                "proxied": False
            }
            response = requests.post(
                f"{self.base_url}/zones/{zone_id}/dns_records",
                headers=self.headers,
                json=payload
            )
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Error creating DNS record: {e}")
            return False
    
    def get_dns_records(self, zone_id: str, name: str = None) -> List[Dict]:
        """Get DNS records for a zone"""
        try:
            url = f"{self.base_url}/zones/{zone_id}/dns_records"
            if name:
                url += f"?name={name}"
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            data = response.json()
            return data.get('result', [])
        except Exception as e:
            logger.error(f"Error fetching DNS records: {e}")
            return []
    
    def update_dns_record(self, zone_id: str, record_id: str, record_type: str, name: str, content: str) -> bool:
        """Update a DNS record"""
        try:
            payload = {
                "type": record_type,
                "name": name,
                "content": content,
                "ttl": 3600,
                "proxied": False
            }
            response = requests.put(
                f"{self.base_url}/zones/{zone_id}/dns_records/{record_id}",
                headers=self.headers,
                json=payload
            )
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Error updating DNS record: {e}")
            return False
    
    def delete_dns_record(self, zone_id: str, record_id: str) -> bool:
        """Delete a DNS record"""
        try:
            response = requests.delete(
                f"{self.base_url}/zones/{zone_id}/dns_records/{record_id}",
                headers=self.headers
            )
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Error deleting DNS record: {e}")
            return False

# Initialize Cloudflare API
cf_api = CloudflareAPI(CLOUDFLARE_EMAIL, CLOUDFLARE_TOKEN)

def is_user_approved(user_id: int) -> bool:
    """Check if user is approved"""
    return user_id == OWNER_ID or user_id in approved_users

def get_user_domain_count(user_id: int) -> int:
    """Get total domain count for user"""
    if user_id not in user_domains:
        return 0
    return sum(len(subdomains) for subdomains in user_domains[user_id].values())

def add_user_domain(user_id: int, domain: str, subdomain: str):
    """Add domain to user's collection"""
    if user_id not in user_domains:
        user_domains[user_id] = {}
    if domain not in user_domains[user_id]:
        user_domains[user_id][domain] = []
    
    full_domain = f"{subdomain}.{domain}"
    user_domains[user_id][domain].append(subdomain)
    domain_owners[full_domain] = user_id

def remove_user_domain(user_id: int, domain: str, subdomain: str):
    """Remove domain from user's collection"""
    if user_id in user_domains and domain in user_domains[user_id]:
        if subdomain in user_domains[user_id][domain]:
            user_domains[user_id][domain].remove(subdomain)
            full_domain = f"{subdomain}.{domain}"
            if full_domain in domain_owners:
                del domain_owners[full_domain]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    user_id = update.effective_user.id
    
    if not is_user_approved(user_id):
        await update.message.reply_text("❌ You are not approved to use this bot.")
        return
    
    keyboard = [
        [InlineKeyboardButton("➕ Add Domain", callback_data="add_domain")],
        [InlineKeyboardButton("🗑️ Remove Domain", callback_data="remove_domain")],
        [InlineKeyboardButton("✏️ Modify Domain", callback_data="modify_domain")],
        [InlineKeyboardButton("📋 My Domains", callback_data="my_domains")]
    ]
    
    if user_id == OWNER_ID:
        keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    welcome_text = f"""
🌐 **Cloudflare Domain Manager Bot**

Welcome! You can manage your domains here.

📊 **Your Stats:**
• Domains Created: {get_user_domain_count(user_id)}/15
• Status: {'Owner' if user_id == OWNER_ID else 'Approved User'}

Choose an option below:
    """
    
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if not is_user_approved(user_id):
        await query.edit_message_text("❌ You are not approved to use this bot.")
        return
    
    if query.data == "add_domain":
        await handle_add_domain(query, context)
    elif query.data == "remove_domain":
        await handle_remove_domain(query, context)
    elif query.data == "modify_domain":
        await handle_modify_domain(query, context)
    elif query.data == "my_domains":
        await handle_my_domains(query, context)
    elif query.data == "admin_panel" and user_id == OWNER_ID:
        await handle_admin_panel(query, context)
    elif query.data.startswith("select_domain_"):
        await handle_domain_selection(query, context)
    elif query.data.startswith("select_modify_"):
        await handle_modify_selection(query, context)
    elif query.data.startswith("select_remove_"):
        await handle_remove_selection(query, context)
    elif query.data.startswith("record_type_"):
        await handle_record_type_selection(query, context)

async def handle_add_domain(query, context):
    """Handle add domain button"""
    user_id = query.from_user.id
    
    if get_user_domain_count(user_id) >= 15:
        await query.edit_message_text("❌ You have reached the maximum limit of 15 domains.")
        return
    
    # Fetch available domains from Cloudflare
    zones = cf_api.get_zones()
    
    if not zones:
        await query.edit_message_text("❌ No domains available or error fetching domains.")
        return
    
    keyboard = []
    for zone in zones:
        domain_name = zone['name']
        keyboard.append([InlineKeyboardButton(domain_name, callback_data=f"select_domain_{domain_name}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "🌐 **Select a domain to add subdomain:**\n\nChoose from available domains:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_domain_selection(query, context):
    """Handle domain selection for adding subdomain"""
    domain = query.data.replace("select_domain_", "")
    user_id = query.from_user.id
    
    # Store selected domain in context
    context.user_data['selected_domain'] = domain
    context.user_data['action'] = 'add'
    
    keyboard = [
        [InlineKeyboardButton("A Record", callback_data="record_type_A")],
        [InlineKeyboardButton("AAAA Record (IPv6)", callback_data="record_type_AAAA")],
        [InlineKeyboardButton("CNAME Record", callback_data="record_type_CNAME")],
        [InlineKeyboardButton("🔙 Back", callback_data="add_domain")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"📝 **Selected Domain:** `{domain}`\n\nChoose record type:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_record_type_selection(query, context):
    """Handle record type selection"""
    record_type = query.data.replace("record_type_", "")
    context.user_data['record_type'] = record_type
    
    await query.edit_message_text(
        f"📝 **Domain:** `{context.user_data['selected_domain']}`\n"
        f"📝 **Record Type:** `{record_type}`\n\n"
        "Please enter the subdomain name (without the main domain):\n"
        "Example: If you want `api.example.com`, just type `api`",
        parse_mode='Markdown'
    )
    
    return WAITING_FOR_SUBDOMAIN

async def handle_subdomain_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle subdomain name input"""
    subdomain = update.message.text.strip()
    user_id = update.effective_user.id
    
    if not subdomain or '.' in subdomain:
        await update.message.reply_text("❌ Invalid subdomain name. Please enter only the subdomain part (e.g., 'api' for api.example.com)")
        return WAITING_FOR_SUBDOMAIN
    
    context.user_data['subdomain'] = subdomain
    domain = context.user_data['selected_domain']
    record_type = context.user_data['record_type']
    
    content_type = "IP address" if record_type in ['A', 'AAAA'] else "target domain/value"
    
    await update.message.reply_text(
        f"📝 **Full Domain:** `{subdomain}.{domain}`\n"
        f"📝 **Record Type:** `{record_type}`\n\n"
        f"Please enter the {content_type}:",
        parse_mode='Markdown'
    )
    
    return WAITING_FOR_IP

async def handle_ip_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle IP/content input and create DNS record"""
    content = update.message.text.strip()
    user_id = update.effective_user.id
    
    domain = context.user_data['selected_domain']
    subdomain = context.user_data['subdomain']
    record_type = context.user_data['record_type']
    full_domain = f"{subdomain}.{domain}"
    
    # Find zone ID
    zones = cf_api.get_zones()
    zone_id = None
    for zone in zones:
        if zone['name'] == domain:
            zone_id = zone['id']
            break
    
    if not zone_id:
        await update.message.reply_text("❌ Domain not found in Cloudflare.")
        return ConversationHandler.END
    
    # Create DNS record
    success = cf_api.create_dns_record(zone_id, record_type, full_domain, content)
    
    if success:
        add_user_domain(user_id, domain, subdomain)
        
        await update.message.reply_text(
            f"✅ **Domain Created Successfully!**\n\n"
            f"🌐 **Domain:** `{full_domain}`\n"
            f"📝 **Type:** `{record_type}`\n"
            f"🎯 **Points to:** `{content}`\n"
            f"⏰ **TTL:** 1 hour\n"
            f"🔒 **Proxy:** Disabled\n\n"
            f"Your domain is now active!",
            parse_mode='Markdown'
        )
        
        # Notify owner about new domain creation
        if user_id != OWNER_ID:
            try:
                await context.bot.send_message(
                    OWNER_ID,
                    f"📊 **New Domain Created**\n\n"
                    f"👤 **User:** {update.effective_user.first_name} (ID: {user_id})\n"
                    f"🌐 **Domain:** `{full_domain}`\n"
                    f"📝 **Type:** `{record_type}`\n"
                    f"🎯 **Content:** `{content}`",
                    parse_mode='Markdown'
                )
            except:
                pass
    else:
        await update.message.reply_text("❌ Failed to create domain. Please try again.")
    
    # Clear context data
    context.user_data.clear()
    return ConversationHandler.END

async def handle_remove_domain(query, context):
    """Handle remove domain button"""
    user_id = query.from_user.id
    
    if user_id not in user_domains or not user_domains[user_id]:
        await query.edit_message_text("❌ You don't have any domains to remove.")
        return
    
    keyboard = []
    for domain, subdomains in user_domains[user_id].items():
        for subdomain in subdomains:
            full_domain = f"{subdomain}.{domain}"
            keyboard.append([InlineKeyboardButton(full_domain, callback_data=f"select_remove_{full_domain}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "🗑️ **Select domain to remove:**",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_remove_selection(query, context):
    """Handle domain removal"""
    full_domain = query.data.replace("select_remove_", "")
    user_id = query.from_user.id
    
    # Check ownership
    if full_domain not in domain_owners or domain_owners[full_domain] != user_id:
        await query.edit_message_text("❌ You don't own this domain.")
        return
    
    # Parse domain parts
    parts = full_domain.split('.')
    subdomain = parts[0]
    domain = '.'.join(parts[1:])
    
    # Find zone and record
    zones = cf_api.get_zones()
    zone_id = None
    for zone in zones:
        if zone['name'] == domain:
            zone_id = zone['id']
            break
    
    if zone_id:
        records = cf_api.get_dns_records(zone_id, full_domain)
        if records:
            record_id = records[0]['id']
            success = cf_api.delete_dns_record(zone_id, record_id)
            
            if success:
                remove_user_domain(user_id, domain, subdomain)
                await query.edit_message_text(f"✅ Domain `{full_domain}` has been removed successfully!", parse_mode='Markdown')
            else:
                await query.edit_message_text("❌ Failed to remove domain from Cloudflare.")
        else:
            await query.edit_message_text("❌ Domain record not found.")
    else:
        await query.edit_message_text("❌ Domain zone not found.")

async def handle_modify_domain(query, context):
    """Handle modify domain button"""
    user_id = query.from_user.id
    
    if user_id not in user_domains or not user_domains[user_id]:
        await query.edit_message_text("❌ You don't have any domains to modify.")
        return
    
    keyboard = []
    for domain, subdomains in user_domains[user_id].items():
        for subdomain in subdomains:
            full_domain = f"{subdomain}.{domain}"
            keyboard.append([InlineKeyboardButton(full_domain, callback_data=f"select_modify_{full_domain}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "✏️ **Select domain to modify:**",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_modify_selection(query, context):
    """Handle domain modification"""
    full_domain = query.data.replace("select_modify_", "")
    user_id = query.from_user.id
    
    # Check ownership
    if full_domain not in domain_owners or domain_owners[full_domain] != user_id:
        await query.edit_message_text("❌ You don't own this domain.")
        return
    
    context.user_data['modify_domain'] = full_domain
    
    await query.edit_message_text(
        f"✏️ **Modifying:** `{full_domain}`\n\nPlease enter the new IP address or content:",
        parse_mode='Markdown'
    )
    
    return WAITING_FOR_EDIT_IP

async def handle_edit_ip_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle IP modification"""
    new_content = update.message.text.strip()
    user_id = update.effective_user.id
    full_domain = context.user_data['modify_domain']
    
    # Parse domain parts
    parts = full_domain.split('.')
    subdomain = parts[0]
    domain = '.'.join(parts[1:])
    
    # Find zone and record
    zones = cf_api.get_zones()
    zone_id = None
    for zone in zones:
        if zone['name'] == domain:
            zone_id = zone['id']
            break
    
    if zone_id:
        records = cf_api.get_dns_records(zone_id, full_domain)
        if records:
            record = records[0]
            success = cf_api.update_dns_record(
                zone_id, record['id'], record['type'], full_domain, new_content
            )
            
            if success:
                await update.message.reply_text(
                    f"✅ **Domain Updated Successfully!**\n\n"
                    f"🌐 **Domain:** `{full_domain}`\n"
                    f"🎯 **New Content:** `{new_content}`\n"
                    f"📝 **Type:** `{record['type']}`",
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text("❌ Failed to update domain.")
        else:
            await update.message.reply_text("❌ Domain record not found.")
    else:
        await update.message.reply_text("❌ Domain zone not found.")
    
    context.user_data.clear()
    return ConversationHandler.END

async def handle_my_domains(query, context):
    """Show user's domains"""
    user_id = query.from_user.id
    
    if user_id not in user_domains or not user_domains[user_id]:
        await query.edit_message_text("📋 You don't have any domains yet.")
        return
    
    domains_text = "📋 **Your Domains:**\n\n"
    total_count = 0
    
    for domain, subdomains in user_domains[user_id].items():
        domains_text += f"🌐 **{domain}:**\n"
        for subdomain in subdomains:
            domains_text += f"   • `{subdomain}.{domain}`\n"
            total_count += 1
        domains_text += "\n"
    
    domains_text += f"📊 **Total:** {total_count}/15 domains"
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(domains_text, reply_markup=reply_markup, parse_mode='Markdown')

async def handle_admin_panel(query, context):
    """Admin panel for owner"""
    keyboard = [
        [InlineKeyboardButton("👥 User Stats", callback_data="user_stats")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "👑 **Admin Panel**\n\nManage bot users and view statistics:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def approve_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Approve a user"""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Only the owner can approve users.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /approve <user_id>")
        return
    
    try:
        user_id = int(context.args[0])
        approved_users.add(user_id)
        await update.message.reply_text(f"✅ User {user_id} has been approved.")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")

async def unapprove_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unapprove a user"""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Only the owner can unapprove users.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /unapprove <user_id>")
        return
    
    try:
        user_id = int(context.args[0])
        approved_users.discard(user_id)
        await update.message.reply_text(f"✅ User {user_id} has been unapproved.")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")

async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return to main menu"""
    await start(update, context)

def main():
    """Main function to run the bot"""
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Conversation handler for domain creation
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_record_type_selection, pattern="^record_type_")],
        states={
            WAITING_FOR_SUBDOMAIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_subdomain_input)],
            WAITING_FOR_IP: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ip_input)],
            WAITING_FOR_EDIT_IP: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_ip_input)],
        },
        fallbacks=[CommandHandler('start', start)],
    )
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("approve", approve_user))
    application.add_handler(CommandHandler("unapprove", unapprove_user))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # Start the bot
    logger.info("Starting bot...")
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=BOT_TOKEN,
        webhook_url=f"https://subdomain-behq.onrender.com/{BOT_TOKEN}"
    )

if __name__ == '__main__':
    main()
