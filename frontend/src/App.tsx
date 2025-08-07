import React, { useState, useRef, useEffect } from 'react';
import AudioRecorder from './components/AudioRecorder';
import ChatInterface from './components/ChatInterface';
import './App.css';

interface Message {
  id: string;
  type: 'user' | 'assistant';
  content: string;
  timestamp: Date;
}

function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    // Initialize WebSocket connection
    const connectWebSocket = () => {
      const wsUrl = `ws://${window.location.hostname}:8000/ws`;
      wsRef.current = new WebSocket(wsUrl);

      wsRef.current.onopen = () => {
        console.log('WebSocket connected');
        setIsConnected(true);
      };

      wsRef.current.onmessage = (event) => {
        const message = JSON.parse(event.data);
        handleWebSocketMessage(message);
      };

      wsRef.current.onclose = () => {
        console.log('WebSocket disconnected');
        setIsConnected(false);
        // Attempt to reconnect after 3 seconds
        setTimeout(connectWebSocket, 3000);
      };

      wsRef.current.onerror = (error) => {
        console.error('WebSocket error:', error);
      };
    };

    connectWebSocket();

    return () => {
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, []);

  const handleWebSocketMessage = (message: any) => {
    switch (message.type) {
      case 'transcript':
        addMessage('user', message.data);
        break;
      case 'response':
        addMessage('assistant', message.data);
        break;
      case 'audio_response':
        // Handle audio playback
        playAudioResponse(message.data);
        break;
    }
  };

  const addMessage = (type: 'user' | 'assistant', content: string) => {
    const newMessage: Message = {
      id: Date.now().toString(),
      type,
      content,
      timestamp: new Date(),
    };
    setMessages(prev => [...prev, newMessage]);
  };

  const playAudioResponse = (audioData: string) => {
    // Convert base64 audio to blob and play
    const audioBlob = new Blob([Uint8Array.from(atob(audioData), c => c.charCodeAt(0))], {
      type: 'audio/wav'
    });
    const audioUrl = URL.createObjectURL(audioBlob);
    const audio = new Audio(audioUrl);
    audio.play();
  };

  const sendTextMessage = (text: string) => {
    if (wsRef.current && isConnected) {
      wsRef.current.send(JSON.stringify({
        type: 'text',
        data: text
      }));
      addMessage('user', text);
    }
  };

  const sendAudioData = (audioData: string) => {
    if (wsRef.current && isConnected) {
      wsRef.current.send(JSON.stringify({
        type: 'audio',
        data: audioData
      }));
    }
  };

  return (
    <div className="App">
      <header className="app-header">
        <h1>Vocalis Conversational AI</h1>
        <div className={`connection-status ${isConnected ? 'connected' : 'disconnected'}`}>
          {isConnected ? '🟢 Connected' : '🔴 Disconnected'}
        </div>
      </header>

      <main className="app-main">
        <ChatInterface 
          messages={messages} 
          onSendMessage={sendTextMessage}
        />
        
        <AudioRecorder 
          onAudioData={sendAudioData}
          isRecording={isRecording}
          setIsRecording={setIsRecording}
          isConnected={isConnected}
        />
      </main>
    </div>
  );
}

export default App;
