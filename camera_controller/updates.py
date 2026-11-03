"""User initiated, checksum-verified GitHub Release updates."""
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from version import VERSION, REPOSITORY

API = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
PREFIX = f'https://github.com/{REPOSITORY}/releases/download/'
INSTALLER = 'VRC-Kameracontroller-Setup.exe'

def version_tuple(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', value)
    if not match:
        raise ValueError('Unbekanntes Versionsformat.')
    return tuple(map(int,match.groups()))

def fetch(url, limit):
    req=urllib.request.Request(url,headers={'User-Agent':f'VRC-Kameracontroller/{VERSION}', 'Accept':'application/vnd.github+json' if url==API else 'application/octet-stream'})
    with urllib.request.urlopen(req,timeout=20) as response:
        data=response.read(limit+1)
    if len(data)>limit:
        raise ValueError('Update-Datei überschreitet die erlaubte Größe.')
    return data

def release_info(data, current=VERSION):
    if data.get('draft') or data.get('prerelease') or version_tuple(data['tag_name'])<=version_tuple(current):
        return None
    assets={a['name']:a for a in data.get('assets',[])}
    if INSTALLER not in assets or 'SHA256SUMS.txt' not in assets:
        return None
    result={'version':data['tag_name'], 'installer':assets[INSTALLER]['browser_download_url'],
            'checksums':assets['SHA256SUMS.txt']['browser_download_url']}
    for key in ('installer','checksums'):
        url=result[key]
        if not url.startswith(PREFIX) or urllib.parse.urlsplit(url).query:
            raise ValueError('Ungültige Update-Adresse.')
    return result

def check():
    try:
        return release_info(json.loads(fetch(API,2_000_000)))
    except urllib.error.HTTPError as exc:
        if exc.code==404:
            return None
        raise

def download(info, folder):
    # Validate again before using an update object from the UI.
    for key in ('installer','checksums'):
        if not info[key].startswith(PREFIX):
            raise ValueError('Ungültige Update-Adresse.')
    checksums=fetch(info['checksums'],32_000).decode('utf-8')
    expected=None
    for line in checksums.splitlines():
        parts=line.split()
        if len(parts)==2 and parts[1].lstrip('*')==INSTALLER and re.fullmatch('[0-9a-fA-F]{64}',parts[0]):
            expected=parts[0].lower()
    if not expected:
        raise ValueError('Prüfsumme für den Installer fehlt.')
    content=fetch(info['installer'],150_000_000)
    if hashlib.sha256(content).hexdigest()!=expected:
        raise ValueError('Prüfsumme stimmt nicht. Update wurde verworfen.')
    if content[:2]!=b'MZ':
        raise ValueError('Keine gültige Windows-Installationsdatei.')
    folder=Path(folder)
    folder.mkdir(parents=True,exist_ok=True)
    target=folder/INSTALLER
    target.write_bytes(content)
    return target
