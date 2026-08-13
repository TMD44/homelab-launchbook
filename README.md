# **Homelab Launchbook**: The Ultimate Homelab Guide

Homelab Launchbook is a practical, step-by-step guide that takes you from a bare server to a dependable self-hosting environment. We begin with the foundations: Proxmox VE, Docker in isolated LXC containers with centralized Docker management, private HTTPS access, monitoring, notifications and backups. Once the foundations are in place, that's where the real fun begins, deploying various applications: media, image and game servers, home automation, personal clouds, developer and AI tools are just the tip of the iceberg. **Consider yourself warned: this is a rabbit hole you won't ever want to climb out of.**

The guide favors **free**, **open-source**, **lightweight**, and **actively maintained** tools and applications. The goal is not to build the largest possible stack, but to create a clear, low-maintenance setup that you understand and can extend with confidence.

## Getting Started

Start with the **[Initial Setup](Initial%20Setup/README.md)** guide to build your core infrastructure. It provides detailed, step-by-step instructions for setting up Proxmox VE, containerized Docker environments, DNS and HTTPS, secure remote access, notifications, monitoring and backups.

Once the core environment is stable and you feel comfortable with it, explore the **[Application Library](TODO.md)** to start deploying the services and applications you need.

Use the **[Quick Action Guides](TODO.md)** for everyday tasks, routine day-to-day management and maintenance, as well as quick fixes for common issues. The **[Other Guides](TODO.md)** offers optional and specific topics like architecture decisions, storage planning, Docker networking, secrets, and troubleshooting. For more complex setups, the **[Advanced Guides](TODO.md)** extend the design into high-level areas such as VLANs, hardware passthrough, security hardening, disaster recovery, clustering, and high availability.

### Table of Contents

- [Initial Setup](Initial%20Setup/README.md)
- [Application Library](TODO.md)
- [Quick Action Guides](TODO.md)
- [Other Guides](TODO.md)
  - [Advanced Guides](TODO.md)

## Architecture

The Homelab Launchbook architecture differs from typical homelab setups. Instead of running all Docker services in a single VM or LXC, this architecture runs each critical Docker service in separate and isolated unprivileged LXC containers while keeping the simplicity of managing everything from a single management platform: Komodo. This ensures that if an issue occurs in one container or service, the rest of your services stay up and running without interruption.

The network architecture is private by design. No ports or services are exposed to the internet, all services are accessed through a reverse proxy, and remote access to the homelab is only possible through VPN.

Open the **[Architecture](Initial%20Setup/0.1%20Architecture.md)** chapter for the complete architecture design.

![Homelab Architecture Diagram](TODO.md)

## Contributing

If you spot an error, outdated information, or a missing step, [open an issue](https://github.com/TMD44/homelab-launchbook/issues) or [submit a pull request](https://github.com/TMD44/homelab-launchbook/pulls). You can also submit a pull request to add a new service to the Application Library.

Join our [Discord server](TODO.md) to ask questions, share your homelab, and discuss ideas with the community.
