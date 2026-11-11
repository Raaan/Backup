# Backup

A personal VPS deployment and maintenance project, preserved here for backup and future rebuilding.

## Background

This project grew out of a VPS setup I have used and maintained for several years. The reusable installer and modular structure were completed recently.

After the blocking I experienced last month, I decided to keep the project on GitHub so that the code and maintenance work would be easier to preserve and recover.

## Current version

**v0.1.0** brings the existing setup into a modular project, with improvements to installation, user accounting, reporting, certificates, and optional email delivery.

The current build passed 34 automated tests on Ubuntu, along with protocol and traffic-accounting checks on a test VPS. Natural monthly rollover and certificate replacement at expiry still need observation over time.

This repository is primarily a personal backup. Detailed setup, operations, and maintenance records remain private.

- [Release notes](RELEASE_NOTES.md)
- [Previous description](docs/archive/README.previous.md)

Built around [Xray-core](https://github.com/XTLS/Xray-core), [Hysteria](https://github.com/apernet/hysteria), and [Certbot](https://github.com/certbot/certbot). Their respective licenses apply to those components.
