# Initial Setup

The **Initial Setup** guide builds the foundational services that make a self-hosted homelab manageable: virtualization, Docker management, private HTTPS access, secure remote access with VPN, monitoring, notifications, recoverable backups and more.

Follow the chapters in order, as each one covers a dedicated topic in detail. Optional chapters and sections will be marked as optional.

## Chapters

- [0.0 Prerequisites](0.0%20Prerequisites.md) — Hardware, knowledge and network requirements before you start
- [0.1 Architecture](0.1%20Architecture.md) — Overview of the homelab architecture
- [1. Setup Proxmox VE](1.%20Setup%20Proxmox%20VE.md) — Install and configure the Proxmox VE hypervisor
- [2. Setup Komodo](2.%20Setup%20Komodo.md) — Deploy centralized management for Docker
- [3. Setup DNS Server](3.%20Setup%20DNS%20Server.md) — Self-hosted DNS server with ad blocking
- [4.0 Setup Custom Domain](4.0%20Setup%20custom%20domain.md) — Prepare and configure your custom domain for private HTTPS certificates
- [4.1 Setup Reverse Proxy](4.1%20Setup%20Reverse%20Proxy.md) — Build private HTTPS routing and layered request protection with Traefik, CrowdSec, and Authelia
- [4.2 Setup Code-Server for Config Files](4.2%20Setup%20Code-Server%20for%20config%20files.md) — Edit reverse proxy configuration files in the browser
- [5. Setup VPN](5.%20Setup%20VPN.md) — Secure remote access with Tailscale
- [6.0 Setup Notification Server](6.0%20Setup%20Notification%20server.md) — Push notifications with Gotify
- [6.1 Setup Monitoring Tools](6.1%20Setup%20Monitoring%20tools.md) — Collect and monitor homelab and services data: resources, health, network and uptime
- [7. Setup Proxmox Backup Server](7.%20Setup%20Proxmox%20Backup%20Server.md) — Scheduled backups on a dedicated hardware (recommended) or an LXC
- [8. Setup UPS Software](8.%20Setup%20UPS%20Software.md) — **(Optional)** Monitor power events and gracefully shut down during power outages
- [9. Checklist & What's Next](9.%20Checklist%20%26%20What's%20next.md) — Final checklist and next steps
