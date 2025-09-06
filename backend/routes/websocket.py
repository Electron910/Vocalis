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
import soundfile as sf
import io
import wave

from ..services.transcription import WhisperTranscriber
from ..services.llm import LLMClient
from ..services.tts import TTSClient
from ..services.conversation_storage import ConversationStorage

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
    
    # Session storage message types
    SAVE_SESSION = "save_session"
    SAVE_SESSION_RESULT = "save_session_result"
    LOAD_SESSION = "load_session"
    LOAD_SESSION_RESULT = "load_session_result"
    LIST_SESSIONS = "list_sessions"
    LIST_SESSIONS_RESULT = "list_sessions_result"
    DELETE_SESSION = "delete_session"
    DELETE_SESSION_RESULT = "delete_session_result"
    
    # Vision feature message types
    VISION_SETTINGS = "vision_settings"
    VISION_SETTINGS_UPDATED = "vision_settings_updated"
    VISION_FILE_UPLOAD = "vision_file_upload"
    VISION_FILE_UPLOAD_RESULT = "vision_file_upload_result"
    VISION_PROCESSING = "vision_processing"
    VISION_READY = "vision_ready"

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
        self.current_vision_context = None  # Store the latest vision context
        
        # File paths
        self.prompt_path = os.path.join("prompts", "system_prompt.md")
        self.profile_path = os.path.join("prompts", "user_profile.json")
        self.vision_settings_path = os.path.join("prompts", "vision_settings.json")
        
        # Load system prompt, user profile, and vision settings
        self.system_prompt = self._load_system_prompt()
        self.user_profile = self._load_user_profile()
        self.vision_settings = self._load_vision_settings()
        
        # Initialize conversation storage
        self.conversation_storage = ConversationStorage()
        
        logger.info("Initialized WebSocket Manager")
    
    def _load_system_prompt(self) -> str:
        """
        Load system prompt from file or use default if file doesn't exist.
        
        Returns:
            str: The system prompt
        """
        default_prompt = (
            "You are a helpful, friendly, and concise voice assistant."
            "Respond to user queries in a natural, conversational manner."
            "Keep responses brief and to the point, as you're communicating via voice."
            "When providing information, focus on the most relevant details."
            "If you don't know something, admit it rather than making up an answer"
            "\n\n"
            "Through the webapp, you can receive and understand photographs and pictures."
            "\n\n"
            "When the user sends a message like '[silent]', '[no response]', or '[still waiting]', it means they've gone quiet or haven't responded."
            "When you see these signals, continue the conversation naturally based on the previous topic and context."
            "Stay on topic, be helpful, and don't mention that they were silent - just carry on the conversation as if you're gently following up."
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
            audio_array = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0

            if self.tts_client.is_processing:
                logger.info("Ignoring microphone input during TTS playback")
                return
            
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
                
                # Notify frontend that transcription occurred (even if it's just "...") to let it reset
                await websocket.send_json({
                    "type": MessageType.TRANSCRIPTION,
                    "text": transcript,
                    "metadata": {},
                    "timestamp": datetime.now().isoformat()
                })

                # Still send TTS_END to fully reset UI
                await websocket.send_json({
                    "type": MessageType.TTS_END,
                    "timestamp": datetime.now().isoformat()
                })
                return
                
            # Check if we have recent vision context to incorporate
            has_vision_context = self.current_vision_context is not None
            
            if has_vision_context:
                logger.info("Processing speech with vision context")
                
                # Add vision context to conversation history
                self._add_vision_context_to_conversation(self.current_vision_context)
                
                # Enhance user query with vision context reference
                enhanced_transcript = f"{transcript} [Note: This question refers to the image I just analyzed.]"
                
                # Get LLM response with vision-aware context
                await self._send_status(websocket, "processing_llm", {"has_vision_context": True})
                llm_response = self.llm_client.get_response(enhanced_transcript, self.system_prompt)
                
                # Clear vision context after use to avoid affecting future non-vision conversations
                # Only clear after successful processing
                self.current_vision_context = None
                logger.info("Vision context processed and cleared")
            else:
                # Normal non-vision processing
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
            
            # Stream audio chunks in real-time
            # async for audio_chunk in self.tts_client.stream_text_to_speech_async(text):
            #     # Check if playback should be interrupted
            #     if self.interrupt_playback.is_set():
            #         logger.info("TTS streaming interrupted")
            #         return
                
            #     # Encode and send each audio chunk immediately
            #     # wav_buffer = io.BytesIO()
            #     # sf.write(wav_buffer, audio_chunk, self.tts_client.sample_rate, format="WAV", subtype="PCM_16")
            #     # wav_bytes = wav_buffer.getvalue()

            #     if isinstance(audio_chunk, np.ndarray):
            #         arr = audio_chunk.astype(np.float32)
            #     else:
            #         arr = np.frombuffer(audio_chunk, dtype=np.float32)
                
            #     arr = np.clip(arr, -1.0, 1.0)
                
            #     # Convert to PCM16 and wrap in WAV
            #     pcm16 = (arr * 32767).astype(np.int16)
            #     wav_buf = io.BytesIO()
            #     sf.write(wav_buf, pcm16, self.tts_client.sample_rate, format="WAV", subtype="PCM_16")
            #     wav_bytes = wav_buf.getvalue()
            #     # Encode and send each audio chunk immediately
            #     encoded_audio = base64.b64encode(wav_bytes).decode("utf-8")
            #     await websocket.send_json({
            #         "type": MessageType.TTS_CHUNK,
            #         "audio_chunk": encoded_audio,
            #         "format": "wav",
            #         "sample_rate": self.tts_client.sample_rate,
            #         "timestamp": datetime.now().isoformat()
            #     })
            async for audio_chunk in self.tts_client.stream_text_to_speech_async(text):
                if self.interrupt_playback.is_set():
                    return
            
                if isinstance(audio_chunk, np.ndarray):
                    arr = audio_chunk.astype(np.float32)
                else:
                    arr = np.frombuffer(audio_chunk, dtype=np.float32)
            
                # Clean + clip
                arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
                arr = np.clip(arr, -1.0, 1.0)
            
                # Convert to PCM16
                pcm16 = (arr * 32767.0).astype(np.int16)
            
                # Use global sample rate setting
                from backend.config import ORPHEUS_SAMPLE_RATE
                # sample_rate = ORPHEUS_SAMPLE_RATE
            
                # Wrap in WAV
                wav_buf = io.BytesIO()
                sf.write(wav_buf, pcm16, format="WAV", subtype="PCM_16")
                wav_bytes = wav_buf.getvalue()
            
                encoded_audio = base64.b64encode(wav_bytes).decode("utf-8")
            
                await websocket.send_json({
                    "type": MessageType.TTS_CHUNK,
                    "audio_chunk": encoded_audio,
                    "format": "wav",
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
    
    async def _handle_save_session(self, websocket: WebSocket, title: Optional[str] = None, session_id: Optional[str] = None):
        """
        Handle save session request.
        
        Args:
            websocket: The WebSocket connection
            title: Optional title for the session
            session_id: Optional ID for the session (for overwriting existing)
        """
        try:
            # Get current conversation history from LLM client
            messages = self.llm_client.conversation_history.copy()
            
            # Don't save empty conversations
            if not messages:
                # Send proper save result with failure instead of generic error
                await websocket.send_json({
                    "type": MessageType.SAVE_SESSION_RESULT,
                    "success": False,
                    "error": "Cannot save empty conversation",
                    "timestamp": datetime.now().isoformat()
                })
                return
            
            # Generate metadata (timestamp, message count, etc.)
            metadata = {
                "message_count": len(messages),
                "user_message_count": sum(1 for m in messages if m.get("role") == "user"),
                "assistant_message_count": sum(1 for m in messages if m.get("role") == "assistant"),
                "user_name": self._get_user_name() or "Anonymous",
            }
            # Save session (now async)
            session_id = await self.conversation_storage.save_session(
                messages=messages,
                title=title,
                session_id=session_id,
                metadata=metadata
            )
            
            # Send confirmation
            await websocket.send_json({
                "type": MessageType.SAVE_SESSION_RESULT,
                "success": True,
                "session_id": session_id,
                "timestamp": datetime.now().isoformat()
            })
            
            logger.info(f"Saved conversation session: {session_id}")
            
        except Exception as e:
            logger.error(f"Error saving session: {e}")
            await self._send_error(websocket, f"Failed to save conversation: {str(e)}")
    
    async def _handle_load_session(self, websocket: WebSocket, session_id: str):
        """
        Handle load session request.
        
        Args:
            websocket: The WebSocket connection
            session_id: ID of the session to load
        """
        try:
            # Load session (now async)
            session = await self.conversation_storage.load_session(session_id)

            if not session:
                await self._send_error(websocket, f"Session not found: {session_id}")
                return
            
            # Update LLM client's conversation history
            self.llm_client.conversation_history = session.get("messages", [])
            
            # Send confirmation
            await websocket.send_json({
                "type": MessageType.LOAD_SESSION_RESULT,
                "success": True,
                "session_id": session_id,
                "title": session.get("title", ""),
                "message_count": len(session.get("messages", [])),
                "timestamp": datetime.now().isoformat()
            })
            
            logger.info(f"Loaded conversation session: {session_id}")
            
        except Exception as e:
            logger.error(f"Error loading session: {e}")
            await self._send_error(websocket, f"Failed to load conversation: {str(e)}")
    
    async def _handle_list_sessions(self, websocket: WebSocket):
        """
        Handle list sessions request.
        
        Args:
            websocket: The WebSocket connection
        """
        try:
            # Get sessions (now async)
            sessions = await self.conversation_storage.list_sessions()

            # Send list
            await websocket.send_json({
                "type": MessageType.LIST_SESSIONS_RESULT,
                "sessions": sessions,
                "timestamp": datetime.now().isoformat()
            })
            
            logger.info(f"Listed {len(sessions)} conversation sessions")
            
        except Exception as e:
            logger.error(f"Error listing sessions: {e}")
            await self._send_error(websocket, f"Failed to list conversations: {str(e)}")
    
    async def _handle_delete_session(self, websocket: WebSocket, session_id: str):
        """
        Handle delete session request.
        
        Args:
            websocket: The WebSocket connection
            session_id: ID of the session to delete
        """
        try:
            # Delete session (now async)
            success = await self.conversation_storage.delete_session(session_id)

            # Send confirmation
            await websocket.send_json({
                "type": MessageType.DELETE_SESSION_RESULT,
                "success": success,
                "session_id": session_id,
                "timestamp": datetime.now().isoformat()
            })
            
            if success:
                logger.info(f"Deleted conversation session: {session_id}")
            else:
                logger.warning(f"Failed to delete conversation session: {session_id}")
            
        except Exception as e:
            logger.error(f"Error deleting session: {e}")
            await self._send_error(websocket, f"Failed to delete conversation: {str(e)}")
    
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
                    
            elif message_type == MessageType.VISION_FILE_UPLOAD:
                # Handle vision image upload
                image_base64 = message.get("image_data", "")
                if image_base64:
                    await self._handle_vision_file_upload(websocket, image_base64)
            
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
            
            elif message_type == "get_vision_settings":
                # Send current vision settings to client
                await self._handle_get_vision_settings(websocket)
                
            elif message_type == "update_vision_settings":
                # Update vision settings
                enabled = message.get("enabled", False)
                await self._handle_update_vision_settings(websocket, enabled)
                
            # Session management handlers
            elif message_type == MessageType.SAVE_SESSION:
                # Save current session
                title = message.get("title")
                session_id = message.get("session_id")  # For updating existing
                await self._handle_save_session(websocket, title, session_id)
                
            elif message_type == MessageType.LOAD_SESSION:
                # Load a saved session
                session_id = message.get("session_id")
                if not session_id:
                    await self._send_error(websocket, "Session ID is required")
                    return
                await self._handle_load_session(websocket, session_id)
                
            elif message_type == MessageType.LIST_SESSIONS:
                # List available sessions
                await self._handle_list_sessions(websocket)
                
            elif message_type == MessageType.DELETE_SESSION:
                # Delete a session
                session_id = message.get("session_id")
                if not session_id:
                    await self._send_error(websocket, "Session ID is required")
                    return
                await self._handle_delete_session(websocket, session_id)
                
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
    
    def _load_vision_settings(self) -> Dict[str, Any]:
        """
        Load vision settings from file or create a default one if it doesn't exist.
        
        Returns:
            Dict[str, Any]: The vision settings
        """
        default_settings = {
            "enabled": False
        }
        
        try:
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(self.vision_settings_path), exist_ok=True)
            
            # Read from file if it exists
            if os.path.exists(self.vision_settings_path):
                with open(self.vision_settings_path, "r") as f:
                    settings = json.load(f)
                    if settings:  # Only use if not empty
                        return settings
            
            # If file doesn't exist or is empty, write default settings
            with open(self.vision_settings_path, "w") as f:
                json.dump(default_settings, f, indent=2)
            
            return default_settings
            
        except Exception as e:
            logger.error(f"Error loading vision settings: {e}")
            return default_settings
    
    def _save_vision_settings(self) -> bool:
        """
        Save vision settings to file.
        
        Returns:
            bool: Whether the save was successful
        """
        try:
            os.makedirs(os.path.dirname(self.vision_settings_path), exist_ok=True)
            with open(self.vision_settings_path, "w") as f:
                json.dump(self.vision_settings, f, indent=2)
            return True
        except Exception as e:
            logger.error(f"Error saving vision settings: {e}")
            return False
    
    async def _handle_get_vision_settings(self, websocket: WebSocket):
        """
        Send the current vision settings to the client.
        
        Args:
            websocket: The WebSocket connection
        """
        try:
            await websocket.send_json({
                "type": MessageType.VISION_SETTINGS,
                "enabled": self.vision_settings.get("enabled", False),
                "timestamp": datetime.now().isoformat()
            })
            logger.info("Sent vision settings to client")
        except Exception as e:
            logger.error(f"Error sending vision settings: {e}")
            await self._send_error(websocket, f"Error sending vision settings: {str(e)}")
    
    async def _handle_update_vision_settings(self, websocket: WebSocket, enabled: bool):
        """
        Update the vision settings.
        
        Args:
            websocket: The WebSocket connection
            enabled: Whether vision is enabled
        """
        try:
            # Update in memory
            self.vision_settings["enabled"] = enabled
            
            # Save to file
            success = self._save_vision_settings()
            
            # Send confirmation
            await websocket.send_json({
                "type": MessageType.VISION_SETTINGS_UPDATED,
                "success": success,
                "timestamp": datetime.now().isoformat()
            })
            
            logger.info(f"Updated vision settings: enabled={enabled}")
        except Exception as e:
            logger.error(f"Error updating vision settings: {e}")
            await self._send_error(websocket, f"Error updating vision settings: {str(e)}")
    
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
            
    def _add_vision_context_to_conversation(self, vision_context: str):
        """
        Add vision context to the conversation history.
        
        Args:
            vision_context: Description of the image from SmolVLM
        """
        # Add as a system message to provide context for future exchanges
        vision_message = {
            "role": "system",
            "content": f"[VISION CONTEXT]: {vision_context}"
        }
        
        # If conversation history is empty, add it as the first message
        # Otherwise, insert after the main system prompt
        if not self.llm_client.conversation_history:
            self.llm_client.conversation_history.append(vision_message)
        else:
            # Find the last system message that's not a vision context
            last_system_idx = -1
            for i, msg in enumerate(self.llm_client.conversation_history):
                if msg["role"] == "system" and not msg["content"].startswith("[VISION CONTEXT]"):
                    last_system_idx = i
            
            # Insert after the last system message, or at the beginning if none found
            if last_system_idx >= 0:
                self.llm_client.conversation_history.insert(last_system_idx + 1, vision_message)
            else:
                self.llm_client.conversation_history.insert(0, vision_message)
    
    async def _handle_vision_file_upload(self, websocket: WebSocket, image_base64: str):
        """
        Handle vision image upload from client.
        
        Args:
            websocket: The WebSocket connection
            image_base64: Base64-encoded image data
        """
        try:
            # Validate vision is enabled
            if not self.vision_settings.get("enabled", False):
                await self._send_error(websocket, "Vision feature is not enabled")
                return
                
            # Notify client that upload was received
            await websocket.send_json({
                "type": MessageType.VISION_FILE_UPLOAD_RESULT,
                "success": True,
                "timestamp": datetime.now().isoformat()
            })
            
            # Send processing status
            await websocket.send_json({
                "type": MessageType.VISION_PROCESSING,
                "status": "Analyzing image...",
                "timestamp": datetime.now().isoformat()
            })
            
            # Process image with vision service
            logger.info("Processing vision image with SmolVLM")
            
            # Import vision service (to avoid circular imports)
            from ..services.vision import vision_service
            
            # Create a descriptive prompt for the image
            prompt = "Describe this image in detail. Include information about objects, people, scenes, text, and any notable elements."
            
            # Process the image (run in a thread pool to not block the event loop)
            vision_context = await asyncio.to_thread(
                vision_service.process_image,
                image_base64,
                prompt
            )
            
            # Store the vision context for later use in conversation
            self.current_vision_context = vision_context
            
            # Send vision ready notification with the generated context
            await websocket.send_json({
                "type": MessageType.VISION_READY,
                "context": vision_context,
                "timestamp": datetime.now().isoformat()
            })
            
            logger.info("Vision processing complete with SmolVLM model")
        except Exception as e:
            logger.error(f"Error processing vision image: {e}")
            await self._send_error(websocket, f"Vision processing error: {str(e)}")

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
