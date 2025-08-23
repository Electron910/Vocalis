"""
Text-to-Speech Service

Handles communication with the local TTS API endpoint.
"""

import json
import requests
import logging
import io
import time
import base64
import asyncio
from typing import Dict, Any, List, Optional, BinaryIO, Generator, AsyncGenerator

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TTSClient:
    """
    Client for communicating with a local TTS API.
    
    This class handles requests to a locally hosted TTS API that follows
    the OpenAI API format for text-to-speech generation.
    """
    
    def __init__(
        self,
        api_endpoint: str = "http://0.0.0.0:5005/v1/audio/speech/stream",
        model: str = "tts-1",
        voice: str = "tara",
        output_format: str = "wav",
        speed: float = 1.0,
        timeout: int = 60,
        chunk_size: int = 4096
    ):
        """
        Initialize the TTS client.
        
        Args:
            api_endpoint: URL of the local TTS API
            model: TTS model name to use
            voice: Voice to use for synthesis
            output_format: Output audio format (mp3, opus, aac, flac)
            speed: Speech speed multiplier (0.25 to 4.0)
            timeout: Request timeout in seconds
            chunk_size: Size of audio chunks to stream in bytes
        """
        self.api_endpoint = api_endpoint
        self.model = model
        self.voice = voice
        self.output_format = output_format
        self.speed = speed
        self.timeout = timeout
        self.chunk_size = chunk_size
        
        # State tracking
        self.is_processing = False
        self.last_processing_time = 0
        
        logger.info(f"Initialized TTS Client with endpoint={api_endpoint}, "
                   f"model={model}, voice={voice}")
    
    def text_to_speech(self, text: str) -> bytes:
        """
        Convert text to speech audio.
        
        Args:
            text: Text to convert to speech
            
        Returns:
            Audio data as bytes
        """
        self.is_processing = True
        start_time = time.time()
        
        try:
            # Prepare request payload
            payload = {
                "model": self.model,
                "input": text,
                "voice": self.voice,
                "response_format": self.output_format,
                "speed": self.speed
            }
            
            logger.info(f"Sending TTS request with {len(text)} characters of text")
            
            # Send request to TTS API
            response = requests.post(
                self.api_endpoint,
                json=payload,
                timeout=self.timeout
            )
            
            # Check if request was successful
            response.raise_for_status()
            
            # Get audio content
            audio_data = response.content
            
            # Calculate processing time
            self.last_processing_time = time.time() - start_time
            
            logger.info(f"Received TTS response after {self.last_processing_time:.2f}s, "
                       f"size: {len(audio_data)} bytes")
            
            return audio_data
            
        except requests.RequestException as e:
            logger.error(f"TTS API request error: {e}")
            raise
        except Exception as e:
            logger.error(f"TTS processing error: {e}")
            raise
        finally:
            self.is_processing = False
    
    def stream_text_to_speech(self, text: str) -> Generator[bytes, None, None]:
        """
        Stream audio data from the TTS API.
        
        Args:
            text: Text to convert to speech
            
        Yields:
            Chunks of audio data
        """
        self.is_processing = True
        start_time = time.time()
        
        try:
            # Prepare request payload
            payload = {
                "model": self.model,
                "input": text,
                "voice": self.voice,
                "response_format": self.output_format,
                "speed": self.speed
            }
            
            logger.info(f"Sending streaming TTS request with {len(text)} characters of text")
            
            # Send request to TTS API
            with requests.post(
                self.api_endpoint,
                json=payload,
                timeout=self.timeout,
                stream=True
            ) as response:
                response.raise_for_status()
                
                # Check if streaming is supported by the API
                is_chunked = response.headers.get('transfer-encoding', '') == 'chunked'
                
                if is_chunked:
                    # The API supports streaming
                    for chunk in response.iter_content(chunk_size=self.chunk_size):
                        if chunk:
                            yield chunk
                else:
                    # The API doesn't support streaming, but we'll fake it by
                    # splitting the response into chunks
                    audio_data = response.content
                    total_chunks = (len(audio_data) + self.chunk_size - 1) // self.chunk_size
                    
                    for i in range(total_chunks):
                        start_idx = i * self.chunk_size
                        end_idx = min(start_idx + self.chunk_size, len(audio_data))
                        yield audio_data[start_idx:end_idx]
                
            # Calculate processing time
            self.last_processing_time = time.time() - start_time
            logger.info(f"Completed TTS streaming after {self.last_processing_time:.2f}s")
            
        except requests.RequestException as e:
            logger.error(f"TTS API streaming request error: {e}")
            raise
        except Exception as e:
            logger.error(f"TTS streaming error: {e}")
            raise
        finally:
            self.is_processing = False
    
    async def async_text_to_speech(self, text: str) -> bytes:
        """
        Asynchronously generate audio data from the TTS API.
        
        This method provides asynchronous TTS capability by running
        the synchronous method in a thread.
        
        Args:
            text: Text to convert to speech
            
        Returns:
            Complete audio data as bytes
        """
        self.is_processing = True
        
        try:
            # Get complete audio data
            audio_data = await asyncio.to_thread(self.text_to_speech, text)
            return audio_data
        except Exception as e:
            logger.error(f"Async TTS error: {e}")
            raise
        finally:
            self.is_processing = False
    
    async def stream_text_to_speech_async(self, text: str):
        """
        Asynchronously stream individual audio chunks from the TTS API.
        Each chunk is a complete playable audio segment.
        
        Args:
            text: Text to convert to speech
            
        Yields:
            Individual audio chunks as they are generated
        """
        self.is_processing = True
        start_time = time.time()
        chunk_count = 0
        
        try:
            # Prepare request payload
            payload = {
                "model": self.model,
                "input": text,
                "voice": self.voice,
                "response_format": self.output_format,
                "speed": self.speed
            }
            
            logger.info(f"Starting real-time TTS streaming for {len(text)} characters")
            
            # Use asyncio-compatible HTTP client for true async streaming
            import aiohttp
            
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout)) as session:
                async with session.post(
                    self.api_endpoint,
                    json=payload,
                    headers={"Content-Type": "application/json"}
                ) as response:
                    response.raise_for_status()
                    
                    wav_header_received = False
                    accumulated_data = bytearray()
                    
                    # Stream raw chunks and reconstruct individual audio segments
                    async for raw_chunk in response.content.iter_chunked(self.chunk_size):
                        if not raw_chunk:
                            continue
                            
                        accumulated_data.extend(raw_chunk)
                        
                        # Skip WAV header on first chunk
                        if not wav_header_received and len(accumulated_data) >= 44:
                            # Remove WAV header (first 44 bytes)
                            accumulated_data = accumulated_data[44:]
                            wav_header_received = True
                            logger.info("WAV header processed, starting audio chunk streaming")
                        
                        # Process audio data in meaningful chunks (e.g., 0.1 seconds of audio)
                        # At 24kHz, 16-bit mono: 0.1s = 2400 samples = 4800 bytes
                        chunk_size_bytes = 4800  # ~0.1 seconds of audio
                        
                        while len(accumulated_data) >= chunk_size_bytes:
                            # Extract one chunk
                            audio_chunk_data = accumulated_data[:chunk_size_bytes]
                            accumulated_data = accumulated_data[chunk_size_bytes:]
                            
                            # Create a complete WAV file for this chunk
                            wav_chunk = self._create_wav_chunk(audio_chunk_data)
                            chunk_count += 1
                            
                            logger.info(f"Yielding audio chunk {chunk_count} ({len(wav_chunk)} bytes)")
                            yield wav_chunk
                    
                    # Process any remaining data
                    if len(accumulated_data) > 0:
                        wav_chunk = self._create_wav_chunk(accumulated_data)
                        chunk_count += 1
                        logger.info(f"Yielding final audio chunk {chunk_count} ({len(wav_chunk)} bytes)")
                        yield wav_chunk
            
            # Calculate processing time
            self.last_processing_time = time.time() - start_time
            logger.info(f"Completed real-time TTS streaming: {chunk_count} chunks in {self.last_processing_time:.2f}s")
            
        except Exception as e:
            logger.error(f"Real-time TTS streaming error: {e}")
            raise
        finally:
            self.is_processing = False
    
    def _create_wav_chunk(self, pcm_data: bytes) -> bytes:
        """
        Create a complete WAV file from PCM data chunk.
        
        Args:
            pcm_data: Raw PCM audio data
            
        Returns:
            Complete WAV file bytes
        """
        import struct
        
        # WAV header parameters
        sample_rate = 24000  # Orpheus TTS sample rate
        num_channels = 1     # Mono
        bits_per_sample = 16
        byte_rate = sample_rate * num_channels * bits_per_sample // 8
        block_align = num_channels * bits_per_sample // 8
        data_size = len(pcm_data)
        
        # Create WAV header
        header = bytearray(44)
        
        # RIFF header
        header[0:4] = b'RIFF'
        struct.pack_into('<I', header, 4, 36 + data_size)  # File size
        header[8:12] = b'WAVE'
        
        # fmt subchunk
        header[12:16] = b'fmt '
        struct.pack_into('<I', header, 16, 16)  # Subchunk1Size
        struct.pack_into('<H', header, 20, 1)   # AudioFormat (PCM)
        struct.pack_into('<H', header, 22, num_channels)  # NumChannels
        struct.pack_into('<I', header, 24, sample_rate)   # SampleRate
        struct.pack_into('<I', header, 28, byte_rate)     # ByteRate
        struct.pack_into('<H', header, 32, block_align)   # BlockAlign
        struct.pack_into('<H', header, 34, bits_per_sample)  # BitsPerSample
        
        # data subchunk
        header[36:40] = b'data'
        struct.pack_into('<I', header, 40, data_size)  # Subchunk2Size
        
        # Combine header and data
        return bytes(header) + pcm_data
    
    def get_config(self) -> Dict[str, Any]:
        """
        Get the current configuration.
        
        Returns:
            Dict containing the current configuration
        """
        return {
            "api_endpoint": self.api_endpoint,
            "model": self.model,
            "voice": self.voice,
            "output_format": self.output_format,
            "speed": self.speed,
            "timeout": self.timeout,
            "chunk_size": self.chunk_size,
            "is_processing": self.is_processing,
            "last_processing_time": self.last_processing_time
        }
