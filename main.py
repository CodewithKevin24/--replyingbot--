import json
import os
import telebot
from telebot import types
from flask import Flask, request, abort
from pymongo import MongoClient

app = Flask(__name__)
TOKEN = os.environ.get('TOKEN')
OWNER_ID = int(os.environ.get('OWNER_ID'))
CALLURL = os.environ.get('WEBHOOK_URL')
CONSOLE_CHANNEL_ID = int(os.environ.get('CONSOLE_CHANNEL_ID'))
MONGO_URI = os.environ.get('MONGO_URI')

# Initialize the bot
bot = telebot.TeleBot(TOKEN)
bot.remove_webhook()
bot.set_webhook(url=CALLURL, drop_pending_updates=False)

# Initialize MongoDB client
client = MongoClient(MONGO_URI)
db = client['telebot_db']
user_chats_collection = db['user_chats']

pending_broadcasts = {}

@app.route('/')
def host():
    base_url = request.base_url
    return f"The HOST URL of this application is: {base_url}"

@app.route('/', methods=['POST'])
def receive_updates():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data(as_text=True)
        update = telebot.types.Update.de_json(json_string)
        if update is not None:
            try:
                bot.process_new_updates([update])
                if update.message and update.message.from_user:
                    user_id = update.message.from_user.id
                    user_chats_collection.update_one(
                        {'_id': user_id},
                        {'$set': {'_id': update.message.chat.id}},
                        upsert=True
                    )
                    console_message = f"User {update.message.from_user.first_name} (ID: {user_id}) Getting Messages."
                    bot.send_message(int(CONSOLE_CHANNEL_ID), console_message, parse_mode="HTML")
            except telebot.apihelper.ApiTelegramException as e:
                if e.error_code == 429:
                    bot.send_message(int(CONSOLE_CHANNEL_ID), "Rate limit exceeded. Waiting for 10 seconds before retrying.")
                else:
                    bot.send_message(int(CONSOLE_CHANNEL_ID), f"Telegram API error: {e}")
        else:
            bot.send_message(int(CONSOLE_CHANNEL_ID), "Received None update")
        return '', 200
    else:
        abort(403)

@bot.message_handler(commands=['start'])
def handle_start(message):
    user_firstname = message.from_user.first_name
    welcome_message = (
        f"👋 Hi, {user_firstname}! 👋\n\n"
        "🤖 This Automated Bot is here to assist you! 🤖\n"
        "Your messages will be forwarded to the Owner. 📬\n"
        "Feel free to start chatting! 💬"
    )
    bot.reply_to(message, welcome_message, parse_mode='Markdown')

@bot.message_handler(commands=['sendall'])
def handle_sendall(message):
    if message.chat.id == OWNER_ID:
        try:
            broadcast_message = message.text.split(" ", 1)[1]  # Extract the message to broadcast
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
            # Export data to JSON file
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
            bot.send_message(OWNER_ID, "Please send the image you want to include with the broadcast message.")
        elif call.data == "confirm_no":
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
        if broadcast_message:
            file_id = message.photo[-1].file_id
            send_broadcast_message(broadcast_message, file_id)
        else:
            bot.send_message(OWNER_ID, "No broadcast message found to send.")
    else:
        user_info = f"**Firstname:** {message.from_user.first_name}\n**Lastname:** {message.from_user.last_name}\n**Username:** {message.from_user.username}\n**Chat ID:** `{message.chat.id}`\n"
        full_message = f"{user_info}\nMessage:```{message.text}```"
        bot.send_message(OWNER_ID, full_message, parse_mode='Markdown')
        bot.reply_to(message, f"📤 Your message has been sent to the Owner.", parse_mode='Markdown')

def send_broadcast_message(broadcast_message, photo_id=None):
    users = user_chats_collection.find()
    total = user_chats_collection.count_documents({})
    successful = 0
    blocked = 0
    deleted = 0
    unsuccessful = 0

    for user in users:
        try:
            if photo_id:
                bot.send_photo(user['_id'], photo_id, caption=broadcast_message)
            else:
                bot.send_message(user['_id'], broadcast_message)
            successful += 1
        except telebot.apihelper.ApiTelegramException as e:
            if e.error_code == 403:
                blocked += 1
            elif e.error_code == 400:
                deleted += 1
            else:
                unsuccessful += 1

    report_message = f"""
<b><u>Broadcast Completed</u></b>
Total Users: <code>{total}</code>
Successful: <code>{successful}</code>
Blocked Users: <code>{blocked}</code>
Deleted Accounts: <code>{deleted}</code>
Unsuccessful: <code>{unsuccessful}</code>
"""
    bot.send_message(OWNER_ID, report_message, parse_mode='HTML')

@bot.message_handler(func=lambda message: True)
def handle_messages(message):
    if message.chat.id == OWNER_ID:
        try:
            user_id, reply_text = map(str, message.text.split(" ", 1))
            user_id = int(user_id)
            if user_id <= 0:
                raise ValueError("Invalid user ID")
            try:
                bot.send_message(user_id, f"👤 **Owner Said:** ```{reply_text}```", parse_mode='Markdown')
                bot.send_message(OWNER_ID, f"✅ Your message to {user_id} has been sent.")
            except telebot.apihelper.ApiTelegramException as e:
                bot.send_message(OWNER_ID, f"❌ Failed to send message to user {user_id}: {e}")
        except ValueError:
            bot.send_message(OWNER_ID, "❗ Please provide a valid user ID and reply message in the correct format.")
    else:
        user_info = f"**Firstname:** {message.from_user.first_name}\n**Lastname:** {message.from_user.last_name}\n**Username:** {message.from_user.username}\n**Chat ID:** `{message.chat.id}`\n"
        full_message = f"{user_info}\nMessage:```{message.text}```"
        bot.send_message(OWNER_ID, full_message, parse_mode='Markdown')
        bot.reply_to(message, f"📤 Your message has been sent to the Owner.", parse_mode='Markdown')

def export_data_to_json():
    # Fetch all documents from the collection
    user_chats = user_chats_collection.find()
    user_chats_list = list(user_chats)

    # Write to JSON file
    with open('telebot_db.json', 'w') as file:
        json.dump(user_chats_list, file, indent=4, default=str)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
