/**
 * WebSocket Service
 *
 * Handles WebSocket connection and communication with the backend
 */

// Message types (corresponds to backend message types)
export enum MessageType {
  AUDIO = "audio",
  TRANSCRIPTION = "transcription",
  LLM_RESPONSE = "llm_response",
  TTS_CHUNK = "tts_chunk",
  TTS_START = "tts_start",
  TTS_END = "tts_end",
  STATUS = "status",
  ERROR = "error",
  PING = "ping",
  PONG = "pong",
  INTERRUPT = "interrupt",
  CLEAR_HISTORY = "clear_history",
  SYSTEM_PROMPT = "system_prompt",
  SYSTEM_PROMPT_UPDATED = "system_prompt_updated",
  USER_PROFILE = "user_profile",
  USER_PROFILE_UPDATED = "user_profile_updated",
  GREETING = "greeting",
  SILENT_FOLLOWUP = "silent_followup",
  
  // Session storage message types
  SAVE_SESSION = "save_session",
  SAVE_SESSION_RESULT = "save_session_result",
  LOAD_SESSION = "load_session",
  LOAD_SESSION_RESULT = "load_session_result",
  LIST_SESSIONS = "list_sessions",
  LIST_SESSIONS_RESULT = "list_sessions_result",
  DELETE_SESSION = "delete_session",
  DELETE_SESSION_RESULT = "delete_session_result",
  
  // Vision feature message types
  VISION_SETTINGS = "vision_settings",
  VISION_SETTINGS_UPDATED = "vision_settings_updated",
  
  // Vision processing message types
  VISION_FILE_UPLOAD = "vision_file_upload",
  VISION_FILE_UPLOAD_RESULT = "vision_file_upload_result", 
  VISION_PROCESSING = "vision_processing",
  VISION_READY = "vision_ready"
}

// Session interface
export interface Session {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  metadata?: {
    message_count?: number;
    user_message_count?: number;
    assistant_message_count?: number;
    user_name?: string;
    [key: string]: any;
  };
}

// Event types
type WebSocketEventType = 
  | 'open'
  | 'close'
  | 'error'
  | 'audio'
  | 'transcription'
  | 'llm_response'
  | 'tts_start'
  | 'tts_chunk'
  | 'tts_end'
  | 'status'
  | 'ping'
  | 'pong'
  | 'error'
  | 'system_prompt'
  | 'system_prompt_updated'
  | 'user_profile'
  | 'user_profile_updated'
  | 'save_session_result'
  | 'load_session_result'
  | 'list_sessions_result'
  | 'delete_session_result'
  | 'vision_settings'
  | 'vision_settings_updated'
  | 'vision_file_upload_result'
  | 'vision_processing'
  | 'vision_ready';

// WebSocket state
export enum ConnectionState {
  CONNECTING = 'connecting',
  CONNECTED = 'connected',
  DISCONNECTED = 'disconnected',
  ERROR = 'error'
}

// Event listener type
interface EventListener {
  type: WebSocketEventType;
  callback: (data: any) => void;
}

// Main WebSocket service class
export class WebSocketService {
  private socket: WebSocket | null = null;
  private url: string;
  private autoReconnect: boolean;
  private reconnectInterval: number; // in milliseconds
  private reconnectAttempts: number;
  private maxReconnectAttempts: number;
  private listeners: EventListener[] = [];
  private pingInterval: number | null = null;
  private connectionState: ConnectionState = ConnectionState.DISCONNECTED;
  
  // Track states that should prevent interrupt signals
  private isInGreetingFlow: boolean = false;

  constructor(
    url: string = 'ws://localhost:8000/ws', 
    autoReconnect: boolean = true,
    reconnectInterval: number = 3000,
    maxReconnectAttempts: number = 5
  ) {
    this.url = url;
    this.autoReconnect = autoReconnect;
    this.reconnectInterval = reconnectInterval;
    this.reconnectAttempts = 0;
    this.maxReconnectAttempts = maxReconnectAttempts;
  }

  /**
   * Connect to the WebSocket server
   */
  public connect(): void {
    if (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)) {
      console.log('WebSocket already connected or connecting');
      return;
    }

    this.setConnectionState(ConnectionState.CONNECTING);
    
    try {
      this.socket = new WebSocket(this.url);
      
      this.socket.onopen = this.onOpen.bind(this);
      this.socket.onclose = this.onClose.bind(this);
      this.socket.onerror = this.onError.bind(this);
      this.socket.onmessage = this.onMessage.bind(this);
    } catch (error) {
      console.error('WebSocket connection error:', error);
      this.setConnectionState(ConnectionState.ERROR);
      this.handleReconnect();
    }
  }

  /**
   * Disconnect from the WebSocket server
   */
  public disconnect(): void {
    if (this.socket) {
      this.autoReconnect = false; // Prevent auto reconnect
      this.socket.close();
      this.socket = null;
    }
    
    if (this.pingInterval) {
      clearInterval(this.pingInterval);
      this.pingInterval = null;
    }
    
    this.setConnectionState(ConnectionState.DISCONNECTED);
  }

  /**
   * Send a message to the WebSocket server
   */
  public send(type: MessageType, data: any = {}): boolean {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      console.error('WebSocket not connected');
      return false;
    }

    try {
      const message = {
        type,
        ...data,
        timestamp: new Date().toISOString()
      };
      
      this.socket.send(JSON.stringify(message));
      return true;
    } catch (error) {
      console.error('Error sending message:', error);
      return false;
    }
  }

  /**
   * Send audio data to the WebSocket server
   */
  public sendAudio(audioData: Float32Array | ArrayBuffer): boolean {
    // Convert to base64 if Float32Array
    let base64Data: string;
    
    if (audioData instanceof Float32Array) {
      // Create a buffer from the Float32Array
      const buffer = new ArrayBuffer(audioData.length * 4); // 4 bytes per float
      const view = new Float32Array(buffer);
      view.set(audioData);
      
      base64Data = this.arrayBufferToBase64(buffer);
    } else {
      base64Data = this.arrayBufferToBase64(audioData);
    }
    
    return this.send(MessageType.AUDIO, {
      audio_data: base64Data
    });
  }

  /**
   * Send an interrupt signal to stop ongoing TTS
   * Will not send if we're in the initial greeting flow
   */
  public interrupt(): boolean {
    // Don't send interrupt during greeting flow
    if (this.isInGreetingFlow) {
      console.log('Interrupt prevented: still in greeting flow');
      return false;
    }
    
    console.log('Sending interrupt signal to server');
    return this.send(MessageType.INTERRUPT);
  }
  
  /**
   * Set whether we're in the initial greeting flow
   * This prevents interrupts during initial greeting and playback
   */
  public setGreetingFlowState(isInGreetingFlow: boolean): void {
    this.isInGreetingFlow = isInGreetingFlow;
    console.log(`Greeting flow state set to: ${isInGreetingFlow}`);
  }

  /**
   * Send a request to clear conversation history
   */
  public clearHistory(): boolean {
    return this.send(MessageType.CLEAR_HISTORY);
  }
  
  /**
   * Request the current system prompt
   */
  public getSystemPrompt(): boolean {
    // Send explicit string type expected by backend
    return this.send("get_system_prompt" as any);
  }
  
  /**
   * Update the system prompt
   */
  public updateSystemPrompt(prompt: string): boolean {
    // Send explicit string type expected by backend
    return this.send("update_system_prompt" as any, {
      prompt
    });
  }
  
  /**
   * Request the current user profile
   */
  public getUserProfile(): boolean {
    // Send explicit string type expected by backend
    return this.send("get_user_profile" as any);
  }
  
  /**
   * Update the user profile
   */
  public updateUserProfile(name: string): boolean {
    // Send explicit string type expected by backend
    return this.send("update_user_profile" as any, {
      name
    });
  }
  
  /**
   * Request the current vision settings
   */
  public getVisionSettings(): boolean {
    // Send explicit string type expected by backend
    return this.send("get_vision_settings" as any);
  }
  
  /**
   * Update the vision settings
   * 
   * @param enabled Whether vision is enabled
   */
  public updateVisionSettings(enabled: boolean): boolean {
    // Send explicit string type expected by backend
    return this.send("update_vision_settings" as any, {
      enabled
    });
  }
  
  /**
   * Send an image for vision processing
   * 
   * @param imageData Base64-encoded image data
   */
  public sendVisionImage(imageData: string): boolean {
    return this.send(MessageType.VISION_FILE_UPLOAD, {
      image_data: imageData
    });
  }
  
  /**
   * Send a greeting request (for conversation starters)
   */
  public sendGreeting(): boolean {
    // When sending a greeting, set the greeting flow state
    this.setGreetingFlowState(true);
    return this.send(MessageType.GREETING);
  }
  
  /**
   * Send a silent follow-up request when user is inactive
   * 
   * @param tier The current follow-up tier (0-2)
   */
  public sendSilentFollowUp(tier: number): boolean {
    return this.send(MessageType.SILENT_FOLLOWUP, { tier });
  }

  /**
   * Save the current conversation session
   * 
   * @param title Optional title for the session
   * @param sessionId Optional ID for overwriting an existing session
   * @returns boolean indicating if the request was sent
   */
  public saveSession(title?: string, sessionId?: string): boolean {
    return this.send(MessageType.SAVE_SESSION, {
      title,
      session_id: sessionId
    });
  }

  /**
   * Load a conversation session
   * 
   * @param sessionId ID of the session to load
   * @returns boolean indicating if the request was sent
   */
  public loadSession(sessionId: string): boolean {
    if (!sessionId) {
      console.error('Session ID is required to load a session');
      return false;
    }
    
    return this.send(MessageType.LOAD_SESSION, {
      session_id: sessionId
    });
  }

  /**
   * List all saved conversation sessions
   * 
   * @returns boolean indicating if the request was sent
   */
  public listSessions(): boolean {
    return this.send(MessageType.LIST_SESSIONS);
  }

  /**
   * Delete a conversation session
   * 
   * @param sessionId ID of the session to delete
   * @returns boolean indicating if the request was sent
   */
  public deleteSession(sessionId: string): boolean {
    if (!sessionId) {
      console.error('Session ID is required to delete a session');
      return false;
    }
    
    return this.send(MessageType.DELETE_SESSION, {
      session_id: sessionId
    });
  }

  /**
   * Add event listener
   */
  public addEventListener(type: WebSocketEventType, callback: (data: any) => void): void {
    this.listeners.push({ type, callback });
  }

  /**
   * Remove event listener
   */
  public removeEventListener(type: WebSocketEventType, callback: (data: any) => void): void {
    this.listeners = this.listeners.filter(
      listener => !(listener.type === type && listener.callback === callback)
    );
  }

  /**
   * Get current connection state
   */
  public getConnectionState(): ConnectionState {
    return this.connectionState;
  }

  /**
   * Handle WebSocket open event
   */
  private onOpen(event: Event): void {
    console.log('WebSocket connected');
    this.setConnectionState(ConnectionState.CONNECTED);
    this.reconnectAttempts = 0;
    
    // Set up ping interval to keep connection alive
    this.pingInterval = setInterval(() => {
      if (this.socket && this.socket.readyState === WebSocket.OPEN) {
        this.send(MessageType.PING);
      }
    }, 30000); // Send ping every 30 seconds
    
    // Notify listeners
    this.notifyListeners('open', { event });
  }

  /**
   * Handle WebSocket close event
   */
  private onClose(event: CloseEvent): void {
    console.log('WebSocket disconnected');
    this.setConnectionState(ConnectionState.DISCONNECTED);
    
    if (this.pingInterval) {
      clearInterval(this.pingInterval);
      this.pingInterval = null;
    }
    
    // Notify listeners
    this.notifyListeners('close', { event });
    
    // Attempt to reconnect if enabled
    this.handleReconnect();
  }

  /**
   * Handle WebSocket error event
   */
  private onError(event: Event): void {
    console.error('WebSocket error:', event);
    this.setConnectionState(ConnectionState.ERROR);
    
    // Notify listeners
    this.notifyListeners('error', { event });
  }

  /**
   * Handle WebSocket message event
   */
  private onMessage(event: MessageEvent): void {
    try {
      const message = JSON.parse(event.data);
      const type = message.type as WebSocketEventType;
      
      // Acknowledge pings but DON'T send pong responses
      // Backend doesn't handle pong messages well
      if (message.type === 'ping') {
        // Just update the last message time - no need to send pong
        console.debug('Received ping from server');
        return;
      }
      
      // Silently ignore pong messages as they're just connection keepalive responses
      if (message.type === 'pong') {
        console.debug('Received pong response');
        return;
      }
      
      // Notify listeners
      this.notifyListeners(type, message);
    } catch (error) {
      console.error('Error parsing message:', error);
    }
  }

  /**
   * Notify all listeners of an event
   */
  private notifyListeners(type: WebSocketEventType, data: any): void {
    this.listeners
      .filter(listener => listener.type === type)
      .forEach(listener => {
        try {
          listener.callback(data);
        } catch (error) {
          console.error(`Error in ${type} listener:`, error);
        }
      });
  }

  /**
   * Handle reconnection attempts
   */
  private handleReconnect(): void {
    if (!this.autoReconnect || this.reconnectAttempts >= this.maxReconnectAttempts) {
      return;
    }
    
    this.reconnectAttempts++;
    
    console.log(`Attempting to reconnect (${this.reconnectAttempts}/${this.maxReconnectAttempts})...`);
    
    setTimeout(() => {
      this.connect();
    }, this.reconnectInterval);
  }

  /**
   * Set the connection state
   */
  private setConnectionState(state: ConnectionState): void {
    this.connectionState = state;
  }

  /**
   * Convert ArrayBuffer to Base64 string
   */
  private arrayBufferToBase64(buffer: ArrayBuffer): string {
    const binary = [];
    const bytes = new Uint8Array(buffer);
    const len = bytes.byteLength;
    
    for (let i = 0; i < len; i++) {
      binary.push(String.fromCharCode(bytes[i]));
    }
    
    return btoa(binary.join(''));
  }

  /**
   * Convert Base64 string to ArrayBuffer
   */
  public static base64ToArrayBuffer(base64: string): ArrayBuffer {
    const binaryString = atob(base64);
    const len = binaryString.length;
    const bytes = new Uint8Array(len);
    
    for (let i = 0; i < len; i++) {
      bytes[i] = binaryString.charCodeAt(i);
    }
    
    return bytes.buffer;
  }
}

// Create a singleton instance
const websocketService = new WebSocketService();
export default websocketService;
