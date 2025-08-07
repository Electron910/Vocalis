import React, { useState } from 'react';
import { Menu } from 'lucide-react';
import ChatInterface from './components/ChatInterface';
import Sidebar from './components/Sidebar';
import websocketService from './services/websocket';

function App() {
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);

  const handleReconnect = () => {
    websocketService.disconnect();
    setTimeout(() => {
      websocketService.connect();
    }, 1000);
  };

  const handleClearHistory = () => {
    websocketService.clearHistory();
  };

  return (
    <div className="relative h-screen overflow-hidden">
      {/* Menu Button */}
      <button
        onClick={() => setIsSidebarOpen(true)}
        className="fixed top-4 left-4 z-40 p-2 rounded-lg bg-slate-900/50 backdrop-blur-sm border border-slate-700/50 hover:bg-slate-800/60 transition-colors"
      >
        <Menu className="w-5 h-5 text-slate-300" />
      </button>

      {/* Main Chat Interface */}
      <ChatInterface />

      {/* Sidebar */}
      {isSidebarOpen && (
        <>
          {/* Backdrop */}
          <div
            className="fixed inset-0 bg-black/50 backdrop-blur-sm z-30"
            onClick={() => setIsSidebarOpen(false)}
          />
          
          {/* Sidebar */}
          <div className="fixed left-0 top-0 z-50 h-full">
            <Sidebar
              onClose={() => setIsSidebarOpen(false)}
              isConnected={websocketService.getConnectionState() === 'connected'}
              onReconnect={handleReconnect}
              onClearHistory={handleClearHistory}
            />
          </div>
        </>
      )}
    </div>
  );
}

export default App;
