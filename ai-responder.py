import os
import time
import json
import logging
import threading
import requests
from meshtastic.tcp_interface import TCPInterface
from meshtastic.serial_interface import SerialInterface
from pubsub import pub

# Configuration Defaults
DEFAULT_MESHTASTIC_HOST = 'meshmonitor'
DEFAULT_MESHTASTIC_PORT = 4404
DEFAULT_AI_PROVIDER = 'ollama'
DEFAULT_OLLAMA_HOST = 'ollama'
DEFAULT_OLLAMA_PORT = '11434'
DEFAULT_OLLAMA_MODEL = 'llama3.2:1b'
ACK_TIMEOUT = 20 # Seconds to wait for radio/neighbor ACK
DEFAULT_ALLOWED_CHANNELS = [0, 3] # Default to DM and Private
CONFIG_FILE = os.environ.get('CONFIG_FILE', '/app/data/config.json')

# Environment Variables (Static config)
INTERFACE_TYPE = os.environ.get('INTERFACE_TYPE', 'tcp').lower()
SERIAL_PORT = os.environ.get('SERIAL_PORT', '/dev/ttyACM0')
MESHTASTIC_HOST = os.environ.get('MESHTASTIC_HOST', DEFAULT_MESHTASTIC_HOST)
MESHTASTIC_PORT = int(os.environ.get('MESHTASTIC_PORT', DEFAULT_MESHTASTIC_PORT))
OLLAMA_HOST = os.environ.get('OLLAMA_HOST', DEFAULT_OLLAMA_HOST)
OLLAMA_PORT = os.environ.get('OLLAMA_PORT', DEFAULT_OLLAMA_PORT)
OLLAMA_MODEL = os.environ.get('OLLAMA_MODEL', DEFAULT_OLLAMA_MODEL)
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
ENV_ADMIN_NODE_ID = os.environ.get('ADMIN_NODE_ID', '')

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger('AI-Responder')

class AIResponder:
    def __init__(self):
        self.iface = None
        self.running = True
        self.config = self.load_config()

    def load_config(self):
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r') as f:
                    config = json.load(f)
                    # Migrate old config if necessary
                    if 'admin_nodes' not in config:
                        config['admin_nodes'] = []
                    logger.info(f"Loaded config: {config}")
                    return config
        except Exception as e:
            logger.error(f"Error loading config: {e}")
        
        # Default config
        admins = []
        default_config = {
            "current_provider": os.environ.get('AI_PROVIDER', DEFAULT_AI_PROVIDER).lower(),
            "allowed_channels": [int(c.strip()) for c in os.environ.get('ALLOWED_CHANNELS', '0,3').split(',') if c.strip().isdigit()],
            "admin_nodes": admins
        }
        logger.info(f"Using default config: {default_config}")
        return default_config

    def save_config(self):
        try:
            os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
            with open(CONFIG_FILE, 'w') as f:
                json.dump(self.config, f, indent=2)
            logger.info("Config saved successfully.")
        except Exception as e:
            logger.error(f"Error saving config: {e}")

    def connect(self):
        while self.running:
            try:
                if INTERFACE_TYPE == 'serial':
                    logger.info(f"Connecting to Serial node at {SERIAL_PORT}...")
                    self.iface = SerialInterface(devPath=SERIAL_PORT)
                else:
                    logger.info(f"Connecting to TCP node at {MESHTASTIC_HOST}:{MESHTASTIC_PORT}...")
                    self.iface = TCPInterface(hostname=MESHTASTIC_HOST, portNumber=MESHTASTIC_PORT)
                
                logger.info(f"Connected successfully to {INTERFACE_TYPE} interface!")
                
                # Standard subscription
                pub.subscribe(self.on_receive, "meshtastic.receive")
                
                # Keep alive loop
                while self.iface and self.running:
                    time.sleep(1)
            except Exception as e:
                logger.error(f"Connection error: {e}. Retrying in 5 seconds...")
                if self.iface:
                    try:
                        self.iface.close()
                    except:
                        pass
                time.sleep(5)

    def resolve_channel_input(self, input_val):
        if input_val.isdigit():
            return int(input_val)
        if self.iface and self.iface.localNode:
            for idx, ch in enumerate(self.iface.localNode.channels):
                if ch and ch.settings and ch.settings.name and ch.settings.name.lower() == input_val.lower():
                    return idx
        return None

    def on_receive(self, packet, interface):
        try:
            decoded = packet.get('decoded', {})
            message = decoded.get('text', '')
            from_node = packet.get('fromId')
            to_node = packet.get('toId')
            channel = packet.get('channel', 0)
            
            if not message:
                return

            # Check if channel is allowed (DMs are always allowed)
            if to_node == '^all' and channel not in self.config['allowed_channels']:
                return

            # Command Processing
            if message.startswith('!ai '):
                self.process_command(message, from_node, to_node, channel)

        except Exception as e:
            logger.error(f"Error processing packet: {e}")

    def is_admin(self, node_id):
        # Bootstrap mode: If no admins configured, everyone is admin
        if not self.config.get('admin_nodes'):
            return True
        return node_id in self.config['admin_nodes']

    def split_message(self, text, limit=200):
        """Splits a message into chunks within the limit, trying to break at words."""
        chunks = []
        while len(text) > limit:
            # Find the last space before the limit
            split_idx = text.rfind(' ', 0, limit)
            if split_idx == -1:
                # No space found, hard split
                split_idx = limit
            
            chunks.append(text[:split_idx].strip())
            text = text[split_idx:].strip()
        
        if text:
            chunks.append(text)
        return chunks

    def process_command(self, message, from_node, to_node, channel):
        args = message.split()
        if len(args) < 2: 
            return

        cmd = args[1]
        
        # !ai -h (Help)
        if cmd == '-h':
            # Admin Help -> Private
            if self.is_admin(from_node):
                help_msg = (
                    "🤖 Admin Commands:\n"
                    "!ai <prompt> : Ask AI\n"
                    "!ai -p [local|online] : Set Provider\n"
                    "!ai -c [add|rm] : Manage Channels\n"
                    "!ai -a [add|rm] : Manage Admins"
                )
                self.send_response(help_msg, from_node, to_node, channel, is_admin_cmd=True)
            # User Help -> Public (if on enabled public channel)
            # Note: send_response handles 'Private if DM' automatically.
            else:
                help_msg = "🤖 Usage: !ai <question>"
                self.send_response(help_msg, from_node, to_node, channel, is_admin_cmd=False)
            return

        # Check Admin for ALL other commands (starting with -)
        if cmd.startswith('-'):
            if not self.is_admin(from_node):
                self.send_response("⛔ Unauthorized: Admin only.", from_node, to_node, channel, is_admin_cmd=True)
                return

        # !ai -p [provider]
        if cmd == '-p':
            if len(args) == 2:
                current = self.config['current_provider']
                providers = ['ollama', 'gemini', 'openai', 'anthropic']
                msg = ["Providers:"]
                for p in providers:
                    status = "✅" if p == current else "❌"
                    msg.append(f"{status} {p}")
                self.send_response(", ".join(msg), from_node, to_node, channel, is_admin_cmd=True)
            else:
                new_provider = args[2].lower()
                if new_provider in ['online', 'gemini', 'openai', 'anthropic']:
                    self.config['current_provider'] = new_provider
                    self.save_config()
                    self.send_response(f"✅ Switched to ONLINE provider ({new_provider}).", from_node, to_node, channel, is_admin_cmd=True)
                elif new_provider in ['local', 'ollama']:
                    self.config['current_provider'] = 'ollama'
                    self.save_config()
                    self.send_response("✅ Switched to LOCAL provider (Ollama).", from_node, to_node, channel, is_admin_cmd=True)
                else:
                    self.send_response("❌ Unknown provider. Use 'local', 'gemini', 'openai', or 'anthropic'.", from_node, to_node, channel, is_admin_cmd=True)

        # !ai -c [-add/-rem]
        elif cmd == '-c':
            if len(args) == 2:
                msg = ["Channels:"]
                if self.iface and self.iface.localNode:
                    for idx, ch in enumerate(self.iface.localNode.channels):
                        try:
                            if ch.role == 0: continue # Skip if no role
                            name = ch.settings.name if ch.settings.name else f"Ch{idx}"
                            status = "✅" if idx in self.config['allowed_channels'] else "❌"
                            msg.append(f"{status} {idx}:{name}")
                        except: pass
                self.send_response(", ".join(msg), from_node, to_node, channel, is_admin_cmd=True)
            else:
                action = args[2]
                if len(args) < 4:
                    self.send_response("❌ Usage: !ai -c add|rm <id/name>", from_node, to_node, channel, is_admin_cmd=True)
                    return
                target_idx = self.resolve_channel_input(args[3])
                if target_idx is None:
                    self.send_response(f"❌ Could not find channel '{args[3]}'", from_node, to_node, channel, is_admin_cmd=True)
                    return
                if action == 'add' or action == '-add':
                    if target_idx not in self.config['allowed_channels']:
                        self.config['allowed_channels'].append(target_idx)
                        self.save_config()
                        self.send_response(f"✅ Added Channel {target_idx}", from_node, to_node, channel, is_admin_cmd=True)
                elif action == 'rm' or action == '-rem' or action == '-rm':
                    if target_idx in self.config['allowed_channels']:
                        self.config['allowed_channels'].remove(target_idx)
                        self.save_config()
                        self.send_response(f"✅ Removed Channel {target_idx}", from_node, to_node, channel, is_admin_cmd=True)

        # !ai -a [-add/-rem]
        elif cmd == '-a':
            if len(args) == 2:
                admins = self.config.get('admin_nodes', [])
                if not admins:
                    # Specific note already here, but using the flag for consistency
                    self.send_response("⚠️ No admins configured (Bootstrap Mode).", from_node, to_node, channel, is_admin_cmd=True)
                else:
                    self.send_response(f"Admins: {', '.join(admins)}", from_node, to_node, channel, is_admin_cmd=True)
            else:
                action = args[2]
                if len(args) < 4:
                    self.send_response("❌ Usage: !ai -a add|rm <node_id>", from_node, to_node, channel, is_admin_cmd=True)
                    return
                target_id = args[3].strip()
                if target_id.lower() == 'me':
                    target_id = from_node
                
                if action == 'add' or action == '-add':
                    if target_id not in self.config['admin_nodes']:
                        self.config['admin_nodes'].append(target_id)
                        self.save_config()
                        self.send_response(f"✅ Added Admin {target_id}", from_node, to_node, channel, is_admin_cmd=True)
                elif action == 'rm' or action == '-rem' or action == '-rm':
                    if target_id in self.config['admin_nodes']:
                        self.config['admin_nodes'].remove(target_id)
                        self.save_config()
                        self.send_response(f"✅ Removed Admin {target_id}", from_node, to_node, channel, is_admin_cmd=True)

        else:
            prompt = message[4:].strip()
            # Run AI request in a separate thread to avoid blocking the radio interface
            threading.Thread(target=self.handle_ai_request, args=(from_node, to_node, channel, prompt)).start()

    def send_response(self, text, from_node, to_node, channel, is_admin_cmd=False):
        """
        Send a response message.
        If dm -> reply dm.
        If broadcast -> broadcast.
        If broadcast AND admin command -> reply dm (to reduce spam).
        """
        target = '^all'
        
        # If it's an admin command (or error), and it came from a specific user, reply privately
        # even if they shouted it to the channel.
        if is_admin_cmd and from_node.startswith('!'):
            target = from_node
        # Normal logic: reply to sender if they DM'd us
        elif to_node != '^all':
            target = from_node

        logger.info(f"Sending response to {target}: {text[:50]}...")
        try:
            self.iface.sendText(text, destinationId=target, channelIndex=channel)
        except Exception as e:
            logger.error(f"Failed to send response: {e}")

    def handle_ai_request(self, from_node, to_node, channel, prompt):
        target = '^all' if to_node == '^all' else from_node
        logger.info(f"Processing AI request from {from_node} (reply to {target}): {prompt[:50]}...")
        try:
            self.iface.sendText("Thinking... 🤖", destinationId=target, channelIndex=channel)
            time.sleep(2) # Give the radio time to send the first packet
        except Exception as e:
            logger.error(f"Failed to send acknowledgment: {e}")
        
        response_text = self.get_ai_response(prompt)
        # Clean up common AI markdown that might be problematic or waste space
        response_text = response_text.replace('**', '')
        
        logger.info(f"Generated AI response: {response_text[:100]}...")
        
        chunks = self.split_message(response_text)
        total_chunks = len(chunks)
        
        for i, chunk in enumerate(chunks):
            if i > 0:
                logger.info(f"Rate limiting: Waiting 30s before sending chunk {i+1}/{total_chunks}...")
                time.sleep(30)
            
            try:
                display_chunk = chunk
                if total_chunks > 1:
                    display_chunk = f"[{i+1}/{total_chunks}] {chunk}"
                
                p = self.iface.sendText(display_chunk, destinationId=target, channelIndex=channel)
                pkt_id = p.get('id') if isinstance(p, dict) else 'unknown'
                logger.info(f"Chunk {i+1}/{total_chunks} queued (ID: {pkt_id}). Waiting for ACK...")
                
                # If it's a broadcast (^all), we won't get a direct ACK, so just wait a bit
                if target == '^all':
                    logger.info("Broadcast sent. Skipping ACK wait.")
                else:
                    try:
                        # Wait for acknowledgment from the next hop or destination
                        if hasattr(p, 'wait_for_ack'):
                            if p.wait_for_ack(timeout=ACK_TIMEOUT):
                                logger.info(f"✅ Received ACK for chunk {i+1}")
                            else:
                                logger.warning(f"⚠️ ACK timeout for chunk {i+1} (Queue might be full/congested)")
                    except Exception as ack_err:
                        logger.warning(f"Error waiting for ACK: {ack_err}")
            except Exception as e:
                logger.error(f"Failed to send chunk {i+1}: {e}")

    def get_ai_response(self, prompt):
        provider = self.config.get('current_provider', 'ollama')
        if provider == 'ollama':
            return self.get_ollama_response(prompt)
        elif provider == 'gemini':
            return self.get_gemini_response(prompt)
        elif provider == 'openai':
            return self.get_openai_response(prompt)
        elif provider == 'anthropic':
            return self.get_anthropic_response(prompt)
        return f"Error: Unknown provider '{provider}'"

    def get_ollama_response(self, prompt):
        url = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/generate"
        payload = {
            "model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
            "system": "Context: Meshtastic network assistant. Concise responses."
        }
        try:
            response = requests.post(url, json=payload, timeout=90)
            response.raise_for_status()
            return response.json().get('response', 'No response.')
        except Exception as e:
            logger.error(f"Ollama error: {e}")
            return f"Error calling Ollama: {str(e)}"

    def get_gemini_response(self, prompt):
        if not GEMINI_API_KEY: return "Error: Gemini API key missing."
        models = ['gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-2.5-pro', 'gemini-2.0-pro-exp']
        payload = {"contents": [{"parts": [{"text": f"Context: Meshtastic assistant. Concise. Task: {prompt}"}]}]}
        last_err = ""
        for model in models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
            try:
                logger.info(f"Trying Gemini model: {model}")
                response = requests.post(url, json=payload, timeout=30)
                if response.status_code == 200:
                    data = response.json()
                    candidates = data.get('candidates', [])
                    if not candidates:
                        logger.error(f"Gemini returned 200 but no candidates: {data}")
                        last_err = "No candidates in response"
                        continue
                    text = candidates[0]['content']['parts'][0]['text'].strip()
                    return text
                else:
                    try:
                        err_msg = response.json().get('error', {}).get('message', response.text)
                    except:
                        err_msg = response.text
                    logger.error(f"Gemini error from {model}: {response.status_code} - {err_msg}")
                    last_err = f"{response.status_code} ({err_msg[:50]})"
            except Exception as e:
                logger.error(f"Gemini connection error: {e}")
                last_err = "Connection Error"
        return f"Gemini Error: {last_err}. Check API Key."

    def get_openai_response(self, prompt):
        if not OPENAI_API_KEY: return "Error: OpenAI API key missing."
        url = 'https://api.openai.com/v1/chat/completions'
        payload = {
            'model': 'gpt-3.5-turbo',
            'messages': [{'role': 'user', 'content': f"Context: Meshtastic assistant. Concise. Task: {prompt}"}],
            'max_tokens': 150
        }
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {OPENAI_API_KEY}'
        }
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            if response.status_code == 200:
                data = response.json()
                return data.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
            else:
                logger.error(f"OpenAI error: {response.status_code} - {response.text}")
                return f"OpenAI Error: {response.status_code}"
        except Exception as e:
            logger.error(f"OpenAI connection error: {e}")
            return f"Error calling OpenAI: {str(e)}"

    def get_anthropic_response(self, prompt):
        if not ANTHROPIC_API_KEY: return "Error: Anthropic API key missing."
        url = 'https://api.anthropic.com/v1/messages'
        payload = {
            'model': 'claude-3-haiku-20240307',
            'max_tokens': 150,
            'messages': [{'role': 'user', 'content': f"Context: Meshtastic assistant. Concise. Task: {prompt}"}]
        }
        headers = {
            'Content-Type': 'application/json',
            'x-api-key': ANTHROPIC_API_KEY,
            'anthropic-version': '2023-06-01'
        }
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            if response.status_code == 200:
                data = response.json()
                return data.get('content', [{}])[0].get('text', '').strip()
            else:
                logger.error(f"Anthropic error: {response.status_code} - {response.text}")
                return f"Anthropic Error: {response.status_code}"
        except Exception as e:
            logger.error(f"Anthropic connection error: {e}")
            return f"Error calling Anthropic: {str(e)}"

if __name__ == "__main__":
    responder = AIResponder()
    responder.connect()
