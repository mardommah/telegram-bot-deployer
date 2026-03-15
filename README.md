# Telegram Bot Deployer

A web dashboard for deploying and managing Python Telegram bots via Docker containers. Upload bot code (`.py` or `.zip`), deploy to isolated containers, monitor logs in real-time, and control the bot lifecycle (start/stop/restart).

## Features

- **Upload & Deploy** — Upload a single `.py` file or a `.zip` archive for multi-file bots
- **Background Deploy** — Deployment runs in the background with real-time progress streaming (phase, progress bar, build logs)
- **Docker Isolation** — Each bot runs in its own container with isolated dependencies
- **Live Logs** — Stream container logs in real-time via SSE (Server-Sent Events)
- **Lifecycle Control** — Start, stop, restart, redeploy, and delete bots from the dashboard
- **Auto-detect Entrypoint** — For zip uploads, automatically detects `main.py`, `bot.py`, `app.py`, or `run.py`
- **Token Encryption** — Bot tokens are stored encrypted (Fernet) in the database
- **Status Polling** — Dashboard auto-refreshes bot statuses every 5 seconds via HTMX

## Tech Stack

| Layer | Tech |
|-------|------|
| Backend | FastAPI + Python |
| Frontend | Jinja2 + HTMX + TailwindCSS (CDN) |
| Database | SQLite (SQLAlchemy async) |
| Container | Docker SDK for Python |
| Auth | Session cookie (itsdangerous) |
| Real-time | SSE (sse-starlette) |

## Quick Start (Local)

```bash
# Clone repo
git clone <repo-url> && cd telegram-bot-deployer

# Setup virtualenv
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Setup environment
cp .env.example .env
# Edit .env — change ADMIN_PASSWORD, SECRET_KEY, and generate FERNET_KEY:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Run
uvicorn app.main:app --reload
```

Open `http://localhost:8000` and log in with the credentials from `.env`.

## VPS Deployment

### Prerequisites

- VPS running Ubuntu 22.04+ (or Debian/CentOS)
- Minimum 1GB RAM, 20GB disk
- Domain (optional, for HTTPS)

### Option 1: Systemd (Direct on VPS)

#### 1. Install Docker & Python

```bash
# Install Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

# Install Python 3.11+
sudo apt update
sudo apt install -y python3 python3-pip python3-venv git
```

#### 2. Clone & Setup

```bash
cd /opt
sudo git clone <repo-url> telegram-bot-deployer
sudo chown -R $USER:$USER telegram-bot-deployer
cd telegram-bot-deployer

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

#### 3. Configure Environment

```bash
cp .env.example .env
nano .env
```

```env
ADMIN_USERNAME=admin
ADMIN_PASSWORD=use-a-strong-password
SECRET_KEY=use-a-long-random-string
FERNET_KEY=<output from: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
DATABASE_URL=sqlite+aiosqlite:///./deployer.db
UPLOAD_DIR=./uploads
```

#### 4. Create Systemd Service

```bash
sudo tee /etc/systemd/system/tgbot-deployer.service << 'EOF'
[Unit]
Description=Telegram Bot Deployer
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/telegram-bot-deployer
Environment=PATH=/opt/telegram-bot-deployer/.venv/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/opt/telegram-bot-deployer/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable tgbot-deployer
sudo systemctl start tgbot-deployer

# Check status
sudo systemctl status tgbot-deployer
sudo journalctl -u tgbot-deployer -f
```

#### 5. Reverse Proxy with Nginx (HTTPS)

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
```

```bash
sudo tee /etc/nginx/sites-available/tgbot-deployer << 'EOF'
server {
    listen 80;
    server_name deploy.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE support
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 86400s;
    }
}
EOF
```

```bash
sudo ln -s /etc/nginx/sites-available/tgbot-deployer /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx

# SSL (replace with your domain)
sudo certbot --nginx -d deploy.example.com
```

---

### Option 2: Docker Compose (Recommended)

The simplest approach — the deployer itself runs in Docker and manages bot containers via the Docker socket.

#### 1. Install Docker

```bash
curl -fsSL https://get.docker.com | sh
```

#### 2. Clone & Setup

```bash
cd /opt
git clone <repo-url> telegram-bot-deployer
cd telegram-bot-deployer
cp .env.example .env
nano .env  # fill in credentials
```

#### 3. Create `docker-compose.yml`

```bash
cat > docker-compose.yml << 'EOF'
services:
  deployer:
    build: .
    container_name: tgbot-deployer
    restart: unless-stopped
    ports:
      - "8000:8000"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ./uploads:/app/uploads
      - ./deployer.db:/app/deployer.db
    env_file:
      - .env
EOF
```

#### 4. Build & Run

```bash
docker compose up -d --build

# Check logs
docker compose logs -f
```

#### 5. (Optional) Add Nginx + SSL

Same as Option 1 step 5, or use Caddy for a simpler setup:

```bash
sudo apt install -y caddy
```

```bash
sudo tee /etc/caddy/Caddyfile << 'EOF'
deploy.example.com {
    reverse_proxy localhost:8000
}
EOF

sudo systemctl reload caddy
```

Caddy automatically handles SSL via Let's Encrypt.

---

## Usage

1. **Login** — Open the dashboard and log in with your admin credentials
2. **Create Bot** — Click "+ New Bot", fill in the name, Telegram token, and upload your file:
   - **Single file**: Upload a `.py` file directly
   - **Multi-file**: Upload a `.zip` archive containing all bot files (entrypoint is auto-detected, or specify it manually)
   - **Dependencies**: Upload a `requirements.txt` separately, or include it inside the `.zip`
3. **Deploy** — Click "Deploy" on the bot detail page. The build runs in the background with a live progress bar and streaming build logs
4. **Monitor** — View status and logs. Click "Live Stream" for real-time container logs
5. **Control** — Stop, start, restart, or delete bots as needed

## Zip Upload Structure

For multi-file bots, create a zip with the following structure:

```
my-bot.zip
├── main.py          # entrypoint (auto-detected)
├── requirements.txt # dependencies (auto-detected)
├── handlers/
│   ├── __init__.py
│   └── commands.py
└── utils/
    └── helpers.py
```

Entrypoint auto-detection order: `main.py` → `bot.py` → `app.py` → `run.py`. If your entrypoint has a different name, specify it in the "Entrypoint" field when creating the bot (e.g., `src/run_bot.py`).

## Updating

```bash
# Systemd
cd /opt/telegram-bot-deployer
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart tgbot-deployer

# Docker Compose
cd /opt/telegram-bot-deployer
git pull
docker compose up -d --build
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Deploy error: permission denied | Make sure the user has Docker socket access (`sudo usermod -aG docker $USER`) |
| Bot status "error" | Check the logs on the detail page — usually caused by a code error or missing dependencies |
| Live stream not working | Ensure the Nginx config includes `proxy_buffering off` for SSE |
| Database locked | Stop the app and make sure no other process is accessing `deployer.db` |
