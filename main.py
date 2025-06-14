import os
import json
import asyncio
import logging
from typing import Dict, List, Optional
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Hardcoded configuration
TELEGRAM_BOT_TOKEN = "8125768320:AAGj35QQfAM0mvxjNDP_PlbrjQaPzNTZYkY"
CLOUDFLARE_API_TOKEN = "kLC0cg3GbAPQ5L-gDrKzxQii88h7dbV-zE1q2a3I"
CLOUDFLARE_EMAIL = "nihalchakradhri9@gmail.com"  # Your Cloudflare account email
ADMIN_ID = 7187126565
MAX_SUBDOMAINS_PER_USER = 15

# File to store approved users and user data
USERS_FILE = "approved_users.json"
USER_DOMAINS_FILE = "user_domains.json"

class CloudflareAPI:
    def __init__(self):
        self.api_token = CLOUDFLARE_API_TOKEN
        self.email = CLOUDFLARE_EMAIL
        self.base_url = "https://api.cloudflare.com/client/v4"
        self.headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json"
        }
    
    async def get_zones(self) -> List[Dict]:
        """Fetch all zones (domains) from Cloudflare"""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.base_url}/zones",
                headers=self.headers
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('result', [])
                else:
                    logger.error(f"Failed to fetch zones: {response.status}")
                    return []
    
    async def get_zone_id(self, domain: str) -> Optional[str]:
        """Get zone ID for a specific domain"""
        zones = await self.get_zones()
        for zone in zones:
            if zone['name'] == domain:
                return zone['id']
        return None
    
    async def create_dns_record(self, zone_id: str, name: str, ip: str) -> bool:
        """Create a DNS A record"""
        data = {
            "type": "A",
            "name": name,
            "content": ip,
            "ttl": 3600,  # 1 hour
            "proxied": False
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/zones/{zone_id}/dns_records",
                headers=self.headers,
                json=data
            ) as response:
                if response.status == 200:
                    return True
                else:
                    error_data = await response.json()
                    logger.error(f"Failed to create DNS record: {error_data}")
                    return False
    
    async def get_dns_records(self, zone_id: str, name: str = None) -> List[Dict]:
        """Get DNS records for a zone"""
        params = {}
        if name:
            params['name'] = name
            
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.base_url}/zones/{zone_id}/dns_records",
                headers=self.headers,
                params=params
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('result', [])
                return []
    
    async def update_dns_record(self, zone_id: str, record_id: str, name: str, ip: str) -> bool:
        """Update a DNS A record"""
        data = {
            "type": "A",
            "name": name,
            "content": ip,
            "ttl": 3600,
            "proxied": False
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.put(
                f"{self.base_url}/zones/{zone_id}/dns_records/{record_id}",
                headers=self.headers,
                json=data
            ) as response:
                return response.status == 200
    
    async def delete_dns_record(self, zone_id: str, record_id: str) -> bool:
        """Delete a DNS record"""
        async with aiohttp.ClientSession() as session:
            async with session.delete(
                f"{self.base_url}/zones/{zone_id}/dns_records/{record_id}",
                headers=self.headers
            ) as response:
                return response.status == 200

class BotData:
    def __init__(self):
        self.approved_users = self.load_approved_users()
        self.user_domains = self.load_user_domains()
        self.available_domains = []
        self.cf_api = CloudflareAPI()
    
    def load_approved_users(self) -> set:
        """Load approved users from file"""
        try:
            if os.path.exists(USERS_FILE):
                with open(USERS_FILE, 'r') as f:
                    return set(json.load(f))
            return {ADMIN_ID}  # Admin is always approved
        except Exception as e:
            logger.error(f"Error loading approved users: {e}")
            return {ADMIN_ID}
    
    def save_approved_users(self):
        """Save approved users to file"""
        try:
            with open(USERS_FILE, 'w') as f:
                json.dump(list(self.approved_users), f)
        except Exception as e:
            logger.error(f"Error saving approved users: {e}")
    
    def load_user_domains(self) -> Dict:
        """Load user domain ownership data"""
        try:
            if os.path.exists(USER_DOMAINS_FILE):
                with open(USER_DOMAINS_FILE, 'r') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logger.error(f"Error loading user domains: {e}")
            return {}
    
    def save_user_domains(self):
        """Save user domain ownership data"""
        try:
            with open(USER_DOMAINS_FILE, 'w') as f:
                json.dump(self.user_domains, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving user domains: {e}")
    
    async def refresh_domains(self):
        """Refresh available domains from Cloudflare"""
        zones = await self.cf_api.get_zones()
        self.available_domains = [zone['name'] for zone in zones]
        logger.info(f"Refreshed domains: {self.available_domains}")

# Initialize bot data
bot_data = BotData()

def is_approved(user_id: int) -> bool:
    """Check if user is approved"""
    return user_id in bot_data.approved_users

def is_admin(user_id: int) -> bool:
    """Check if user is admin"""
    return user_id == ADMIN_ID

def get_user_subdomain_count(user_id: int) -> int:
    """Get count of subdomains owned by user"""
    user_id_str = str(user_id)
    return len(bot_data.user_domains.get(user_id_str, {}))

def owns_subdomain(user_id: int, full_domain: str) -> bool:
    """Check if user owns a specific subdomain"""
    user_id_str = str(user_id)
    user_subdomains = bot_data.user_domains.get(user_id_str, {})
    return full_domain in user_subdomains

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    user_id = update.effective_user.id
    
    if not is_approved(user_id):
        await update.message.reply_text(
            "❌ You are not approved to use this bot. Please contact the administrator."
        )
        return
    
    # Refresh domains on start
    await bot_data.refresh_domains()
    
    keyboard = [
        [InlineKeyboardButton("➕ Add Subdomain", callback_data="add_domain")],
        [InlineKeyboardButton("🗑️ Remove Subdomain", callback_data="remove_domain")],
        [InlineKeyboardButton("✏️ Modify Subdomain", callback_data="modify_domain")],
        [InlineKeyboardButton("📋 My Subdomains", callback_data="list_domains")]
    ]
    
    if is_admin(user_id):
        keyboard.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    welcome_text = f"""
🌐 **Cloudflare DNS Manager Bot**

Welcome! You can manage subdomains across our available domains.

**Your Stats:**
• Subdomains: {get_user_subdomain_count(user_id)}/{MAX_SUBDOMAINS_PER_USER}
• Available domains: {len(bot_data.available_domains)}

**Features:**
• TTL: 1 hour
• Proxy: Disabled
• Type: A Record

Choose an option below:
    """
    
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard button presses"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if not is_approved(user_id):
        await query.edit_message_text("❌ You are not approved to use this bot.")
        return
    
    data = query.data
    
    if data == "add_domain":
        await handle_add_domain(query, context)
    elif data == "remove_domain":
        await handle_remove_domain(query, context)
    elif data == "modify_domain":
        await handle_modify_domain(query, context)
    elif data == "list_domains":
        await handle_list_domains(query, context)
    elif data == "admin_panel" and is_admin(user_id):
        await handle_admin_panel(query, context)
    elif data.startswith("select_domain_"):
        await handle_domain_selection(query, context)
    elif data.startswith("remove_subdomain_"):
        await handle_subdomain_removal(query, context)
    elif data.startswith("modify_subdomain_"):
        await handle_subdomain_modification(query, context)

async def handle_add_domain(query, context):
    """Handle add domain button"""
    user_id = query.from_user.id
    
    if get_user_subdomain_count(user_id) >= MAX_SUBDOMAINS_PER_USER:
        await query.edit_message_text(
            f"❌ You have reached the maximum limit of {MAX_SUBDOMAINS_PER_USER} subdomains."
        )
        return
    
    if not bot_data.available_domains:
        await query.edit_message_text("❌ No domains available. Please contact admin.")
        return
    
    keyboard = []
    for domain in bot_data.available_domains:
        keyboard.append([InlineKeyboardButton(domain, callback_data=f"select_domain_{domain}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "🌐 **Select a domain to create subdomain:**\n\n"
        "Click on any domain to proceed.",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_domain_selection(query, context):
    """Handle domain selection for adding subdomain"""
    domain = query.data.split("_", 2)[2]
    context.user_data['selected_domain'] = domain
    context.user_data['action'] = 'add'
    
    await query.edit_message_text(
        f"🌐 **Domain Selected:** `{domain}`\n\n"
        "Now send the subdomain name you want to create.\n\n"
        "**Example:** If you send `test`, it will create `test.{domain}`\n\n"
        "Send your subdomain name:",
        parse_mode='Markdown'
    )

async def handle_remove_domain(query, context):
    """Handle remove domain button"""
    user_id = query.from_user.id
    user_id_str = str(user_id)
    
    user_subdomains = bot_data.user_domains.get(user_id_str, {})
    
    if not user_subdomains:
        await query.edit_message_text("❌ You don't own any subdomains.")
        return
    
    keyboard = []
    for subdomain in user_subdomains.keys():
        keyboard.append([InlineKeyboardButton(
            f"🗑️ {subdomain}", 
            callback_data=f"remove_subdomain_{subdomain}"
        )])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "🗑️ **Your Subdomains:**\n\n"
        "Select a subdomain to delete:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_subdomain_removal(query, context):
    """Handle subdomain removal"""
    subdomain = query.data.split("_", 2)[2]
    user_id = query.from_user.id
    user_id_str = str(user_id)
    
    if not owns_subdomain(user_id, subdomain):
        await query.edit_message_text("❌ You don't own this subdomain.")
        return
    
    # Extract domain from subdomain
    domain_parts = subdomain.split('.')
    domain = '.'.join(domain_parts[-2:])  # Get last two parts (domain.tld)
    
    # Get zone ID
    zone_id = await bot_data.cf_api.get_zone_id(domain)
    if not zone_id:
        await query.edit_message_text("❌ Domain not found in Cloudflare.")
        return
    
    # Get DNS records to find the one to delete
    records = await bot_data.cf_api.get_dns_records(zone_id, subdomain)
    if not records:
        await query.edit_message_text("❌ DNS record not found.")
        return
    
    # Delete the DNS record
    record_id = records[0]['id']
    success = await bot_data.cf_api.delete_dns_record(zone_id, record_id)
    
    if success:
        # Remove from user's domains
        del bot_data.user_domains[user_id_str][subdomain]
        if not bot_data.user_domains[user_id_str]:
            del bot_data.user_domains[user_id_str]
        bot_data.save_user_domains()
        
        await query.edit_message_text(f"✅ Successfully deleted `{subdomain}`", parse_mode='Markdown')
    else:
        await query.edit_message_text(f"❌ Failed to delete `{subdomain}`", parse_mode='Markdown')

async def handle_modify_domain(query, context):
    """Handle modify domain button"""
    user_id = query.from_user.id
    user_id_str = str(user_id)
    
    user_subdomains = bot_data.user_domains.get(user_id_str, {})
    
    if not user_subdomains:
        await query.edit_message_text("❌ You don't own any subdomains.")
        return
    
    keyboard = []
    for subdomain, ip in user_subdomains.items():
        keyboard.append([InlineKeyboardButton(
            f"✏️ {subdomain} ({ip})", 
            callback_data=f"modify_subdomain_{subdomain}"
        )])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "✏️ **Your Subdomains:**\n\n"
        "Select a subdomain to modify its IP:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_subdomain_modification(query, context):
    """Handle subdomain modification"""
    subdomain = query.data.split("_", 2)[2]
    user_id = query.from_user.id
    
    if not owns_subdomain(user_id, subdomain):
        await query.edit_message_text("❌ You don't own this subdomain.")
        return
    
    context.user_data['modify_subdomain'] = subdomain
    context.user_data['action'] = 'modify'
    
    current_ip = bot_data.user_domains[str(user_id)][subdomain]
    await query.edit_message_text(
        f"✏️ **Modifying:** `{subdomain}`\n"
        f"**Current IP:** `{current_ip}`\n\n"
        "Send the new IP address:",
        parse_mode='Markdown'
    )

async def handle_list_domains(query, context):
    """Handle list domains button"""
    user_id = query.from_user.id
    user_id_str = str(user_id)
    
    user_subdomains = bot_data.user_domains.get(user_id_str, {})
    
    if not user_subdomains:
        text = "📋 **Your Subdomains:**\n\nYou don't own any subdomains yet."
    else:
        text = "📋 **Your Subdomains:**\n\n"
        for subdomain, ip in user_subdomains.items():
            text += f"• `{subdomain}` → `{ip}`\n"
        text += f"\n**Total:** {len(user_subdomains)}/{MAX_SUBDOMAINS_PER_USER}"
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def handle_admin_panel(query, context):
    """Handle admin panel"""
    keyboard = [
        [InlineKeyboardButton("🔄 Refresh Domains", callback_data="refresh_domains")],
        [InlineKeyboardButton("👥 User Stats", callback_data="user_stats")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = f"""
⚙️ **Admin Panel**

**Available Domains:** {len(bot_data.available_domains)}
**Approved Users:** {len(bot_data.approved_users)}
**Total Subdomains:** {sum(len(domains) for domains in bot_data.user_domains.values())}

Use /approve and /unapprove commands to manage users.
    """
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages"""
    user_id = update.effective_user.id
    
    if not is_approved(user_id):
        return
    
    text = update.message.text.strip()
    action = context.user_data.get('action')
    
    if action == 'add':
        await handle_subdomain_creation(update, context, text)
    elif action == 'modify':
        await handle_ip_modification(update, context, text)

async def handle_subdomain_creation(update, context, subdomain_name):
    """Handle subdomain creation process"""
    user_id = update.effective_user.id
    domain = context.user_data.get('selected_domain')
    
    if not domain:
        await update.message.reply_text("❌ No domain selected. Please start over.")
        return
    
    # Validate subdomain name
    if not subdomain_name.replace('-', '').replace('_', '').isalnum():
        await update.message.reply_text(
            "❌ Invalid subdomain name. Use only letters, numbers, hyphens, and underscores."
        )
        return
    
    full_domain = f"{subdomain_name}.{domain}"
    
    # Check if subdomain already exists
    zone_id = await bot_data.cf_api.get_zone_id(domain)
    if not zone_id:
        await update.message.reply_text("❌ Domain not found in Cloudflare.")
        return
    
    existing_records = await bot_data.cf_api.get_dns_records(zone_id, full_domain)
    if existing_records:
        await update.message.reply_text(f"❌ Subdomain `{full_domain}` already exists.", parse_mode='Markdown')
        return
    
    context.user_data['full_domain'] = full_domain
    context.user_data['zone_id'] = zone_id
    context.user_data['action'] = 'add_ip'
    
    await update.message.reply_text(
        f"✅ **Subdomain:** `{full_domain}`\n\n"
        "Now send the IP address for this subdomain:",
        parse_mode='Markdown'
    )

async def handle_ip_modification(update, context, new_ip):
    """Handle IP modification for existing subdomain"""
    user_id = update.effective_user.id
    user_id_str = str(user_id)
    subdomain = context.user_data.get('modify_subdomain')
    
    if not subdomain or not owns_subdomain(user_id, subdomain):
        await update.message.reply_text("❌ Invalid subdomain or you don't own it.")
        return
    
    # Validate IP
    if not is_valid_ip(new_ip):
        await update.message.reply_text("❌ Invalid IP address format.")
        return
    
    # Extract domain from subdomain
    domain_parts = subdomain.split('.')
    domain = '.'.join(domain_parts[-2:])
    
    # Get zone ID and record
    zone_id = await bot_data.cf_api.get_zone_id(domain)
    if not zone_id:
        await update.message.reply_text("❌ Domain not found in Cloudflare.")
        return
    
    records = await bot_data.cf_api.get_dns_records(zone_id, subdomain)
    if not records:
        await update.message.reply_text("❌ DNS record not found.")
        return
    
    # Update DNS record
    record_id = records[0]['id']
    success = await bot_data.cf_api.update_dns_record(zone_id, record_id, subdomain, new_ip)
    
    if success:
        # Update user's domains
        bot_data.user_domains[user_id_str][subdomain] = new_ip
        bot_data.save_user_domains()
        
        await update.message.reply_text(
            f"✅ Successfully updated `{subdomain}` to IP `{new_ip}`",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(f"❌ Failed to update `{subdomain}`", parse_mode='Markdown')
    
    # Clear context
    context.user_data.clear()

def is_valid_ip(ip: str) -> bool:
    """Validate IP address format"""
    parts = ip.split('.')
    if len(parts) != 4:
        return False
    
    try:
        for part in parts:
            num = int(part)
            if not 0 <= num <= 255:
                return False
        return True
    except ValueError:
        return False

async def approve_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Approve user command (admin only)"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /approve <user_id>")
        return
    
    try:
        user_id = int(context.args[0])
        bot_data.approved_users.add(user_id)
        bot_data.save_approved_users()
        await update.message.reply_text(f"✅ User {user_id} has been approved.")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")

async def unapprove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unapprove user command (admin only)"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /unapprove <user_id>")
        return
    
    try:
        user_id = int(context.args[0])
        if user_id == ADMIN_ID:
            await update.message.reply_text("❌ Cannot unapprove admin.")
            return
        
        bot_data.approved_users.discard(user_id)
        bot_data.save_approved_users()
        await update.message.reply_text(f"✅ User {user_id} has been unapproved.")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")

def main():
    """Start the bot"""
    # Create application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("approve", approve_command))
    application.add_handler(CommandHandler("unapprove", unapprove_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    # Start the bot
    logger.info("Starting bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
