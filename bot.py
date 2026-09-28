import os
import pickle
import subprocess
import shlex
import asyncio
import re
import time
import json
import random
import string
import psutil

# --- Telegram Imports ---
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode

# --- Google Drive Imports ---
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request

# --- ‼️ IMPORTANT CONFIGURATION ‼️ ---
# 🔴 REPLACE THESE WITH YOUR OWN DETAILS
TELEGRAM_BOT_TOKEN = '' 
OWNER_ID =   # Replace with your numeric User ID

# 📁 THE FOLDER WHERE VIDEOS WILL BE TEMPORARILY DOWNLOADED
DOWNLOAD_DIR = "/root/drm/downloads"

# ⚙️ SET HOW MANY DOWNLOADS CAN RUN AT THE SAME TIME
MAX_CONCURRENT_DOWNLOADS = 3

# 📄 FILES TO STORE DATA
PERMISSIONS_FILE = 'permissions.json'
DRIVE_IDS_FILE = 'user_drive_ids.json'
TOKEN_FILE = 'token.pickle'
CREDENTIALS_FILE = 'credentials.json'

# --- Bot State and Concurrency Management ---
DOWNLOAD_TASKS = {}
SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
BOT_START_TIME = time.time()

# --- Permissions & Helper Functions ---
def load_permissions():
    if os.path.exists(PERMISSIONS_FILE):
        with open(PERMISSIONS_FILE, 'r') as f:
            return json.load(f)
    else:
        default_permissions = {'authorized_users': [OWNER_ID], 'authorized_groups': []}
        with open(PERMISSIONS_FILE, 'w') as f:
            json.dump(default_permissions, f, indent=4)
        return default_permissions

def save_permissions(permissions):
    with open(PERMISSIONS_FILE, 'w') as f:
        json.dump(permissions, f, indent=4)

async def is_authorized(update: Update) -> bool:
    permissions = load_permissions()
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    if user_id in permissions['authorized_users'] or chat_id in permissions['authorized_groups']:
        return True
    await update.message.reply_text("⛔️ You are not authorized to use this bot.")
    return False

def get_readable_time(seconds):
    result = ""
    (days, rem) = divmod(seconds, 86400)
    (hours, rem) = divmod(rem, 3600)
    (minutes, secs) = divmod(rem, 60)
    if days > 0: result += f"{int(days)}d"
    if hours > 0: result += f"{int(hours)}h"
    if minutes > 0: result += f"{int(minutes)}m"
    result += f"{int(secs)}s"
    return result

def get_readable_size(size_in_bytes):
    if size_in_bytes is None: return "0B"
    power = 1024
    n = 0
    power_labels = {0: '', 1: 'Ki', 2: 'Mi', 3: 'Gi', 4: 'Ti'}
    while size_in_bytes >= power and n < len(power_labels):
        size_in_bytes /= power
        n += 1
    return f"{size_in_bytes:.2f} {power_labels[n]}B"

def generate_task_id(length=6):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

def escape_markdown_v2(text: str) -> str:
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', str(text))

# --- Drive ID Storage Functions ---
def load_drive_ids():
    if os.path.exists(DRIVE_IDS_FILE):
        with open(DRIVE_IDS_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_drive_ids(drive_ids):
    with open(DRIVE_IDS_FILE, 'w') as f:
        json.dump(drive_ids, f, indent=4)

# --- Google Drive Section (Non-Blocking) ---
def get_gdrive_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, 'rb') as token:
                creds = pickle.load(token)
        except Exception:
            print("Error loading token.pickle. It might be corrupted.")
            return None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                with open(TOKEN_FILE, 'wb') as token:
                    pickle.dump(creds, token)
            except Exception as e:
                print(f"Error refreshing token: {e}")
                return None
        else:
            return None
    return build('drive', 'v3', credentials=creds)

def _blocking_gdrive_upload(service, file_metadata, media):
    """Synchronous function to be run in a separate thread."""
    return service.files().create(body=file_metadata, media_body=media, fields='id, webViewLink').execute()

async def upload_to_gdrive(file_path, file_name, task_id):
    """Asynchronous wrapper for the blocking Google Drive upload."""
    service = get_gdrive_service()
    if not service:
        print("Error: Google Drive credentials are not valid or token.pickle is missing.")
        return None
    
    user_id = str(DOWNLOAD_TASKS[task_id]['user'].id)
    drive_ids = load_drive_ids()
    folder_id = drive_ids.get(user_id)
    
    if folder_id:
        file_metadata = {'name': file_name, 'parents': [folder_id]}
    else:
        file_metadata = {'name': file_name}
    
    media = MediaFileUpload(file_path, mimetype='video/mp4', resumable=True)
    loop = asyncio.get_running_loop()
    
    try:
        file = await loop.run_in_executor(None, _blocking_gdrive_upload, service, file_metadata, media)
        file_id = file.get('id')
        # Make file readable by anyone with the link (Optional - remove if you want private files)
        await loop.run_in_executor(None, lambda: service.permissions().create(fileId=file_id, body={'type': 'anyone', 'role': 'reader'}).execute())
        return file.get('webViewLink')
    except Exception as e:
        print(f"Upload Error: {e}")
        return None

# --- Core Download & Upload Logic ---
def _blocking_download(command, task_id):
    print(f"[Task {task_id}] Starting download process...")
    # Using shlex to ensure arguments are parsed correctly
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    DOWNLOAD_TASKS[task_id]['process'] = process
    stdout, stderr = process.communicate()
    
    if process.returncode != 0:
        print(f"[Task {task_id}] Download failed. Error: {stderr}")
        raise Exception(stderr)
    
    print(f"[Task {task_id}] Download finished successfully.")
    return True

async def run_download_and_upload_task(update, context, command, final_filepath, final_filename, task_id, status_message):
    async with SEMAPHORE:
        try:
            DOWNLOAD_TASKS[task_id]['status'] = 'Downloading 📥'
            await status_message.edit_text(f"**File**: `{escape_markdown_v2(final_filename)}`\n**Status**: `Downloading 📥`", parse_mode=ParseMode.MARKDOWN_V2)
            
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _blocking_download, command, task_id)
            
            final_file_size = get_readable_size(os.path.getsize(final_filepath))

            DOWNLOAD_TASKS[task_id]['status'] = 'Uploading 📤'
            await status_message.edit_text(f"**File**: `{escape_markdown_v2(final_filename)}`\n**Status**: `Uploading 📤`\n\nThis may take a while, please be patient\.", parse_mode=ParseMode.MARKDOWN_V2)
            
            gdrive_link = await upload_to_gdrive(final_filepath, final_filename, task_id)
            
            if gdrive_link:
                safe_filename = escape_markdown_v2(final_filename)
                safe_gdrive_link = escape_markdown_v2(gdrive_link)
                safe_size = escape_markdown_v2(final_file_size)
                success_message = (f'✅ **Upload successful\!**\n\n'
                                   f'**File**: `{safe_filename}`\n'
                                   f'**Size**: `{safe_size}`\n'
                                   f'**Link**: {safe_gdrive_link}')
                await status_message.edit_text(success_message, parse_mode=ParseMode.MARKDOWN_V2)
            else:
                await status_message.edit_text(f"❌ **Upload failed for task `{task_id}`**\nCheck server logs or token.pickle.", parse_mode=ParseMode.MARKDOWN_V2)

        except Exception as e:
            error_msg = str(e)[:1000]
            await status_message.edit_text(f'❌ **Task failed for `{escape_markdown_v2(final_filename)}`**\n\n`{escape_markdown_v2(error_msg)}`', parse_mode=ParseMode.MARKDOWN_V2)
        finally:
            if os.path.exists(final_filepath):
                os.remove(final_filepath)
            if task_id in DOWNLOAD_TASKS:
                del DOWNLOAD_TASKS[task_id]

# --- Telegram Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('Hello! Use /m3u8 <command> to start a download.')

async def handle_m3u8_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_authorized(update): return
    try:
        args_string = update.message.text.split(' ', 1)[1]
    except IndexError:
        await update.message.reply_text("Usage: /m3u8 <arguments for N_m3u8DL-RE>")
        return
    
    task_id = generate_task_id()
    parsed_args = shlex.split(args_string)
    
    # Simple logic to guess filename
    save_name = f"video_{task_id}"
    output_format = "mp4"
    if "--save-name" in parsed_args:
        try:
            save_name = parsed_args[parsed_args.index("--save-name") + 1]
        except IndexError: pass
            
    if "-M" in parsed_args:
         try:
            meta_idx = parsed_args.index("-M") + 1
            if meta_idx < len(parsed_args) and "format=mkv" in parsed_args[meta_idx]:
                output_format = 'mkv'
         except IndexError: pass

    final_filename = f"{save_name}.{output_format}"
    final_filepath = os.path.join(DOWNLOAD_DIR, final_filename)
    
    DOWNLOAD_TASKS[task_id] = {'process': None, 'status': 'Queued ⌛', 'filename': final_filename, 'user': update.effective_user}
    
    status_message = await update.message.reply_text(f"✅ Task queued: `{escape_markdown_v2(final_filename)}`", parse_mode=ParseMode.MARKDOWN_V2)
    
    command_list = ['N_m3u8DL-RE'] + parsed_args
    if '--save-dir' not in command_list:
        command_list.extend(['--save-dir', DOWNLOAD_DIR])
    
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    asyncio.create_task(run_download_and_upload_task(update, context, command_list, final_filepath, final_filename, task_id, status_message))

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_authorized(update): return
    status_lines = []
    if not DOWNLOAD_TASKS:
        status_lines.append("*No active tasks\.*\n")
    else:
        status_lines.append("**Active Tasks:**")
        for task_id, task in DOWNLOAD_TASKS.items():
            filename = escape_markdown_v2(task['filename'])
            status = escape_markdown_v2(task['status'])
            status_lines.append(f"🔹 `ID: {task_id}` \- `{filename}` \- `{status}`")
        status_lines.append("")
    
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    disk = psutil.disk_usage('/').percent
    uptime = get_readable_time(time.time() - BOT_START_TIME)
    
    status_lines.extend([
        "**Server Status:**", 
        f"CPU: `{escape_markdown_v2(str(cpu))}%` \| RAM: `{escape_markdown_v2(str(ram))}%` \| DISK: `{escape_markdown_v2(str(disk))}%`", 
        f"UPTIME: `{escape_markdown_v2(uptime)}`"
    ])
    await update.message.reply_text("\n".join(status_lines), parse_mode=ParseMode.MARKDOWN_V2)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_authorized(update): return
    try:
        task_id = context.args[0]
    except IndexError:
        await update.message.reply_text("Usage: /cancel <task_id>")
        return
    
    if task_id in DOWNLOAD_TASKS:
        task = DOWNLOAD_TASKS[task_id]
        if update.effective_user.id == task['user'].id or update.effective_user.id == OWNER_ID:
            if task.get('process'):
                try:
                    task['process'].terminate()
                    await update.message.reply_text(f"✅ Cancel signal sent to task `{task_id}`.")
                except ProcessLookupError:
                    await update.message.reply_text(f"✅ Task `{task_id}` already finished or was cancelled.")
            else:
                del DOWNLOAD_TASKS[task_id]
                await update.message.reply_text(f"✅ Queued task `{task_id}` has been removed.")
        else:
            await update.message.reply_text("⛔️ You are not authorized to cancel this task.")
    else:
        await update.message.reply_text("❌ Task ID not found.")

async def set_drive_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_authorized(update): return
    try:
        drive_id = context.args[0]
        user_id = str(update.effective_user.id)
        drive_ids = load_drive_ids()
        drive_ids[user_id] = drive_id
        save_drive_ids(drive_ids)
        await update.message.reply_text(f"✅ Drive folder ID set successfully\! Uploads will go to:\n`{escape_markdown_v2(drive_id)}`", parse_mode=ParseMode.MARKDOWN_V2)
    except IndexError:
        await update.message.reply_text("Usage: /setid <google_drive_folder_id>")

async def adduser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔️ Only the owner can use this command.")
        return
    try:
        user_id_to_add = int(context.args[0])
        permissions = load_permissions()
        if user_id_to_add not in permissions['authorized_users']:
            permissions['authorized_users'].append(user_id_to_add)
            save_permissions(permissions)
            await update.message.reply_text(f"✅ User `{user_id_to_add}` authorized.")
        else:
            await update.message.reply_text(f"User `{user_id_to_add}` is already authorized.")
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /adduser <user_id>")

async def authorize_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔️ Only the owner can use this command.")
        return
    if update.effective_chat.type not in ['group', 'supergroup']:
        await update.message.reply_text("This command can only be used in a group.")
        return
    
    chat_id = update.effective_chat.id
    permissions = load_permissions()
    if chat_id not in permissions['authorized_groups']:
        permissions['authorized_groups'].append(chat_id)
        save_permissions(permissions)
        await update.message.reply_text(f"✅ Group `{escape_markdown_v2(update.effective_chat.title)}` authorized.", parse_mode=ParseMode.MARKDOWN_V2)
    else:
        await update.message.reply_text("This group is already authorized.")

# --- NEW: UNIFIED FILE HANDLER ---
async def handle_files(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != OWNER_ID:
        return
    
    document = update.message.document
    if not document:
        return

    file_name = document.file_name

    if file_name == CREDENTIALS_FILE:
        # Fixed: Escaped dots and exclamation marks
        await update.message.reply_text('`credentials.json` received\. Saving\.\.\.', parse_mode=ParseMode.MARKDOWN_V2)
        doc_file = await document.get_file()
        await doc_file.download_to_drive(CREDENTIALS_FILE)
        await update.message.reply_text('✅ `credentials.json` updated\!', parse_mode=ParseMode.MARKDOWN_V2)

    elif file_name == TOKEN_FILE:
        # Fixed: Escaped dots, exclamation marks, and fixed bold syntax
        await update.message.reply_text('`token.pickle` received\. Saving\.\.\.', parse_mode=ParseMode.MARKDOWN_V2)
        doc_file = await document.get_file()
        await doc_file.download_to_drive(TOKEN_FILE)
        await update.message.reply_text(
            '✅ `token.pickle` updated\! \n\n⚠️ *Please restart the bot for this to take effect\.*', 
            parse_mode=ParseMode.MARKDOWN_V2
        )

    else:
        # Fixed: Escaped dots
        await update.message.reply_text(
            f"⚠️ Ignored `{escape_markdown_v2(file_name)}`\. I only accept `{CREDENTIALS_FILE}` or `{TOKEN_FILE}`\.", 
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def send_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔️ Unauthorized.")
        return
    if os.path.exists(TOKEN_FILE):
        await update.message.reply_document(document=open(TOKEN_FILE, 'rb'), filename=TOKEN_FILE)
    else:
        await update.message.reply_text('`token.pickle` not found.')

def main() -> None:
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("m3u8", handle_m3u8_command))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("adduser", adduser))
    application.add_handler(CommandHandler("authorize", authorize_group))
    application.add_handler(CommandHandler("send_token", send_token))
    application.add_handler(CommandHandler("setid", set_drive_id))
    
    # File Handler (Supports both JSON and Pickle)
    application.add_handler(MessageHandler(
        filters.Document.FileExtension("json") | filters.Document.FileExtension("pickle"), 
        handle_files
    ))
    
    print("Bot is running...")
    application.run_polling()

if __name__ == '__main__':
    main()
