import asyncio
import json
import logging
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from backend.routes.websocket import websocket_endpoint
from backend.services.transcription import WhisperTranscriber
from backend.services.llm import LLMClient
from backend.services.tts import TTSClient
from backend.config import get_config

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Get configuration
config = get_config()

# Initialize services
transcriber = WhisperTranscriber(
    model_size=config["whisper_model"],
    device="cuda" if "cuda" in str(config.get("device", "cpu")) else "cpu"
)
llm_client = LLMClient(config["llm_api_endpoint"])
tts_client = TTSClient(config["tts_api_endpoint"])

app = FastAPI(title="Vocalis Conversational AI", version="1.0.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.websocket("/ws")
async def websocket_route(websocket: WebSocket):
    """WebSocket endpoint for real-time communication"""
    await websocket_endpoint(websocket, transcriber, llm_client, tts_client)

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy", 
        "message": "Vocalis Conversational AI is running",
        "config": {
            "vision_enabled": config.get("vision_enabled", False),
            "whisper_model": config.get("whisper_model", "base.en")
        }
    }

@app.get("/config")
async def get_app_config():
    """Get application configuration"""
    return {
        "vision_enabled": config.get("vision_enabled", False),
        "tts_enabled": True,
        "stt_enabled": True,
        "websocket_port": config["websocket_port"]
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=config["websocket_host"],
        port=config["websocket_port"],
        reload=False
    )
