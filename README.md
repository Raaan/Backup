# Backup

A personal project, preserved here for backup and future rebuilding.

## Background

This project brings together tools and maintenance work developed through several years of personal use. Its installer and modular structure were consolidated recently.

The repository provides a versioned backup of the project and makes future maintenance and recovery easier.

## Installation

Use a fresh Ubuntu amd64 environment. Ubuntu 26.04 compatibility has not yet been fully verified. Log in as root and run each command separately:

```bash
apt-get update
apt-get install -y git python3 ca-certificates openssl iproute2
git clone https://github.com/Raaan/Backup.git /root/LA_Portal
cd /root/LA_Portal
bash install.sh
```

Allow TCP 80, 443 and 8443, plus UDP 443; keep your SSH port accessible. Follow the installer prompts. Email is optional and configured separately.

After verifying real client connections and traffic accounting, run `python3 portal.py activate --apply --clients-verified` from the installation directory.


## 安装向导中文说明

适用于全新 Ubuntu amd64 VPS。Ubuntu 26.04 的完整兼容性尚未验证。安装前确保 TCP 80、443、8443 和 UDP 443 可从公网访问，并保留 SSH 端口。TCP 80 用于证书验证；安装程序不会自动修改防火墙。

逐条执行上面的安装命令。若 `git clone` 失败，请先解决错误，不要继续执行后续命令。向导中方括号内是默认值，直接按 Enter 使用默认值。

1. **Detected public IPv4**：自动检测的公网 IPv4，同时作为客户端连接地址和 TLS 证书身份。若自动检测失败，按提示输入新 VPS 可访问的公网 IP 或域名。
2. **ACME contact email (not a mail sending account)**：输入可接收证书相关通知的邮箱，如 `you@example.com`。这不是发送周报的邮箱，无需输入邮箱密码。
3. **Unique server ID**：服务器唯一标识，默认 `california-01`；多台服务器请使用不同名称，只能使用英文字母、数字、下划线、点或连字符。
4. **Reachable REALITY TLS target hostname**：输入 VPS 能访问的 REALITY 目标网站域名，不带 `https://`、路径或端口。程序使用该域名的 443 端口；请确保目标可访问且支持所需的 TLS。
5. **Canonical user names, comma separated**：初始用户名，多个用英文逗号分隔，如 `Owner,Alice`。默认 `Owner`；用户名以英文字母开头，后续可用英文字母、数字、下划线、点和连字符，不能重复。安装会生成新的客户端凭据。
6. **Monthly settlement day (1-28)**：每月结算日，默认 8。
7. **Settlement time HH:MM**：结算时间，24 小时制，默认 `00:10`。
8. **Settlement timezone**：结算时区，默认 `Asia/Hong_Kong`；使用 IANA 名称，不要输入 `+8`。
9. **Monthly reporting quota**：月报额度数值，默认 `1000`。此值用于报告，不会自动限制代理流量。
10. **Quota unit GB or GiB**：额度单位，默认 `GiB`。GB 为十进制，GiB 为二进制。
11. **Estimated VPS bandwidth multiplier**：估算 VPS 带宽消耗的倍率，默认 `2`；按实际计费方式填写，`1` 表示不放大。
12. **Accept CA terms? Type YES**：阅读屏幕显示的 CA 条款链接，同意后输入大写 `YES`。未同意则不会保存配置。
13. 向导显示安装计划后，**Install on this NEW server? Type INSTALL**：核对无误后输入大写 `INSTALL`，开始实际安装。程序下载 Xray 和 Hysteria、申请证书、生成配置并启动服务。

配置保存于 `/root/private/la-portal.json`，请妥善保管；基础安装不导入旧服务器用户、凭据或流量历史。已有配置时安装脚本会复用它。不要在已有服务的服务器上重新运行新机安装。

安装完成后，使用真实客户端分别测试连接和上下行流量，并检查 `python3 portal.py status`。确认流量入库后，在项目目录运行 `python3 portal.py activate --apply --clients-verified` 启用配置请求的月结。周报邮件是独立配置：`python3 portal.py mail-setup --apply`。

客户端链接和配置文件包含敏感凭据，请勿公开分享。若安装出错，保留报错信息；不要反复运行安装或随意清理已有文件。

## Current version

**v0.1.0** brings the existing setup into a modular project, with improvements to installation, user accounting, reporting, certificates, and optional email delivery.

The current build passed 34 automated tests on Ubuntu, along with protocol and traffic-accounting checks in a test environment. Natural monthly rollover and certificate replacement at expiry still need observation over time.

This repository is primarily a personal backup. Detailed setup, operations, and maintenance records remain private.

