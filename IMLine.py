# IMLine.py
from flask import Flask, request, jsonify
import config
import logging
import requests
from linebot import LineBotApi
from linebot.exceptions import LineBotApiError
from linebot.models import TextSendMessage

# Configure logging with file output for debugging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Initialize LineBotApi
try:
    line_bot_api = LineBotApi(config.LINE_ACCESS_TOKEN)
    logger.info("LineBotApi initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize LineBotApi: {e}")
    raise

user_ids = set()

def add_user_id(user_id: str):
    user_ids.add(user_id)
    logger.debug(f"Added user_id={user_id} to user_ids set")

def get_line_user_display_name(user_id: str) -> str:
    try:
        profile = line_bot_api.get_profile(user_id)
        display_name = profile.display_name or "User"
        logger.debug(f"Fetched display name for user_id={user_id}: {display_name}")
        return display_name
    except LineBotApiError as e:
        logger.warning(f"Failed to fetch display name for user_id={user_id}: {e}")
        return "User"

@app.route('/webhook', methods=['POST'], strict_slashes=False)
def webhook():
    # Log request headers and raw payload
    logger.debug(f"Received webhook request with headers: {dict(request.headers)}")
    try:
        body = request.get_data(as_text=True)
        logger.debug(f"Webhook payload: {body}")
    except Exception as e:
        logger.error(f"Failed to read webhook payload: {e}")
        return {"ok": False, "message": "Invalid payload"}, 400

    # Parse JSON payload
    try:
        data = request.get_json()
        logger.debug(f"Parsed webhook JSON: {data}")
    except Exception as e:
        logger.error(f"Failed to parse webhook JSON: {e}")
        return {"ok": False, "message": "Invalid JSON"}, 400

    if not data or 'events' not in data:
        logger.error("Invalid or missing 'events' in webhook payload")
        return {"ok": False, "message": "Invalid request, missing events"}, 400

    for event in data['events']:
        logger.debug(f"Processing event: {event}")
        if event.get('type') != 'message' or event.get('message', {}).get('type') != 'text':
            logger.debug(f"Skipping non-text message event: {event.get('type')}")
            continue

        message_text = event['message'].get('text', '').strip()
        if not message_text:
            logger.debug("Empty message text, skipping")
            continue

        source = event.get('source', {})
        user_id = source.get('userId')
        if not user_id:
            logger.error("Missing userId in event source")
            continue

        chat_id = source.get('groupId') or source.get('roomId') or user_id
        if not chat_id:
            logger.error("Could not determine chat_id from event source")
            continue

        display_name = get_line_user_display_name(user_id) if source.get('type') == 'user' else "Group"
        logger.info(f"Received message: chat_id={chat_id}, user_id={user_id}, display_name={display_name}, text={message_text}")
        add_user_id(user_id)

        payload = {
            "message_text": message_text,
            "chat_id": chat_id,
            "platform": "line",
            "user_id": user_id,
            "username": display_name
        }
        try:
            response = requests.post(config.IOT_BROKER_URL + "/parse_message", json=payload, timeout=5)
            if response.status_code == 200:
                logger.info("Successfully processed by IoT Broker")
            else:
                logger.error(f"Failed to process in IoT Broker: {response.text}")
        except Exception as e:
            logger.error(f"Error calling IoT Broker: {e}")

    return {"ok": True}, 200

@app.route('/send_message', methods=['POST'])
def send_line_message():
    try:
        data = request.json
        chat_id = data['chat_id']
        text = data['text']
        line_bot_api.push_message(chat_id, TextSendMessage(text=text))
        return jsonify({"ok": True}), 200
    except LineBotApiError as e:
        logger.error(f"Failed to send Line message: {e}")
        return jsonify({"ok": False, "message": str(e)}), 500
    except Exception as e:
        logger.error(f"Error in send_message: {e}")
        return jsonify({"ok": False, "message": str(e)}), 500
    
if __name__ == "__main__":
    try:
        app.run(host="0.0.0.0", port=config.LINE_API_PORT, threaded=True, debug=False)
        logger.info(f"IMLine server started on port {config.LINE_API_PORT}")
    except Exception as e:
        logger.error(f"Failed to start IMLine server: {e}")
        raise