"""
This module implements the BuFLO countermeasure proposed by Dyer et al.
"""
import time

import obfsproxy.common.log as logging
from obfsproxy.transports.wfpadtools import histo
from obfsproxy.transports.wfpadtools import const
from obfsproxy.transports.wfpadtools.wfpad import WFPadTransport

log = logging.get_obfslogger()


class DynaflowTransport(WFPadTransport):
    """Implementation of the BuFLO countermeasure.

    It extends the BasePadder by choosing a constant probability distribution
    for time, and a constant probability distribution for packet lengths. The
    minimum time for which the link will be padded is also specified.
    """

    def __init__(self):
        super(DynaflowTransport, self).__init__()

        # Defaults for Dynaflow specifications.
        self._mintime = -1
        self._first_time_gap = 12
        self._poss_time_gaps = [12, 5]
        self._block_size = 400
        self._subseq_length = 4
        self._memory = 100
        self._length = const.MPU
        self._curr_time = time.time()
        self._no_sent = 0
        self._no_recv = 0

        # Set constant length for messages
        self._lengthDataProbdist = histo.uniform(self._length)

        # The stop condition in BuFLO:
        # BuFLO stops padding if the visit has finished and the
        # elapsed time has exceeded the minimum padding time.
        def stopConditionHandler(s):
            elapsed = s.getElapsed()
            log.debug("[buflo {}] - elapsed = {}, mintime = {}, visiting = {}"
                      .format(self.end, elapsed, s._mintime, s.isVisiting()))
            return elapsed > s._mintime and not s.isVisiting()

        self.stopCondition = stopConditionHandler

    @classmethod
    def register_external_mode_cli(cls, subparser):
        """Register CLI arguments for BuFLO parameters."""
        subparser.add_argument("--period",
                               required=False,
                               type=float,
                               help="Time rate at which transport sends "
                                    "messages (Default: 12ms).",
                               dest="period")
        subparser.add_argument("--psize",
                               required=False,
                               type=int,
                               help="Length of messages to be transmitted"
                                    " (Default: MTU).",
                               dest="psize")
        subparser.add_argument("--mintime",
                               required=False,
                               type=int,
                               help="Minimum padding time per visit."
                                    " (Default: no minimum time).",
                               dest="mintime")

        super(BuFLOTransport, cls).register_external_mode_cli(subparser)

    @classmethod
    def validate_external_mode_cli(cls, args):
        """Assign the given command line arguments to local variables.

        BuFLO pads at a constant rate `period` and pads the packets to a
        constant size `psize`.
        """
        super(BuFLOTransport, cls).validate_external_mode_cli(args)

        if args.mintime:
            cls._mintime = int(args.mintime)
        if args.period:
            cls._period = args.period
        if args.psize:
            cls._length = args.psize

    def _find_new_time_gap(past_times, curr_time, time_gap, poss_time_gaps, memory, block_size):
        """Finds new time gap for defended sequence."""
    
        # find average time gap
        if len(self._past_times) >= self._memory:
            average_time_gap = float(self._past_times[-1] - self._past_times[-self._memory]) / (self._memory - 1)
        elif len(self._past_times) > 10:
            average_time_gap = float(self._past_times[-1] - self._past_times[0]) / (len(self._past_times) - 1)
        else:
            average_time_gap = self.time_gap
    
        # find expected time gap
        exp_packet_num = self._block_size + 1 * float(self._curr_time - self._past_times[-1]) / average_time_gap
        exp_time_gap = self._block_size / exp_packet_num * average_time_gap
    
        # choose next timeg gap
        min_diff = 99999
        for i in range(0, len(self._poss_time_gaps)):
            if min_diff > abs(exp_time_gap - self._poss_time_gaps[i]):
                min_diff = abs(exp_time_gap - self._poss_time_gaps[i])
            else:
                self.time_gap = self._poss_time_gaps[i - 1]
                return
        self.time_gap = self._poss_time_gaps[-1]

    def _configure_padding(self):
        if self.weAreClient:
            period = self.time_gap * self._subseq_length
        else:
            period = (self._time_gap * self._subseq_length) / (self._subseq_length - 1)
        self.constantRatePaddingDistrib(period)

    def onSessionStarts(self, sessId):
        #log.debug("[dynaflow {}] - params: mintime={}, period={}, psize={}"
        #          .format(self.end, self._mintime, self._period, self._length))
        self._configure_padding()
        WFPadTransport.onSessionStarts(self, sessId)

    def whenReceivedUpstream(self, data):
        """count number of packets sent upstream"""
        if self.weAreClient:
            if self._no_sent * self._subseq_length in self._switch_sizes:
                self._find_new_time_gap()
                self._configure_padding()
        self._no_sent += 1
        self._past_times.append(time.time())

    def whenReceivedDownstream(self, data):
        """count number of packets recieved downstream"""
        if not self.weAreClient:
            if (self._no_sent//(self._subseq_length-1)) * self._subseq_length in self._switch_sizes:
                self._find_new_time_gap()
                self._configure_padding()
        self._no_recv += 1
        self._past_times.append(time.time())

    def sendDataMessage(self, payload="", paddingLen=0):
        """Send data message."""
        super(DynaflowTransport, self).sendDataMessage(payload, paddingLen)
        self._curr_time = time.time()
        


class DynaflowClient(DynaflowTransport):

    def __init__(self):
        """Initialize a BuFLOClient object."""
        DynaflowTransport.__init__(self)


class DynaflowServer(DynaflowTransport):

    def __init__(self):
        """Initialize a BuFLOServer object."""
        DynaflowTransport.__init__(self)
