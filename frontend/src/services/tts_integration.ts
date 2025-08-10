/**
 * TTS Integration Service
 * 
 * This replaces the WebSocket TTS chunks with direct HTTP calls to Orpheus TTS
 * Use this to integrate your working Orpheus TTS endpoint
 */

// Your working TTS endpoint
const TTS_ENDPOINT = 'https://561pjq4x4ud1px-5005.proxy.runpod.net/tts';

export interface TTSRequest {
  prompt: string;
  voice?: string;
  temperature?: number;
  top_p?: number;
  max_tokens?: number;
  repetition_penalty?: number;
}

export class TTSService {
  /**
   * Generate TTS audio from text using your Orpheus endpoint
   */
  static async generateSpeech(request: TTSRequest): Promise<ArrayBuffer> {
    const params = new URLSearchParams({
      prompt: request.prompt,
      voice: request.voice || 'tara',
      temperature: (request.temperature || 0.4).toString(),
      top_p: (request.top_p || 0.9).toString(),
      max_tokens: (request.max_tokens || 2000).toString(),
      repetition_penalty: (request.repetition_penalty || 1.1).toString()
    });

    const url = `${TTS_ENDPOINT}?${params.toString()}`;
    
    try {
      console.log(`🗣️ Requesting TTS from: ${url}`);
      
      const response = await fetch(url, {
        method: 'GET',
        headers: {
          'Accept': 'audio/wav'
        }
      });

      if (!response.ok) {
        throw new Error(`TTS request failed: ${response.status} ${response.statusText}`);
      }

      const audioBuffer = await response.arrayBuffer();
      console.log(`✅ TTS generated: ${audioBuffer.byteLength} bytes`);
      
      return audioBuffer;
    } catch (error) {
      console.error('❌ TTS generation failed:', error);
      throw error;
    }
  }

  /**
   * Generate TTS and play it immediately
   */
  static async generateAndPlay(request: TTSRequest): Promise<void> {
    try {
      const audioBuffer = await this.generateSpeech(request);
      
      // Convert ArrayBuffer to base64 for the existing audio service
      const base64Audio = this.arrayBufferToBase64(audioBuffer);
      
      // Use the existing audio service to play it
      const { default: audioService } = await import('./audio');
      await audioService.playAudioChunk(base64Audio, 'wav');
      
    } catch (error) {
      console.error('❌ TTS generation and playback failed:', error);
      throw error;
    }
  }

  /**
   * Convert ArrayBuffer to Base64 string
   */
  private static arrayBufferToBase64(buffer: ArrayBuffer): string {
    const binary = [];
    const bytes = new Uint8Array(buffer);
    const len = bytes.byteLength;
    
    for (let i = 0; i < len; i++) {
      binary.push(String.fromCharCode(bytes[i]));
    }
    
    return btoa(binary.join(''));
  }
}

export default TTSService;
