import os
import re
import time
import json
import requests
import firebase_admin
from firebase_admin import db
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import yt_dlp
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# Initialize Firebase
cred_obj = firebase_admin.credentials.Certificate(json.loads(os.getenv("FIREBASE_CREDENTIALS")))
firebase_admin.initialize_app(cred_obj, {
    'databaseURL': os.getenv("FIREBASE_DATABASE_URL")
})

# Rebrandly API setup
REBRANDLY_API_KEY = os.getenv("REBRANDLY_API_KEY")
REBRANDLY_DOMAIN = "rebrand.ly"

# Cleanup function to delete old links
def cleanup_old_links(context: ContextTypes.DEFAULT_TYPE):
    print("Cleaning up old links...")
    ref = db.reference('downloads')
    links = ref.get()
    if links:
        current_time = int(time.time())
        for link_id, link_data in links.items():
            if current_time - link_data['timestamp'] > 24 * 60 * 60:  # 24 hours
                ref.child(link_id).delete()
    print("Deleted old links")

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send me a YouTube video link, and I'll help you download it!")

# Handle incoming messages
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_text = update.message.text
    youtube_url_pattern = r'(https?://(?:www\.)?youtube\.com/watch\?v=[\w-]+|https?://youtu\.be/[\w-]+)'
    
    if re.match(youtube_url_pattern, message_text):
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "format_sort": ["res", "ext:mp4"], "username": "oauth2", "password": ""}) as ydl:
                info = ydl.extract_info(message_text, download=False)
                formats = info.get('formats', [])
                
                # Filter formats for video + audio
                video_formats = [f for f in formats if f.get('vcodec') != 'none' and f.get('acodec') != 'none']
                resolutions = sorted(set(f.get('height') for f in video_formats if f.get('height')), reverse=True)
                
                if not resolutions:
                    await update.message.reply_text("No downloadable formats found for this video.")
                    return
                
                # Create buttons for each resolution
                keyboard = [
                    [InlineKeyboardButton(f"{res}p", callback_data=f"{message_text}|{res}")]
                    for res in resolutions
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text("Choose a resolution:", reply_markup=reply_markup)
        except Exception as e:
            await update.message.reply_text(f"Error fetching link: {str(e)}\nTry another video.")
    else:
        await update.message.reply_text("Please send a valid YouTube video link.")

# Handle button clicks
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    url, resolution = query.data.split("|")
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "format_sort": ["res", "ext:mp4"], "username": "oauth2", "password": ""}) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = info.get('formats', [])
            
            # Find the format for the selected resolution
            target_format = None
            for f in formats:
                if f.get('height') == int(resolution) and f.get('vcodec') != 'none' and f.get('acodec') != 'none':
                    target_format = f
                    break
            
            if not target_format:
                await query.message.reply_text("Selected resolution not available.")
                return
            
            format_id = target_format['format_id']
            ydl_opts = {
                "format": f"{format_id}+bestaudio/best",
                "get_url": True,
                "merge_output_format": "mp4",
                "postprocessors": [{
                    "key": "FFmpegMerge",
                    "preferredcodec": "mp4",
                }],
                "username": "oauth2",
                "password": "",
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                direct_link = info.get('url')
                
                if not direct_link:
                    await query.message.reply_text("Could not get direct download link.")
                    return
                
                # Shorten the link using Rebrandly
                headers = {
                    "Content-Type": "application/json",
                    "apikey": REBRANDLY_API_KEY
                }
                data = {
                    "destination": direct_link,
                    "domain": {"fullName": REBRANDLY_DOMAIN}
                }
                response = requests.post("https://api.rebrandly.com/v1/links", headers=headers, json=data)
                
                if response.status_code == 200:
                    short_url = response.json()['shortUrl']
                    # Store in Firebase
                    ref = db.reference('downloads')
                    ref.push({
                        'url': short_url,
                        'timestamp': int(time.time())
                    })
                    await query.message.reply_text(f"Download link: {short_url}")
                else:
                    await query.message.reply_text("Error shortening link.")
    except Exception as e:
        await query.message.reply_text(f"Error: {str(e)}")

# Dummy HTTP server for health check
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

def start_health_check_server():
    server = HTTPServer(("0.0.0.0", 8000), HealthCheckHandler)
    server.serve_forever()

# Main function
if __name__ == "__main__":
    print("Starting bot...")
    # Start health check server in a background thread
    threading.Thread(target=start_health_check_server, daemon=True).start()
    
    # Initialize Telegram bot
    app = Application.builder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    # Schedule cleanup job (every 24 hours)
    app.job_queue.run_repeating(cleanup_old_links, interval=24*60*60, first=0)
    
    # Start the bot
    app.run_polling()
