#!/bin/sh
# Run on a new Ubuntu host after obtaining this private repository.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
profile=/root/private/la-portal.json
assume_yes=false
while [ "$#" -gt 0 ]; do
    case "$1" in
        --config) [ "$#" -ge 2 ] || { echo '--config needs a path' >&2; exit 2; }; profile=$2; shift 2 ;;
        --yes) assume_yes=true; shift ;;
        *) echo 'Usage: bash install.sh [--config PATH] [--yes]' >&2; exit 2 ;;
    esac
done
if [ "$(id -u)" != 0 ]; then
    echo 'Run as root on the new server.' >&2
    exit 1
fi
echo 'Ports: REALITY TCP443 | XHTTP/TLS TCP8443 | HY2 UDP443'
echo 'ACME needs reachable TCP80. Mail is installed separately.'
if [ ! -f "$profile" ]; then
    if [ "$assume_yes" = true ]; then
        echo 'Create a complete private config first: python3 portal.py setup --config PATH' >&2
        exit 2
    fi
    python3 portal.py setup --config "$profile"
else
    echo "Using existing configuration: $profile"
fi
python3 portal.py plan --config "$profile"
if [ "$assume_yes" != true ]; then
    printf '\nInstall on this NEW server? Type INSTALL: '
    read -r answer
    [ "$answer" = INSTALL ] || exit 1
fi
python3 portal.py install --config "$profile" --apply
echo 'Verify client connections and traffic ingestion.'
echo 'Then: python3 portal.py activate --apply --clients-verified'
echo 'Optional mail: python3 portal.py mail-setup --apply'
