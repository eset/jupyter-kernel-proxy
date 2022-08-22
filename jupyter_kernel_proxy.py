#!/usr/bin/env python
# -*- encoding: utf8 -*-
#
# Copyright (c) 2022 ESET spol. s r.o.
# Author: Marc-Etienne M.Léveillé <leveille@eset.com>
# See LICENSE file for redistribution.

import sys
import json
import hmac
import uuid
import hashlib
import datetime
import glob
import os
import six
from collections import namedtuple, OrderedDict
from operator import attrgetter

from jupyter_core.paths import jupyter_runtime_dir, jupyter_data_dir

import zmq
from tornado import ioloop
from zmq.eventloop import zmqstream

# As defined here: https://jupyter-client.readthedocs.io/en/stable/messaging.html#the-wire-protocol
DELIMITER = b"<IDS|MSG>"

SocketInfo = namedtuple("SocketInfo", (
    "name",
    "server_type",
    "client_type",
    "signed",
))

KERNEL_SOCKETS = (
    SocketInfo("hb",      zmq.REP,    zmq.REQ,    False),
    SocketInfo("iopub",   zmq.PUB,    zmq.SUB,    True),
    SocketInfo("control", zmq.ROUTER, zmq.DEALER, True),
    SocketInfo("stdin",   zmq.ROUTER, zmq.DEALER, True),
    SocketInfo("shell",   zmq.ROUTER, zmq.DEALER, True),
)

KERNEL_SOCKETS_NAMES = tuple(map(attrgetter("name"), KERNEL_SOCKETS))

SocketGroup = namedtuple("SocketGroup", KERNEL_SOCKETS_NAMES)


class AbstractProxyKernel(object):
    def __init__(self, config, role, zmq_context=zmq.Context.instance()):
        if role not in ("server", "client"):
            raise ValueError("role value must be 'server' or 'client'")
        self.role = role
        self.config = config.copy()
        self.zmq_context = zmq_context
        if role == "server":
            self._create_sockets("bind")
        elif role == "client":
            self._create_sockets("connect")

    def _url_for_port(self, port):
        return "{:s}://{:s}:{:d}".format(
            self.config.get("transport", "tcp"),
            self.config.get("ip", "localhost"),
            port
        )

    def _create_sockets(self, bind_or_connect):
        if bind_or_connect not in ("bind", "connect"):
            raise ValueError("bind_or_connect must be 'bind' or 'connect'")
        ctx = self.zmq_context
        zmq_type_key = self.role + "_type"
        self.sockets = SocketGroup(*[ctx.socket(getattr(s, zmq_type_key)) for s in KERNEL_SOCKETS])
        for i, sock in enumerate(KERNEL_SOCKETS):
            sock_bind = getattr(self.sockets[i], bind_or_connect)
            sock_bind(self._url_for_port(self.config.get(sock.name + "_port", 0)))
            if getattr(sock, zmq_type_key) == zmq.SUB:
                self.sockets[i].setsockopt(zmq.SUBSCRIBE, b'')
        self.streams = SocketGroup(*map(zmqstream.ZMQStream, self.sockets))

    def sign(self, message, key=None):
        if key is None:
            key = self.config.get("key")
        h = hmac.HMAC(six.ensure_binary(key), digestmod=hashlib.sha256)
        for m in message:
            h.update(m)
        return six.ensure_binary(h.hexdigest())


class ProxyKernelClient(AbstractProxyKernel):
    def __init__(self, config, role="client", zmq_context=zmq.Context.instance()):
        super(ProxyKernelClient, self).__init__(config, role, zmq_context)

InterceptionFilter = namedtuple("InterceptionFilter", ("stream_type", "msg_type", "callback"))

class ProxyKernelServer(AbstractProxyKernel):

    def __init__(self, config, role="server", zmq_context=zmq.Context.instance()):
        super(ProxyKernelServer, self).__init__(config, role, zmq_context)
        self.filters = []
        self.session_id = None
        self.proxy_target = None

    def _proxy_to(self, other_stream, socktype=None, validate_using=None, resign_using=None):
        # request
        # Notebook -> ProxyServer -> ProxyClient -> Real kernel
        # reply
        # Notebook <- ProxyServer <- ProxyClient <- Real kernel
        is_reply = other_stream in self.streams
        if is_reply:
            validate_using = validate_using or self.proxy_target.config.get("key")
            resign_using = resign_using or self.config.get("key")
        else:
            validate_using = validate_using or self.config.get("key")
            resign_using = resign_using or self.proxy_target.config.get("key")
        def handler(data):
            if socktype.signed and validate_using:
                sig = data[data.index(DELIMITER)+1]
                if sig != self.sign(data[data.index(DELIMITER)+2:], validate_using):
                    raise ValueError("Wrong sig")
            if not self.session_id and is_reply:
                # We catch the session ID here so that if we inject custom
                # messages we can use `make_multipart_message` to get a one with
                # the right ID
                header = json.loads(data[data.index(DELIMITER)+2])
                self.session_id = header.get("session")
            for stream_type, msg_type, callback in self.filters:
                header = json.loads(data[data.index(DELIMITER)+2])
                if stream_type == socktype and msg_type == header.get("msg_type"):
                    new_data = callback(self, other_stream, data)
                    if new_data is None:
                        return
                    else:
                        data = new_data
            if socktype.signed and resign_using:
                sig = self.sign(data[data.index(DELIMITER)+2:], resign_using)
                data[data.index(DELIMITER)+1] = sig
            other_stream.send_multipart(data)
            other_stream.flush()
        return handler

    def set_proxy_target(self, proxy_client):
        if self.proxy_target is not None:
            for stream in self.proxy_target.streams:
                stream.stop_on_recv()
        self.proxy_target = proxy_client
        for i, socktype in enumerate(KERNEL_SOCKETS):
            if socktype.server_type != zmq.PUB:
                self.streams[i].on_recv(
                    self._proxy_to(proxy_client.streams[i], socktype=socktype)
                )
            if socktype.client_type != zmq.PUB:
                proxy_client.streams[i].on_recv(
                    self._proxy_to(self.streams[i], socktype=socktype)
                )

    def intercept_message(self, stream_type=None, msg_type=None, callback=None):
        if stream_type in KERNEL_SOCKETS_NAMES:
            stream_type = KERNEL_SOCKETS[KERNEL_SOCKETS_NAMES.index(stream_type)]
        if stream_type not in KERNEL_SOCKETS:
            raise ValueError("stream_type should be one of " + ", ".join(KERNEL_SOCKETS_NAMES))
        if not callable(callback):
            raise ValueError("callback must be callable")
        self.filters.append(InterceptionFilter(stream_type, msg_type, callback))

    def make_multipart_message(self, msg_type, content={}, parent_header={}, metadata={}):
        header = {
            "date": datetime.datetime.now().isoformat(),
            "msg_id": str(uuid.uuid4()),
            "username": "kernel",
            "session": self.session_id,
            "msg_type": msg_type,
            "version": "5.0",
        }
        payload = [
            six.ensure_binary(json.dumps(c))
            for c in (header, parent_header, metadata, content)
        ]
        return [DELIMITER, self.sign(payload)] + payload


class KernelProxyManager(object):
    def __init__(self, server):
        if isinstance(server, ProxyKernelServer):
            self.server = server
        else:
            self.server = ProxyKernelServer(server)
        self.server.intercept_message("shell", "execute_request", self._catch_proxy_magic_command)
        self.magic_command = "%proxy"

        self._kernel_info_requests = []
        self.server.intercept_message("shell", "kernel_info_request", self._on_kernel_info_request)
        self.server.intercept_message("shell", "kernel_info_reply", self._on_kernel_info_reply)

        self.connect_to_last()

    def _catch_proxy_magic_command(self, server, target_stream, data):
        header = json.loads(data[data.index(DELIMITER)+2])
        content = json.loads(data[data.index(DELIMITER)+5])
    
        def send(text, stream="stdout"):
            server.streams.iopub.send_multipart(server.make_multipart_message(
                "stream", { "name": stream, "text": text }, parent_header=header
            ))

        if content.get("code").startswith(self.magic_command):
            server.streams.iopub.send_multipart(server.make_multipart_message(
                "status", { "execution_state": "busy"}, parent_header=header
            ))

            argv = list(filter(lambda x: len(x) > 0, content.get("code").split(" ")))

            def send_usage():
                send("Usage: {:s} [ list | connect <file>]".format(self.magic_command))

            if len(argv) < 2:
                send_usage()
            elif argv[1] == "list":
                self.update_running_kernels()
                send(self._formatted_kernel_list())
            elif argv[1] == "connect":
                if len(argv) > 2:
                    try:
                        self.connect_to(argv[2], request_kernel_info=True)
                        send("Connecting to " + self.connected_kernel_name)
                    except ValueError:
                        send("Unknown kernel " + argv[2], "stderr")
                else:
                    send_usage()
            else:
                send("Unknown subcommand " + argv[1], "stderr")
                send_usage()

            server.streams.iopub.send_multipart(server.make_multipart_message(
                "status", { "execution_state": "idle"}, parent_header=header
            ))

            server.streams.shell.send_multipart(data[:data.index(DELIMITER)] +
                server.make_multipart_message(
                "execute_reply", {"status": "ok"}, parent_header=header
            ))
            return None
        else:
             return data

    def _formatted_kernel_list(self):
        return "\n".join(
            " {:s} {:s} ({:s})".format(
                (filename == self.connected_kernel_name) and "*" or " ",
                filename,
                config.get("kernel_name") or "no name"
            )
            for filename, config in self.kernels.items()
        )

    def update_running_kernels(self):
        "Update self.kernels with an ordored dict where "
        files = glob.glob(os.path.join(jupyter_runtime_dir(), "kernel-*.json"))
        self.kernels = OrderedDict()
        for path in reversed(sorted(files, key=lambda f: os.stat(f).st_atime)):
            try:
                filename = os.path.basename(path)
                with open(path, "r") as f:
                    self.kernels[filename] = json.load(f)
            except:
                # print something to stderr
                pass
        return self.kernels

    def _on_kernel_info_request(self, server, target_stream, data):
        header = json.loads(data[data.index(DELIMITER)+2])
        self._kernel_info_requests.append(header.get("msg_id"))
        ioloop.IOLoop.current().call_later(3, self._send_proxy_kernel_info, data)
        return data

    def _on_kernel_info_reply(self, server, target_stream, data):
        parent_header = json.loads(request[request.index(DELIMITER)+3])
        if parent_header.get("msg_id") in self._kernel_info_requests:
            self._kernel_info_requests.remove(parent_header.get("msg_id"))
        else:
            self._kernel_info_requests.pop(0)
        return data

    def _send_proxy_kernel_info(self, request):
        parent_header = json.loads(request[request.index(DELIMITER)+2])
        if not parent_header.get("msg_id") in self._kernel_info_requests:
            return
        msg = self.server.make_multipart_message("kernel_info_reply", {
            "status": "ok",
            "protocol_version": "5.3",
            "implementation": "proxy",
            "banner": "Jupyter kernel proxy. Not connected or connected to unresponsive kernel. Use %proxy to connect.",
            "language_info": {
                "name": "magic",
            },
        }, parent_header=parent_header)
        self.server.streams.shell.send_multipart(request[:request.index(DELIMITER)] + msg)
        self.server.streams.iopub.send_multipart(self.server.make_multipart_message(
            "status", { "execution_state": "idle"}, parent_header=parent_header
        ))
        self._kernel_info_requests.remove(parent_header.get("msg_id"))

    def connect_to_last(self):
        self.update_running_kernels()
        # Kernel at index 0 is highly likely to be the newly started proxy
        # _server_ (self.server) so we pick the one after that.
        self.connect_to(list(self.kernels.keys())[1])

    def connect_to(self, kernel_file_name, request_kernel_info=False):
        matching = next((n for n in self.kernels if kernel_file_name in n), None)
        if matching is None:
            raise ValueError("Unknown kernel " + kernel_file_name)
        self.connected_kernel_name = matching
        self.connected_kernel = ProxyKernelClient(self.kernels[matching])
        self.server.set_proxy_target(self.connected_kernel)
        if request_kernel_info:
            self.connected_kernel.streams.shell.send_multipart(
                self.server.make_multipart_message("kernel_info_request")
            )

def install():
    user_kernels_dir = os.path.join(jupyter_data_dir(), "kernels")
    if not os.path.exists(user_kernels_dir):
        os.mkdir(user_kernels_dir, 0o700)
    proxy_kernel_dir = os.path.join(user_kernels_dir, "proxy")
    if not os.path.exists(proxy_kernel_dir):
        os.mkdir(proxy_kernel_dir, 0o700)
    with open(os.path.join(proxy_kernel_dir, "kernel.json"), "w") as f:
        json.dump({
            "argv": [
                sys.executable,
                "-m",
                "jupyter_kernel_proxy",
                "start",
                "{connection_file}"
            ],
            "display_name": "Existing session",
        }, f)

def start(connection_file):
    loop = ioloop.IOLoop.current()
    
    with open(connection_file) as f:
        notebook_config = json.load(f)
    
    proxy_manager = KernelProxyManager(notebook_config)

    loop.start()

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "install":
        install()
    elif len(sys.argv) > 2 and sys.argv[1] == "start":
        start(sys.argv[2])
    else:
        print("Usage: {:s} [install | start <connection_file>]".format(sys.argv[0]))

if __name__ == "__main__":
    main()
