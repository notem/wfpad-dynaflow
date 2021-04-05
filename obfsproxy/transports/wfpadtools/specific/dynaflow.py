"""
This module implements the BuFLO countermeasure proposed by Dyer et al.
"""
import time

import obfsproxy.common.log as logging
from obfsproxy.transports.wfpadtools import histo
from obfsproxy.transports.wfpadtools import const
from obfsproxy.transports.wfpadtools.wfpad import WFPadTransport

from twisted.internet import reactor
from twisted.internet.protocol import Protocol, Factory
from twisted.internet.endpoints import TCP4ServerEndpoint

import struct

log = logging.get_obfslogger()



class DynaflowTransport(WFPadTransport):
    """Implementation of the Dynaflow countermeasure.

    It extends the BasePadder by choosing a constant probability distribution
    for time, and a constant probability distribution for packet lengths. The
    minimum time for which the link will be padded is also specified.
    """

    def __init__(self):
        super(DynaflowTransport, self).__init__()

        # Defaults for Dynaflow specifications.
        self._first_time_gap = 12
        self._poss_time_gaps = [12, 5]
        self._switch_sizes = [400, 1200, 2000, 2800]
        self._block_size = 400
        self._subseq_length = 4
        self._memory = 100
        self._length = const.MPU
        self._curr_time = time.time()

        self._time_gap = self._first_time_gap
        self._no_sent = 0
        self._no_recv = 0
        self._past_times = []
        self._queue_times = []

        # possible end-sizes (low to high)
        k = 1.2
        self._end_sizes = []
        for i in range(0, 9999):
            t = (k ** i) * self._subseq_length
            if t > 10000000:
                break
            self._end_sizes.append(round(t))

        self._end_size = self._end_sizes[-1]

        # Set constant length for messages
        self._lengthDataProbdist = histo.uniform(self._length)

        # dynaflow stops when total packets sent is equal to some m^i value.
        def stopConditionHandler(s):
            return self._end_size <= (self._no_sent + self._no_recv)

        self.stopCondition = stopConditionHandler

        self._initializeCrawlerListener()
        #self._configure_padding()


    @classmethod
    def register_external_mode_cli(cls, subparser):
        """Register CLI arguments for BuFLO parameters."""
        #subparser.add_argument("--period",
        #                       required=False,
        #                       type=float,
        #                       help="Time rate at which transport sends "
        #                            "messages (Default: 12ms).",
        #                       dest="period")
        #subparser.add_argument("--psize",
        #                       required=False,
        #                       type=int,
        #                       help="Length of messages to be transmitted"
        #                            " (Default: MTU).",
        #                       dest="psize")
        #subparser.add_argument("--mintime",
        #                       required=False,
        #                       type=int,
        #                       help="Minimum padding time per visit."
        #                            " (Default: no minimum time).",
        #                       dest="mintime")

        super(DynaflowTransport, cls).register_external_mode_cli(subparser)

    def _initializeCrawlerListener(self):
        if self.weAreClient:
            self._listener = WalkieTalkieListener(9149, self)
            log.warning("[dynaflow - %s] starting listener on port 9149", self.end)
            self._listener.listen()

    @classmethod
    def validate_external_mode_cli(cls, args):
        """Assign the given command line arguments to local variables.

        BuFLO pads at a constant rate `period` and pads the packets to a
        constant size `psize`.
        """
        super(DynaflowTransport, cls).validate_external_mode_cli(args)

        #if args.mintime:
        #    cls._mintime = int(args.mintime)
        #if args.period:
        #    cls._period = args.period
        #if args.psize:
        #    cls._length = args.psize

    def _find_new_time_gap(self):
        """Finds new time gap for defended sequence."""
    
        # find average time gap
        if len(self._past_times) >= self._memory:
            average_time_gap = float(self._past_times[-1] - self._past_times[-self._memory]) / (self._memory - 1)
            average_time_gap *= 1000
        elif len(self._past_times) > 10:
            average_time_gap = float(self._past_times[-1] - self._past_times[0]) / (len(self._past_times) - 1)
            average_time_gap *= 1000
        else:
            average_time_gap = self._time_gap
    
        # find expected time gap
        exp_packet_num = self._block_size + 1 * (float(self._curr_time - self._past_times[-1])*1000) / average_time_gap
        exp_time_gap = self._block_size / exp_packet_num * average_time_gap
    
        # choose next timeg gap
        min_diff = 99999
        for i in range(0, len(self._poss_time_gaps)):
            if min_diff > abs(exp_time_gap - self._poss_time_gaps[i]):
                min_diff = abs(exp_time_gap - self._poss_time_gaps[i])
            else:
                self._time_gap = self._poss_time_gaps[i - 1]
                return
        self._time_gap = self._poss_time_gaps[-1]
        log.debug(f"New timegap {self._time_gap} (from expected {exp_time_gap})")

    def _configure_padding(self):
        if self.weAreClient:
            period = self._time_gap * self._subseq_length
        else:
            period = (self._time_gap * self._subseq_length) / (self._subseq_length - 1)
        self.constantRatePaddingDistrib(period)

    def onSessionStarts(self, sessId):
        """configure the initial padding state"""
        #log.debug("[dynaflow {}] - params: mintime={}, period={}, psize={}"
        #          .format(self.end, self._mintime, self._period, self._length))
        self._time_gap = self._first_time_gap
        self._no_sent = 0
        self._no_recv = 0
        self._past_times = []
        self._queue_times = []
        self._configure_padding()
        WFPadTransport.onSessionStarts(self, sessId)

    def onSessionEnds(self, sessId):
        """find the correct endsize for end padding"""
        pkt_count = self._no_sent + self._no_recv
        for size in self._end_sizes:
            if pkt_count < size:
                self._end_size = size
                break
        WFPadTransport.onSessionEnds(self, sessId)

    def whenReceivedUpstream(self, data):
        """count number of packets sent upstream"""
        self._past_times.append(time.time())
        self._queue_times.append(time.time())

    def whenReceivedDownstream(self, data):
        """count number of packets recieved downstream"""
        self._no_recv += 1

    def sendDataMessage(self, payload="", paddingLen=0):
        """Send data message."""
        log.debug("[wfpad - %s] Sending data message with %s bytes payload"
                  " and %s bytes padding", self.end, len(payload), paddingLen)
        self._no_sent += 1
        if self.weAreClient:
            if self._no_sent * self._subseq_length in self._switch_sizes:
                self._find_new_time_gap()
                self._configure_padding()
        if not self.weAreClient:
            if (self._no_sent//(self._subseq_length-1)) * self._subseq_length in self._switch_sizes:
                self._find_new_time_gap()
                self._configure_padding()
        self._curr_time = time.time()
        timeoffset = int(abs(self._curr_time-self._queue_times[-1])*1000)
        self.sendDownstream(self._msgFactory.new(payload, paddingLen, queueTime=timeoffset))

    def sendIgnore(self, paddingLength=None):
        self._no_sent += 1
        if self.weAreClient:
            if self._no_sent * self._subseq_length in self._switch_sizes:
                self._find_new_time_gap()
                self._configure_padding()
        if not self.weAreClient:
            if (self._no_sent//(self._subseq_length-1)) * self._subseq_length in self._switch_sizes:
                self._find_new_time_gap()
                self._configure_padding()
        super(DynaflowTransport, self).sendIgnore(paddingLength)

    def processMessages(self, data):
        """Extract WFPad protocol messages.

        Data is written to the local application and padding messages are
        filtered out.
        """
        log.debug("[wfpad - %s] Parse protocol messages from stream.", self.end)

        # Make sure there actually is data to be parsed
        if (data is None) or (len(data) == 0):
            return None

        # Try to extract protocol messages
        msgs = []
        try:
            msgs = self._msgExtractor.extract(data)
        except Exception as e:
            log.exception("[wfpad - %s] Exception extracting "
                          "messages from stream: %s", self.end, str(e))

        self.session.lastRcvDownstreamTs = time.time()
        direction = const.IN if self.weAreClient else const.OUT
        for msg in msgs:
            log.debug("[wfpad - %s] A new message has been parsed!", self.end)
            msg.rcvTime = time.time()

            if msg.flags & const.FLAG_CONTROL:
                # Process control messages
                payload = msg.payload
                if len(payload) > 0:
                    self.circuit.upstream.write(payload)
                log.debug("[wfpad - %s] Control flag detected, processing opcode %d.", self.end, msg.opcode)
                self.receiveControlMessage(msg.opcode, msg.args)
                self.session.history.append(
                    (time.time(), const.FLAG_CONTROL, direction, msg.totalLen, len(msg.payload)))

            self.deferBurstPadding('rcv')
            self.session.numMessages['rcv'] += 1
            self.session.totalBytes['rcv'] += msg.totalLen
            log.debug("total bytes and total len of message: %s" % msg.totalLen)

            # Filter padding messages out.
            if msg.flags & const.FLAG_PADDING:
                log.debug("[wfpad - %s] Padding message ignored.", self.end)

                self.session.history.append(
                    (time.time(), const.FLAG_PADDING, direction, msg.totalLen, len(msg.payload)))

            # Forward data to the application.
            elif msg.flags & const.FLAG_DATA:
                log.debug("[wfpad - %s] Data flag detected, relaying upstream", self.end)
                self.session.dataBytes['rcv'] += len(msg.payload)
                self.session.dataMessages['rcv'] += 1

                self.circuit.upstream.write(msg.payload)

                self.session.lastRcvDataDownstreamTs = time.time()
                self.session.history.append(
                    (time.time(), const.FLAG_DATA, direction, msg.totalLen, len(msg.payload)))

                self._past_times.append((msg.queueTime/1000) + time.time())

            # Otherwise, flag not recognized
            else:
                log.error("[wfpad - %s] Invalid message flags: %d.", self.end, msg.flags)
        return msgs

    def onEndPadding(self):
        # on conclusion of tail-padding, signal to the crawler that the
        #   trace is over by severing it's connection to the listener
        if self.weAreClient:
            self._listener.closeCrawler()
        self.session.is_padding = False


class DynaflowClient(DynaflowTransport):

    def __init__(self):
        """Initialize a DynaflowClient object."""
        DynaflowTransport.__init__(self)


class DynaflowServer(DynaflowTransport):

    def __init__(self):
        """Initialize a DynaflowServer object."""
        DynaflowTransport.__init__(self)


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
            log.debug('[wt-listener]: received message')
            if data:
                command = struct.unpack("<i", data[:4])[0]
                log.debug('[wt-listener]: received opcode {}'.format(command))
                if command == const.WT_OP_SESSION_ENDS:
                    log.debug('[wt-listener]: received session end notification from crawler')
                    self._factory._listener.setCrawler(self)
                    self._transport.onSessionEnds(1)
                else:
                    log.debug(f'[wt-listener]: invalid command recieved {command}')

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
        except Exception as e:
            log.exception("[wt-listener - %s] Error when listening on port %d:", self._transport.end, self._port, e)

    def setCrawler(self, connection):
        self._crawler = connection

    def closeCrawler(self):
        if self._crawler:
            self._crawler.transport.loseConnection()
