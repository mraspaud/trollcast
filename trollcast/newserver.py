#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (c) 2014 Martin Raspaud

# Author(s):

#   Martin Raspaud <martin.raspaud@smhi.se>

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""New version of the trollcast server
"""

from ConfigParser import ConfigParser
from zmq import Context, Poller, LINGER, PUB, REP, REQ, POLLIN, NOBLOCK
from threading import Thread, Event, Lock
from posttroll.message import Message
import logging
import time
from datetime import datetime, timedelta
from posttroll import strp_isoformat
from fnmatch import fnmatch
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from urlparse import urlparse
import os
import numpy as np
import random

logger = logging.getLogger(__name__)

class CADU(object):
    @staticmethod
    def is_it(data):
        return False

class HRPT(object):
    dtype = np.dtype([('frame_sync', '>u2', (6, )),
                      ('id', [('id', '>u2'),
                              ('spare', '>u2')]),
                              ('timecode', '>u2', (4, )),
                      ('telemetry', [("ramp_calibration", '>u2', (5, )),
                                     ("PRT", '>u2', (3, )),
                                     ("ch3_patch_temp", '>u2'),
                                     ("spare", '>u2'),]),
                      ('back_scan', '>u2', (10, 3)),
                      ('space_data', '>u2', (10, 5)),
                      ('sync', '>u2'),
                      ('TIP_data', '>u2', (520, )),
                      ('spare', '>u2', (127, )),
                      ('image_data', '>u2', (2048, 5)),
                      ('aux_sync', '>u2', (100, ))])
    
    hrpt_sync = np.array([ 994, 1011, 437, 701, 644, 277, 452, 467, 833, 224,
                           694, 990, 220, 409, 1010, 403, 654, 105, 62, 867,
                           75, 149, 320, 725, 668, 581, 866, 109, 166, 941,
                           1022, 59, 989, 182, 461, 197, 751, 359, 704, 66,
                           387, 238, 850, 746, 473, 573, 282, 6, 212, 169, 623,
                           761, 979, 338, 249, 448, 331, 911, 853, 536, 323,
                           703, 712, 370, 30, 900, 527, 977, 286, 158, 26, 796,
                           705, 100, 432, 515, 633, 77, 65, 489, 186, 101, 406,
                           560, 148, 358, 742, 113, 878, 453, 501, 882, 525,
                           925, 377, 324, 589, 594, 496, 972], dtype=np.uint16)

    hrpt_sync_start = np.array([644, 367, 860, 413, 527, 149], dtype=np.uint16)

    satellites = {7: "NOAA 15",
                  3: "NOAA 16",
                  13: "NOAA 18",
                  15: "NOAA 19"}

    line_size = 11090 * 2

    @staticmethod
    def is_it(data):
        return True

    @staticmethod
    def timecode(tc_array):
        word = tc_array[0]
        day = word
        word = tc_array[1]
        msecs = ((127) & word) * 1024
        word = tc_array[2]
        msecs += word & 1023
        msecs *= 1024
        word = tc_array[3]
        msecs += word & 1023
        return timedelta(days=int(day/2 - 1), milliseconds=int(msecs))

    def read(self, data):
        """Read hrpt data.
        """
        lines = np.fromstring(data, dtype=self.dtype,
                              count=len(data)/self.line_size)
        elts = []

        now = datetime.utcnow()
        year = now.year

        i = 0

        for line in lines:
            days = self.timecode(line["timecode"])
            utctime = datetime(year, 1, 1) + days
            if utctime > now:
                # Can't have data from the future... yet :)
                utctime = datetime(year - 1, 1, 1) + days
                
            if not (np.all(line['aux_sync'] == self.hrpt_sync) and
                    np.all(line['frame_sync'] == self.hrpt_sync_start)):
                logger.info("Garbage line: " + str(utctime))
                continue
            
            satellite = self.satellites[((line["id"]["id"] >> 3) & 15)]

            #elevation = self._orbital.get_observer_look(utctime,
            #                                            *self._coords)[1]
            elevation = random.uniform(0, 90)
            logger.debug("Got line " + utctime.isoformat() + " "
                         + satellite + " "
                         + str(elevation))


            
            # TODO:
            # - serve also already present files
            # - timeout and close the file

            elts.append((satellite, utctime, elevation,
                         data[self.line_size * i: self.line_size * (i+1)]))
            i += 1
            
        return elts, self.line_size * i
        
        
FORMATS = [CADU, HRPT]


class Watcher(Thread):

    """Watch incoming data.
    """

    pass

class FileWatcher(FileSystemEventHandler):
    """Watch files
    """
    def __init__(self, holder, uri):
        FileSystemEventHandler.__init__(self)
        self._holder = holder
        self._uri = uri
        self._loop = True
        self._notifier = Observer()
        self._path, self._pattern = os.path.split(urlparse(self._uri).path)
        self._notifier.schedule(self, self._path, recursive=False)
        self._readers = {}

    def _reader(self, pathname):
        """Read the file
        """
        with open(pathname) as fp_:
            try:
                filereader, position = self._readers[pathname]
                fp_.seek(position)
                data = fp_.read()
                elts, offset = filereader.read(data)
                self._readers[pathname] = filereader, position + offset
                return elts
            except KeyError:
                data = fp_.read()
                for filetype in FORMATS:
                    if filetype.is_it(data):
                        filereader = filetype()
                        elts, position = filereader.read(data)
                        self._readers[pathname] = filereader, position
                        return elts
                
    def start(self):
        self._notifier.start()

    def stop(self):
        self._notifier.stop()

    def on_modified(self, event):
        path, fname = os.path.split(event.src_path)
        del path
        if not fnmatch(fname, self._pattern):
            return
        
        for sat, key, elevation, data in self._reader(event.src_path):
            self._holder.add(sat, key, elevation, data)
        
class DummyWatcher(Watcher):
    """Dummy watcher for test purposes
    """
    def __init__(self, holder, uri):
        Thread.__init__(self)
        self._holder = holder
        self._uri = uri
        self._loop = True
        self._event = Event()

    def run(self):
        while self._loop:
            self._holder.add("NOAA 17", datetime.utcnow(),
                             18, "dummy data")
            self._event.wait(self._uri)
    
    def stop(self):
        """Stop adding stuff
        """
        self._loop = False
        self._event.set()
    
class Holder(object):
    """The mighty data holder
    """
    
    def __init__(self, pub, origin):
        self._data = {}
        self._pub = pub
        self._origin = origin

    def get(self, sat, key):
        """get the value of *sat* and *key*
        """
        return self._data[sat][key]

    def get_data(self, sat, key):
        """get the data of *sat* and *key*
        """
        return self.get(sat, key)[1]

    def add(self, sat, key, elevation, data):
        """Add some data.
        """
        self._data.setdefault(sat, {})[key] = elevation, data
        logger.debug("Got stuff for " + str((sat, key, elevation)))
        self.have(sat, key, elevation)

    def have(self, sat, key, elevation):
        """Tell the world about our new data.
        """
        to_send = {}
        to_send["satellite"] = sat
        to_send["timecode"] = key
        to_send["elevation"] = elevation
        to_send["origin"] = self._origin
        msg = Message(subject, "have", to_send).encode()
        self._pub.send(msg)
        
class Publisher(object):
    """Publish stuff.
    """
    def __init__(self, context, port):
        self._context = context
        self._socket = self._context.socket(PUB)
        self._socket.bind("tcp://*:" + str(port))
        self._lock = Lock()

    def send(self, message):
        """Publish something
        """
        with self._lock:
            self._socket.send(str(message))

    def stop(self):
        """Stop publishing.
        """
        with self._lock:
            self._socket.setsockopt(LINGER, 0)
            self._socket.close()

class Heart(Thread):
    """Send heartbeats once in a while.
    """

    def __init__(self, pub, address, interval):
        Thread.__init__(self)
        self._loop = True
        self._event = Event()
        self._address = address
        self._pub = pub
        self._interval = interval

    def run(self):
        while self._loop:
            to_send = {}
            to_send["next_pass_time"] = "unknown"
            to_send["addr"] = self._address
            msg =  Message(subject, "heartbeat", to_send).encode()
            logger.debug("sending heartbeat: " + str(msg))
            self._pub.send(msg)
            self._event.wait(self._interval)
            
    def stop(self):
        """Cardiac arrest
        """
        self._loop = False
        self._event.set()

class RequestManager(Thread):
    """Manage requests.
    """

    def __init__(self, context, holder, port, station):
        Thread.__init__(self)

        self._holder = holder
        self._loop = True
        self._port = port
        self._station = station
        self._lock = Lock()
        self._socket = context.socket(REP)
        self._socket.bind("tcp://*:" + str(self._port))
        self._poller = Poller()
        self._poller.register(self._socket, POLLIN)
        
    def send(self, message):
        with self._lock:
            if message.binary:
                logger.debug("Response: " + " ".join(str(message).split()[:6]))
            else:
                logger.debug("Response: " + str(message))
            self._socket.send(str(message))

    def pong(self):
        self.send(Message(subject, "pong", {"station": self._station}))

    def scanline(self, message):
        sat = message.data["satellite"]
        key = strp_isoformat(message.data["utctime"])
        try:
            data = self._holder.get_data(sat, key)
        except KeyError:
            resp = Message(subject, "missing")
        else:
            resp = Message(subject, "scanline", data, binary=True)
        self.send(resp)
            
        
    def run(self):
        while self._loop:
            socks = dict(self._poller.poll(timeout=2000)) 
            if self._socket in socks and socks[self._socket] == POLLIN:
                message = Message(rawstr=self._socket.recv(NOBLOCK))
                logger.debug("processing request: " + str(message))
                if message.type == "ping":
                    self.pong()
                if (message.type == "request" and
                    message.data["type"] == "scanline"):
                    self.scanline(message)
                    
            else: # timeout
                pass

    def stop(self):
        """Stop the request manager.
        """
        self._loop = False
        self._socket.setsockopt(LINGER, 0)
        self._socket.close()
        
def serve(configfile):
    """Serve forever.
    """

    context = Context()

    try:
#    while True:
        cfg = ConfigParser()
        cfg.read(configfile)
        
        host = cfg.get("local_reception", "localhost")

        # for messages
        global subject
        station = cfg.get("local_reception", "station")
        subject = '/oper/polar/direct_readout/' + station

        # publisher
        pubport = cfg.getint(host, "pubport")
        pub = Publisher(context, pubport)

        # heart
        hostname = cfg.get(host, "hostname")
        pubaddress = hostname + ":" + str(pubport)
        heart = Heart(pub, pubaddress, 30)
        heart.start()

        # holder
        holder = Holder(pub, pubaddress)

        # watcher
        #watcher = DummyWatcher(holder, 2)
        watcher = FileWatcher(holder, "/tmp/trolltest/*.temp")
        watcher.start()

        # request manager
        reqport = cfg.getint(host, "reqport")
        reqman = RequestManager(context, holder, reqport, station)
        reqman.start()


        while True:
            time.sleep(10000)

    finally:
        reqman.stop()
        watcher.stop()
        heart.stop()
        pub.stop()
        context.term()


        
if __name__ == '__main__':
    import sys
    ch1 = logging.StreamHandler()
    ch1.setLevel(logging.DEBUG)

    formatter = logging.Formatter('[%(levelname)s %(name)s %(asctime)s] '
                                  '%(message)s')
    ch1.setFormatter(formatter)

    logging.getLogger('').setLevel(logging.DEBUG)
    logging.getLogger('').addHandler(ch1)
    logger = logging.getLogger("trollcast_server")

    try:
        serve(sys.argv[1])
    except KeyboardInterrupt:
        print "ok, stopping"
        
    
