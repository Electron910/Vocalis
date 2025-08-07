import asyncio
import json
import logging
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from backend.config import get_config
from backend.audio_handler import AudioHandler
from backend.llm_client import LLMClient
from backend.tts_client import TTSClient

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Vocalis Conversational AI", version="1.0.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Get configuration
config = get_config()

# Initialize clients
audio_handler = AudioHandler()
llm_client = LLMClient(config["llm_api_endpoint"])
tts_client = TTSClient(config["tts_api_endpoint"])

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("WebSocket connection established")
    
    try:
        while True:
            # Receive audio data
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message["type"] == "audio":
                # Process audio input
                audio_data = message["data"]
                
                # Speech-to-text
                transcript = await audio_handler.transcribe_audio(audio_data)
                
                if transcript:
                    logger.info(f"Transcribed: {transcript}")
                    
                    # Send transcript to client
                    await websocket.send_text(json.dumps({
                        "type": "transcript",
                        "data": transcript
                    }))
                    
                    # Generate LLM response
                    llm_response = await llm_client.generate_response(transcript)
                    
                    if llm_response:
                        logger.info(f"LLM Response: {llm_response}")
                        
                        # Send LLM response to client
                        await websocket.send_text(json.dumps({
                            "type": "response",
                            "data": llm_response
                        }))
                        
                        # Generate TTS audio
                        audio_response = await tts_client.generate_speech(llm_response)
                        
                        if audio_response:
                            # Send audio response to client
                            await websocket.send_text(json.dumps({
                                "type": "audio_response",
                                "data": audio_response
                            }))
            
            elif message["type"] == "text":
                # Handle text-only input
                text_input = message["data"]
                
                # Generate LLM response
                llm_response = await llm_client.generate_response(text_input)
                
                if llm_response:
                    # Send response
                    await websocket.send_text(json.dumps({
                        "type": "response",
                        "data": llm_response
                    }))
                    
                    # Generate TTS audio
                    audio_response = await tts_client.generate_speech(llm_response)
                    
                    if audio_response:
                        await websocket.send_text(json.dumps({
                            "type": "audio_response",
                            "data": audio_response
                        }))
    
    except WebSocketDisconnect:
        logger.info("WebSocket connection closed")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await websocket.close()

@app.get("/health")
async def health_check():
    return {"status": "healthy", "message": "Vocalis Conversational AI is running"}

@app.get("/config")
async def get_app_config():
    return {
        "vision_enabled": False,
        "tts_enabled": True,
        "stt_enabled": True,
        "websocket_port": config["websocket_port"]
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host=config["websocket_host"],
        port=config["websocket_port"],
        reload=False
    )
