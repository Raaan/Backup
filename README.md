# Backup

A personal project, preserved here for backup and future rebuilding.

## Background

This project brings together tools and maintenance work developed through several years of personal use. Its installer and modular structure were consolidated recently.

The repository provides a versioned backup of the project and makes future maintenance and recovery easier.

## Installation

Use a fresh Ubuntu 22.04 or 24.04 amd64 environment. Log in as root and run each command separately:

```bash
apt-get update
apt-get install -y git python3 ca-certificates openssl iproute2
git clone https://github.com/Raaan/Backup.git /root/LA_Portal
cd /root/LA_Portal
bash install.sh
```

Allow TCP 80, 443 and 8443, plus UDP 443; keep your SSH port accessible. Follow the installer prompts. Email is optional and configured separately.

After verifying real client connections and traffic accounting, run `python3 portal.py activate --apply --clients-verified` from the installation directory.

## Current version

**v0.1.0** brings the existing setup into a modular project, with improvements to installation, user accounting, reporting, certificates, and optional email delivery.

The current build passed 34 automated tests on Ubuntu, along with protocol and traffic-accounting checks in a test environment. Natural monthly rollover and certificate replacement at expiry still need observation over time.

This repository is primarily a personal backup. Detailed setup, operations, and maintenance records remain private.

- [Release notes](RELEASE_NOTES.md)
- [Version history](CHANGELOG.md)
- [Previous description](docs/archive/README.previous.md)

Built around [Xray-core](https://github.com/XTLS/Xray-core), [Hysteria](https://github.com/apernet/hysteria), and [Certbot](https://github.com/certbot/certbot). Their respective licenses apply to those components.
