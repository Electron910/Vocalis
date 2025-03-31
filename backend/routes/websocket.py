"""
WebSocket Route Handler

Handles WebSocket connections for bidirectional audio streaming.
"""

import json
import logging
import asyncio
import numpy as np
import base64
import os
from typing import Dict, Any, List, Optional, AsyncGenerator
from fastapi import WebSocket, WebSocketDisconnect, BackgroundTasks
from pydantic import BaseModel
from datetime import datetime

from ..services.transcription import WhisperTranscriber
from ..services.llm import LLMClient
from ..services.tts import TTSClient

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# WebSocket message types
class MessageType:
    AUDIO = "audio"
    TRANSCRIPTION = "transcription"
    LLM_RESPONSE = "llm_response"
    TTS_CHUNK = "tts_chunk"
    TTS_START = "tts_start"
    TTS_END = "tts_end"
    STATUS = "status"
    ERROR = "error"
    SYSTEM_PROMPT = "system_prompt"
    SYSTEM_PROMPT_UPDATED = "system_prompt_updated"
    GREETING = "greeting"
    SILENT_FOLLOWUP = "silent_followup"
    USER_PROFILE = "user_profile"
    USER_PROFILE_UPDATED = "user_profile_updated"

class WebSocketManager:
    """
    Manages WebSocket connections and audio processing.
    """
    
    def __init__(
        self,
        transcriber: WhisperTranscriber,
        llm_client: LLMClient,
        tts_client: TTSClient
    ):
        """
        Initialize the WebSocket manager.
        
        Args:
            transcriber: Whisper transcription service
            llm_client: LLM client service
            tts_client: TTS client service
        """
        self.transcriber = transcriber
        self.llm_client = llm_client
        self.tts_client = tts_client
        
        # State tracking
        self.active_connections: List[WebSocket] = []
        self.is_processing = False
        self.speech_buffer = []
        self.current_audio_task = None
        self.interrupt_playback = asyncio.Event()
        
        # File paths
        self.prompt_path = os.path.join("prompts", "system_prompt.md")
        self.profile_path = os.path.join("prompts", "user_profile.json")
        
        # Load system prompt and user profile
        self.system_prompt = self._load_system_prompt()
        self.user_profile = self._load_user_profile()
        
        logger.info("Initialized WebSocket Manager")
    
    def _load_system_prompt(self) -> str:
        """
        Load system prompt from file or use default if file doesn't exist.
        
        Returns:
            str: The system prompt
        """
        default_prompt = (
            "You are a helpful, friendly, and concise voice assistant. "
            "Respond to user queries in a natural, conversational manner. "
            "Keep responses brief and to the point, as you're communicating via voice. "
            "When providing information, focus on the most relevant details. "
            "If you don't know something, admit it rather than making up an answer."
        )
        
        try:
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(self.prompt_path), exist_ok=True)
            
            # Read from file if it exists
            if os.path.exists(self.prompt_path):
                with open(self.prompt_path, "r") as f:
                    prompt = f.read().strip()
                    if prompt:  # Only use if not empty
                        return prompt
            
            # If file doesn't exist or is empty, write default prompt
            with open(self.prompt_path, "w") as f:
                f.write(default_prompt)
            
            return default_prompt
            
        except Exception as e:
            logger.error(f"Error loading system prompt: {e}")
            return default_prompt
    
    async def connect(self, websocket: WebSocket):
        """
        Handle a new WebSocket connection.
        
        Args:
            websocket: The WebSocket connection
        """
        await websocket.accept()
        self.active_connections.append(websocket)
        
        # Send initial status
        await self._send_status(websocket, "connected", {
            "transcription_active": self.transcriber.is_processing,
            "llm_active": self.llm_client.is_processing,
            "tts_active": self.tts_client.is_processing
        })
        
        logger.info(f"Client connected. Active connections: {len(self.active_connections)}")
    
    def disconnect(self, websocket: WebSocket):
        """
        Handle a WebSocket disconnection.
        
        Args:
            websocket: The WebSocket connection
        """
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info(f"Client disconnected. Active connections: {len(self.active_connections)}")
    
    async def _send_status(self, websocket: WebSocket, status: str, data: Dict[str, Any]):
        """
        Send a status update to a WebSocket client.
        
        Args:
            websocket: The WebSocket connection
            status: Status message
            data: Additional data
        """
        await websocket.send_json({
            "type": MessageType.STATUS,
            "status": status,
            "timestamp": datetime.now().isoformat(),
            "data": data
        })
    
    async def _send_error(self, websocket: WebSocket, error: str, details: Optional[Dict[str, Any]] = None):
        """
        Send an error message to a WebSocket client.
        
        Args:
            websocket: The WebSocket connection
            error: Error message
            details: Additional error details
        """
        await websocket.send_json({
            "type": MessageType.ERROR,
            "error": error,
            "timestamp": datetime.now().isoformat(),
            "details": details or {}
        })
    
    async def handle_audio(self, websocket: WebSocket, audio_data: bytes):
        """
        Process incoming audio data from a WebSocket client.
        
        Args:
            websocket: The WebSocket connection
            audio_data: Raw audio data
        """
        try:
            # We're receiving WAV data, so we need to parse the WAV header
            # WAV format: 44-byte header followed by PCM data
            # Let whisper handle the WAV data directly - it can parse WAV headers
            audio_array = np.frombuffer(audio_data, dtype=np.uint8)
            
            # Interrupt any ongoing TTS playback
            if self.tts_client.is_processing:
                logger.info("Interrupting TTS playback due to new speech")
                self.interrupt_playback.set()
                
                # Let any current processing finish before starting new
                if self.current_audio_task and not self.current_audio_task.done():
                    try:
                        await self.current_audio_task
                    except asyncio.CancelledError:
                        logger.info("Previous audio task cancelled")
            
            # Process the audio segment in a background task
            # Whisper will handle voice activity detection internally
            self.current_audio_task = asyncio.create_task(
                self._process_speech_segment(websocket, audio_array)
            )
            
            # Send processing status update
            await self._send_status(websocket, "audio_processing", {
                "transcription_active": self.transcriber.is_processing
            })
                
        except Exception as e:
            logger.error(f"Error processing audio: {e}")
            await self._send_error(websocket, f"Audio processing error: {str(e)}")
    
    async def _process_speech_segment(self, websocket: WebSocket, speech_audio: np.ndarray):
        """
        Process a complete speech segment.
        
        Args:
            websocket: The WebSocket connection
            speech_audio: Speech audio as numpy array
        """
        try:
            # Set processing flag
            self.is_processing = True
            self.interrupt_playback.clear()
            
            # Transcribe speech
            await self._send_status(websocket, "transcribing", {})
            transcript, metadata = self.transcriber.transcribe(speech_audio)
            
            # Send transcription result
            await websocket.send_json({
                "type": MessageType.TRANSCRIPTION,
                "text": transcript,
                "metadata": metadata,
                "timestamp": datetime.now().isoformat()
            })
            
            # Skip LLM and TTS if transcription is empty
            if not transcript.strip():
                logger.info("Empty transcription, skipping LLM and TTS")
                return
            
            # Get LLM response
            await self._send_status(websocket, "processing_llm", {})
            llm_response = self.llm_client.get_response(transcript, self.system_prompt)
            
            # Send LLM response
            await websocket.send_json({
                "type": MessageType.LLM_RESPONSE,
                "text": llm_response["text"],
                "metadata": {k: v for k, v in llm_response.items() if k != "text"},
                "timestamp": datetime.now().isoformat()
            })
            
            # Generate and send TTS audio
            await self._send_tts_response(websocket, llm_response["text"])
            
        except Exception as e:
            logger.error(f"Error processing speech segment: {e}")
            await self._send_error(websocket, f"Speech processing error: {str(e)}")
        finally:
            self.is_processing = False
    
    async def _send_tts_response(self, websocket: WebSocket, text: str):
        """
        Generate and send TTS audio.
        
        Args:
            websocket: The WebSocket connection
            text: Text to convert to speech
        """
        if not text.strip():
            logger.info("Empty text for TTS, skipping")
            return
        
        try:
            # Signal TTS start
            await websocket.send_json({
                "type": MessageType.TTS_START,
                "timestamp": datetime.now().isoformat()
            })
            
            await self._send_status(websocket, "generating_speech", {})
            
            # Get the complete audio file
            audio_data = await self.tts_client.async_text_to_speech(text)
            
            # Check if playback should be interrupted
            if self.interrupt_playback.is_set():
                logger.info("TTS generation interrupted")
                return
            
            # Encode and send the complete audio file
            encoded_audio = base64.b64encode(audio_data).decode("utf-8")
            await websocket.send_json({
                "type": MessageType.TTS_CHUNK,
                "audio_chunk": encoded_audio,
                "format": self.tts_client.output_format,
                "timestamp": datetime.now().isoformat()
            })
            
            # Signal TTS end
            if not self.interrupt_playback.is_set():
                await websocket.send_json({
                    "type": MessageType.TTS_END,
                    "timestamp": datetime.now().isoformat()
                })
            
        except Exception as e:
            logger.error(f"Error streaming TTS: {e}")
            await self._send_error(websocket, f"TTS streaming error: {str(e)}")
    
    def _load_user_profile(self) -> Dict[str, Any]:
        """
        Load user profile from file or create a default one if it doesn't exist.
        
        Returns:
            Dict[str, Any]: The user profile
        """
        default_profile = {
            "name": "",
            "preferences": {}
        }
        
        try:
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(self.profile_path), exist_ok=True)
            
            # Read from file if it exists
            if os.path.exists(self.profile_path):
                with open(self.profile_path, "r") as f:
                    profile = json.load(f)
                    if profile:  # Only use if not empty
                        return profile
            
            # If file doesn't exist or is empty, write default profile
            with open(self.profile_path, "w") as f:
                json.dump(default_profile, f, indent=2)
            
            return default_profile
            
        except Exception as e:
            logger.error(f"Error loading user profile: {e}")
            return default_profile
    
    def _save_user_profile(self) -> bool:
        """
        Save user profile to file.
        
        Returns:
            bool: Whether the save was successful
        """
        try:
            os.makedirs(os.path.dirname(self.profile_path), exist_ok=True)
            with open(self.profile_path, "w") as f:
                json.dump(self.user_profile, f, indent=2)
            return True
        except Exception as e:
            logger.error(f"Error saving user profile: {e}")
            return False
    
    def _get_user_name(self) -> str:
        """Get the user's name from the profile, or empty string if not set."""
        return self.user_profile.get("name", "")
    
    def _set_user_name(self, name: str) -> bool:
        """
        Set the user's name in the profile.
        
        Args:
            name: User name to set
            
        Returns:
            bool: Whether the update was successful
        """
        self.user_profile["name"] = name
        return self._save_user_profile()
    
    def _get_greeting_prompt(self, is_returning_user: bool = False) -> str:
        """
        Get the greeting prompt.
        
        Args:
            is_returning_user: Whether this is a returning user
            
        Returns:
            str: The greeting prompt
        """
        user_name = self._get_user_name()
        
        if user_name:
            if is_returning_user:
                return f"Create a friendly greeting for {user_name} who just activated their microphone. Be brief and conversational, but treat it like you've met them before. Do not do anything else."
            else:
                return f"Create a friendly greeting for {user_name} who just activated their microphone. Be brief and conversational, but treat it like you're meeting them for the first time. Do not do anything else."
        else:
            if is_returning_user:
                return "Create a friendly greeting for someone who just activated their microphone. Be brief and conversational, but treat it like you've met them before. Do not do anything else."
            else:
                return "Create a friendly greeting for someone who just activated their microphone. Be brief and conversational, but treat it like you're meeting them for the first time. Do not do anything else."
    
    def _get_followup_prompt(self, tier: int) -> str:
        """
        Get the follow-up prompt.
        
        Args:
            tier: The follow-up tier (0-2)
            
        Returns:
            str: The follow-up prompt
        """
        user_name = self._get_user_name()
        name_part = f" {user_name}" if user_name else ""
        
        # Adjust approach based on tier
        if tier == 0:
            approach = "gentle check-in"
        elif tier == 1:
            approach = "casual follow-up"
        else:  # tier == 2
            approach = "friendly reminder"
        
        if user_name:    
            return f"Create a {approach} for {user_name} who hasn't responded to your last message. Be brief and conversational. Do not do anything else."
        else:
            return f"Create a {approach} for someone who hasn't responded to your last message. Be brief and conversational. Do not do anything else."

    def _initialize_conversation_context(self):
        """
        Initialize or update the conversation context with user information.
        This ensures the LLM has access to the user's name throughout the conversation.
        """
        # Check if user has a name
        user_name = self._get_user_name()
        if not user_name:
            logger.info("No user name set, skipping context initialization")
            return False
            
        logger.info(f"Initializing conversation context with user name: {user_name}")
        
        # Format the context message
        context_message = {
            "role": "system",
            "content": f"USER CONTEXT: The user's name is {user_name}."
        }
        
        # Check if we already have a system prompt as the first message
        if self.llm_client.conversation_history and self.llm_client.conversation_history[0]["role"] == "system":
            # Check if we already have a user context message
            if len(self.llm_client.conversation_history) > 1 and "USER CONTEXT" in self.llm_client.conversation_history[1].get("content", ""):
                # Replace existing context message
                self.llm_client.conversation_history[1] = context_message
            else:
                # Insert after system prompt
                self.llm_client.conversation_history.insert(1, context_message)
        else:
            # No system prompt, add context as first message
            self.llm_client.conversation_history.insert(0, context_message)
            
        return True

    async def _handle_greeting(self, websocket: WebSocket):
        """
        Handle greeting request when user first clicks microphone.
        """
        try:
            # Check if user has conversation history
            has_history = len(self.llm_client.conversation_history) > 0
            
            # Save current conversation history and temporarily clear it
            saved_history = self.llm_client.conversation_history.copy()
            self.llm_client.conversation_history = []
            
            # Get customized greeting prompt
            instruction = self._get_greeting_prompt(is_returning_user=has_history)
            
            # Get response from LLM without adding to conversation history, with moderate temperature
            # Use instruction as user message, not as system message
            logger.info("Generating greeting")
            llm_response = self.llm_client.get_response(instruction, self.system_prompt, add_to_history=False, temperature=0.7)
            
            # Restore saved conversation history
            self.llm_client.conversation_history = saved_history
            
            # Initialize conversation context with user information
            # This ensures the LLM knows the user's name in subsequent interactions
            self._initialize_conversation_context()
            
            # Send LLM response
            await websocket.send_json({
                "type": MessageType.LLM_RESPONSE,
                "text": llm_response["text"],
                "metadata": {k: v for k, v in llm_response.items() if k != "text"},
                "timestamp": datetime.now().isoformat()
            })
            
            # Generate and send TTS audio
            await self._send_tts_response(websocket, llm_response["text"])
            
        except Exception as e:
            logger.error(f"Error generating greeting: {e}")
            await self._send_error(websocket, f"Greeting error: {str(e)}")
    
    async def _handle_silent_followup(self, websocket: WebSocket, tier: int):
        """
        Handle silent follow-up when user doesn't respond.
        
        Args:
            websocket: The WebSocket connection
            tier: Current follow-up tier (0-2)
        """
        try:
            # Save full conversation history
            full_history = self.llm_client.conversation_history.copy()
            
            # Extract recent conversation context (keeping last few exchanges)
            context_messages = []
            
            # If there's a system message, keep it at the beginning
            if full_history and full_history[0]["role"] == "system":
                context_messages.append(full_history[0])
                recent_history = full_history[1:]
            else:
                recent_history = full_history
            
            # Include the last several exchanges for context (up to 6 messages)
            # This provides enough context for a meaningful continuation
            num_context_messages = min(6, len(recent_history))
            context_messages.extend(recent_history[-num_context_messages:])
            
            # Temporarily set conversation history to just these context messages
            self.llm_client.conversation_history = context_messages
            
            # Select appropriate silence indicator based on tier
            user_input = "[silent]" if tier == 0 else "[no response]" if tier == 1 else "[still waiting]"
            
            # Generate the follow-up with the silence indicator as user input
            logger.info(f"Generating contextual follow-up (tier {tier+1})")
            llm_response = self.llm_client.get_response(user_input, self.system_prompt, add_to_history=False, temperature=0.7)
            
            # Restore original conversation history
            self.llm_client.conversation_history = full_history
            
            # Send LLM response
            await websocket.send_json({
                "type": MessageType.LLM_RESPONSE,
                "text": llm_response["text"],
                "metadata": {k: v for k, v in llm_response.items() if k != "text"},
                "timestamp": datetime.now().isoformat()
            })
            
            # Generate and send TTS audio
            await self._send_tts_response(websocket, llm_response["text"])
            
        except Exception as e:
            logger.error(f"Error generating silent follow-up: {e}")
            await self._send_error(websocket, f"Follow-up error: {str(e)}")
    
    async def handle_client_message(self, websocket: WebSocket, message: Dict[str, Any]):
        """
        Handle a message from a WebSocket client.
        
        Args:
            websocket: The WebSocket connection
            message: The message from the client
        """
        try:
            message_type = message.get("type", "")
            
            if message_type == MessageType.AUDIO:
                # Handle audio data
                audio_base64 = message.get("audio_data", "")
                if audio_base64:
                    audio_bytes = base64.b64decode(audio_base64)
                    await self.handle_audio(websocket, audio_bytes)
            
            elif message_type == "interrupt":
                # Handle interrupt request
                logger.info("Received interrupt request from client")
                self.interrupt_playback.set()
                await self._send_status(websocket, "interrupted", {})
                
            elif message_type == "clear_history":
                # Clear conversation history
                self.llm_client.clear_history(keep_system_prompt=True)
                
                # Reinitialize conversation context to maintain user name awareness
                # This ensures the LLM retains knowledge of the user's name even after history is cleared
                self._initialize_conversation_context()
                logger.info("Reinitialized user context after clearing history")
                
                await self._send_status(websocket, "history_cleared", {})
                
            elif message_type == MessageType.GREETING:
                # Handle greeting request
                await self._handle_greeting(websocket)
                
            elif message_type == MessageType.SILENT_FOLLOWUP:
                # Handle silent follow-up
                tier = message.get("tier", 0)
                await self._handle_silent_followup(websocket, tier)
                
            elif message_type == "get_system_prompt":
                # Send current system prompt to client
                await self._handle_get_system_prompt(websocket)
                
            elif message_type == "update_system_prompt":
                # Update system prompt
                new_prompt = message.get("prompt", "")
                if new_prompt:
                    await self._handle_update_system_prompt(websocket, new_prompt)
                else:
                    await self._send_error(websocket, "Empty system prompt")
                    
            elif message_type == "get_user_profile":
                # Send current user profile to client
                await self._handle_get_user_profile(websocket)
                
            elif message_type == "update_user_profile":
                # Update user profile
                name = message.get("name", "")
                await self._handle_update_user_profile(websocket, name)
                
            elif message_type == "ping":
                # Respond to ping
                await websocket.send_json({
                    "type": "pong",
                    "timestamp": datetime.now().isoformat()
                })
            
            elif message_type == "pong":
                # Silently accept pong messages (client keepalive response)
                # No need to do anything with them
                pass
                
            else:
                logger.warning(f"Unknown message type: {message_type}")
                await self._send_error(websocket, f"Unknown message type: {message_type}")
                
        except Exception as e:
            logger.error(f"Error handling client message: {e}")
            await self._send_error(websocket, f"Message handling error: {str(e)}")
    
    async def _handle_get_user_profile(self, websocket: WebSocket):
        """
        Send the current user profile to the client.
        
        Args:
            websocket: The WebSocket connection
        """
        try:
            await websocket.send_json({
                "type": MessageType.USER_PROFILE,
                "name": self._get_user_name(),
                "timestamp": datetime.now().isoformat()
            })
            logger.info("Sent user profile to client")
        except Exception as e:
            logger.error(f"Error sending user profile: {e}")
            await self._send_error(websocket, f"Error sending user profile: {str(e)}")
    
    async def _handle_update_user_profile(self, websocket: WebSocket, name: str):
        """
        Update the user profile.
        
        Args:
            websocket: The WebSocket connection
            name: User name to set
        """
        try:
            # Update name
            success = self._set_user_name(name)
            
            # Update conversation context with the new name
            if success:
                # Initialize conversation context with the updated name
                self._initialize_conversation_context()
                logger.info(f"Updated user profile name to: {name} and refreshed conversation context")
            else:
                logger.error("Failed to update user profile")
            
            # Send confirmation
            await websocket.send_json({
                "type": MessageType.USER_PROFILE_UPDATED,
                "success": success,
                "timestamp": datetime.now().isoformat()
            })
            
            if not success:
                await self._send_error(websocket, "Failed to update user profile")
                
        except Exception as e:
            logger.error(f"Error updating user profile: {e}")
            await self._send_error(websocket, f"Error updating user profile: {str(e)}")
            
    async def _handle_get_system_prompt(self, websocket: WebSocket):
        """
        Send the current system prompt to the client.
        
        Args:
            websocket: The WebSocket connection
        """
        try:
            await websocket.send_json({
                "type": MessageType.SYSTEM_PROMPT,
                "prompt": self.system_prompt,
                "timestamp": datetime.now().isoformat()
            })
            logger.info("Sent system prompt to client")
        except Exception as e:
            logger.error(f"Error sending system prompt: {e}")
            await self._send_error(websocket, f"Error sending system prompt: {str(e)}")
    
    async def _handle_update_system_prompt(self, websocket: WebSocket, new_prompt: str):
        """
        Update the system prompt.
        
        Args:
            websocket: The WebSocket connection
            new_prompt: New system prompt
        """
        try:
            # Validate prompt (basic check for non-empty)
            if not new_prompt.strip():
                await self._send_error(websocket, "System prompt cannot be empty")
                return
            
            # Update in memory
            self.system_prompt = new_prompt
            
            # Save to file
            os.makedirs(os.path.dirname(self.prompt_path), exist_ok=True)
            with open(self.prompt_path, "w") as f:
                f.write(new_prompt)
            
            # Send confirmation
            await websocket.send_json({
                "type": MessageType.SYSTEM_PROMPT_UPDATED,
                "success": True,
                "timestamp": datetime.now().isoformat()
            })
            
            logger.info("Updated system prompt")
        except Exception as e:
            logger.error(f"Error updating system prompt: {e}")
            await self._send_error(websocket, f"Error updating system prompt: {str(e)}")

async def websocket_endpoint(
    websocket: WebSocket,
    transcriber: WhisperTranscriber,
    llm_client: LLMClient,
    tts_client: TTSClient
):
    """
    FastAPI WebSocket endpoint.
    
    Args:
        websocket: The WebSocket connection
        transcriber: Whisper transcription service
        llm_client: LLM client service
        tts_client: TTS client service
    """
    # Create WebSocket manager
    manager = WebSocketManager(transcriber, llm_client, tts_client)
    
    try:
        # Accept connection
        await manager.connect(websocket)
        
        # Handle messages
        while True:
            try:
                # Receive message with a timeout
                message = await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=30.0  # 30 second timeout
                )
                
                # Process message
                await manager.handle_client_message(websocket, message)
                
            except asyncio.TimeoutError:
                # Send a ping to keep the connection alive
                await websocket.send_json({
                    "type": "ping",
                    "timestamp": datetime.now().isoformat()
                })
                
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        # Disconnect
        manager.disconnect(websocket)
