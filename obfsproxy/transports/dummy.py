#!/usr/bin/python
# -*- coding: utf-8 -*-

""" This module contains an implementation of the 'dummy' transport. """

from obfsproxy.transports.base import BaseTransport

import obfsproxy.common.log as logging
log = logging.get_obfslogger()


class DummyTransport(BaseTransport):
    """
    Implements the dummy protocol. A protocol that simply proxies data
    without obfuscating them.
    """

    def __init__(self):
        """
        If you override __init__, you ought to call the super method too.
        """
        super(DummyTransport, self).__init__()

    def receivedDownstream(self, data):
        """
        Got data from downstream; relay them upstream.
        """
        log.info('recieved {} bytes of data from downstream'.format(len(data)))
        self.circuit.upstream.write(data.read())

    def receivedUpstream(self, data):
        """
        Got data from upstream; relay them downstream.
        """
        log.info('recieved {} bytes of data from upstream'.format(len(data)))
        self.circuit.downstream.write(data.read())

class DummyClient(DummyTransport):

    """
    DummyClient is a client for the 'dummy' protocol.
    Since this protocol is so simple, the client and the server are identical and both just trivially subclass DummyTransport.
    """

    pass


class DummyServer(DummyTransport):

    """
    DummyServer is a server for the 'dummy' protocol.
    Since this protocol is so simple, the client and the server are identical and both just trivially subclass DummyTransport.
    """

    pass


