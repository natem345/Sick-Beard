import sys


import locale
import os
import threading
import time
import signal
import traceback
import getopt

import sickbeard

from sickbeard import db
from sickbeard.tv import TVShow
from sickbeard import logger
from sickbeard.version import SICKBEARD_VERSION

from sickbeard.webserveInit import initWebServer

from lib.configobj import ConfigObj

myDB = db.DBConnection()
sqlResults = myDB.select("SELECT * FROM tv_shows")

for sqlShow in sqlResults:
    print "-----------------------"
    print sqlShow["show_name"]
    print "-----------------------"
    '''
    try:
        curShow = TVShow(int(sqlShow["tvdb_id"]))
        sickbeard.showList.append(curShow)
    except Exception, e:
        logger.log(u"There was an error creating the show in "+sqlShow["location"]+": "+str(e).decode('utf-8'), logger.ERROR)
        logger.log(traceback.format_exc(), logger.DEBUG)
    '''

    #TODO: make it update the existing shows if the showlist has something in it