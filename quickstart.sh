#!/usr/bin/env bash
# THE INSTITUTION - QUICK START
# Run: curl -sL https://raw.githubusercontent.com/8modee/auto_ai/main/quickstart.sh | bash

set -e

echo "=========================================="
echo "  THE INSTITUTION - Phase 1 Setup"
echo "=========================================="
echo ""

# Install basics
if command -v apt-get &>/dev/null; then
    sudo apt-get update -qq >/dev/null 2>&1
    sudo apt-get install -y -qq python3 python3-pip python3-venv sqlite3 git curl >/dev/null 2>&1
fi

# Setup directory
sudo mkdir -p /opt/institution
sudo chown -R $USER:$USER /opt/institution
cd /opt/institution

# Download code
git clone https://github.com/8modee/auto_ai.git . 2>/dev/null ||     curl -sL https://github.com/8modee/auto_ai/archive/refs/heads/main.tar.gz | tar -xz --strip-components=1

# Use minimal config
cp config_minimal.yaml config.yaml
cp .env.simple .env

# Create directories
mkdir -p data/db data/cache logs/system logs/agents reports/daily
mkdir -p sites/templates sites/output products/templates products/output

# Install Python packages
python3 -m venv venv >/dev/null 2>&1
venv/bin/pip install -q flask requests pyyaml python-dotenv sqlite3 reportlab pillow beautifulsoup4 jinja2 markdown psutil 2>/dev/null

# Setup database
sqlite3 data/db/institution.db < /opt/institution/setup_db.sql 2>/dev/null || true

# Create startup script
cat > /opt/institution/start.sh << 'STARTSCRIPT'
#!/bin/bash
cd /opt/institution
nohup venv/bin/python meta_agent.py > logs/system/meta_agent.log 2>&1 &
nohup venv/bin/python dashboard.py > logs/system/dashboard.log 2>&1 &
echo "Institution started. Dashboard: http://$(hostname -I | awk '{print $1}'):8080"
STARTSCRIPT

chmod +x /opt/institution/start.sh

# Create simple database setup
cat > /opt/institution/setup_db.sql << 'SQLSCRIPT'
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, description TEXT NOT NULL, agent TEXT NOT NULL DEFAULT 'steward', stream TEXT, status TEXT DEFAULT 'queued' CHECK(status IN ('queued','in_progress','completed','failed','cancelled')), priority INTEGER DEFAULT 3, autonomy_level INTEGER DEFAULT 1, requires_approval INTEGER DEFAULT 0, approved INTEGER DEFAULT 0, result TEXT, error TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, started_at DATETIME, completed_at DATETIME, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP);

CREATE TABLE IF NOT EXISTS streams (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, slug TEXT UNIQUE NOT NULL, status TEXT DEFAULT 'active' CHECK(status IN ('active','paused','killed','pending')), autonomy_level INTEGER DEFAULT 1);

CREATE TABLE IF NOT EXISTS agents (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, agent_type TEXT NOT NULL, stream TEXT, status TEXT DEFAULT 'idle');

CREATE TABLE IF NOT EXISTS founder_checkins (id INTEGER PRIMARY KEY AUTOINCREMENT, energy INTEGER CHECK(energy BETWEEN 1 AND 5), pain INTEGER CHECK(pain BETWEEN 1 AND 5), fear INTEGER CHECK(fear BETWEEN 1 AND 5), created_at DATETIME DEFAULT CURRENT_TIMESTAMP);

INSERT OR IGNORE INTO streams (name, slug, status, autonomy_level) VALUES ('Content Sites', 'content_sites', 'active', 3), ('Digital Products', 'digital_products', 'active', 2), ('Newsletter', 'newsletter', 'active', 2);
SQLSCRIPT

sqlite3 data/db/institution.db < /opt/institution/setup_db.sql

# Start everything
echo ""
echo "Starting services..."
cd /opt/institution
nohup venv/bin/python meta_agent.py > logs/system/meta_agent.log 2>&1 &
nohup venv/bin/python dashboard.py > logs/system/dashboard.log 2>&1 &

echo ""
echo "=========================================="
echo "  SETUP COMPLETE!"
echo "=========================================="
echo ""
echo "  Dashboard URL: http://$(hostname -I 2>/dev/null | awk '{print $1}'):8080"
echo "  or: http://localhost:8080"
echo ""
echo "  Your 3 streams are starting:"
echo "    - Content Sites"
echo "    - Digital Products"  
echo "    - Newsletter"
echo ""
echo "  To stop:  killall python3"
echo "  To restart: /opt/institution/start.sh"
echo ""
echo "=========================================="
