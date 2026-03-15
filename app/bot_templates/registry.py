from dataclasses import dataclass, field


@dataclass
class TemplateField:
    name: str
    label: str
    type: str = "text"  # text, textarea, number, select
    placeholder: str = ""
    required: bool = False
    default: str = ""
    help: str = ""
    options: list[str] = field(default_factory=list)  # for select type


@dataclass
class BotTemplate:
    id: str
    name: str
    description: str
    icon: str  # emoji
    category: str
    fields: list[TemplateField]
    code_template: str
    requirements: str


TEMPLATES: dict[str, BotTemplate] = {}


def _register(t: BotTemplate):
    TEMPLATES[t.id] = t


def get_template(template_id: str) -> BotTemplate | None:
    return TEMPLATES.get(template_id)


# ─── Template: Echo Bot ───────────────────────────────────────────────────────

_register(BotTemplate(
    id="echo",
    name="Echo Bot",
    description="Replies back with the same message the user sends. Great for testing.",
    icon="E",
    category="Basic",
    fields=[
        TemplateField(
            name="welcome_message",
            label="Welcome Message",
            placeholder="Hello! I'm an echo bot. Send me any message!",
            default="Hello! I'm an echo bot. Send me any message and I'll repeat it back to you!",
            help="Message shown when user sends /start",
        ),
    ],
    requirements="python-telegram-bot==21.10",
    code_template='''\
import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

WELCOME_MESSAGE = """{welcome_message}"""


async def start(update: Update, context):
    await update.message.reply_text(WELCOME_MESSAGE)


async def echo(update: Update, context):
    await update.message.reply_text(update.message.text)


def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    logger.info("Echo bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
''',
))

# ─── Template: Welcome Bot ───────────────────────────────────────────────────

_register(BotTemplate(
    id="welcome",
    name="Welcome Bot",
    description="Greets new members joining a group chat with a customizable message.",
    icon="W",
    category="Group",
    fields=[
        TemplateField(
            name="welcome_text",
            label="Welcome Message",
            type="textarea",
            placeholder="Welcome to the group, {name}!",
            default="Welcome to the group, {name}! Please read the rules in the pinned message.",
            help="Use {name} for user's name, {group} for group name",
        ),
        TemplateField(
            name="goodbye_text",
            label="Goodbye Message",
            type="textarea",
            placeholder="Goodbye {name}!",
            default="Goodbye {name}, we'll miss you!",
            help="Leave empty to disable. Use {name} for user's name",
        ),
    ],
    requirements="python-telegram-bot==21.10",
    code_template='''\
import os
import logging
from telegram import Update
from telegram.ext import Application, ChatMemberHandler, CommandHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

WELCOME_TEXT = """{welcome_text}"""
GOODBYE_TEXT = """{goodbye_text}"""


async def start(update: Update, context):
    await update.message.reply_text(
        "Add me to a group and I'll welcome new members!"
    )


async def chat_member_updated(update: Update, context):
    result = update.chat_member
    old = result.old_chat_member
    new = result.new_chat_member

    name = new.user.first_name or new.user.username or "there"
    group = update.effective_chat.title or "the group"

    # User joined
    if old.status in ("left", "kicked") and new.status in ("member", "restricted"):
        if WELCOME_TEXT.strip():
            msg = WELCOME_TEXT.replace("{{name}}", name).replace("{{group}}", group)
            await update.effective_chat.send_message(msg)

    # User left
    elif old.status in ("member", "restricted", "administrator") and new.status in ("left", "kicked"):
        if GOODBYE_TEXT.strip():
            msg = GOODBYE_TEXT.replace("{{name}}", name).replace("{{group}}", group)
            await update.effective_chat.send_message(msg)


def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(ChatMemberHandler(chat_member_updated, ChatMemberHandler.CHAT_MEMBER))
    logger.info("Welcome bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
''',
))

# ─── Template: Auto-Reply Bot ────────────────────────────────────────────────

_register(BotTemplate(
    id="autoreply",
    name="Auto-Reply Bot",
    description="Automatically replies based on keywords. Configure keyword-response pairs.",
    icon="A",
    category="Utility",
    fields=[
        TemplateField(
            name="rules",
            label="Auto-Reply Rules",
            type="textarea",
            placeholder="hello = Hi there! How can I help?\nprice = Please check our website for pricing.\nhelp = Available commands: /start, /help",
            default="hello = Hi there! How can I help you?\nhelp = Available commands: /start, /help\nthanks = You're welcome!",
            help="One rule per line: keyword = response. Case-insensitive matching.",
        ),
        TemplateField(
            name="default_reply",
            label="Default Reply (no keyword match)",
            placeholder="Sorry, I don't understand. Type /help for available commands.",
            default="",
            help="Leave empty to ignore unmatched messages",
        ),
        TemplateField(
            name="start_message",
            label="Start Message",
            placeholder="Hello! I'm an auto-reply bot.",
            default="Hello! I'm an auto-reply bot. Just send me a message!",
        ),
    ],
    requirements="python-telegram-bot==21.10",
    code_template='''\
import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

START_MESSAGE = """{start_message}"""
DEFAULT_REPLY = """{default_reply}"""

# Parse rules from config
RULES = {{}}
_raw_rules = """{rules}"""
for line in _raw_rules.strip().splitlines():
    line = line.strip()
    if "=" in line:
        keyword, response = line.split("=", 1)
        RULES[keyword.strip().lower()] = response.strip()

logger.info(f"Loaded {{len(RULES)}} auto-reply rules")


async def start(update: Update, context):
    await update.message.reply_text(START_MESSAGE)


async def handle_message(update: Update, context):
    text = update.message.text.lower()
    for keyword, response in RULES.items():
        if keyword in text:
            await update.message.reply_text(response)
            return
    if DEFAULT_REPLY.strip():
        await update.message.reply_text(DEFAULT_REPLY)


def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Auto-reply bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
''',
))

# ─── Template: Scheduled Message Bot ─────────────────────────────────────────

_register(BotTemplate(
    id="scheduled",
    name="Scheduled Message Bot",
    description="Sends a message to a chat at a fixed interval (e.g., daily reminders).",
    icon="S",
    category="Utility",
    fields=[
        TemplateField(
            name="chat_id",
            label="Target Chat ID",
            placeholder="-1001234567890",
            required=True,
            help="Group/channel chat ID. Add the bot to the group first, then use @userinfobot or similar to get the ID.",
        ),
        TemplateField(
            name="message_text",
            label="Scheduled Message",
            type="textarea",
            placeholder="Daily reminder: Don't forget to check in!",
            default="Daily reminder: Don't forget to check in!",
            required=True,
        ),
        TemplateField(
            name="interval_hours",
            label="Interval (hours)",
            type="number",
            placeholder="24",
            default="24",
            required=True,
            help="How often to send the message (in hours). E.g., 24 = once a day.",
        ),
    ],
    requirements="python-telegram-bot==21.10\napscheduler==3.10.4",
    code_template='''\
import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

CHAT_ID = "{chat_id}"
MESSAGE_TEXT = """{message_text}"""
INTERVAL_HOURS = {interval_hours}


async def send_scheduled(context):
    try:
        await context.bot.send_message(chat_id=CHAT_ID, text=MESSAGE_TEXT)
        logger.info(f"Scheduled message sent to {{CHAT_ID}}")
    except Exception as e:
        logger.error(f"Failed to send scheduled message: {{e}}")


async def start(update: Update, context):
    await update.message.reply_text(
        f"Scheduled bot active! Sending message every {{INTERVAL_HOURS}}h to chat {{CHAT_ID}}."
    )


async def post_init(application):
    job_queue = application.job_queue
    job_queue.run_repeating(send_scheduled, interval=INTERVAL_HOURS * 3600, first=10)
    logger.info(f"Scheduled job: every {{INTERVAL_HOURS}}h to {{CHAT_ID}}")


def main():
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    logger.info("Scheduled message bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
''',
))

# ─── Template: Forwarder Bot ─────────────────────────────────────────────────

_register(BotTemplate(
    id="forwarder",
    name="Forwarder Bot",
    description="Forwards all messages from one chat to another (e.g., channel to group).",
    icon="F",
    category="Utility",
    fields=[
        TemplateField(
            name="source_chat_id",
            label="Source Chat ID",
            placeholder="-1001234567890",
            required=True,
            help="Chat ID to listen for messages",
        ),
        TemplateField(
            name="dest_chat_id",
            label="Destination Chat ID",
            placeholder="-1009876543210",
            required=True,
            help="Chat ID to forward messages to",
        ),
        TemplateField(
            name="forward_mode",
            label="Forward Mode",
            type="select",
            options=["forward", "copy"],
            default="copy",
            help="'forward' keeps the original sender shown, 'copy' sends as the bot",
        ),
    ],
    requirements="python-telegram-bot==21.10",
    code_template='''\
import os
import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

SOURCE_CHAT_ID = int("{source_chat_id}")
DEST_CHAT_ID = int("{dest_chat_id}")
FORWARD_MODE = "{forward_mode}"


async def start(update: Update, context):
    await update.message.reply_text(
        f"Forwarder bot active!\\nFrom: {{SOURCE_CHAT_ID}}\\nTo: {{DEST_CHAT_ID}}\\nMode: {{FORWARD_MODE}}"
    )


async def handle_message(update: Update, context):
    if update.effective_chat.id != SOURCE_CHAT_ID:
        return
    try:
        if FORWARD_MODE == "forward":
            await update.message.forward(chat_id=DEST_CHAT_ID)
        else:
            await update.message.copy(chat_id=DEST_CHAT_ID)
        logger.info(f"Forwarded message {{update.message.message_id}}")
    except Exception as e:
        logger.error(f"Forward failed: {{e}}")


def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))
    logger.info(f"Forwarder bot started: {{SOURCE_CHAT_ID}} -> {{DEST_CHAT_ID}}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
''',
))

# ─── Template: FAQ Bot ───────────────────────────────────────────────────────

_register(BotTemplate(
    id="faq",
    name="FAQ Bot",
    description="Inline keyboard FAQ bot. Users tap buttons to navigate questions and answers.",
    icon="Q",
    category="Business",
    fields=[
        TemplateField(
            name="bot_title",
            label="Bot Title",
            placeholder="My FAQ Bot",
            default="FAQ Bot",
        ),
        TemplateField(
            name="faq_items",
            label="FAQ Items",
            type="textarea",
            placeholder="What is this? | This is an FAQ bot.\nHow to contact? | Email us at support@example.com",
            default="What is this? | This is a FAQ bot that answers common questions.\nHow to contact support? | Email us at support@example.com\nWhat are your hours? | We are available Mon-Fri, 9 AM - 5 PM.",
            required=True,
            help="One FAQ per line: Question | Answer",
        ),
    ],
    requirements="python-telegram-bot==21.10",
    code_template='''\
import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

BOT_TITLE = """{bot_title}"""

# Parse FAQ items
FAQ = []
_raw = """{faq_items}"""
for line in _raw.strip().splitlines():
    line = line.strip()
    if "|" in line:
        q, a = line.split("|", 1)
        FAQ.append((q.strip(), a.strip()))

logger.info(f"Loaded {{len(FAQ)}} FAQ items")


def build_menu():
    buttons = []
    for i, (question, _) in enumerate(FAQ):
        buttons.append([InlineKeyboardButton(question, callback_data=f"faq_{{i}}")])
    return InlineKeyboardMarkup(buttons)


async def start(update: Update, context):
    await update.message.reply_text(
        f"{{BOT_TITLE}}\\n\\nSelect a question below:",
        reply_markup=build_menu(),
    )


async def faq_callback(update: Update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("faq_"):
        idx = int(data.split("_")[1])
        if 0 <= idx < len(FAQ):
            question, answer = FAQ[idx]
            await query.edit_message_text(
                f"*{{question}}*\\n\\n{{answer}}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("<< Back to menu", callback_data="back")]]
                ),
            )
    elif data == "back":
        await query.edit_message_text(
            f"{{BOT_TITLE}}\\n\\nSelect a question below:",
            reply_markup=build_menu(),
        )


def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(faq_callback))
    logger.info("FAQ bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
''',
))

# ─── Template: Feedback Collector Bot ─────────────────────────────────────────

_register(BotTemplate(
    id="feedback",
    name="Feedback Collector Bot",
    description="Collects user feedback/messages and forwards them to an admin chat.",
    icon="M",
    category="Business",
    fields=[
        TemplateField(
            name="admin_chat_id",
            label="Admin Chat ID",
            placeholder="123456789",
            required=True,
            help="Your personal Telegram user ID or a group ID where feedback will be sent",
        ),
        TemplateField(
            name="welcome_msg",
            label="Welcome Message",
            type="textarea",
            default="Hello! Send me your feedback, questions, or suggestions and I'll forward them to the team.",
        ),
        TemplateField(
            name="thank_you_msg",
            label="Thank You Message",
            default="Thank you for your feedback! We'll get back to you soon.",
        ),
    ],
    requirements="python-telegram-bot==21.10",
    code_template='''\
import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

ADMIN_CHAT_ID = int("{admin_chat_id}")
WELCOME_MSG = """{welcome_msg}"""
THANK_YOU_MSG = """{thank_you_msg}"""


async def start(update: Update, context):
    await update.message.reply_text(WELCOME_MSG)


async def handle_feedback(update: Update, context):
    user = update.effective_user
    user_info = f"From: {{user.first_name or ''}} {{user.last_name or ''}} (@{{user.username or 'N/A'}}, ID: {{user.id}})"

    if update.message.text:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"New feedback:\\n{{user_info}}\\n\\n{{update.message.text}}",
        )
    elif update.message.photo:
        await context.bot.send_photo(
            chat_id=ADMIN_CHAT_ID,
            photo=update.message.photo[-1].file_id,
            caption=f"Photo feedback:\\n{{user_info}}\\n\\n{{update.message.caption or ''}}",
        )
    elif update.message.document:
        await context.bot.send_document(
            chat_id=ADMIN_CHAT_ID,
            document=update.message.document.file_id,
            caption=f"Document feedback:\\n{{user_info}}\\n\\n{{update.message.caption or ''}}",
        )

    # Store user ID for admin reply
    context.bot_data.setdefault("user_map", {{}})
    context.bot_data["user_map"][str(user.id)] = user.id

    await update.message.reply_text(THANK_YOU_MSG)
    logger.info(f"Feedback received from {{user.id}}")


def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(
        filters.ALL & ~filters.COMMAND & filters.ChatType.PRIVATE,
        handle_feedback,
    ))
    logger.info(f"Feedback bot started, admin: {{ADMIN_CHAT_ID}}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
''',
))
