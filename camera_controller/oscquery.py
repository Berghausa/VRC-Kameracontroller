"""Advertise a dedicated OSC receiver without taking another application's port."""
import json
import socket
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zeroconf import Zeroconf, ServiceInfo, IPVersion, ServiceBrowser


class Advertisement:
    def __init__(self, controller):
        self.controller = controller
        self.closed = threading.Event()
        self.targets = {}
        name = f'BeerHideout-Camera-{controller.receive_port}'
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path == '/?HOST_INFO':
                    payload = dict(NAME=name, OSC_IP='127.0.0.1', OSC_PORT=controller.receive_port,
                                   OSC_TRANSPORT='UDP', EXTENSIONS={'ACCESS': True, 'VALUE': True})
                else:
                    payload = {'FULL_PATH': '/', 'ACCESS': 0, 'CONTENTS': {
                        'dolly': {'FULL_PATH': '/dolly', 'CONTENTS': {
                            'Play': {'FULL_PATH': '/dolly/Play', 'TYPE': 'T', 'ACCESS': 2}}},
                        'avatar': {'FULL_PATH': '/avatar', 'CONTENTS': {
                            'change': {'FULL_PATH': '/avatar/change', 'TYPE': 's', 'ACCESS': 2},
                            'parameters': {'FULL_PATH': '/avatar/parameters', 'ACCESS': 2}}},
                        'usercamera': {'FULL_PATH': '/usercamera', 'CONTENTS': {
                            'Mode': {'FULL_PATH': '/usercamera/Mode', 'TYPE': 'i', 'ACCESS': 2},
                            'Pose': {'FULL_PATH': '/usercamera/Pose', 'TYPE': 'ffffff', 'ACCESS': 2}}}}}
                    if self.path != '/':
                        node = payload
                        try:
                            for part in self.path.split('?')[0].strip('/').split('/'):
                                node = node['CONTENTS'][part]
                            payload = node
                        except KeyError:
                            self.send_error(404)
                            return
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.zc = Zeroconf(ip_version=IPVersion.V4Only)
        self.services = []
        try:
            for kind, port in [('_oscjson._tcp.local.', self.server.server_port), ('_osc._udp.local.', controller.receive_port)]:
                info = ServiceInfo(kind, name+'.'+kind, addresses=[socket.inet_aton('127.0.0.1')],
                                   port=port, server=name+'.local.')
                self.zc.register_service(info)
                self.services.append(info)
            self.browser = ServiceBrowser(self.zc, '_oscjson._tcp.local.', self)
            threading.Thread(target=self.poll, daemon=True).start()
        except Exception:
            self.close()
            raise

    def add_service(self, zc, kind, name):
        if 'VRChat-Client' in name:
            info=zc.get_service_info(kind,name,timeout=1000)
            if info:
                self.targets[name]=info.port

    def update_service(self, zc, kind, name):
        self.add_service(zc,kind,name)

    def remove_service(self, zc, kind, name):
        self.targets.pop(name,None)

    def poll(self):
        while not self.closed.is_set():
            for port in list(self.targets.values()):
                try:
                    base=f'http://127.0.0.1:{port}'
                    with urllib.request.urlopen(base+'/?HOST_INFO',timeout=1) as response:
                        host=json.load(response)
                    if not host.get('NAME','').startswith('VRChat-Client'):
                        continue
                    with urllib.request.urlopen(base+'/dolly/Play',timeout=1) as response:
                        node=json.load(response)
                    value=node.get('VALUE',[None])[0]
                    if isinstance(value,bool):
                        with self.controller.lock:
                            self.controller.playing=value
                            self.controller.feedback_at=time.time()
                            self.controller.query_at=time.time()
                        if self.controller.setting('osc_port') is None:
                            self.controller.configure(host['OSC_PORT'])
                except (OSError, ValueError, KeyError, IndexError):
                    pass
            self.closed.wait(.4)

    def close(self):
        self.closed.set()
        if hasattr(self,'browser'):
            self.browser.cancel()
        for info in self.services:
            self.zc.unregister_service(info)
        self.zc.close()
        self.server.shutdown()
        self.server.server_close()
