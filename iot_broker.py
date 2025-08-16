# iot_broker.py
import requests
import config
import logging
import re
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

def send_message(chat_id: str, text: str, platform: str = "telegram", user_id: str = None, username: str = None) -> bool:
    """Send message to the appropriate platform via IM microservice API"""
    try:
        if platform == "telegram":
            url = config.TELEGRAM_IM_URL + "/send_message"
        elif platform == "line":
            url = config.LINE_IM_URL + "/send_message"
        else:
            logger.error(f"Unsupported platform: {platform}")
            return False

        payload = {
            "chat_id": chat_id,
            "text": text,
            "user_id": user_id,
            "username": username
        }
        response = requests.post(url, json=payload, timeout=5)
        if response.status_code == 200:
            logger.info(f"Message sent successfully to {platform}: {text}")
            return True
        else:
            logger.error(f"Failed to send message to {platform}: {response.text}")
            return False
    except Exception as e:
        logger.error(f"Error sending message to {platform}: {e}")
        return False

def IoTParse_Message(message_text: str, chat_id: str, platform: str = "telegram", user_id: str = None, username: str = None) -> dict:
    """Parse user message and execute the corresponding action"""
    message_text = message_text.lower().strip()
    logger.info(f"Parsing IoT message: {message_text}, username={username}, platform={platform}, user_id={user_id}, chat_id={chat_id}")
    try:
        if not username:
            username = "User"
        if not user_id:
            user_id = "Unknown"
        bot_token = config.TELEGRAM_BOT_TOKEN if platform == "telegram" else config.LINE_ACCESS_TOKEN

        if message_text in ["hi", "hello", "/start"]:
            help_text = (
                f"Hi, {username}\n"
                "This is an IoT control bot\n"
                "Use the following commands:\n"
                "turn on {device_id} to enable device\n"
                "turn off {device_id} to disable device\n"
                "get status {device_id} to get device status\n"
                "/bind {device_id} to bind to device"
            )
            send_message(chat_id, help_text, platform, user_id=user_id, username=username)
            return {"success": True, "action": "Help"}

        bind_match = re.match(r"^/bind\s+([\w_]+)$", message_text)
        if bind_match:
            device_id = bind_match.group(1)
            if device_id not in config.SUPPORTED_DEVICES:
                send_message(chat_id, f"Invalid device ID: {device_id}. Available devices: {', '.join(config.SUPPORTED_DEVICES)}", platform, user_id=user_id, username=username)
                return {"success": False, "message": "Invalid device ID"}
            
            if config.save_binding(device_id, chat_id, platform):
                send_message(chat_id, f"Successfully bound to device {device_id}", platform, user_id=user_id, username=username)
                return {"success": True, "action": "Bind", "device_id": device_id}
            else:
                send_message(chat_id, f"Failed to bind to device {device_id}", platform, user_id=user_id, username=username)
                return {"success": False, "message": "Failed to bind to device"}

        enable_match = re.match(r"^(turn on|/enable)(\s+([\w_]+))?$", message_text)
        disable_match = re.match(r"^(turn off|/disable)(\s+([\w_]+))?$", message_text)
        status_match = re.match(r"^(get status|/status)(\s+([\w_]+))?$", message_text)

        device_id = enable_match.group(3) if enable_match and enable_match.group(3) else \
                    disable_match.group(3) if disable_match and disable_match.group(3) else \
                    status_match.group(3) if status_match and status_match.group(3) else config.DEVICE_ID

        if device_id not in config.SUPPORTED_DEVICES:
            send_message(chat_id, f"Invalid device ID: {device_id}. Available devices: {', '.join(config.SUPPORTED_DEVICES)}", platform, user_id=user_id, username=username)
            return {"success": False, "message": "Invalid device ID"}

        action = None
        if enable_match:
            action = "enable"
            endpoint = "Enable"
        elif disable_match:
            action = "disable"
            endpoint = "Disable"
        elif status_match:
            action = "get_status"
            endpoint = "GetStatus"
        else:
            send_message(chat_id, "Invalid command. Please use /start to view help.", platform, user_id=user_id, username=username)
            return {"success": False, "message": "Invalid command"}

        # Send "Command received" message before calling device microservice
        action_text = "Enable" if action == "enable" else "Disable" if action == "disable" else "Get status of"
        send_message(chat_id, f"Command received: {action_text} {device_id}", platform, user_id=user_id, username=username)

        # Call the device microservice
        device_url = f"{config.IOT_DEVICE_URL}/{endpoint}"
        params = {
            "device_id": device_id,
            "chat_id": chat_id,
            "platform": platform,
            "user_id": user_id,
            "username": username,
            "bot_token": bot_token
        }
        
        response = requests.get(device_url, params=params, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "success":
                send_message(chat_id, data["message"], platform, user_id=user_id, username=username)
                return {"success": True, "action": action.capitalize(), "device_id": device_id}
            else:
                send_message(chat_id, data.get("message", "Failed to process device action"), platform, user_id=user_id, username=username)
                return {"success": False, "message": data.get("message", "Failed to process device action")}
        else:
            error_msg = f"Failed to {action} device {device_id}"
            send_message(chat_id, error_msg, platform, user_id=user_id, username=username)
            return {"success": False, "message": error_msg}

    except Exception as e:
        logger.error(f"Error parsing message '{message_text}': {e}", exc_info=True)
        send_message(chat_id, "An error occurred while processing your command.", platform, user_id=user_id, username=username)
        return {"success": False, "message": "Error processing command"}

@app.route('/parse_message', methods=['POST'])
def parse_message():
    try:
        data = request.json
        if not data:
            return jsonify({"success": False, "message": "No data provided"}), 400

        message_text = data.get('message_text')
        chat_id = data.get('chat_id')
        platform = data.get('platform')
        user_id = data.get('user_id')
        username = data.get('username')

        if not all([message_text, chat_id, platform]):
            return jsonify({"success": False, "message": "Missing required fields"}), 400

        result = IoTParse_Message(message_text, chat_id, platform, user_id, username)
        return jsonify(result), 200 if result['success'] else 500
    except Exception as e:
        logger.error(f"Error in parse_message: {e}")
        return jsonify({"success": False, "message": "Internal error"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.IOT_BROKER_PORT, threaded=True, debug=False)