Commands : 
Terminal 1 : 
cd workspace
git clone https://github.com/Electron910/Vocalis
cd Vocalis
cd backend
python3 -m venv env
source env/bin/activate
apt update && apt install -y git python3 python3-pip python3-venv nodejs npm curl wget ffmpeg
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
pip install fastapi==0.109.2 uvicorn==0.27.1 python-dotenv==1.0.1 websockets==12.0 numpy==1.26.4 transformers faster-whisper==1.1.1 requests==2.31.0 python-multipart==0.0.9 torch>=2.0.1 ffmpeg-python==0.2.0 aiohttp==3.9.1
CMAKE_ARGS="-DLLAMA_CUDA=on" pip install llama-cpp-python[server] --force-reinstall --no-cache-dir
mkdir -p /workspace/models
cd /workspace/models
wget https://huggingface.co/lex-au/Vocalis-Q4_K_M.gguf/resolve/main/Vocalis-q4_k_m.gguf
cd /workspace/Vocalis-WebRTC/backend
cat > .env << 'EOF'
LLM_API_ENDPOINT=http://0.0.0.0:1234/v1/chat/completions
TTS_API_ENDPOINT=http://0.0.0.0:5005/v1/audio/speech/stream
WHISPER_MODEL=base
TTS_MODEL=tts-1
TTS_VOICE=ऋतिका
TTS_FORMAT=wav
WEBSOCKET_HOST=0.0.0.0
WEBSOCKET_PORT=8000
VAD_THRESHOLD=0.1
VAD_BUFFER_SIZE=30
AUDIO_SAMPLE_RATE=44100
EOF
python -m llama_cpp.server     --model /workspace/models/Vocalis-q4_k_m.gguf     --host 0.0.0.0     --port 1234     --n_gpu_layers -1     --n_ctx 4096 &

# Test a simple completion to measure speed
curl http://0.0.0.0:1234/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Vocalis-q4_k_m.gguf",
    "messages": [{"role": "user", "content": "What is your name?"}],
    "max_tokens": 100
  }' \
  -w "\n%{time_total} seconds\n"

cd /workspace/Vocalis
python -m backend.main


for shutting down llama : pkill -f llama

Terminal 2: 
cd workspace
git clone https://github.com/Electron910/Orpheus-FastAPI/
cd Orpheus-FastAPI/
python3 -m venv tts_env
source tts_env/bin/activate
apt-get update
apt-get install -y portaudio19-dev libasound2-dev libsndfile1-dev libportaudio2 libportaudiocpp0
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
pip install fastapi uvicorn transformers numpy soundfile librosa python-dotenv sounddevice psutil snac einops python-multipart
CMAKE_ARGS="-DLLAMA_CUDA=on" pip install llama-cpp-python[server] --force-reinstall --no-cache-dir
mkdir -p /workspace/models
cd /workspace/models
wget https://huggingface.co/lex-au/Orpheus-3b-FT-Q8_0.gguf/resolve/main/Orpheus-3b-FT-Q8_0.gguf
cd /workspace/Orpheus-FastAPI
cat > .env << 'EOF'
ORPHEUS_API_URL=http://0.0.0.0:1235/v1/completions
ORPHEUS_API_TIMEOUT=120
ORPHEUS_MAX_TOKENS=8192
ORPHEUS_TEMPERATURE=0.6
ORPHEUS_TOP_P=0.9
ORPHEUS_SAMPLE_RATE=24000
ORPHEUS_MODEL_NAME=Orpheus-3b-FT-Q8_0.gguf
ORPHEUS_PORT=5005
ORPHEUS_HOST=0.0.0.0
EOF
python -m llama_cpp.server     --model /workspace/models/Orpheus-3b-FT-Q8_0.gguf     --host 0.0.0.0     --port 1235     --n_gpu_layers -1 &   
python app.py --host 0.0.0.0 --port 5005


Terminal 3: 

apt clean
apt autoclean

apt purge -y nodejs* npm* libnode* node-* 
apt autoremove -y

rm -rf /usr/lib/node_modules
rm -rf /usr/local/lib/node_modules
rm -rf ~/.npm

curl -fsSL https://deb.nodesource.com/setup_18.x | bash -
apt install -y nodejs

node --version
npm --version

cd /workspace/Vocalis/frontend

npm install 
npm run dev -- --host 0.0.0.0 --port 3000




