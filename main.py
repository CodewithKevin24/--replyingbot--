import json
import os
import telebot
from telebot import types
from flask import Flask, request, abort
from pymongo import MongoClient

app = Flask(__name__)
TOKEN = os.getenv('TOKEN')
OWNER_ID = int(os.getenv('OWNER_ID'))
CALLURL = os.getenv('WEBHOOK_URL')
CONSOLE_CHANNEL_ID = int(os.getenv('CONSOLE_CHANNEL_ID'))
MONGO_URI = os.getenv('MONGO_URI')
LOG_CHANNEL_ID = int(os.getenv('LOG_CHANNEL_ID'))

bot = telebot.TeleBot(TOKEN)
bot.remove_webhook()
bot.set_webhook(url=CALLURL, drop_pending_updates=False)

client = MongoClient(MONGO_URI, server_api=ServerApi('1'), tlsCAFile=certifi.where())
db = client['telebot_db']
user_chats_collection = db['user_chats']
pending_broadcasts = {}

@app.route('/')
def host():
    return f"The HOST URL of this application is: {request.base_url}"

@app.route('/', methods=['POST'])
def receive_updates():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data(as_text=True)
        update = telebot.types.Update.de_json(json_string)
        if update is not None:
            try:
                bot.process_new_updates([update])
                if update.message and update.message.from_user:
                    user_first_name = update.message.from_user.first_name
                    user_id = update.message.from_user.id
                    console_message = f"User {user_first_name} (Chat ID: {user_id}) Getting Videos."
                    bot.send_message(CONSOLE_CHANNEL_ID, console_message, parse_mode="HTML")
            except telebot.apihelper.ApiTelegramException as e:
                if e.error_code == 429:
                    print(f"Rate limit exceeded. Waiting for 10 seconds before retrying.")
                    time.sleep(10)
                    bot.process_new_updates([update])
                else:
                    print(f"Telegram API error: {e}")
        else:
            print("Received None update")
        return '', 200
    else:
        abort(403)

@bot.message_handler(commands=['start'])
def handle_start(message):
    welcome_message = (
        f"👋 Hi, {message.from_user.first_name}! 👋\n\n"
        "🤖 This Automated Bot is here to assist you! 🤖\n"
        "Your messages will be forwarded to the Owner. 📬\n"
        "Feel free to start chatting! 💬"
    )
    bot.reply_to(message, welcome_message, parse_mode='Markdown')

@bot.message_handler(commands=['sendall'])
def handle_sendall(message):
    if message.chat.id == OWNER_ID:
        try:
            broadcast_message = message.text.split(" ", 1)[1]
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("Yes", callback_data="confirm_yes"),
                       types.InlineKeyboardButton("No", callback_data="confirm_no"))
            pending_broadcasts[OWNER_ID] = broadcast_message
            bot.send_message(OWNER_ID, "Do you want to send an image with the broadcast message?", reply_markup=markup)
        except IndexError:
            bot.send_message(OWNER_ID, "❗ Please provide a message to broadcast.")
    else:
        bot.send_message(message.chat.id, "❗ You are not authorized to use this command.")

@bot.message_handler(commands=['exportdata'])
def handle_exportdata(message):
    if message.chat.id == OWNER_ID:
        try:
            export_data_to_json()
            with open('telebot_db.json', 'rb') as file:
                bot.send_document(OWNER_ID, file, caption="Here is the exported data.")
            bot.send_message(OWNER_ID, "✅ Data export completed and sent.")
        except Exception as e:
            bot.send_message(OWNER_ID, f"❌ An error occurred: {e}")
    else:
        bot.send_message(message.chat.id, "❗ You are not authorized to use this command.")

@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_"))
def confirm_broadcast(call):
    if call.message.chat.id == OWNER_ID:
        if call.data == "confirm_yes":
            bot.edit_message_text("Please send the image you want to include with the broadcast message.", chat_id=call.message.chat.id, message_id=call.message.message_id)
        elif call.data == "confirm_no":
            bot.delete_message(chat_id=call.message.chat.id, message_id=call.message.message_id)
            broadcast_message = pending_broadcasts.pop(OWNER_ID, "")
            if broadcast_message:
                send_broadcast_message(broadcast_message)
            else:
                bot.send_message(OWNER_ID, "No broadcast message found to send.")
    bot.answer_callback_query(call.id)

@bot.message_handler(content_types=['photo'])
def handle_image(message):
    if message.chat.id == OWNER_ID and OWNER_ID in pending_broadcasts:
        broadcast_message = pending_broadcasts.pop(OWNER_ID, "")
        send_broadcast_message(broadcast_message, message.photo[-1].file_id)
    else:
        forward_to_owner(message)

def send_broadcast_message(broadcast_message, photo_id=None):
    users = user_chats_collection.find()
    stats = {'total': user_chats_collection.count_documents({}), 'successful': 0, 'blocked': 0, 'deleted': 0, 'unsuccessful': 0}

    for user in users:
        try:
            if photo_id:
                bot.send_photo(user['_id'], photo_id, caption=broadcast_message)
            else:
                bot.send_message(user['_id'], broadcast_message)
            stats['successful'] += 1
        except telebot.apihelper.ApiTelegramException as e:
            if e.error_code == 403:
                stats['blocked'] += 1
            elif e.error_code == 400:
                stats['deleted'] += 1
            else:
                stats['unsuccessful'] += 1

    report_message = (
        f"<b><u>Broadcast Completed</u></b>\n"
        f"Total Users: <code>{stats['total']}</code>\n"
        f"Successful: <code>{stats['successful']}</code>\n"
        f"Blocked Users: <code>{stats['blocked']}</code>\n"
        f"Deleted Accounts: <code>{stats['deleted']}</code>\n"
        f"Unsuccessful: <code>{stats['unsuccessful']}</code>\n"
    )
    bot.send_message(OWNER_ID, report_message, parse_mode='HTML')

@bot.message_handler(func=lambda message: True)
def handle_messages(message):
    if message.chat.id == OWNER_ID:
        handle_owner_message(message)
    else:
        forward_to_owner(message)

def handle_owner_message(message):
    try:
        user_id, reply_text = map(str, message.text.split(" ", 1))
        user_id = int(user_id)
        if user_id > 0:
            bot.send_message(user_id, f"👤 **Owner Said:** {reply_text}", parse_mode='Markdown')
            bot.send_message(OWNER_ID, f"✅ Your message to {user_id} has been sent.")
        else:
            raise ValueError("Invalid user ID")
    except (ValueError, IndexError):
        bot.send_message(OWNER_ID, "❗ Please provide a valid user ID and reply message in the correct format.")

def forward_to_owner(message):
    user_info = (
        f"**Firstname:** {message.from_user.first_name}\n"
        f"**Lastname:** {message.from_user.last_name}\n"
        f"**Username:** {message.from_user.username}\n"
        f"**Chat ID:** `{message.chat.id}`\n"
    )
    full_message = f"{user_info}\nMessage:{message.text}"
    bot.send_message(OWNER_ID, full_message, parse_mode='Markdown')
    bot.reply_to(message, "📤 Your message has been sent to the Owner.", parse_mode='Markdown')

def export_data_to_json():
    user_chats = list(user_chats_collection.find())
    with open('telebot_db.json', 'w') as file:
        json.dump(user_chats, file, indent=4, default=str)

@bot.message_handler(func=lambda message: True, content_types=['text', 'photo', 'audio', 'video', 'document', 'sticker', 'voice', 'location', 'contact', 'video_note'])
def forward_to_log_channel(message):
    try:
        bot.forward_message(LOG_CHANNEL_ID, message.chat.id, message.message_id)
    except Exception as e:
        print(f"Failed to forward message: {e}")

# Send a ping to confirm a successful connection
try:
    client.admin.command('ping')
    print("Pinged your deployment. You successfully connected to MongoDB!")
    bot.send_message(CONSOLE_CHANNEL_ID, "Pinged your deployment. You successfully connected to MongoDB!", parse_mode="HTML")
except Exception as e:
    print(e)
    bot.send_message(CONSOLE_CHANNEL_ID, f"Failed to connect to MongoDB: {e}", parse_mode="HTML")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
import json
import os
import telebot
from telebot import types
from flask import Flask, request, abort
from pymongo import MongoClient

app = Flask(__name__)
TOKEN = os.getenv('TOKEN')
OWNER_ID = int(os.getenv('OWNER_ID'))
CALLURL = os.getenv('WEBHOOK_URL')
CONSOLE_CHANNEL_ID = int(os.getenv('CONSOLE_CHANNEL_ID'))
MONGO_URI = os.getenv('MONGO_URI')
LOG_CHANNEL_ID = int(os.getenv('LOG_CHANNEL_ID'))

bot = telebot.TeleBot(TOKEN)
bot.remove_webhook()
bot.set_webhook(url=CALLURL, drop_pending_updates=False)

client = MongoClient(MONGO_URI)
db = client['telebot_db']
user_chats_collection = db['user_chats']
pending_broadcasts = {}

@app.route('/')
def host():
    return f"The HOST URL of this application is: {request.base_url}"

@app.route('/', methods=['POST'])
def receive_updates():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data(as_text=True)
        update = telebot.types.Update.de_json(json_string)
        if update is not None:
            try:
                bot.process_new_updates([update])
                if update.message and update.message.from_user:
                    user_first_name = update.message.from_user.first_name
                    user_id = update.message.from_user.id
                    console_message = f"User {user_first_name} (Chat ID: {user_id}) Getting Videos."
                    bot.send_message(CONSOLE_CHANNEL_ID, console_message, parse_mode="HTML")
            except telebot.apihelper.ApiTelegramException as e:
                if e.error_code == 429:
                    print(f"Rate limit exceeded. Waiting for 10 seconds before retrying.")
                    time.sleep(10)
                    bot.process_new_updates([update])
                else:
                    print(f"Telegram API error: {e}")
        else:
            print("Received None update")
        return '', 200
    else:
        abort(403)

@bot.message_handler(commands=['start'])
def handle_start(message):
    welcome_message = (
        f"👋 Hi, {message.from_user.first_name}! 👋\n\n"
        "🤖 This Automated Bot is here to assist you! 🤖\n"
        "Your messages will be forwarded to the Owner. 📬\n"
        "Feel free to start chatting! 💬"
    )
    bot.reply_to(message, welcome_message, parse_mode='Markdown')

@bot.message_handler(commands=['sendall'])
def handle_sendall(message):
    if message.chat.id == OWNER_ID:
        try:
            broadcast_message = message.text.split(" ", 1)[1]
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("Yes", callback_data="confirm_yes"),
                       types.InlineKeyboardButton("No", callback_data="confirm_no"))
            pending_broadcasts[OWNER_ID] = broadcast_message
            bot.send_message(OWNER_ID, "Do you want to send an image with the broadcast message?", reply_markup=markup)
        except IndexError:
            bot.send_message(OWNER_ID, "❗ Please provide a message to broadcast.")
    else:
        bot.send_message(message.chat.id, "❗ You are not authorized to use this command.")

@bot.message_handler(commands=['exportdata'])
def handle_exportdata(message):
    if message.chat.id == OWNER_ID:
        try:
            export_data_to_json()
            with open('telebot_db.json', 'rb') as file:
                bot.send_document(OWNER_ID, file, caption="Here is the exported data.")
            bot.send_message(OWNER_ID, "✅ Data export completed and sent.")
        except Exception as e:
            bot.send_message(OWNER_ID, f"❌ An error occurred: {e}")
    else:
        bot.send_message(message.chat.id, "❗ You are not authorized to use this command.")

@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_"))
def confirm_broadcast(call):
    if call.message.chat.id == OWNER_ID:
        if call.data == "confirm_yes":
            bot.edit_message_text("Please send the image you want to include with the broadcast message.", chat_id=call.message.chat.id, message_id=call.message.message_id)
        elif call.data == "confirm_no":
            bot.delete_message(chat_id=call.message.chat.id, message_id=call.message.message_id)
            broadcast_message = pending_broadcasts.pop(OWNER_ID, "")
            if broadcast_message:
                send_broadcast_message(broadcast_message)
            else:
                bot.send_message(OWNER_ID, "No broadcast message found to send.")
    bot.answer_callback_query(call.id)

@bot.message_handler(content_types=['photo'])
def handle_image(message):
    if message.chat.id == OWNER_ID and OWNER_ID in pending_broadcasts:
        broadcast_message = pending_broadcasts.pop(OWNER_ID, "")
        send_broadcast_message(broadcast_message, message.photo[-1].file_id)
    else:
        forward_to_owner(message)

def send_broadcast_message(broadcast_message, photo_id=None):
    users = user_chats_collection.find()
    stats = {'total': user_chats_collection.count_documents({}), 'successful': 0, 'blocked': 0, 'deleted': 0, 'unsuccessful': 0}

    for user in users:
        try:
            if photo_id:
                bot.send_photo(user['_id'], photo_id, caption=broadcast_message)
            else:
                bot.send_message(user['_id'], broadcast_message)
            stats['successful'] += 1
        except telebot.apihelper.ApiTelegramException as e:
            if e.error_code == 403:
                stats['blocked'] += 1
            elif e.error_code == 400:
                stats['deleted'] += 1
            else:
                stats['unsuccessful'] += 1

    report_message = (
        f"<b><u>Broadcast Completed</u></b>\n"
        f"Total Users: <code>{stats['total']}</code>\n"
        f"Successful: <code>{stats['successful']}</code>\n"
        f"Blocked Users: <code>{stats['blocked']}</code>\n"
        f"Deleted Accounts: <code>{stats['deleted']}</code>\n"
        f"Unsuccessful: <code>{stats['unsuccessful']}</code>\n"
    )
    bot.send_message(OWNER_ID, report_message, parse_mode='HTML')

@bot.message_handler(func=lambda message: True)
def handle_messages(message):
    if message.chat.id == OWNER_ID:
        handle_owner_message(message)
    else:
        forward_to_owner(message)

def handle_owner_message(message):
    try:
        user_id, reply_text = map(str, message.text.split(" ", 1))
        user_id = int(user_id)
        if user_id > 0:
            bot.send_message(user_id, f"👤 **Owner Said:** {reply_text}", parse_mode='Markdown')
            bot.send_message(OWNER_ID, f"✅ Your message to {user_id} has been sent.")
        else:
            raise ValueError("Invalid user ID")
    except (ValueError, IndexError):
        bot.send_message(OWNER_ID, "❗ Please provide a valid user ID and reply message in the correct format.")

def forward_to_owner(message):
    user_info = (
        f"**Firstname:** {message.from_user.first_name}\n"
        f"**Lastname:** {message.from_user.last_name}\n"
        f"**Username:** {message.from_user.username}\n"
        f"**Chat ID:** `{message.chat.id}`\n"
    )
    full_message = f"{user_info}\nMessage:{message.text}"
    bot.send_message(OWNER_ID, full_message, parse_mode='Markdown')
    bot.reply_to(message, "📤 Your message has been sent to the Owner.", parse_mode='Markdown')

def export_data_to_json():
    user_chats = list(user_chats_collection.find())
    with open('telebot_db.json', 'w') as file:
        json.dump(user_chats, file, indent=4, default=str)

@bot.message_handler(func=lambda message: True, content_types=['text', 'photo', 'audio', 'video', 'document', 'sticker', 'voice', 'location', 'contact', 'video_note'])
def forward_to_log_channel(message):
    try:
        bot.forward_message(LOG_CHANNEL_ID, message.chat.id, message.message_id)
    except Exception as e:
        print(f"Failed to forward message: {e}")

# Send a ping to confirm a successful connection
try:
    client.admin.command('ping')
    print("Pinged your deployment. You successfully connected to MongoDB!")
    bot.send_message(CONSOLE_CHANNEL_ID, "Pinged your deployment. You successfully connected to MongoDB!", parse_mode="HTML")
except Exception as e:
    print(e)
    bot.send_message(CONSOLE_CHANNEL_ID, f"Failed to connect to MongoDB: {e}", parse_mode="HTML")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
