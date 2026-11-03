"""Local VRChat Dolly controller. Python standard library only."""
import argparse
import json
import os
import re
import socket
import sqlite3
import struct
import subprocess
import threading
import time
import queue
import math
import sys
import secrets
import ipaddress
import hmac
from contextlib import contextmanager
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from version import VERSION
import updates
from help_content import HELP

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'VRC-Kameracontroller'
DB = DATA_DIR / 'club_analytics.sqlite3'
WORLD = re.compile(r'wrld_[0-9a-fA-F-]{36}')
DEFAULT_LOGS = Path(os.environ.get('USERPROFILE', str(Path.home()))) / 'AppData/LocalLow/VRChat/VRChat'

def osc(address, value=None):
    def string(s):
        b = s.encode('utf-8') + b'\0'
        return b + b'\0' * (-len(b) % 4)
    tag = ',' if value is None else ',T' if value is True else ',F' if value is False else ',i' if isinstance(value,int) else ',s'
    body=string(value) if isinstance(value,str) else struct.pack('>i',value) if type(value) is int else b''
    return string(address) + string(tag) + body

def decode(data):
    def string(offset):
        end = data.index(b'\0', offset)
        return data[offset:end].decode('utf-8'), (end + 4) & ~3
    address, pos = string(0)
    tags, pos = string(pos)
    if tags in (',T', ',F'):
        return address, tags == ',T'
    if tags == ',i':
        return address, bool(struct.unpack_from('>i', data, pos)[0])
    if tags == ',s':
        return address, string(pos)[0]
    if tags == ',ffffff':
        return address, list(struct.unpack_from('>6f',data,pos))
    return address, None

def same_path(expected, actual):
    """Round-trip comparison allowing the client's float serialization error."""
    if isinstance(expected, bool):
        return type(actual) is bool and actual == expected
    if isinstance(expected, (int, float)):
        return isinstance(actual,(int,float)) and math.isclose(expected,actual,rel_tol=1e-5,abs_tol=1e-3)
    if isinstance(expected, dict):
        return isinstance(actual,dict) and all(k in actual and same_path(v,actual[k]) for k,v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual,list) and len(expected)==len(actual) and all(same_path(a,b) for a,b in zip(expected,actual))
    return expected == actual

def world_from_log(text):
    pending = None
    current = None
    for line in text.splitlines():
        if 'OnLeftRoom' in line or 'OnApplicationQuit' in line:
            current = pending = None
        if 'Joining wrld_' in line:
            match = WORLD.search(line)
            pending = match.group(0) if match else None
            current = None
        if 'Successfully joined room' in line or 'OnJoinedRoom' in line:
            current = pending
    return current

class Controller:
    def __init__(self, db=DB, logs=DEFAULT_LOGS, receive_port=9001, auto_port=False):
        self.db, self.logs = Path(db), Path(logs)
        self.lock = threading.RLock()
        self.operation = threading.Lock()
        self.closed = threading.Event()
        self.playing = None
        self.feedback_at = None
        self.requested = None
        self.error = ''
        self.active = None
        self.running = False
        self.osc_at = None
        self.query_at = None
        self.camera_pose = None
        self.camera_pose_at = None
        self.transport_error = ''
        self.receive_port = receive_port
        self.auto_port = auto_port
        self.advertisement = None
        self.discovery_error = ''
        self.exported_file = None
        self.exported_at = 0
        self.load_state = 'idle'
        self.camera_mode = None
        self.spout_active = None
        self.camera_confirmed_at = 0
        self._repeat = False
        self._repeat_world = None
        self._repeat_seen_play = False
        self._repeat_sent_at = 0
        self.db.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS camera_schema(version INTEGER PRIMARY KEY);
            INSERT OR IGNORE INTO camera_schema VALUES(1);
            CREATE TABLE IF NOT EXISTS camera_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS camera_paths(id INTEGER PRIMARY KEY,name TEXT NOT NULL,world_id TEXT NOT NULL,payload TEXT NOT NULL,source TEXT NOT NULL,updated REAL NOT NULL);''')
            c.execute('CREATE TABLE IF NOT EXISTS camera_worlds(world_id TEXT PRIMARY KEY,name TEXT NOT NULL)')
        self.sock = None
        self.bind_socket()
        threading.Thread(target=self.receive, daemon=True).start()
        threading.Thread(target=self.repeat_worker,daemon=True).start()

    def bind_socket(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(('127.0.0.1', self.receive_port))
            sock.settimeout(.3)
            with self.lock:
                self.sock = sock
                self.transport_error = ''
            return True
        except OSError:
            if self.auto_port:
                try:
                    sock.bind(('127.0.0.1', 0))
                    sock.settimeout(.3)
                    self.receive_port = sock.getsockname()[1]
                    self.sock = sock
                    self.transport_error = ''
                    return True
                except OSError:
                    pass
            sock.close()
            self.transport_error = f'OSC-Port {self.receive_port} ist belegt. Warte auf Freigabe …'
            return False

    def advertise(self):
        try:
            from oscquery import Advertisement
            self.advertisement = Advertisement(self)
        except Exception as exc:
            self.discovery_error = f'Automatische OSC-Erkennung: {exc}'

    @contextmanager
    def connection(self):
        c = sqlite3.connect(self.db)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def setting(self, key, default=None):
        with self.connection() as c:
            row = c.execute('SELECT value FROM camera_settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def configure(self, port):
        port = int(port)
        if not 1 <= port <= 65535:
            raise ValueError('Ungültiger OSC-Eingangsport')
        with self.connection() as c:
            c.execute('INSERT OR REPLACE INTO camera_settings VALUES(?,?)', ('osc_port', json.dumps(port)))

    def set_setting(self,key,value):
        with self.connection() as db:
            db.execute('INSERT OR REPLACE INTO camera_settings VALUES(?,?)',(key,json.dumps(value)))
        if key=='repeat_playback' and not value:self._repeat=False

    def prepare_camera(self):
        if not self.setting('prepare_camera',True):return
        self.load_state='preparing_camera'
        before=time.time()
        self.send('/usercamera/Mode',2)
        if self.closed.wait(.25):raise ValueError('Controller wird geschlossen.')
        self.send('/usercamera/Streaming',True)
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            if self.camera_confirmed_at>=before and self.camera_mode==2 and self.spout_active is True:return
            if self.closed.wait(.05):break
        raise ValueError('Kamera/Spout nicht bestätigt. Kamera in VRChat öffnen und OSC prüfen.')

    def repeat_worker(self):
        while not self.closed.wait(.75):
            try:self.repeat_tick()
            except Exception as exc:
                self._repeat=False
                self.error=f'Wiederholung pausiert: {exc}'

    def repeat_tick(self):
        if not self._repeat:return
        if not self.operation.acquire(blocking=False):return
        try:
            if not self._repeat:return
            if self.world()!=self._repeat_world:
                self._repeat=False
                return
            if not self.feedback_at or time.time()-self.feedback_at>15:
                self._repeat=False
                self.error='Wiederholung pausiert: keine aktuelle Kamerarückmeldung.'
                return
            if self.feedback_at<self._repeat_sent_at:return
            if self.playing is True:
                self._repeat_seen_play=True
            elif self.playing is False and self._repeat_seen_play:
                self._repeat_sent_at=time.time()
                self._repeat_seen_play=False
                self.send('/dolly/Play',True)
            elif time.time()-self._repeat_sent_at>5:
                self._repeat=False
                self.error='Wiederholung pausiert: Start nicht bestätigt.'
        finally:self.operation.release()

    def play_slot(self,slot):
        world=self.world()
        available=[p for p in self.paths() if world and p['world_id']==world]
        if not 1<=slot<=len(available):
            raise ValueError('Für diesen Platz ist in der aktuellen Welt keine Fahrt gespeichert.')
        self.play(available[slot-1]['id'])

    def configure_server(self, pc_ip, server_ip):
        for value in (pc_ip,server_ip):
            address=ipaddress.ip_address(value)
            if address.version!=4 or not address.is_private or address.is_loopback or address.is_unspecified:
                raise ValueError('Bitte eine lokale IPv4-Adresse eingeben, zum Beispiel 192.168.1.12.')
        token=self.setting('api_token') or secrets.token_urlsafe(32)
        with self.connection() as db:
            for key,value in [('api_host',pc_ip),('companion_ip',server_ip),('api_token',token)]:
                db.execute('INSERT OR REPLACE INTO camera_settings VALUES(?,?)',(key,json.dumps(value)))
        return token

    def world(self):
        # A historical log must never unlock playback while VRChat is closed.
        try:
            result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq VRChat.exe', '/FO', 'CSV', '/NH'], capture_output=True, text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=3)
            self.running = 'vrchat.exe' in result.stdout.lower()
            if not self.running:
                with self.lock:
                    self.playing = self.feedback_at = self.osc_at = self.query_at = None
                return None
            files = list(self.logs.glob('output_log_*.txt'))
            latest = max(files, key=lambda p: p.stat().st_mtime)
            # Fail closed if only old logs are available.
            if time.time() - latest.stat().st_mtime > 86400:
                return None
            return world_from_log(latest.read_text(encoding='utf-8', errors='replace'))
        except (OSError, ValueError, subprocess.SubprocessError):
            self.running = False
            return None

    def receive(self):
        while not self.closed.is_set():
            if self.sock is None:
                if not self.bind_socket():
                    self.closed.wait(2)
                    continue
            try:
                data, peer = self.sock.recvfrom(65535)
                address, value = decode(data)
                if address == '/usercamera/Pose' and isinstance(value,list) and all(math.isfinite(v) for v in value):
                    with self.lock:
                        self.camera_pose, self.camera_pose_at = value, time.time()
                if address in ('/dolly/Export','/dolly/ExportLocal') and isinstance(value,str):
                    self.exported_file, self.exported_at = value, time.time()
                if address.startswith(('/dolly/', '/avatar/', '/usercamera/', '/tracking/')):
                    self.osc_at = time.time()
                if address == '/dolly/Play' and isinstance(value, bool):
                    with self.lock:
                        self.playing, self.feedback_at = value, time.time()
            except (socket.timeout, ValueError, UnicodeError, struct.error):
                continue
            except OSError:
                if self.sock:
                    self.sock.close()
                self.sock = None
                self.closed.wait(1)

    def send(self, address, value=None):
        packet = osc(address, value)
        if len(packet) > 65000:
            raise ValueError('Fahrt zu groß für ein OSC-UDP-Paket (max. 65 KB). Fahrt verkürzen.')
        if self.sock is None:
            raise ValueError(self.transport_error or 'OSC wartet auf Verbindung.')
        self.sock.sendto(packet, ('127.0.0.1', self.setting('osc_port', 9000)))

    def paths(self):
        with self.connection() as c:
            return [dict(r) for r in c.execute('SELECT id,name,world_id,source,updated FROM camera_paths ORDER BY name,id')]

    def register(self, name, filename, world_id):
        if not WORLD.fullmatch(world_id):
            raise ValueError('Eine vollständige wrld_-ID auswählen.')
        name = name.strip()
        if not name:
            raise ValueError('Name fehlt.')
        path = Path(filename).resolve()
        if path.stat().st_size > 65000:
            raise ValueError('Datei zu groß (max. 65 KB).')
        payload = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(payload, (dict, list)) or not payload:
            raise ValueError('Keine gültige Dolly-JSON-Struktur.')
        payload = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        if len(osc('/dolly/Import', payload)) > 65000:
            raise ValueError('OSC-Paket zu groß.')
        with self.connection() as c:
            cur = c.execute('INSERT INTO camera_paths(name,world_id,payload,source,updated) VALUES(?,?,?,?,?)', (name, world_id, payload, str(path), time.time()))
            return cur.lastrowid

    def worlds(self):
        worlds = {}
        with self.connection() as c:
            for r in c.execute('SELECT world_id,name FROM camera_worlds'):
                worlds[r[0]]=r[1]
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='observations'").fetchone():
                for r in c.execute('SELECT world,location FROM observations'):
                    m = WORLD.search(r['location'] or '')
                    if m:
                        worlds[m.group(0)] = r['world']
            for r in c.execute('SELECT DISTINCT world_id FROM camera_paths'):
                worlds.setdefault(r[0], r[0])
        current = self.world()
        if current:
            worlds.setdefault(current, 'Aktuelle Welt')
        return worlds

    def migrate(self, filename):
        source=Path(filename).resolve()
        if source==self.db.resolve():
            raise ValueError('Diese Datenbank wird bereits verwendet.')
        src=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)
        src.row_factory=sqlite3.Row
        try:
            tables={r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'camera_paths' not in tables:
                raise ValueError('Diese Datei enthält keine Kamera-Fahrten.')
            with self.connection() as dest:
                # Preserve stable Companion IDs; merging unrelated libraries could
                # otherwise make a button start a different path.
                if dest.execute('SELECT COUNT(*) FROM camera_paths').fetchone()[0]:
                    raise ValueError('Datenübernahme nur in eine leere Fahrtenbibliothek möglich.')
                count=0
                for row in src.execute('SELECT id,name,world_id,payload,source,updated FROM camera_paths'):
                    json.loads(row['payload'])
                    dest.execute('INSERT INTO camera_paths VALUES(?,?,?,?,?,?)',tuple(row))
                    count+=1
                if 'camera_settings' in tables:
                    for row in src.execute("SELECT key,value FROM camera_settings WHERE key='osc_port'"):
                        dest.execute('INSERT OR REPLACE INTO camera_settings VALUES(?,?)',tuple(row))
                if 'camera_worlds' in tables:
                    for row in src.execute('SELECT world_id,name FROM camera_worlds'):
                        dest.execute('INSERT OR REPLACE INTO camera_worlds VALUES(?,?)',tuple(row))
                if 'observations' in tables:
                    for row in src.execute('SELECT world,location FROM observations'):
                        match=WORLD.search(row['location'] or '')
                        if match:
                            dest.execute('INSERT OR REPLACE INTO camera_worlds VALUES(?,?)',(match.group(),row['world'] or match.group()))
            return count
        finally:
            src.close()

    def status(self):
        world = self.world()
        with self.lock:
            connected = bool(self.running and max(self.osc_at or 0,self.query_at or 0) > time.time()-15)
            return dict(world_id=world, active_path_id=self.active, playing=self.playing if connected else None,
                        vrchat_running=self.running, osc_connected=connected,
                        connection_state='offline' if not self.running else 'connected' if connected else 'waiting_osc',
                        osc_received_at=self.osc_at, transport_error=self.transport_error,
                        receive_port=self.receive_port, discovery_error=self.discovery_error,
                        query_received_at=self.query_at,
                        load_state=self.load_state,
                        camera_mode=self.camera_mode,spout_active=self.spout_active,repeat_enabled=self._repeat,
                        feedback_at=self.feedback_at, requested=self.requested, error=self.error,
                        configured=self.setting('osc_port') is not None,
                        paths=[dict(p, available=bool(world and p['world_id'] == world)) for p in self.paths()])

    def stop(self):
        self._repeat=False
        with self.operation:
            # A running load may have re-armed repetition while Stop waited.
            self._repeat=False
            self.send('/dolly/Play', False)
            self.requested = 'stop'

    def import_path(self, row, world):
        expected = json.loads(row['payload'])
        if not isinstance(expected, list) or not expected:
            raise ValueError('Diese Fahrt hat kein unterstütztes VRChat-Punktformat. Bitte neu exportieren.')
        cache = self.db.parent / 'camera_runtime'
        cache.mkdir(parents=True, exist_ok=True)
        path = (cache / f"path-{row['id']}.json").resolve()
        # Keep one stable import file per saved path; reuse it on subsequent starts.
        if not path.exists() or path.read_text(encoding='utf-8') != row['payload']:
            path.write_text(row['payload'], encoding='utf-8')
        self.load_state = 'importing'
        self.send('/dolly/Import', str(path))
        # VRChat provides no import acknowledgement. Allow the file import to
        # finish without requesting exports that would fill the user's library.
        if self.closed.wait(1):
            raise ValueError('Controller wird geschlossen.')
        if self.world() != world:
            raise ValueError('Welt während des Ladens gewechselt.')
        self.load_state = 'loaded_unconfirmed'

    def play(self, path_id):
        if not self.operation.acquire(blocking=False):
            raise ValueError('Eine andere Fahrt wird gerade geladen.')
        try:
            self._repeat=False
            self.load_state='stopping'
            with self.connection() as c:
                row = c.execute('SELECT * FROM camera_paths WHERE id=?', (int(path_id),)).fetchone()
            if row is None:
                raise ValueError('Fahrt nicht gefunden.')
            if self.setting('osc_port') is None:
                raise ValueError('Bitte zuerst die Ersteinrichtung speichern.')
            world = self.world()
            if not world or row['world_id'] != world:
                raise ValueError('Start gesperrt: aktuelle Welt unbekannt oder unpassend.')
            self.prepare_camera()
            before = time.time()
            self.send('/dolly/Play', False)
            # Do not import into a possibly still playing camera. Require feedback.
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if self.feedback_at and self.feedback_at >= before and self.playing is False:
                    break
                time.sleep(.02)
            else:
                raise ValueError('Keine Stopp-Bestätigung von VRChat. OSC-Ausgang/Router prüfen.')
            if self.world() != world:
                raise ValueError('Welt während des Ladens gewechselt.')
            self.import_path(row,world)
            if self.world() != world:
                raise ValueError('Welt während des Ladens gewechselt.')
            self._repeat_sent_at=time.time()
            self.send('/dolly/Play', True)
            self.active, self.requested, self.error = row['id'], 'play', ''
            self._repeat_world=world
            self._repeat_seen_play=False
            self._repeat=self.setting('repeat_playback',True)
            self.load_state='started'
        except Exception as exc:
            self._repeat=False
            self.error = str(exc)
            self.load_state='failed'
            raise
        finally:
            self.operation.release()

    def close(self):
        self._repeat=False
        self.closed.set()
        if self.advertisement:
            self.advertisement.close()
        if self.sock:
            self.sock.close()

def api(controller, port, host='127.0.0.1'):
    token=controller.setting('api_token') if host!='127.0.0.1' else None
    allowed=controller.setting('companion_ip') if token else None
    if host!='127.0.0.1' and not (token and allowed):
        raise ValueError('Server-Verbindung zuerst einrichten: lokale IP, Companion-IP und Zugriffsschlüssel fehlen.')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def authorized(self):
            if not token:
                return True
            peer=self.client_address[0]
            if peer not in ('127.0.0.1',host,allowed):
                self.reply(403,{'error':'Dieser Rechner ist nicht als Companion-Server freigegeben.'})
                return False
            provided=self.headers.get('Authorization','')
            if not hmac.compare_digest(provided,'Bearer '+token):
                self.reply(401,{'error':'Companion-Zugriffsschlüssel fehlt oder ist ungültig.'})
                return False
            return True

        def do_GET(self):
            if not self.authorized():return
            if self.path != '/api/status':
                return self.reply(404, {'error': 'Nicht gefunden'})
            self.reply(200, controller.status())

        def do_POST(self):
            if not self.authorized():return
            # Companion uses POST without a body. Reject browser cross-origin calls.
            if self.headers.get('Origin'):
                return self.reply(403, {'error': 'Browser-Ursprung nicht erlaubt'})
            try:
                if self.path == '/api/stop':
                    controller.stop()
                elif re.fullmatch(r'/api/paths/\d+/play', self.path):
                    controller.play(int(self.path.split('/')[3]))
                elif re.fullmatch(r'/api/slots/\d+/play', self.path):
                    controller.play_slot(int(self.path.split('/')[3]))
                else:
                    return self.reply(404, {'error': 'Nicht gefunden'})
                self.reply(200, controller.status())
            except (ValueError, OSError) as exc:
                self.reply(409, {'error': str(exc)})

        def reply(self, code, body):
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server

def gui(c, api_port):
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, simpledialog
    root = tk.Tk()
    root.title(f'VRC Kameracontroller · {VERSION}')
    root.geometry('1120x740')
    root.minsize(960, 640)
    root.configure(bg='#10141e')
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.', font=('Segoe UI', 11), background='#10141e', foreground='#e7edf8')
    style.configure('TFrame', background='#10141e')
    style.configure('TLabel', background='#10141e', foreground='#a6b4cd')
    style.configure('Title.TLabel', font=('Segoe UI Semibold', 28), foreground='#f4f7ff')
    style.configure('Card.TFrame', background='#1a2232')
    style.configure('Card.TLabel', background='#1a2232', foreground='#a6b4cd')
    style.configure('Value.TLabel', background='#1a2232', foreground='#f4f7ff', font=('Segoe UI Semibold', 16))
    style.configure('TButton', padding=(18,12), background='#273349', foreground='#e7edf8', borderwidth=0)
    style.configure('TEntry', fieldbackground='#202b3e', foreground='#f4f7ff', insertcolor='#ffffff', padding=8,
                    selectbackground='#6c5ce7', selectforeground='#ffffff')
    style.map('TEntry', fieldbackground=[('disabled','#1a2232'),('!disabled','#202b3e')],
              foreground=[('disabled','#8998b1'),('!disabled','#f4f7ff')])
    style.configure('TCombobox', fieldbackground='#202b3e', background='#273349', foreground='#f4f7ff',
                    arrowcolor='#f4f7ff', insertcolor='#ffffff', padding=8,
                    selectbackground='#6c5ce7', selectforeground='#ffffff')
    style.map('TCombobox', fieldbackground=[('readonly','#202b3e'),('!disabled','#202b3e')],
              foreground=[('disabled','#8998b1'),('!disabled','#f4f7ff')],
              selectbackground=[('!disabled','#6c5ce7')], selectforeground=[('!disabled','#ffffff')])
    root.option_add('*TCombobox*Listbox.background', '#202b3e')
    root.option_add('*TCombobox*Listbox.foreground', '#f4f7ff')
    root.option_add('*TCombobox*Listbox.selectBackground', '#6c5ce7')
    root.option_add('*TCombobox*Listbox.selectForeground', '#ffffff')
    root.option_add('*Entry.background', '#202b3e')
    root.option_add('*Entry.foreground', '#f4f7ff')
    root.option_add('*Entry.insertBackground', '#ffffff')
    style.map('TButton', background=[('active','#364866'),('disabled','#1a2232')],foreground=[('disabled','#65738b')])
    style.configure('Accent.TButton', background='#6c5ce7', foreground='white')
    style.map('Accent.TButton', background=[('active','#8575ff'),('disabled','#302e4b')])
    style.configure('Treeview', background='#1a2232', fieldbackground='#1a2232', foreground='#e7edf8', rowheight=48, borderwidth=0)
    style.configure('Treeview.Heading', background='#202b3e',foreground='#a6b4cd',padding=12,font=('Segoe UI Semibold',10))
    style.map('Treeview',background=[('selected','#393764')],foreground=[('selected','#ffffff')])
    frame = ttk.Frame(root, padding=30)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='BEERHIDEOUT  /  CAMERA STUDIO', foreground='#958aff',font=('Segoe UI Semibold',10)).pack(anchor='w')
    ttk.Label(frame, text='Deine Perspektive. Ein Klick.', style='Title.TLabel').pack(anchor='w',pady=(10,6))
    ttk.Label(frame, text='Kamerafahrten verwalten und live steuern. Dein Studio bleibt auch ohne VRChat bereit.').pack(anchor='w')
    cards = ttk.Frame(frame)
    cards.pack(fill='x',pady=24)
    variables = {}
    for index,(key,title,initial) in enumerate([('connection','VERBINDUNG','Wird geprüft …'),('world','AKTUELLE WELT','Noch keine Welt'),('playback','KAMERA','Bereit')]):
        cards.columnconfigure(index,weight=1,uniform='cards')
        card=ttk.Frame(cards,style='Card.TFrame',padding=18)
        card.grid(row=0,column=index,sticky='nsew',padx=(0,10 if index<2 else 0))
        ttk.Label(card,text=title,style='Card.TLabel',font=('Segoe UI Semibold',9)).pack(anchor='w')
        variables[key]=tk.StringVar(value=initial)
        ttk.Label(card,textvariable=variables[key],style='Value.TLabel',wraplength=290).pack(anchor='w',pady=(10,0))
    status = tk.StringVar(value='Du kannst Fahrten auch ohne geöffnetes VRChat hinzufügen.')
    ttk.Label(frame,text='Deine Kamerafahrten',font=('Segoe UI Semibold',16),foreground='#f4f7ff').pack(anchor='w')
    tree = ttk.Treeview(frame, columns=('id', 'name', 'world', 'available'), show='headings', height=4)
    for col, label, width in [('id','ID',45), ('name','Kamerafahrt',260), ('world','Welt',360), ('available','Status',140)]:
        tree.heading(col, text=label)
        tree.column(col, width=width)
    tree.pack(fill='both', expand=True, pady=14)
    tree.tag_configure('unavailable',foreground='#7888a3')
    events = queue.Queue()
    latest = {}
    quit_event = threading.Event()
    update_info = {}
    update_busy = threading.Event()
    def run(fn):
        def work():
            try:
                fn()
            except Exception as exc:
                events.put(('error',str(exc)))
        threading.Thread(target=work, daemon=True).start()
    def selected():
        ids = tree.selection()
        if not ids:
            raise ValueError('Bitte eine Fahrt auswählen.')
        return int(ids[0])
    def add():
        filename = filedialog.askopenfilename(title='VRChat-Dolly-Export auswählen', filetypes=[('Dolly JSON','*.json')], initialdir=str(Path.home()/'Documents/VRChat/CameraPaths'))
        if not filename:
            return
        win = tk.Toplevel(root)
        win.title('Erste Verwendung · Fahrt einer Welt zuordnen')
        box = ttk.Frame(win, padding=18)
        box.pack(fill='both', expand=True)
        ttk.Label(box, text='Diese Zuordnung wird in der Datenbank gespeichert.').pack(anchor='w')
        name = tk.StringVar(value=Path(filename).stem)
        ttk.Entry(box, textvariable=name, width=80).pack(pady=10)
        worlds = c.worlds()
        options = [f'{wid} | {label}' for wid,label in worlds.items()]
        current = c.world()
        value = tk.StringVar(value=next((v for v in options if current and v.startswith(current)), ''))
        ttk.Label(box, text='Welt aus Datenbank auswählen oder vollständige wrld_-ID eingeben:').pack(anchor='w')
        ttk.Combobox(box, textvariable=value, values=options, width=95).pack(pady=10)
        ttk.Label(box, text='Die JSON-Koordinaten bleiben erhalten. Relative Fahrten benötigen denselben Ausgangspunkt.', wraplength=650).pack(anchor='w')
        def save():
            try:
                c.register(name.get(), filename, value.get().split(' | ')[0].strip())
                win.destroy()
            except Exception as exc:
                messagebox.showerror('Import', str(exc), parent=win)
        ttk.Button(box, text='Fahrt speichern', command=save).pack(pady=12)
    def setup():
        port = simpledialog.askinteger('Ersteinrichtung', 'In VRChat OSC aktivieren.\nVRChat-Eingangsport (Standard 9000):', initialvalue=c.setting('osc_port',9000), minvalue=1,maxvalue=65535)
        if port:
            c.configure(port)
            messagebox.showinfo('Einrichtung', 'Gespeichert. Das Studio erkennt VRChat automatisch über OSCQuery.\nEin vorhandener OSC-Router kann weiterlaufen.\nDanach eine Fahrt hinzufügen und ihrer Welt zuordnen.')
    def help_window():
        win=tk.Toplevel(root)
        win.title('Kurzanleitung · VRC Kameracontroller')
        win.geometry('820x680')
        win.configure(bg='#10141e')
        text=tk.Text(win,wrap='word',bg='#1a2232',fg='#e7edf8',font=('Segoe UI',11),padx=22,pady=20,relief='flat')
        scroll=ttk.Scrollbar(win,command=text.yview)
        scroll.pack(side='right',fill='y')
        text.configure(yscrollcommand=scroll.set)
        text.pack(fill='both',expand=True)
        text.insert('1.0',HELP)
        text.configure(state='disabled')
        bottom=ttk.Frame(win,padding=10)
        bottom.pack(fill='x')
        def open_data():
            os.startfile(str(c.db.parent))
        def migrate():
            path=filedialog.askopenfilename(parent=win,title='Vorhandene Club-Datenbank auswählen',filetypes=[('SQLite-Datenbank','*.sqlite3 *.db')])
            if path:
                try:
                    count=c.migrate(path)
                    messagebox.showinfo('Daten übernommen',f'{count} Fahrten übernommen. IDs für Companion bleiben erhalten.',parent=win)
                except Exception as exc:
                    messagebox.showerror('Datenübernahme',str(exc),parent=win)
        ttk.Button(bottom,text='Datenordner öffnen',command=open_data).pack(side='left')
        ttk.Button(bottom,text='Daten übernehmen',command=migrate).pack(side='left',padx=8)
        ttk.Button(bottom,text='Companion-Server',command=server_setup).pack(side='left')
        ttk.Button(bottom,text='Schließen',command=win.destroy).pack(side='right')
    def server_setup():
        pc=simpledialog.askstring('Companion-Server','Lokale IP dieses Kamera-PCs:',initialvalue=c.setting('api_host','192.168.1.167'),parent=root)
        if not pc:return
        peer=simpledialog.askstring('Companion-Server','Lokale IP des Companion-Servers:',initialvalue=c.setting('companion_ip','192.168.1.12'),parent=root)
        if not peer:return
        try:
            token=c.configure_server(pc.strip(),peer.strip())
            win=tk.Toplevel(root);win.title('Companion-Verbindung')
            box=ttk.Frame(win,padding=18);box.pack(fill='both',expand=True)
            ttk.Label(box,text='Nach Neustart des Studios erreichbar unter:').pack(anchor='w')
            ttk.Label(box,text=f'http://{pc.strip()}:{api_port}').pack(anchor='w',pady=8)
            ttk.Label(box,text='HTTP-Header für Companion (Zugriffsschlüssel vertraulich behandeln):').pack(anchor='w')
            entry=ttk.Entry(box,width=85);entry.pack(fill='x',pady=8)
            entry.insert(0,json.dumps({'Authorization':'Bearer '+token}))
            ttk.Label(box,text=f'Zugriff ist auf Server {peer.strip()} beschränkt. Studio danach neu starten.').pack(anchor='w')
        except ValueError as exc:messagebox.showerror('Companion-Server',str(exc))
    def check_updates(manual=False):
        if update_busy.is_set():
            return
        update_busy.set()
        update_label.set('Suche nach Updates …')
        def work():
            try:
                events.put(('update',(updates.check(),manual,None)))
            except Exception as exc:
                events.put(('update',(None,manual,str(exc))))
            finally:
                update_busy.clear()
        threading.Thread(target=work,daemon=True).start()
    def install_update():
        if update_busy.is_set():
            return
        if not update_info:
            return check_updates(True)
        if not messagebox.askyesno('Update herunterladen',f"Version {update_info['version']} ist verfügbar. Installer herunterladen?\nDeine Fahrten bleiben erhalten."):
            return
        update_busy.set()
        update_label.set('Update wird heruntergeladen …')
        def work():
            try:
                path=updates.download(dict(update_info),c.db.parent/'updates')
                events.put(('installer',str(path)))
            except Exception as exc:
                events.put(('update',(None,True,str(exc))))
            finally:
                update_busy.clear()
        threading.Thread(target=work,daemon=True).start()
    buttons = ttk.Frame(frame)
    # Reserve controls before the expanding list, including at high Windows DPI.
    buttons.pack(fill='x', before=tree, pady=(12,0))
    def start():
        try:
            ident=selected()
            run(lambda:c.play(ident))
        except ValueError as exc:
            status.set(str(exc))
    start_button=ttk.Button(buttons,text='▶  Fahrt starten',style='Accent.TButton',command=start)
    start_button.pack(side='right')
    for label, command in [('＋  Fahrt hinzufügen',add), ('Einstellungen',setup), ('■  Stopp',lambda:run(c.stop))]:
        ttk.Button(buttons,text=label,command=command).pack(side='left',padx=(0,8))
    toolbar=ttk.Frame(frame)
    toolbar.pack(fill='x',before=tree,pady=(8,0))
    update_label=tk.StringVar(value=f'Version {VERSION}')
    ttk.Label(toolbar,textvariable=update_label).pack(side='left')
    ttk.Button(toolbar,text='Anleitung',command=help_window).pack(side='right')
    update_button=ttk.Button(toolbar,text='Updates',command=install_update)
    update_button.pack(side='right',padx=8)
    startup_options=ttk.Frame(frame)
    startup_options.pack(fill='x',before=tree,pady=(8,0))
    prepare_var=tk.BooleanVar(value=c.setting('prepare_camera',True))
    repeat_var=tk.BooleanVar(value=c.setting('repeat_playback',True))
    ttk.Checkbutton(startup_options,text='Kamera + Spout beim Start vorbereiten',variable=prepare_var,command=lambda:c.set_setting('prepare_camera',prepare_var.get())).pack(side='left')
    ttk.Checkbutton(startup_options,text='Fahrt wiederholen',variable=repeat_var,command=lambda:c.set_setting('repeat_playback',repeat_var.get())).pack(side='left',padx=16)
    ttk.Label(frame,text='Welt-Verankerung einmal in VRChat wählen; dafür bietet OSC keinen Umschaltbefehl.',font=('Segoe UI',9),foreground='#a6b4cd').pack(anchor='w',before=tree)
    ttk.Label(frame,textvariable=status,wraplength=1000).pack(anchor='w',pady=(12,6),before=tree)
    ttk.Label(frame,text=f"Companion · {c.setting('api_host','127.0.0.1')}:{api_port}    /    OSC-Empfang · {c.receive_port}",font=('Segoe UI',9),foreground='#65738b').pack(anchor='w',before=tree)
    def monitor():
        while not quit_event.is_set():
            try:
                s=c.status()
                s['world_names']=c.worlds()
                events.put(('status',s))
            except Exception as exc:
                events.put(('error',str(exc)))
            quit_event.wait(2)
    def selection_changed(event=None):
        ids=tree.selection()
        available=any(str(p['id']) in ids and p['available'] for p in latest.get('paths',[]))
        start_button.configure(state='normal' if available and latest.get('configured') else 'disabled')
    tree.bind('<<TreeviewSelect>>',selection_changed)
    def refresh():
        try:
            while True:
                kind,data=events.get_nowait()
                if kind=='update':
                    info,manual,error=data
                    if error:
                        update_label.set('Update-Prüfung derzeit nicht erreichbar')
                        if manual:messagebox.showinfo('Updates',f'Die Update-Prüfung ist derzeit nicht möglich.\n{error}')
                    elif info:
                        update_info.clear();update_info.update(info)
                        update_label.set(f"Neue Version {info['version']} verfügbar")
                        update_button.configure(text='Update installieren')
                    else:
                        update_label.set(f'Version {VERSION} · aktuell')
                        if manual:messagebox.showinfo('Updates','Keine neuere vollständige Version verfügbar.')
                    continue
                if kind=='installer':
                    update_label.set('Update ist heruntergeladen und geprüft')
                    if messagebox.askyesno('Update installieren','Installer jetzt starten? Das Studio wird dafür geschlossen. Deine Fahrten bleiben erhalten.'):
                        subprocess.Popen([data],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                        close_window()
                        return
                    continue
                if kind=='error':
                    status.set(data)
                    continue
                latest.update(data)
                s=data
                connected=s['osc_connected']
                variables['connection'].set('● OSC verbunden' if connected else '● Warte auf OSC' if s['vrchat_running'] else '○ VRChat geschlossen')
                wid=s['world_id']
                variables['world'].set(s['world_names'].get(wid,'Welt erkannt') if wid else 'Welt wird geladen …' if s['vrchat_running'] else 'Noch keine Welt')
                loading={'preparing_camera':'Kamera + Spout öffnen …','stopping':'Fahrt wird gestoppt …','importing':'Auswahl wird geladen …','verifying':'Geladene Fahrt prüfen …'}
                variables['playback'].set(loading.get(s['load_state']) or ('▶ Fahrt läuft' if s['playing'] is True else '■ Gestoppt' if s['playing'] is False else 'Warte auf Status' if s['vrchat_running'] else 'Studio bereit'))
                if s['transport_error'] or s['error'] or s['discovery_error']:
                    status.set(s['transport_error'] or s['error'] or s['discovery_error'])
                elif not s['configured']:
                    status.set('Willkommen! Unter Einstellungen einmal den OSC-Port bestätigen, dann deine erste Fahrt hinzufügen.')
                elif not s['vrchat_running']:
                    status.set('Alles bereit. Starte VRChat, wenn du loslegen möchtest – die Erkennung läuft automatisch.')
                elif not connected:
                    status.set('VRChat erkannt. Warte auf OSC-Daten. OSC in VRChat aktivieren und den Rückmeldeport prüfen.')
                else:
                    status.set('VRChat-Verbindung bestätigt. Wähle eine zur aktuellen Welt passende Kamerafahrt.')
                keep=set()
                for p in s['paths']:
                    ident=str(p['id']);keep.add(ident)
                    values=(p['id'],p['name'],s['world_names'].get(p['world_id'],p['world_id']),'Bereit' if p['available'] else 'Andere Welt' if wid else 'Warte auf VRChat')
                    tags=() if p['available'] else ('unavailable',)
                    if tree.exists(ident):
                        tree.item(ident,values=values,tags=tags)
                    else:
                        tree.insert('','end',iid=ident,values=values,tags=tags)
                for ident in tree.get_children():
                    if ident not in keep:tree.delete(ident)
                selection_changed()
        except queue.Empty:
            pass
        finally:
            if not quit_event.is_set():root.after(150,refresh)
    def close_window():
        quit_event.set()
        root.destroy()
    root.protocol('WM_DELETE_WINDOW',close_window)
    root.report_callback_exception=lambda cls,exc,tb: status.set(str(exc))
    threading.Thread(target=monitor,daemon=True).start()
    def periodic_updates():
        if not quit_event.is_set():
            check_updates()
            root.after(6*60*60*1000,periodic_updates)
    root.after(1800,periodic_updates)
    refresh()
    root.mainloop()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--api-port', type=int, default=8766)
    parser.add_argument('--api-host', default=None)
    parser.add_argument('--receive-port', type=int, default=9001)
    parser.add_argument('--logs', type=Path, default=DEFAULT_LOGS)
    parser.add_argument('--db', type=Path, default=DB)
    parser.add_argument('--smoke-test', action='store_true',help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.smoke_test:
        import tkinter as tk
        root=tk.Tk();root.withdraw();root.update();root.destroy()
        print(f'VRC Kameracontroller {VERSION}: OK')
        return
    c = Controller(db=args.db, logs=args.logs, receive_port=args.receive_port, auto_port=True)
    c.advertise()
    server = None
    try:
        try:
            server = api(c,args.api_port,host=args.api_host or c.setting('api_host','127.0.0.1'))
        except OSError:
            c.error = f'Companion-Port {args.api_port} belegt. Fenster bleibt bereit; andere Instanz schließen und Tool neu starten oder --api-port verwenden.'
            if args.headless:
                raise
        if args.headless:
            while True:
                time.sleep(1)
        else:
            gui(c,args.api_port)
    finally:
        if server:
            server.shutdown()
            server.server_close()
        c.close()

if __name__ == '__main__':
    try:
        # Only one installed instance may own the Companion endpoint.
        if sys.platform=='win32':
            import ctypes
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.CreateMutexW.restype=ctypes.c_void_p
            instance_mutex=kernel.CreateMutexW(None,False,'Local\\VRC-Kameracontroller-Studio')
            if ctypes.get_last_error()==183:
                from tkinter import messagebox
                messagebox.showinfo('VRC Kameracontroller','Das Studio ist bereits geöffnet. Bitte das vorhandene Fenster verwenden.')
                sys.exit(0)
        main()
    except Exception as exc:
        import traceback
        DATA_DIR.mkdir(parents=True,exist_ok=True)
        logfile=DATA_DIR/'startup-error.log'
        logfile.write_text(traceback.format_exc(),encoding='utf-8')
        from tkinter import messagebox
        messagebox.showerror('VRC Kameracontroller',f'Das Studio konnte nicht gestartet werden.\n{exc}\n\nDetails: {logfile}')
        sys.exit(1)
