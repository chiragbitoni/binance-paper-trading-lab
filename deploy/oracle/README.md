# Oracle Always Free deployment

This deployment runs the repository's **Binance paper-only** service. It uses public Binance market-data WebSockets, has no API-key requirement, and cannot submit orders.

The separate Dhan MCX connector is live-capable but is not started by either service unit. Before using it on this VM, assign and preserve a public static IP, complete Dhan's required static-IP whitelisting for order endpoints, keep Dhan secrets only in the host environment, and run the margin/risk preview successfully. Do not add those values to this repository.

## VM choice

Create one Ubuntu VM in the Oracle home region using the **Always Free Eligible** `VM.Standard.A1.Flex` shape (1 OCPU, 6 GB RAM, 50 GB boot volume). Do not expose port 8765 to the internet.

## Install

After the VM exists, connect with SSH and run:

```bash
sudo apt update && sudo apt install -y git python3-venv python3-pip
sudo mkdir -p /opt
sudo chown "$USER":"$USER" /opt
git clone https://github.com/chiragbitoni/binance-paper-trading-lab.git /opt/binance-paper-trading-lab
cd /opt/binance-paper-trading-lab
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
sudo cp deploy/oracle/binance-paper-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now binance-paper-realtime binance-paper-dashboard
sudo systemctl status binance-paper-realtime --no-pager
```

The project database stays on the VM disk at `data/trading_bot.db`. The system services restart automatically after a reboot or temporary WebSocket failure.

## View the live dashboard securely

Leave the dashboard bound to `127.0.0.1` as configured. From your own computer, use a tunnel instead of making the paper dashboard public:

```powershell
ssh -L 8765:127.0.0.1:8765 ubuntu@YOUR_VM_PUBLIC_IP
```

Then open `http://127.0.0.1:8765`. The page refreshes every five seconds and displays the most recent WebSocket price when the worker is connected.

## Health checks

```bash
sudo systemctl status binance-paper-realtime --no-pager
sudo journalctl -u binance-paper-realtime -n 100 --no-pager
```

Do not add Binance keys. This is intentionally a paper-trading deployment.
