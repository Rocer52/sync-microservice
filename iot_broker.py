# iot_broker.py
import requests
import config
import logging
import re
import os
import shutil
from flask import Flask, request, jsonify, send_from_directory
from flask_swagger_ui import get_swaggerui_blueprint

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# --- Swagger UI Setup ---
SWAGGER_URL = '/IoTBroker/swagger'
API_URL = '/static/openapi.yaml'
swaggerui_blueprint = get_swaggerui_blueprint(
    SWAGGER_URL,
    API_URL,
    config={'app_name': "IoT Broker Service"}
)
app.register_blueprint(swaggerui_blueprint, url_prefix=SWAGGER_URL)

@app.route('/static/<path:path>')
def send_static(path):
    return send_from_directory('static', path)
# ------------------------

def send_message(chat_id: str, text: str, platform: str = "telegram", user_id: str = None, username: str = None) -> bool:
    """
    Send message to the appropriate platform via IM microservice API.
    Args:
        chat_id (str): The chat ID to send the message to.
        text (str): The message content.
        platform (str): 'telegram' or 'line'.
        user_id (str): User ID (required for Line).
        username (str): Display name.
    Returns:
        bool: True if successful, False otherwise.
    """
    try:
        if platform == "telegram":
            url = config.TELEGRAM_IM_URL + "/IMTelegram/send_message"
        elif platform == "line":
            url = config.LINE_IM_URL + "/IMLine/send_message"
        else:
            logger.error(f"Unsupported platform: {platform}")
            return False

        payload = {
            "chat_id": chat_id,
            "text": text,
            "user_id": user_id,
            "username": username
        }
        
        logger.info(f"Sending message to {platform} at {url}")
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

def notify_bound_users(device_id: str, current_state: str, operator_username: str, operator_chat_id: str):
    """
    Notify all users bound to a device about a status change.
    
    Args:
        device_id (str): The ID of the device.
        current_state (str): The new state of the device (on/off).
        operator_username (str): The username of the person who changed the state.
        operator_chat_id (str): The chat ID of the operator (to avoid double notification).
    """
    try:
        bindings = config.load_bindings()
        device_bindings = bindings.get(device_id, [])
        
        if not device_bindings:
            logger.info(f"No bound users found for device {device_id}")
            return

        notification_message = (
            f"Status Update: Device {device_id} is now {current_state}.\n"
            f"Modified by: {operator_username}"
        )

        count = 0
        for binding in device_bindings:
            target_chat_id = binding.get("chat_id")
            target_platform = binding.get("platform")
            
            # Skip the operator (they already receive a direct reply)
            if str(target_chat_id) == str(operator_chat_id):
                continue
            
            send_message(
                chat_id=target_chat_id, 
                text=notification_message, 
                platform=target_platform
            )
            count += 1
            
        logger.info(f"Broadcasted status update to {count} bound users for device {device_id}")

    except Exception as e:
        logger.error(f"Failed to notify bound users: {e}")

def IoTParse_Message(message_text: str, chat_id: str, platform: str = "telegram", user_id: str = None, username: str = None) -> dict:
    """
    Parse user message and execute the corresponding action (Bind, Enable, Disable, GetStatus).
    """
    message_text = message_text.lower().strip()
    logger.info(f"Parsing IoT message: {message_text}, username={username}, platform={platform}, user_id={user_id}, chat_id={chat_id}")
    
    try:
        if not username:
            username = "User"
        if not user_id:
            user_id = "Unknown"
        
        bot_token = config.TELEGRAM_BOT_TOKEN if platform == "telegram" else config.LINE_ACCESS_TOKEN

        # 1. Handle Help Command
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

        # 2. Handle Bind Command
        bind_match = re.match(r"^/bind\s+([\w_]+)$", message_text)
        if bind_match:
            device_id = bind_match.group(1)
            
            if device_id not in config.SUPPORTED_DEVICES:
                send_message(chat_id, f"Invalid device ID: {device_id}. Available: {', '.join(config.SUPPORTED_DEVICES)}", platform, user_id=user_id, username=username)
                return {"success": False, "message": "Invalid device ID"}
            
            if config.save_binding(device_id, chat_id, platform):
                send_message(chat_id, f"Successfully bound to device {device_id}", platform, user_id=user_id, username=username)
                return {"success": True, "action": "Bind", "device_id": device_id}
            else:
                send_message(chat_id, f"Failed to bind to device {device_id}", platform, user_id=user_id, username=username)
                return {"success": False, "message": "Failed to bind to device"}

        # 3. Handle Control Commands
        enable_match = re.match(r"^(turn on|/enable)(\s+([\w_]+))?$", message_text)
        disable_match = re.match(r"^(turn off|/disable)(\s+([\w_]+))?$", message_text)
        status_match = re.match(r"^(get status|/status)(\s+([\w_]+))?$", message_text)

        device_id = enable_match.group(3) if enable_match and enable_match.group(3) else \
                    disable_match.group(3) if disable_match and disable_match.group(3) else \
                    status_match.group(3) if status_match and status_match.group(3) else config.DEVICE_ID

        if device_id not in config.SUPPORTED_DEVICES:
            send_message(chat_id, f"Invalid device ID: {device_id}. Available: {', '.join(config.SUPPORTED_DEVICES)}", platform, user_id=user_id, username=username)
            return {"success": False, "message": "Invalid device ID"}

        action = None
        endpoint = None
        
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

        # Call the IoT Device Microservice
        device_url = f"{config.IOT_DEVICE_URL}/IoTDevice/{endpoint}"
        params = {
            "device_id": device_id,
            "chat_id": chat_id,
            "platform": platform,
            "user_id": user_id,
            "username": username,
            "bot_token": bot_token
        }
        
        logger.info(f"Forwarding request to IoT Device Service: {device_url}")
        response = requests.get(device_url, params=params, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "success":
                # 1. Send success response to the user who initiated the command
                send_message(chat_id, data["message"], platform, user_id=user_id, username=username)
                
                # 2. Broadcast to other bound users if state changed (Enable/Disable)
                # Note: We assume state change happened if status is success for enable/disable
                if action in ["enable", "disable"]:
                    current_state = data.get("state", "unknown")
                    notify_bound_users(device_id, current_state, username, chat_id)
                
                return {"success": True, "action": action.capitalize(), "device_id": device_id}
            else:
                fail_msg = data.get("message", "Failed to process device action")
                send_message(chat_id, fail_msg, platform, user_id=user_id, username=username)
                return {"success": False, "message": fail_msg}
        else:
            error_msg = f"Failed to connect to device service: {response.status_code}"
            logger.error(error_msg)
            send_message(chat_id, error_msg, platform, user_id=user_id, username=username)
            return {"success": False, "message": error_msg}

    except Exception as e:
        logger.error(f"Error parsing message '{message_text}': {e}", exc_info=True)
        send_message(chat_id, "An error occurred while processing your command.", platform, user_id=user_id, username=username)
        return {"success": False, "message": "Error processing command"}

@app.route('/IoTBroker/parse_message', methods=['POST'])
def parse_message():
    """
    API Endpoint to receive parsed message requests from IM services.
    """
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
        logger.error(f"Error in parse_message route: {e}")
        return jsonify({"success": False, "message": "Internal server error"}), 500

@app.route('/IoTBroker/bind', methods=['POST'])
def bind_device():
    """
    API Endpoint to directly bind a device via API call.
    """
    try:
        data = request.json
        device_id = data.get('device_id')
        chat_id = data.get('chat_id')
        platform = data.get('platform')

        if not all([device_id, chat_id, platform]):
            return jsonify({"success": False, "message": "Missing required fields"}), 400

        if device_id not in config.SUPPORTED_DEVICES:
             return jsonify({"success": False, "message": "Invalid device ID"}), 400

        if config.save_binding(device_id, chat_id, platform):
            return jsonify({"success": True, "message": "Binding successful"}), 200
        else:
            return jsonify({"success": False, "message": "Failed to save binding"}), 500
    except Exception as e:
        logger.error(f"Error in bind_device route: {e}")
        return jsonify({"success": False, "message": "Internal server error"}), 500

if __name__ == "__main__":
    # Ensure static directory exists and openapi.yaml is available
    if not os.path.exists('static'):
        os.makedirs('static')
    if os.path.exists('openapi.yaml'):
        shutil.copy('openapi.yaml', 'static/openapi.yaml')

    logger.info(f"Starting IoTBroker service on port {config.IOT_BROKER_PORT}")
    logger.info(f"Swagger UI available at http://localhost:{config.IOT_BROKER_PORT}{SWAGGER_URL}")
    app.run(host="0.0.0.0", port=config.IOT_BROKER_PORT, threaded=True, debug=False)