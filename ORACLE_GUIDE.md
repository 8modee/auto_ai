# Oracle Cloud Free Tier - Deployment Guide

## Step-by-Step Setup (For Mike)

### What You Need:
- An Oracle Cloud account (free)
- 5 minutes of your time
- That's it

---

## Step 1: Create Oracle Cloud Account

1. Go to: https://signup.cloud.oracle.com/
2. Fill in your details
3. Verify your email
4. Sign in to Oracle Cloud Console

---

## Step 2: Create a Free Tier Instance

1. Click **"Create a VM instance"** (big button on dashboard)
2. **Name:** institution-server
3. **Placement:** Keep defaults
4. **Image:** Ubuntu 22.04 (should be selected by default)
5. **Shape:** Select **"Virtual Machine"**
6. **Series:** Ampere
7. **Shape:** VM.Standard.A1.Flex
8. **OCPUs:** 4 (free tier allows this)
9. **Memory:** 24 GB (free tier allows this)
10. **Storage:** 200 GB (free tier allows this)
11. **Network:** Keep defaults
12. **SSH Keys:** 
    - Option A: Paste your existing SSH key
    - Option B: Let Oracle generate one (download it!)
13. Click **"Create"**

**Wait 2-3 minutes** for the instance to provision.

---

## Step 3: Connect to Your Server

### On Windows (using PuTTY):
1. Open PuTTY
2. Enter your server's public IP (from Oracle dashboard)
3. Port: 22
4. Connection type: SSH
5. Click "Open"
6. Login as: **ubuntu**
7. When prompted for SSH key, paste your private key

### On Mac/Linux (Terminal):
1. Open Terminal
2. Run: ssh ubuntu@YOUR_SERVER_IP
3. When prompted, paste your private key or enter password

---

## Step 4: Run the Quick Start

Once connected to your server, run this ONE command:

curl -sL https://raw.githubusercontent.com/8modee/auto_ai/main/quickstart.sh | bash

This will:
- Install all needed software
- Download the Institution code
- Set up the 3 Phase 1 streams
- Start everything automatically

**This takes 5-10 minutes.** You can walk away.

---

## Step 5: Access Your Dashboard

After setup completes, you'll see a message like:

  Dashboard URL: http://123.45.67.89:8080

1. Copy that URL
2. Paste it into your browser
3. Bookmark it

**You're done!** The system is now running 24/7.

---

## Daily Use

### Every Morning:
1. Open your dashboard bookmark
2. Click "Daily Check-in"
3. Select your energy (1-5), pain (1-5), fear (1-5)
4. Read your ONE action for the day

### If You Want to Stop:
1. Connect to your server via SSH
2. Run: sudo systemctl stop institution institution-dashboard

### If You Want to Start Again:
1. Connect to your server via SSH
2. Run: sudo systemctl start institution institution-dashboard

---

## Troubleshooting

### Dashboard won't load?
- Check if the service is running: sudo systemctl status institution-dashboard
- Check logs: tail -50 /opt/institution/logs/system/dashboard.log
- Restart: sudo systemctl restart institution-dashboard

### Nothing is happening?
- Check main service: sudo systemctl status institution
- Check logs: tail -50 /opt/institution/logs/system/institution.log
- Restart: sudo systemctl restart institution

### Forgot the dashboard URL?
- It's always: http://YOUR_SERVER_IP:8080
- Find your server IP in Oracle Cloud dashboard

---

## Adding API Keys (Optional)

The system works with ZERO API keys. But if you want better quality:

1. Connect to your server: ssh ubuntu@YOUR_SERVER_IP
2. Edit the config file: nano /opt/institution/.env
3. Add one API key at a time (recommended order):
   - GROQ_API_KEY=your_key_here
   - GEMINI_API_KEY=your_key_here
4. Save and exit (Ctrl+O, Enter, Ctrl+X)
5. Restart: sudo systemctl restart institution institution-dashboard

---

## Cost

**$0.00 per month** - Everything runs on Oracle Cloud Free Tier forever.

---

## Need Help?

Just ask me: "My Oracle Cloud setup is stuck, what do I do?"

I'll walk you through it step by step.
