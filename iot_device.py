# iot_device.py
import requests
import config
import logging
import time
import base64
import os
import shutil
from flask import Flask, request, jsonify, send_from_directory
from flask_swagger_ui import get_swaggerui_blueprint

# Try to import cryptographic libraries
try:
    from Crypto.Signature import DSS
    from Crypto.Hash import SHA256
    from Crypto.PublicKey import ECC
except ImportError:
    logging.error("pycryptodome not installed. Signature generation will fail.")
    raise

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# --- Swagger UI Setup ---
SWAGGER_URL = '/IoTDevice/swagger'
API_URL = '/static/openapi.yaml'
swaggerui_blueprint = get_swaggerui_blueprint(
    SWAGGER_URL,
    API_URL,
    config={'app_name': "IoT Device Controller"}
)
app.register_blueprint(swaggerui_blueprint, url_prefix=SWAGGER_URL)

@app.route('/static/<path:path>')
def send_static(path):
    return send_from_directory('static', path)
# ------------------------

# Load private key for signature generation
private_key = None
try:
    if os.path.exists("ecdsa_private.pem"):
        with open("ecdsa_private.pem", "rt") as f:
            private_key = ECC.import_key(f.read())
        logger.info("Private key loaded successfully")
    else:
        logger.warning("ecdsa_private.pem not found. Please generate keys.")
except Exception as e:
    logger.error(f"Failed to load private key: {e}")

def generate_signature(chat_id: str):
    """
    Generate a digital signature for the request using ECDSA.
    Args:
        chat_id (str): The chat ID included in the message to sign.
    Returns:
        tuple: (timestamp, base64_encoded_signature)
    """
    if not private_key:
        raise ValueError("Private key is not loaded")
        
    timestamp = str(int(time.time()))
    message = f"{chat_id}:{timestamp}".encode('utf-8')
    h = SHA256.new(message)
    signer = DSS.new(private_key, 'fips-186-3')
    signature = signer.sign(h)
    signature_b64 = base64.b64encode(signature).decode('utf-8')
    return timestamp, signature_b64

class Device:
    def __init__(self, name: str, device_id: str = config.DEVICE_ID, platform: str = "unknown", chat_id: str = None):
        self.name = name
        self.device_id = device_id
        self.chat_id = chat_id
        self.platform = platform
        self.manufacturer = "raspberrypi" if "raspberrypi" in device_id else "esp32"
        self.device_type = "light" if "light" in device_id else "fan"
        self.url = self.get_device_url()
        # Simple in-memory cache to track if we've greeted this user in this session
        self.greeted_users = set()
        logger.info(f"Initializing device proxy: device_id={device_id}, manufacturer={self.manufacturer}")

    def get_device_url(self):
        """Retrieve the physical/virtual device's base URL from config.py"""
        device_config = config.load_device_config()
        if self.manufacturer == "esp32":
            return device_config['esp32']['url']
        elif self.manufacturer == "raspberrypi":
            return device_config['raspberry_pi']['url']
        else:
            raise ValueError("Unknown manufacturer")

    def send_request(self, action: str, chat_id: str, platform: str, user_id: str, username: str, bot_token: str) -> dict:
        """
        Send an HTTP request to the physical/virtual device API.
        Args:
            action (str): 'enable', 'disable', or 'getstatus'.
            ... (other args for payload)
        Returns:
            dict: Result with success flag, message, and state.
        """
        
        # Determine the endpoint based on manufacturer and action
        # Note: Virtual devices typically use /ESP32/{id}/Action or /Pi/{id}/Action
        if self.manufacturer == "esp32":
            endpoint_prefix = f"/ESP32/{self.device_id}"
        else:  # raspberrypi
            endpoint_prefix = f"/Pi/{self.device_id}"

        if action.lower() == "enable":
            endpoint = f"{endpoint_prefix}/Enable"
        elif action.lower() == "disable":
            endpoint = f"{endpoint_prefix}/Disable"
        elif action.lower() == "getstatus":
            endpoint = f"{endpoint_prefix}/GetStatus"
        else:
            return {"success": False, "message": "Unknown action"}

        url = f"{self.url}{endpoint}"
        logger.info(f"Requesting physical device API: {url}")
        
        try:
            timestamp, signature_b64 = generate_signature(chat_id)
        except Exception as e:
            logger.error(f"Signature generation failed: {e}")
            return {"success": False, "message": "Internal security error"}

        params = {
            "device_id": self.device_id,
            "chat_id": chat_id,
            "timestamp": timestamp,
            "signature": signature_b64,
            "username": username,
            "bot_token": bot_token
        }
        
        logger.debug(f"Request parameters: {params}")
        
        try:
            # Send request to the actual hardware/virtual device service
            response = requests.get(url, params=params, timeout=5)
            logger.debug(f"Device API response code: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                status = data.get("state", "unknown")
                
                # Construct a friendly message
                greeting = f"Hi, {username}\n" if chat_id not in self.greeted_users else ""
                if greeting:
                    self.greeted_users.add(chat_id)
                
                message = f"{greeting}Device {self.device_id} is now {status}, operated by user {username}"
                
                return {
                    "success": True,
                    "state": status,
                    "message": message
                }
            else:
                logger.error(f"Failed to {action} device: {response.text}")
                return {
                    "success": False,
                    "message": f"Failed to {action} device {self.device_id}. Response: {response.status_code}"
                }
        except requests.exceptions.ConnectionError:
             logger.error(f"Connection refused to device at {url}")
             return {
                "success": False,
                "message": f"Device {self.device_id} is unreachable."
            }
        except Exception as e:
            logger.error(f"Error sending request to device: {e}")
            return {
                "success": False,
                "message": f"Error processing request for device {self.device_id}"
            }

# Wrapper function to handle request parsing and device invocation
def handle_device_action(action: str):
    try:
        device_id = request.args.get('device_id', config.DEVICE_ID)
        chat_id = request.args.get('chat_id', '')
        platform = request.args.get('platform', 'telegram')
        user_id = request.args.get('user_id', '')
        username = request.args.get('username', 'User')
        
        # Determine default bot token if not provided
        default_token = config.TELEGRAM_BOT_TOKEN if platform == 'telegram' else config.LINE_ACCESS_TOKEN
        bot_token = request.args.get('bot_token', default_token)

        if device_id not in config.SUPPORTED_DEVICES:
            return jsonify({
                "status": "error",
                "message": f"Invalid device ID: {device_id}"
            }), 400

        # Initialize Device controller
        device = Device("IoTDevice", device_id=device_id, platform=platform, chat_id=chat_id)
        
        # Execute action
        result = device.send_request(action, chat_id, platform, user_id, username, bot_token)
        
        if result['success']:
            return jsonify({
                "status": "success",
                "message": result['message'],
                "state": result.get('state'),
                "device_id": device_id
            }), 200
        else:
            return jsonify({
                "status": "error",
                "message": result['message']
            }), 500 # Internal Server Error or Bad Gateway depending on context
            
    except Exception as e:
        logger.error(f"Error in handle_device_action ({action}): {e}")
        return jsonify({
            "status": "error",
            "message": f"Internal server error: {str(e)}"
        }), 500

# API Endpoints with namespaced paths

@app.route('/IoTDevice/Enable', methods=['GET'])
def enable_device():
    """API Endpoint to enable an IoT device."""
    return handle_device_action("enable")

@app.route('/IoTDevice/Disable', methods=['GET'])
def disable_device():
    """API Endpoint to disable an IoT device."""
    return handle_device_action("disable")

@app.route('/IoTDevice/GetStatus', methods=['GET'])
def get_device_status():
    """API Endpoint to get the status of an IoT device."""
    return handle_device_action("getstatus")

if __name__ == "__main__":
    # Ensure static directory exists and openapi.yaml is available
    if not os.path.exists('static'):
        os.makedirs('static')
    if os.path.exists('openapi.yaml'):
        shutil.copy('openapi.yaml', 'static/openapi.yaml')
        
    logger.info(f"Starting IoTDevice service on port {config.IOT_DEVICE_PORT}")
    logger.info(f"Swagger UI available at http://localhost:{config.IOT_DEVICE_PORT}{SWAGGER_URL}")
    app.run(host="0.0.0.0", port=config.IOT_DEVICE_PORT, threaded=True, debug=False)