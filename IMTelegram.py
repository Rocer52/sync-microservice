# IMTelegram.py
from flask import Flask, request, jsonify, send_from_directory
from flask_swagger_ui import get_swaggerui_blueprint
import config
import logging
import requests
import os
import shutil

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

chat_ids = set()

def add_chat_id(chat_id: str):
    chat_ids.add(chat_id)
    logger.debug(f"Added chat_id={chat_id} to chat_ids set")

# --- Swagger UI Setup ---
SWAGGER_URL = '/IMTelegram/swagger'
API_URL = '/static/openapi.yaml'
swaggerui_blueprint = get_swaggerui_blueprint(
    SWAGGER_URL,
    API_URL,
    config={'app_name': "IM Telegram Service"}
)
app.register_blueprint(swaggerui_blueprint, url_prefix=SWAGGER_URL)

@app.route('/static/<path:path>')
def send_static(path):
    return send_from_directory('static', path)
# ------------------------

@app.route('/IMTelegram/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    if data is None or 'message' not in data:
        logger.error("Invalid or missing message in webhook request")
        return {"ok": False, "message": "Invalid request"}, 400

    message_text = data['message'].get('text', '')
    chat = data['message'].get('chat')
    if not chat:
        return {"ok": False, "message": "No chat info"}, 400

    chat_id = str(chat.get('id'))
    user_id = str(data['message']['from'].get('id', 'Unknown'))
    username = data['message']['from'].get('username', data['message']['from'].get('first_name', 'User')).lstrip('@')
    
    logger.info(f"Received message: chat_id={chat_id}, user_id={user_id}, username={username}, text={message_text}")
    add_chat_id(chat_id)

    payload = {
        "message_text": message_text,
        "chat_id": chat_id,
        "platform": "telegram",
        "user_id": user_id,
        "username": username
    }
    try:
        # Forward to IoT Broker
        response = requests.post(config.IOT_BROKER_URL + "/IoTBroker/parse_message", json=payload, timeout=5)
        if response.status_code == 200:
            logger.info("Successfully processed by IoT Broker")
        else:
            logger.error(f"Failed to process in IoT Broker: {response.text}")
    except Exception as e:
        logger.error(f"Error calling IoT Broker: {e}")

    return {"ok": True}, 200

@app.route('/IMTelegram/send_message', methods=['POST'])
def send_telegram_message():
    try:
        data = request.json
        chat_id = data['chat_id']
        text = data['text']
        url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        response = requests.post(url, json=payload, timeout=5)
        if response.status_code == 200 and response.json().get("ok"):
            return jsonify({"ok": True}), 200
        else:
            logger.error(f"Failed to send Telegram message: {response.text}")
            return jsonify({"ok": False, "message": response.text}), 500
    except Exception as e:
        logger.error(f"Error in send_message: {e}")
        return jsonify({"ok": False, "message": str(e)}), 500

if __name__ == "__main__":
    # Ensure static directory exists and openapi.yaml is available
    if not os.path.exists('static'):
        os.makedirs('static')
    if os.path.exists('openapi.yaml'):
        shutil.copy('openapi.yaml', 'static/openapi.yaml')
    
    logger.info(f"Starting IMTelegram service on port {config.TELEGRAM_API_PORT}")
    logger.info(f"Swagger UI available at http://localhost:{config.TELEGRAM_API_PORT}{SWAGGER_URL}")
    app.run(host="0.0.0.0", port=config.TELEGRAM_API_PORT)