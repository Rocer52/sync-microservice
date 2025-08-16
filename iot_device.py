# iot_device.py
import requests
import config
import logging
import time
import base64
from Crypto.Signature import DSS
from Crypto.Hash import SHA256
from Crypto.PublicKey import ECC
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Load private key for signature generation
private_key = None
try:
    with open("ecdsa_private.pem", "rt") as f:
        private_key = ECC.import_key(f.read())
    logger.info("Private key loaded successfully")
except Exception as e:
    logger.error(f"Failed to load private key: {e}")
    raise

def generate_signature(chat_id: str):
    """Generate a digital signature for the request"""
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
        self.group_id = None
        self.group_members = set()
        self.manufacturer = "raspberrypi" if "raspberrypi" in device_id else "esp32"
        self.device_type = "light" if "light" in device_id else "fan"
        self.url = self.get_device_url()
        self.greeted_users = set()
        logger.info(f"Initializing device: device_id={device_id}, manufacturer={self.manufacturer}, device_type={self.device_type}")

    def get_device_url(self):
        """Retrieve the device's URL from config.py"""
        device_config = config.load_device_config()
        if self.manufacturer == "esp32":
            return device_config['esp32']['url']
        elif self.manufacturer == "raspberrypi":
            return device_config['raspberry_pi']['url']
        else:
            raise ValueError("Unknown manufacturer")

    def send_request(self, action: str, chat_id: str, platform: str, user_id: str, username: str, bot_token: str) -> bool:
        """Send an HTTP request to the virtual device and handle the response"""
        endpoint = "/GetStatus" if action == "get_status" else f"/{action.capitalize()}"
        url = f"{self.url}{endpoint}"
        
        logger.info(f"Requesting device API endpoint: {url}")
        
        timestamp, signature_b64 = generate_signature(chat_id)
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
            response = requests.get(url, params=params, timeout=5)
            logger.debug(f"Device API response: {response.status_code} - {response.text}")
            
            if response.status_code == 200:
                data = response.json()
                status = data.get("state", "unknown")
                greeting = f"Hi, {username}\n" if chat_id not in self.greeted_users else ""
                if greeting:
                    self.greeted_users.add(chat_id)
                return {
                    "success": True,
                    "state": status,
                    "message": f"{greeting}Device {self.device_id} is now {status}, operated by user {username}"
                }
            else:
                logger.error(f"Failed to {action} device: {response.text}")
                return {
                    "success": False,
                    "message": f"Failed to {action} device {self.device_id}"
                }
        except Exception as e:
            logger.error(f"Error sending request to device: {e}")
            return {
                "success": False,
                "message": f"Error processing request for device {self.device_id}"
            }

@app.route('/Enable', methods=['GET'])
def enable_device():
    """Enable an IoT device"""
    return handle_device_action("enable")

@app.route('/Disable', methods=['GET'])
def disable_device():
    """Disable an IoT device"""
    return handle_device_action("disable")

@app.route('/GetStatus', methods=['GET'])
def get_device_status():
    """Get the status of an IoT device"""
    return handle_device_action("get_status")

def handle_device_action(action: str):
    try:
        device_id = request.args.get('device_id', config.DEVICE_ID)
        chat_id = request.args.get('chat_id', '')
        platform = request.args.get('platform', 'telegram')
        user_id = request.args.get('user_id', '')
        username = request.args.get('username', 'User')
        bot_token = request.args.get('bot_token', config.TELEGRAM_BOT_TOKEN if platform == 'telegram' else config.LINE_ACCESS_TOKEN)

        if device_id not in config.SUPPORTED_DEVICES:
            return jsonify({
                "status": "error",
                "message": f"Invalid device ID: {device_id}"
            }), 400

        device = Device("IoTDevice", device_id=device_id, platform=platform, chat_id=chat_id)
        result = device.send_request(action, chat_id, platform, user_id, username, bot_token)
        
        if result['success']:
            return jsonify({
                "status": "success",
                "message": result['message'],
                "state": result['state'],
                "device_id": device_id
            }), 200
        else:
            return jsonify({
                "status": "error",
                "message": result['message']
            }), 500
    except Exception as e:
        logger.error(f"Error in {action} device: {e}")
        return jsonify({
            "status": "error",
            "message": f"Internal server error: {str(e)}"
        }), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.IOT_DEVICE_PORT, threaded=True, debug=False)