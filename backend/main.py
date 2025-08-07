"""
Vocalis Backend Server - RunPod Compatible Version
"""

import logging
import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

# Import configuration
from . import config

# Import services
from .services.transcription import WhisperTranscriber
from .services.llm import LLMClient
from .services.tts import TTSClient

# Import routes
from .routes.websocket import websocket_endpoint

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Global service instances
transcription_service = None
llm_service = None
tts_service = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup and shutdown events for the FastAPI application.
    """
    # Load configuration
    cfg = config.get_config()
    
    # Initialize services on startup
    logger.info("Initializing services...")
    
    global transcription_service, llm_service, tts_service
    
    # Initialize transcription service
    transcription_service = WhisperTranscriber(
        model_size=cfg["whisper_model"],
        sample_rate=cfg["audio_sample_rate"]
    )
    
    # Initialize LLM service
    llm_service = LLMClient(
        api_endpoint=cfg["llm_api_endpoint"]
    )
    
    # Initialize TTS service
    tts_service = TTSClient(
        api_endpoint=cfg["tts_api_endpoint"],
        model=cfg["tts_model"],
        voice=cfg["tts_voice"],
        output_format=cfg["tts_format"]
    )
    
    logger.info("All services initialized successfully")
    
    yield
    
    # Cleanup on shutdown
    logger.info("Shutting down services...")
    logger.info("Shutdown complete")

# Create FastAPI application
app = FastAPI(
    title="Vocalis Backend",
    description="Speech-to-Speech AI Assistant Backend",
    version="0.1.0",
    lifespan=lifespan
)

# Configure CORS for RunPod
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for RunPod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
@app.get("/")
async def root():
    """Root endpoint for health check."""
    return {"status": "ok", "message": "Vocalis backend is running"}

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "ok",
        "services": {
            "transcription": transcription_service is not None,
            "llm": llm_service is not None,
            "tts": tts_service is not None,
        },
        "config": {
            "whisper_model": config.WHISPER_MODEL,
            "tts_voice": config.TTS_VOICE,
            "websocket_port": config.WEBSOCKET_PORT,
            "vision_enabled": False
        }
    }

@app.get("/config")
async def get_full_config():
    """Get full configuration."""
    return {
        "transcription": transcription_service.get_config() if transcription_service else {},
        "llm": llm_service.get_config() if llm_service else {},
        "tts": tts_service.get_config() if tts_service else {},
        "system": config.get_config()
    }

# WebSocket route
@app.websocket("/ws")
async def websocket_route(websocket: WebSocket):
    """WebSocket endpoint for bidirectional audio streaming."""
    await websocket_endpoint(
        websocket, 
        transcription_service, 
        llm_service, 
        tts_service
    )

# Run server directly if executed as script
if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host=config.WEBSOCKET_HOST,
        port=config.WEBSOCKET_PORT,
        reload=False
    )
