import os
import json
import asyncio
import subprocess
import signal
import sys
from typing import Optional, Dict, Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# --- Modular Path Resolution ---
# manager.py is in dashboard/backend/
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DASHBOARD_DIR = os.path.join(BASE_DIR, "dashboard")
FRONTEND_DIR = os.path.join(DASHBOARD_DIR, "frontend")
STATIC_DIR = os.path.join(FRONTEND_DIR, "static")
TEMPLATES_DIR = os.path.join(FRONTEND_DIR, "templates")

CONFIG_PATH = os.environ.get("CONFIG_FILE", os.path.join(BASE_DIR, "data", "config.json"))
LOG_MAX_LINES = 1000

app = FastAPI(title="AI Responder Manager")

# Mount static files
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Global state for the AI Responder process
class ResponderState:
    def __init__(self):
        self.process: Optional[subprocess.Popen] = None
        self.logs = []
        self.running = False
        self.clients = set()

state = ResponderState()

# --- Schemas ---
class ConfigUpdate(BaseModel):
    current_provider: Optional[str] = None
    allowed_channels: Optional[list] = None
    admin_nodes: Optional[list] = None
    interface_type: Optional[str] = None
    serial_port: Optional[str] = None
    meshtastic_host: Optional[str] = None
    meshtastic_port: Optional[int] = None
    gemini_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    ollama_model: Optional[str] = None

# --- Helpers ---
def load_config() -> Dict[str, Any]:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_config(config_data: Dict[str, Any]):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config_data, f, indent=2)

async def log_reader():
    """Reads logs from the subprocess and broadcasts to clients."""
    if not state.process or not state.process.stdout:
        return

    while state.running:
        line = await asyncio.to_thread(state.process.stdout.readline)
        if not line:
            break
        
        log_line = line.decode('utf-8', errors='ignore').strip()
        state.logs.append(log_line)
        if len(state.logs) > LOG_MAX_LINES:
            state.logs.pop(0)
            
        # Broadcast to all connected WebSockets
        for client in list(state.clients):
            try:
                await client.send_text(log_line)
            except:
                state.clients.remove(client)

# --- Routes ---

@app.get("/api/config")
async def get_config():
    return load_config()

@app.post("/api/config")
async def update_config(update: ConfigUpdate):
    current = load_config()
    update_data = update.dict(exclude_unset=True)
    current.update(update_data)
    save_config(current)
    return current

@app.get("/api/status")
async def get_status():
    if state.process and state.process.poll() is None:
        state.running = True
    else:
        state.running = False
    return {"running": state.running}

@app.post("/api/start")
async def start_responder():
    if state.process and state.process.poll() is None:
        return {"status": "already running"}

    try:
        # Pass current environment variables to the subprocess
        env = os.environ.copy()
        # Ensure we point to the same config file
        env["CONFIG_FILE"] = CONFIG_PATH
        
        script_path = os.path.join(BASE_DIR, "ai_responder.py")
        
        # Start the responder as a subprocess
        state.process = subprocess.Popen(
            [sys.executable, script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            bufsize=1,
            cwd=BASE_DIR
        )
        state.running = True
        state.logs = [] # Clear old logs on restart
        
        # Start logs reader task
        asyncio.create_task(log_reader())
        
        return {"status": "started"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/stop")
async def stop_responder():
    if not state.process or state.process.poll() is not None:
        state.running = False
        return {"status": "not running"}

    if sys.platform == 'win32':
        state.process.terminate()
    else:
        state.process.send_signal(signal.SIGTERM)
        
    state.running = False
    return {"status": "stopped"}

@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    await websocket.accept()
    state.clients.add(websocket)
    
    # Send history first
    for line in state.logs:
        await websocket.send_text(line)
        
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        state.clients.remove(websocket)

# --- Scripts Integration (Discovery/Validation) ---

@app.get("/api/discover/ports")
async def discover_ports():
    try:
        import serial.tools.list_ports
        ports = [p.device for p in serial.tools.list_ports.comports()]
        return {"ports": ports}
    except ImportError:
        return {"ports": [], "error": "pyserial not installed"}

@app.get("/api/discover/models")
async def discover_models(provider: str):
    script_path = os.path.join(BASE_DIR, "scripts", "fetch_models.py")
    try:
        result = subprocess.run(
            [sys.executable, script_path, provider, "none"],
            capture_output=True, text=True,
            cwd=BASE_DIR
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
        return {"error": result.stderr}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/validate")
async def validate_connection(provider: str, model: str, key: str = "none"):
    script_path = os.path.join(BASE_DIR, "scripts", "validate_connection.py")
    try:
        result = subprocess.run(
            [sys.executable, script_path, provider, model, key],
            capture_output=True, text=True,
            cwd=BASE_DIR
        )
        return json.loads(result.stdout)
    except Exception as e:
        return {"success": False, "message": str(e)}

# Serve UI
@app.get("/", response_class=HTMLResponse)
async def get_ui():
    index_path = os.path.join(TEMPLATES_DIR, "index.html")
    if os.path.exists(index_path):
        # We'll use Jinja2 for potential server-side data later
        from fastapi import Request
        return templates.TemplateResponse("index.html", {"request": {}}) 
    return HTMLResponse("<h1>AI Responder Web Dashboard</h1><p>Frontend templates missing.</p>")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
