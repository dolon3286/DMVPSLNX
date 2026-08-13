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
from urllib.parse import urljoin
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode

# --- Google Drive Imports ---
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

# --- System Stats Import ---
import psutil

# --- ‼️ IMPORTANT CONFIGURATION ‼️ ---
# 🤖 PUT YOUR TELEGRAM BOT TOKEN HERE
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
# 👑 SET YOUR OWN TELEGRAM USER ID HERE! This is the superuser of the bot.
OWNER_ID = int(os.getenv('OWNER_ID', '0'))
# 📁 THE FOLDER WHERE VIDEOS WILL BE TEMPORARILY DOWNLOADED
DOWNLOAD_DIR = "/root/drm/downloads"
# ⚙️ SET HOW MANY DOWNLOADS CAN RUN AT THE SAME TIME (A small number like 3 is recommended)
MAX_CONCURRENT_DOWNLOADS = 3000
# 📄 File to store authorized user and group IDs
PERMISSIONS_FILE = 'permissions.json'
# --- NEW: File to store user-specific Drive Folder IDs ---
DRIVE_IDS_FILE = 'user_drive_ids.json'
VPN_CONFIG_FILE = 'vpn_config.json'
VPN_CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
VPN_STATE = {'process': None}
QUALITY_SELECTIONS = {}

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
    if days > 0:
        result += f"{int(days)}d"
    if hours > 0:
        result += f"{int(hours)}h"
    if minutes > 0:
        result += f"{int(minutes)}m"
    result += f"{int(secs)}s"
    return result

def get_readable_size(size_in_bytes):
    if size_in_bytes is None:
        return "0B"
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
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)


# --- VPN Configuration Helpers ---
def load_vpn_config():
    if os.path.exists(VPN_CONFIG_FILE):
        with open(VPN_CONFIG_FILE, 'r') as f:
            return json.load(f)
    return {'enabled': False, 'config_path': ''}

def save_vpn_config(config):
    with open(VPN_CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)

def find_local_vpn_config():
    for filename in os.listdir(VPN_CONFIG_DIR):
        if filename.lower().endswith(('.ovpn', '.conf')):
            return os.path.join(VPN_CONFIG_DIR, filename)
    return None

def set_default_vpn_config_if_available():
    config = load_vpn_config()
    config_path = config.get('config_path')
    if config_path and os.path.exists(config_path):
        return config
    local_config = find_local_vpn_config()
    if local_config:
        config.update({'config_path': local_config})
        save_vpn_config(config)
    return config

def get_vpn_server_from_config(config_path):
    if not config_path or not os.path.exists(config_path):
        return 'unknown'
    try:
        with open(config_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith('remote '):
                    parts = stripped.split()
                    if len(parts) >= 2:
                        return parts[1]
                if stripped.startswith('Endpoint') and '=' in stripped:
                    endpoint = stripped.split('=', 1)[1].strip()
                    return endpoint.rsplit(':', 1)[0]
    except OSError:
        pass
    return os.path.basename(config_path)

def get_public_ip_info():
    try:
        from urllib.request import urlopen, Request
        request = Request('https://ipinfo.io/json', headers={'User-Agent': 'Mozilla/5.0'})
        with urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode('utf-8', errors='replace'))
        return {
            'ip': data.get('ip', 'unknown'),
            'city': data.get('city', 'unknown'),
            'region': data.get('region', 'unknown'),
            'country': data.get('country', 'unknown'),
            'org': data.get('org', 'unknown'),
        }
    except Exception as exc:
        return {'error': str(exc)}

def format_ip_info(info):
    if info.get('error'):
        return f"IP check failed: {info['error']}"
    return f"IP: {info['ip']} | Location: {info['city']}, {info['region']}, {info['country']} | ISP: {info['org']}"

def get_vpn_start_command(config_path):
    ext = os.path.splitext(config_path)[1].lower()
    if ext == '.ovpn':
        return ['openvpn', '--config', config_path]
    if ext == '.conf':
        return ['wg-quick', 'up', config_path]
    raise ValueError('Unsupported VPN config type. Put a NordVPN .ovpn file or WireGuard .conf file next to bot.py.')

def get_vpn_stop_command(config_path=None):
    config_path = config_path or load_vpn_config().get('config_path')
    if not config_path:
        return []
    ext = os.path.splitext(config_path)[1].lower()
    if ext == '.ovpn':
        return ['pkill', '-f', f'openvpn --config {config_path}']
    if ext == '.conf':
        return ['wg-quick', 'down', config_path]
    return []

def is_vpn_connected():
    process = VPN_STATE.get('process')
    return process is not None and process.poll() is None

def _run_command_quietly(command):
    subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)

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
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open('token.pickle', 'wb') as token:
                pickle.dump(creds, token)
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
        print("Error: Google Drive credentials are not valid.")
        return None

    user_id = str(DOWNLOAD_TASKS[task_id]['user'].id)
    drive_ids = load_drive_ids()
    folder_id = drive_ids.get(user_id)

    if folder_id:
        print(f"[Task {task_id}] User {user_id} has set a destination folder ID: {folder_id}")
        file_metadata = {'name': file_name, 'parents': [folder_id]}
    else:
        file_metadata = {'name': file_name}

    media = MediaFileUpload(file_path, mimetype='video/mp4', resumable=True)
    loop = asyncio.get_running_loop()
    file = await loop.run_in_executor(None, _blocking_gdrive_upload, service, file_metadata, media)
    file_id = file.get('id')
    await loop.run_in_executor(None, lambda: service.permissions().create(fileId=file_id, body={'type': 'anyone', 'role': 'reader'}).execute())
    return file.get('webViewLink')

# --- Core Download & Upload Logic ---
def _blocking_download(command, task_id):
    """Run the download command using the bot's current network route."""
    print(f"[Task {task_id}] Starting download process...")
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
            await status_message.edit_text(f"**File**: `{escape_markdown_v2(final_filename)}`\n**Status**: `Uploading 📤`\n\nThis may take a while, please be patient\\.", parse_mode=ParseMode.MARKDOWN_V2)
            gdrive_link = await upload_to_gdrive(final_filepath, final_filename, task_id)

            if gdrive_link:
                safe_filename = escape_markdown_v2(final_filename)
                safe_gdrive_link = escape_markdown_v2(gdrive_link)
                safe_size = escape_markdown_v2(final_file_size)
                success_message = (f'✅ **Upload successful\\!**\n\n'
                                   f'**File**: `{safe_filename}`\n'
                                   f'**Size**: `{safe_size}`\n'
                                   f'**Link**: {safe_gdrive_link}')
                await status_message.edit_text(success_message, parse_mode=ParseMode.MARKDOWN_V2)
            else:
                await status_message.edit_text(f"❌ **Upload failed for task `{task_id}`**", parse_mode=ParseMode.MARKDOWN_V2)

        except Exception as e:
            await status_message.edit_text(f'❌ **Task failed for `{escape_markdown_v2(final_filename)}`**\n\n`{escape_markdown_v2(str(e)[:1000])}`', parse_mode=ParseMode.MARKDOWN_V2)
        finally:
            if os.path.exists(final_filepath):
                os.remove(final_filepath)
            if task_id in DOWNLOAD_TASKS:
                del DOWNLOAD_TASKS[task_id]

# --- Telegram Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('Hello! Use /m3u8 <command> to start a download.')


def extract_m3u8_url(parsed_args):
    for arg in parsed_args:
        if arg.startswith('http://') or arg.startswith('https://'):
            return arg
    return None

def parse_m3u8_variants(content, base_url):
    variants = []
    lines = content.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith('#EXT-X-STREAM-INF'):
            continue
        attributes = {}
        for key, value in re.findall(r'([A-Z0-9-]+)=((?:"[^"]+")|[^,]+)', line):
            attributes[key] = value.strip('"')
        stream_url = None
        for next_line in lines[index + 1:]:
            next_line = next_line.strip()
            if next_line and not next_line.startswith('#'):
                stream_url = next_line
                break
        if not stream_url:
            continue
        if not stream_url.startswith(('http://', 'https://')):
            stream_url = urljoin(base_url, stream_url)
        resolution = attributes.get('RESOLUTION', 'unknown')
        bandwidth = attributes.get('BANDWIDTH', '0')
        try:
            bandwidth_label = f"{int(bandwidth) // 1000} kbps"
        except ValueError:
            bandwidth_label = f"{bandwidth} bps"
        name = attributes.get('NAME') or f"{resolution} ({bandwidth_label})"
        variants.append({'name': name, 'url': stream_url, 'resolution': resolution, 'bandwidth': bandwidth})
    return variants

def _blocking_fetch_qualities(url):
    from urllib.request import urlopen, Request
    request = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urlopen(request, timeout=20) as response:
        content = response.read().decode('utf-8', errors='replace')
    return parse_m3u8_variants(content, url)

async def show_quality_buttons(update, context, parsed_args):
    m3u8_url = extract_m3u8_url(parsed_args)
    if not m3u8_url:
        await update.message.reply_text('No m3u8 URL found in your command.')
        return
    loop = asyncio.get_running_loop()
    variants = await loop.run_in_executor(None, _blocking_fetch_qualities, m3u8_url)
    if not variants:
        await update.message.reply_text('No variant qualities found. Starting direct download instead.')
        await start_m3u8_download(update, context, parsed_args)
        return
    selection_id = generate_task_id()
    QUALITY_SELECTIONS[selection_id] = {
        'user_id': update.effective_user.id,
        'parsed_args': parsed_args,
        'original_url': m3u8_url,
        'variants': variants,
        'selected': set(),
        'created_at': time.time(),
    }
    keyboard = build_quality_keyboard(selection_id)
    await update.message.reply_text('Select one or more qualities, then press Start downloads:', reply_markup=keyboard)

def build_quality_keyboard(selection_id):
    selection = QUALITY_SELECTIONS[selection_id]
    buttons = []
    for index, variant in enumerate(selection['variants']):
        checked = '✅ ' if index in selection['selected'] else ''
        label = f"{checked}{variant['name']}"
        buttons.append([InlineKeyboardButton(label[:64], callback_data=f'qsel:{selection_id}:{index}')])
    buttons.append([
        InlineKeyboardButton('Start downloads', callback_data=f'qstart:{selection_id}'),
        InlineKeyboardButton('Cancel', callback_data=f'qcancel:{selection_id}'),
    ])
    return InlineKeyboardMarkup(buttons)

async def handle_quality_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action, selection_id, *rest = query.data.split(':')
    selection = QUALITY_SELECTIONS.get(selection_id)
    if not selection:
        await query.edit_message_text('This quality selection has expired.')
        return
    if query.from_user.id != selection['user_id'] and query.from_user.id != OWNER_ID:
        await query.answer('You cannot use this selection.', show_alert=True)
        return
    if action == 'qsel':
        index = int(rest[0])
        if index in selection['selected']:
            selection['selected'].remove(index)
        else:
            selection['selected'].add(index)
        await query.edit_message_reply_markup(reply_markup=build_quality_keyboard(selection_id))
        return
    if action == 'qcancel':
        del QUALITY_SELECTIONS[selection_id]
        await query.edit_message_text('Quality selection cancelled.')
        return
    if action == 'qstart':
        selected_indexes = sorted(selection['selected'])
        if not selected_indexes:
            await query.answer('Select at least one quality first.', show_alert=True)
            return
        await query.edit_message_text(f'Starting {len(selected_indexes)} selected download(s)...')
        for index in selected_indexes:
            variant = selection['variants'][index]
            args = [variant['url'] if arg == selection['original_url'] else arg for arg in selection['parsed_args']]
            quality_suffix = re.sub(r'[^A-Za-z0-9_-]+', '_', variant['name']).strip('_') or f'quality_{index + 1}'
            if '--save-name' in args:
                name_index = args.index('--save-name') + 1
                args[name_index] = f"{args[name_index]}_{quality_suffix}"
            else:
                args.extend(['--save-name', f'video_{quality_suffix}'])
            await start_m3u8_download(update, context, args, query.message)
        del QUALITY_SELECTIONS[selection_id]

async def start_m3u8_download(update, context, parsed_args, reply_target=None):
    task_id = generate_task_id()
    save_name = f"video_{task_id}"
    output_format = "mp4"
    if "--save-name" in parsed_args:
        save_name = parsed_args[parsed_args.index("--save-name") + 1]
    if "-M" in parsed_args and "format=mkv" in parsed_args[parsed_args.index("-M") + 1]:
        output_format = 'mkv'
    final_filename = f"{save_name}.{output_format}"
    final_filepath = os.path.join(DOWNLOAD_DIR, final_filename)
    DOWNLOAD_TASKS[task_id] = {'process': None, 'status': 'Queued ⌛', 'filename': final_filename, 'user': update.effective_user}
    target = reply_target or update.message
    status_message = await target.reply_text(f"✅ Task queued: `{escape_markdown_v2(final_filename)}`", parse_mode=ParseMode.MARKDOWN_V2)
    command_list = ['N_m3u8DL-RE'] + parsed_args
    if '--save-dir' not in command_list:
        command_list.extend(['--save-dir', DOWNLOAD_DIR])
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    asyncio.create_task(run_download_and_upload_task(update, context, command_list, final_filepath, final_filename, task_id, status_message))

async def handle_m3u8_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_authorized(update):
        return
    try:
        args_string = update.message.text.split(' ', 1)[1]
    except IndexError:
        await update.message.reply_text("Usage: /m3u8 <arguments for N_m3u8DL-RE>")
        return
    parsed_args = shlex.split(args_string)
    if '--quality-select' in parsed_args:
        parsed_args.remove('--quality-select')
        await show_quality_buttons(update, context, parsed_args)
        return
    await start_m3u8_download(update, context, parsed_args)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_authorized(update):
        return
    status_lines = []
    if not DOWNLOAD_TASKS:
        status_lines.append("*No active tasks\\.*\n")
    else:
        status_lines.append("**Active Tasks:**")
        for task_id, task in DOWNLOAD_TASKS.items():
            filename = escape_markdown_v2(task['filename'])
            status = escape_markdown_v2(task['status'])
            status_lines.append(f"🔹 `ID: {task_id}` \\- `{filename}` \\- `{status}`")
        status_lines.append("")
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    disk = psutil.disk_usage('/').percent
    uptime = get_readable_time(time.time() - BOT_START_TIME)
    cpu_str = escape_markdown_v2(str(cpu))
    ram_str = escape_markdown_v2(str(ram))
    disk_str = escape_markdown_v2(str(disk))
    uptime_str = escape_markdown_v2(uptime)
    status_lines.extend(["**Server Status:**", f"CPU: `{cpu_str}%` \\| RAM: `{ram_str}%` \\| DISK: `{disk_str}%`", f"UPTIME: `{uptime_str}`"])
    await update.message.reply_text("\n".join(status_lines), parse_mode=ParseMode.MARKDOWN_V2)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_authorized(update):
        return
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
    if not await is_authorized(update):
        return
    try:
        drive_id = context.args[0]
        user_id = str(update.effective_user.id)

        drive_ids = load_drive_ids()
        drive_ids[user_id] = drive_id
        save_drive_ids(drive_ids)

        await update.message.reply_text(f"✅ Drive folder ID set successfully\\! Your uploads will now go to:\n`{escape_markdown_v2(drive_id)}`", parse_mode=ParseMode.MARKDOWN_V2)
    except IndexError:
        await update.message.reply_text("Usage: /setid <google_drive_folder_id>")


async def connect_vpn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔️ Only the owner can use this command.")
        return
    if is_vpn_connected():
        config = load_vpn_config()
        server = get_vpn_server_from_config(config.get('config_path'))
        info = format_ip_info(get_public_ip_info())
        await update.message.reply_text(f"✅ VPN is already connected.\nServer: {server}\n{info}")
        return

    config = set_default_vpn_config_if_available()
    config_path = config.get('config_path')
    if not config_path or not os.path.exists(config_path):
        await update.message.reply_text("❌ Put your NordVPN .ovpn or WireGuard .conf file in the same directory as bot.py, then type /connect.")
        return

    server = get_vpn_server_from_config(config_path)
    await update.message.reply_text(f"🔌 Connecting VPN...\nConfig: {os.path.basename(config_path)}\nServer: {server}")
    try:
        command = get_vpn_start_command(config_path)
        if os.path.splitext(config_path)[1].lower() == '.ovpn':
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            VPN_STATE['process'] = process
            await asyncio.sleep(8)
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ''
                raise Exception(output or 'OpenVPN exited before connection completed.')
        else:
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
            if result.returncode != 0:
                raise Exception(result.stderr or result.stdout)
            VPN_STATE['process'] = None
        config['enabled'] = True
        config['connected_at'] = time.time()
        save_vpn_config(config)
        info = format_ip_info(get_public_ip_info())
        await update.message.reply_text(f"✅ VPN connected.\nServer: {server}\n{info}")
    except Exception as exc:
        VPN_STATE['process'] = None
        config['enabled'] = False
        save_vpn_config(config)
        await update.message.reply_text(f"❌ VPN connection failed:\n{str(exc)[:1000]}")

async def vpn_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_authorized(update):
        return
    config = set_default_vpn_config_if_available()
    connected = is_vpn_connected() or bool(config.get('enabled') and os.path.splitext(config.get('config_path', ''))[1].lower() == '.conf')
    state = 'connected' if connected else 'disconnected'
    path = config.get('config_path') or 'not found'
    server = get_vpn_server_from_config(path)
    info = format_ip_info(get_public_ip_info())
    await update.message.reply_text(f"VPN is {state}\nConfig: {os.path.basename(path)}\nServer: {server}\n{info}")

async def disconnect_vpn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔️ Only the owner can use this command.")
        return
    config = load_vpn_config()
    process = VPN_STATE.get('process')
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
    stop_command = get_vpn_stop_command(config.get('config_path'))
    if stop_command:
        _run_command_quietly(stop_command)
    VPN_STATE['process'] = None
    config['enabled'] = False
    save_vpn_config(config)
    info = format_ip_info(get_public_ip_info())
    await update.message.reply_text(f"✅ VPN disconnected.\n{info}")

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
            await update.message.reply_text(f"✅ User `{user_id_to_add}` has been authorized.")
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
        await update.message.reply_text(f"✅ Group `{escape_markdown_v2(update.effective_chat.title)}` is now authorized.", parse_mode=ParseMode.MARKDOWN_V2)
    else:
        await update.message.reply_text("This group is already authorized.")

async def handle_credentials(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message.from_user.id != OWNER_ID:
        return
    if not update.message.document:
        return
    filename = update.message.document.file_name
    if filename == 'credentials.json':
        destination = 'credentials.json'
    elif filename == 'token.pickle':
        destination = 'token.pickle'
    elif filename.lower().endswith(('.ovpn', '.conf')):
        destination = os.path.join(VPN_CONFIG_DIR, filename)
    else:
        return

    await update.message.reply_text(f'{filename} received. Saving...')
    doc_file = await update.message.document.get_file()
    await doc_file.download_to_drive(destination)

    if filename.lower().endswith(('.ovpn', '.conf')):
        config = load_vpn_config()
        config.update({'config_path': destination})
        save_vpn_config(config)
        await update.message.reply_text(f'✅ VPN config saved next to bot.py as {filename}. Type /connect to connect.')
    else:
        await update.message.reply_text(f'✅ {filename} has been updated!')

async def send_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔️ Unauthorized.")
        return
    if os.path.exists('token.pickle'):
        await update.message.reply_document(document=open('token.pickle', 'rb'), filename='token.pickle')
    else:
        await update.message.reply_text('`token.pickle` not found.')

def main() -> None:
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("m3u8", handle_m3u8_command))
    application.add_handler(CallbackQueryHandler(handle_quality_callback, pattern=r"^q(sel|start|cancel):"))
    application.add_handler(CommandHandler("connect", connect_vpn))
    application.add_handler(CommandHandler("vpnstatus", vpn_status))
    application.add_handler(CommandHandler("disconnect", disconnect_vpn))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("adduser", adduser))
    application.add_handler(CommandHandler("authorize", authorize_group))
    application.add_handler(CommandHandler("send_token", send_token))
    application.add_handler(CommandHandler("upload_credentials", handle_credentials))
    application.add_handler(CommandHandler("setid", set_drive_id))
    application.add_handler(MessageHandler(filters.Document.FileExtension("json") | filters.Document.FileExtension("pickle") | filters.Document.FileExtension("ovpn") | filters.Document.FileExtension("conf"), handle_credentials))
    print("Bot is running...")
    application.run_polling()

if __name__ == '__main__':
    main()
