"""
The module implements the Walkie-Talkie WF countermeasure proposed by Wang .
"""
from obfsproxy.transports.wfpadtools import const
from obfsproxy.transports.wfpadtools.wfpad import WFPadTransport

import os
import pickle
import time

import obfsproxy.common.log as logging
from obfsproxy.transports.wfpadtools import histo
from obfsproxy.transports.wfpadtools.common import deferLater

from twisted.internet import reactor
from twisted.internet.protocol import Protocol, Factory
from twisted.internet.endpoints import TCP4ServerEndpoint


log = logging.get_obfslogger()


class WalkieTalkieTransport(WFPadTransport):
    """Implementation of the Walkie-Talkie countermeasure.

    It extends the BasePadder by choosing a constant probability distribution
    for time, and a constant probability distribution for packet lengths. The
    minimum time for which the link will be padded is also specified.
    """
    def __init__(self):
        super(WalkieTalkieTransport, self).__init__()

        self._burst_directory = const.WT_BASE_DIR
        self._decoy_directory = const.WT_DECOY_DIR

        # Set constant length for messages
        self._length = const.MPU
        self._lengthDataProbdist = histo.uniform(self._length)

        # padding sequence to be used
        self._pad_seq = []

        # Start listening for URL signals
        self._port = const.WT_PORT
        self._initializeWTListener()

        self._initializeWTState()

    @classmethod
    def register_external_mode_cli(cls, subparser):
        """Register CLI arguments for Walkie-Talkie parameters."""
        subparser.add_argument("--psize",
                               required=False,
                               type=int,
                               help="Length of messages to be transmitted"
                                    " (Default: MTU).",
                               dest="psize")
        subparser.add_argument("--port",
                               required=False,
                               type=int,
                               help="Network port to listen for webpage identification notifications.",
                               dest="port")
        subparser.add_argument("--decoy",
                               required=False,
                               type=str,
                               help="Directory containing webpage decoy burst sequences.",
                               dest="decoy")
        subparser.add_argument("--bursts",
                               required=False,
                               type=str,
                               help="Directory containing webpage burst sequences.",
                               dest="bursts")
        super(WalkieTalkieTransport, cls).register_external_mode_cli(subparser)

    @classmethod
    def validate_external_mode_cli(cls, args):
        """Assign the given command line arguments to local variables."""
        super(WalkieTalkieTransport, cls).validate_external_mode_cli(args)

        if args.psize:
            cls._length = args.psize
        if args.port:
            cls._port = args.port
        if args.decoy:
            cls._decoy_directory = args.decoy
        if args.bursts:
            cls._burst_directory = args.bursts

    def _initializeWTListener(self):
        if self.weAreClient:
            self._listener = WalkieTalkieListener(self._port, self)
            log.warning("[walkie-talkie - %s] starting WT listener on port %d", self.end, self._port)
            self._listener.listen()

    def _initializeWTState(self):
        self._burst_count = 0
        self._pad_count = 0
        if self.weAreClient:    # client begins in Talkie mode
            self._talkie = True
        else:                   # bridge begins in Walkie mode
            self._talkie = False

    def receiveSessionPageId(self, id):
        """Handle receiving new session page information (url)"""
        log.debug("[walkie-talkie] received url id for new session, %s", id)
        id = id.split("://")[1] if '://' in id else id  # remove protocol text
        self._setPadSequence(id)
        # if we are the client, relay the session page information to the bridge's PT
        if self.weAreClient:
            log.debug("[walkie-talkie - %s] relaying session url to bridge", self.end)
            self.sendControlMessage(const.OP_WT_PAGEID, [id])

    def _setPadSequence(self, id):
        """Load the burst sequence and decoy burst sequence for a particular webpage
        then compute the number of padding packets required for each incoming/outgoing burst"""
        rbs = self._loadSequence(id, self._burst_directory)
        dbs = self._loadSequence(id, self._decoy_directory)
        pad_seq = []
        for index, _ in enumerate(dbs):
            if len(rbs) < index:
                break
            pair = (max(0, dbs[index][0] - rbs[index][0]),
                    max(0, dbs[index][1] - rbs[index][1]))
            pad_seq.append(pair)
        self._pad_seq = pad_seq

    def _loadSequence(self, id, directory):
        """Load a burst sequence from a pickle file"""
        seq = []
        fname = os.path.join(directory, id, ".pkl")
        if os.path.exists(fname):
            with open(fname) as fi:
                seq = pickle.load(fi)
        else:
            log.info('[walkie-talkie - %s] unable to load sequence for %s from %s', self.end, id, directory)
        return seq

    def _is_padding(self, data):
        """Check if all messages in some data are padding messages"""
        # Make sure there actually is data to be parsed
        if (data is None) or (len(data) == 0):
            return True
        # Try to extract protocol messages
        msgs = []
        try:
            msgs = self._msgExtractor.extract(data)
        except Exception, e:
            log.exception("[wfpad - %s] Exception extracting "
                          "messages from stream: %s", self.end, str(e))
        for msg in msgs:
            # If any message in data is not a padding packet, return false.
            if not (msg.flags & const.FLAG_PADDING):
                return False
        return True

    def whenReceivedUpstream(self, data):
        """Switch to talkie mode if outgoing packet is first in a new burst
        dont consider padding messages when mode switching"""
        if not self._talkie:
            self._burst_count += 1
            self._pad_count = 0
            self._talkie = True
            log.info('[walkie-talkie - %s] switching to Talkie mode', self.end)

    def whenReceivedDownstream(self, data):
        """Switch to walkie mode if incoming packet is first in a new burst
        dont consider padding messages when mode switching"""
        if self._talkie:
            self._burst_count += 1
            self._pad_count = 0
            self._talkie = False
            log.info('[walkie-talkie - %s] switching to Walkie mode', self.end)

    def getCurrentBurstPaddingTarget(self):
        """Retrieve the number of padding messages that should be sent
        so as to achieve WT mold-padding for the current burst"""
        burst_pair_no = self._burst_count//2
        pad_pair = self._pad_seq[burst_pair_no] if burst_pair_no < len(self._pad_seq) else (0, 0)
        pad_target = pad_pair[0] if self.weAreClient else pad_pair[1]
        return pad_target

    def flushBuffer(self):
        """Overwrite WFPadTransport flushBuffer.
        In case the buffer is not empty, the buffer is flushed and we send
        these data over the wire. However, if the buffer is empty we immediately
        send the required number of padding messages for the burst.
        """
        dataLen = len(self._buffer)

        # If data buffer is empty and the PT is currently in talkie mode, send padding immediately
        if dataLen <= 0 & self._talkie:
            pad_target = self.getCurrentBurstPaddingTarget() - self._pad_count
            log.debug("[walkie-talkie - %s] buffer is empty, send mold padding (%d).", self.end, pad_target)
            while pad_target > 0:
                self.sendIgnore()
                pad_target -= 1
            return

        log.debug("[walkie-talkie - %s] %s bytes of data found in buffer."
                  " Flushing buffer.", self.end, dataLen)
        payloadLen = self._lengthDataProbdist.randomSample()

        # INF_LABEL = -1 means we don't pad packets (can be done in crypto layer)
        if payloadLen is const.INF_LABEL:
            payloadLen = const.MPU if dataLen > const.MPU else dataLen
        msgTotalLen = payloadLen + const.MIN_HDR_LEN

        self.session.consecPaddingMsgs = 0

        # If data in buffer fills the specified length, we just
        # encapsulate and send the message.
        if dataLen > payloadLen:
            self.sendDataMessage(self._buffer.read(payloadLen))

        # If data in buffer does not fill the message's payload,
        # pad so that it reaches the specified length.
        else:
            paddingLen = payloadLen - dataLen
            self.sendDataMessage(self._buffer.read(), paddingLen)
            log.debug("[walkie-talkie - %s] Padding message to %d (adding %d).", self.end, msgTotalLen, paddingLen)

        log.debug("[walkie-talkie - %s] Sent data message of length %d.", self.end, msgTotalLen)
        self.session.lastSndDataDownstreamTs = self.session.lastSndDownstreamTs = time.time()

        # schedule next call to flush the buffer
        dataDelay = self._delayDataProbdist.randomSample()
        self._deferData = deferLater(dataDelay, self.flushBuffer)
        log.debug("[walkie-talkie - %s] data waiting in buffer, flushing again "
                  "after delay of %s ms.", self.end, dataDelay)

    def sendIgnore(self, paddingLength=None):
        """Overwrite sendIgnore (sendPadding) function so
        as to set a hard limit on the number of padding messages are sent per burst"""
        if self._talkie:    # only send padding when in Talkie mode
            pad_target = self.getCurrentBurstPaddingTarget()
            if self._pad_count < pad_target:
                WFPadTransport.sendIgnore(self, paddingLength)
                self._pad_count += 1
                log.debug("[walkie-talkie] sent burst padding. running count = %d", self._pad_count)


class WalkieTalkieClient(WalkieTalkieTransport):
    """Extend the TamarawTransport class."""

    def __init__(self):
        """Initialize a TamarawClient object."""
        WalkieTalkieTransport.__init__(self)


class WalkieTalkieServer(WalkieTalkieTransport):
    """Extend the TamarawTransport class."""

    def __init__(self):
        """Initialize a TamarawServer object."""
        WalkieTalkieTransport.__init__(self)


class WalkieTalkieListener(object):
    """The walkie-talkie listener listens for incoming connection from the tor crawler/browser
    The crawler/browser should send the url/webpage identifier to this listener when beginning a browsing session
    This allows the proxy to identify what decoy should be used for mold-padding
    """
    class _ServerProtocol(Protocol):
        """Protocol handles connection establishment, loss, and data received events"""
        def __init__(self, factory, transport):
            self._factory = factory
            self._transport = transport

        def connectionMade(self):
            log.warning('[wt-listener]: making connection with crawler')

        def connectionLost(self, reason):
            log.warning('[wt-listener]: connection to crawler closed')

        def dataReceived(self, data):
            if data:
                log.debug('[wt-listener]: received new webpage session notification from crawler')
                self._transport.receiveSessionPageId(data)

    class _ServerFactory(Factory):
        """Builds protocols for handling incoming connections to WT listener"""
        _listener = None

        def __init__(self, listener):
            self._listener = listener

        def buildProtocol(self, addr):
            return self._listener._ServerProtocol(self, self._listener._transport)

    def __init__(self, port, transport):
        self._transport = transport
        self._port = port
        self._ep = TCP4ServerEndpoint(reactor, self._port, interface="127.0.0.1")
        self._crawler = None

    def listen(self):
        try:
            d = self._ep.listen(self._ServerFactory(self))
            d.addCallback(lambda a: self.setCrawler(a))
        except Exception, e:
            log.exception("[walkie-talkie - %s] Error when listening on port %d:", self._transport.end, self._port, e)

    def setCrawler(self, obj):
        self._crawler = obj