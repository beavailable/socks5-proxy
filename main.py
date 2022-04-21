#!/bin/env python3
import sys
import argparse
import socket
import asyncio
import logging


class SocksServer:

    def __init__(self, config):
        self._config = config
        self._log = logging.getLogger(SocksServer.__name__)
        self._log.setLevel(logging.INFO)

    async def start(self):
        server = await asyncio.start_server(self.handle_connection, self._config.host, self._config.port)
        async with server:
            for sock in server.sockets:
                address = sock.getsockname()
                self._log.info('listening on %s port %s', address[0], address[1])
            await server.serve_forever()

    async def handle_connection(self, reader, writer):
        async with SocketWrapper(reader, writer, self._config.timeout) as client:
            address = client.address()
            self._log.info('new client from %s port %s', address[0], address[1])
            try:
                await self.handle_connection_core(client)
            except Exception as e:
                self._log.info('unexpected exception with %s', type(e).__name__)

    async def handle_connection_core(self, client):
        # request
        # +-------+----------+----------+
        # |  VER  | NMETHODS | METHODS  |
        # +-------+----------+----------+
        # | 0x05  |    1     |  1-255   |
        # +-------+----------+----------+
        VERSION = 0x05
        ver, nmethods = await client.readexactly(2)
        if ver != VERSION or nmethods < 1:
            return
        await client.readexactly(nmethods)
        # response
        # +-------+----------+
        # |  VER  |  METHOD  |
        # +-------+----------+
        # | 0x05  |    1     |
        # +-------+----------+
        await client.write(bytes((0x05, 0x00)))
        # request
        # +-------+-------+-------+-------+----------+----------+
        # |  VER  |  CMD  |  RSV  | ATYP  | DST.ADDR | DST.PORT |
        # +-------+-------+-------+-------+----------+----------+
        # | 0x05  |   1   | 0x00  |   1   | Variable |    2     |
        # +-------+-------+-------+-------+----------+----------+
        CMD_CONNECT = 0x01
        ATYP_IPV4 = 0x01
        ATYP_DOMAIN = 0x03
        ATYP_IPV6 = 0x04
        ver, cmd, _, atyp = await client.readexactly(4)
        if ver != VERSION or cmd != CMD_CONNECT:
            return
        if atyp == ATYP_IPV4:
            host = await client.readipv4()
        elif atyp == ATYP_DOMAIN:
            host = await client.readstr(await client.readbyte())
        elif atyp == ATYP_IPV6:
            host = await client.readipv6()
        else:
            return
        port = await client.readshort()
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port, proto=socket.IPPROTO_TCP, flags=socket.AI_PASSIVE), 10)
        except Exception as e:
            self._log.warning('connect to %s port %s failed with %s', host, port, type(e).__name__)
            return
        async with SocketWrapper(reader, writer, self._config.timeout) as remote:
            self._log.info('connected to %s port %s', host, port)
            # response
            # +-------+-------+-------+-------+----------+----------+
            # |  VER  |  REP  |  RSV  | ATYP  | BND.ADDR | BND.PORT |
            # +-------+-------+-------+-------+----------+----------+
            # | 0x05  | 0x00  | 0x00  | 0x01  | Variable |    2     |
            # +-------+-------+-------+-------+----------+----------+
            await client.write(bytes((0x05, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00)))
            await asyncio.gather(self.forward(client, remote), self.forward(remote, client))

    async def forward(self, src, dst):
        BUF_SIZE = 65536
        while True:
            data = await src.read(BUF_SIZE)
            if not data:
                break
            await dst.write(data)


class SocketWrapper:

    def __init__(self, reader, writer, timeout):
        self._reader = reader
        self._writer = writer
        self._timeout = timeout

    def address(self):
        return self._writer.get_extra_info('peername')

    async def read(self, n):
        return await asyncio.wait_for(self._reader.read(n), self._timeout)

    async def readexactly(self, n):
        return await asyncio.wait_for(self._reader.readexactly(n), self._timeout)

    async def readbyte(self):
        data = await self.readexactly(1)
        return data[0]

    async def readshort(self):
        data = await self.readexactly(2)
        return (data[0] << 8) | data[1]

    async def readstr(self, n):
        data = await self.readexactly(n)
        return data.decode()

    async def readipv4(self):
        data = await self.readexactly(4)
        return socket.inet_ntop(socket.AF_INET, data)

    async def readipv6(self):
        data = await self.readexactly(16)
        return socket.inet_ntop(socket.AF_INET6, data)

    async def write(self, data):
        self._writer.write(data)
        await asyncio.wait_for(self._writer.drain(), self._timeout)

    async def close(self):
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except Exception:
            pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *ex):
        await self.close()


class Config:

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout


async def main():
    logging.basicConfig(format='%(asctime)s %(name)s.%(levelname)s: %(message)s')

    parser = argparse.ArgumentParser(allow_abbrev=False, add_help=False)

    general = parser.add_argument_group('general options')
    general.add_argument('-b', '--bind', default='localhost', help='bind address [default: localhost]')
    general.add_argument('-p', '--port', type=int, default=8000, help='port [default: 8000]')
    general.add_argument('-t', '--timeout', type=int, default=300, help='socket timeout in seconds [default: 300]')
    general.add_argument('-h', '--help', action='help', help='show this help message and exit')

    args = parser.parse_args()
    config = Config(args.bind, args.port, args.timeout)
    server = SocksServer(config)
    await server.start()


asyncio.run(main())
