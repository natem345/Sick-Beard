import unittest

import sys, os.path
sys.path.append(os.path.abspath('..'))
#sys.path.append(os.path.abspath('../lib'))

from sickbeard import history, common

import sickbeard
from sickbeard import db
#from sickbeard.databases import mainDB
sickbeard.SYS_ENCODING = 'UTF-8'

from sickbeard.common import SNATCHED, Quality
import datetime
dateFormat = "%Y%m%d%H%M%S"

class SearchResults:
    def __init__(self, episodes):
        self.episodes=episodes
        self.quality=4
        self.provider=None
        self.name="searchName"

class Show:
    def __init__(self,tvdbid):
        self.tvdbid = tvdbid
        
        
class Episode:
    def __init__(self, tvdbid, season, episode):
        self.show=Show(tvdbid)
        self.season=season
        self.episode=episode
        self.status="DOWNLOADED"

class HistoryTests(unittest.TestCase):
    
    def test_logSnatch(self):
        myDB = db.DBConnection("../sickbeard.db")
        #res = myDB.select("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
        
        #for r in res:
            #print r["name"]
        
        #print "tables-----------------------"
        searchResult=SearchResults([Episode(12345,2,4),Episode(54321,1,3)])
        history.logSnatch(searchResult)
        
        #check if elements added
        res=myDB.select("SELECT COUNT(*) FROM history WHERE (action=? AND date=? AND showid=? AND season=? AND episode=? AND quality=? AND resource=? AND provider=?)",
                [Quality.compositeStatus(SNATCHED, 4), datetime.datetime.today().strftime(dateFormat), 12345, 2, 4, 4, "searchName", "unknown"])

        self.assertEqual(len(res),1)
        res=myDB.select("SELECT COUNT(*) FROM history WHERE (action=? AND date=? AND showid=? AND season=? AND episode=? AND quality=? AND resource=? AND provider=?)",
                [Quality.compositeStatus(SNATCHED, 4), datetime.datetime.today().strftime(dateFormat), 54321, 1, 3, 4, "searchName", "unknown"])

        self.assertEqual(len(res),1)
        
        #delete just-added elements
        myDB.action("DELETE FROM history WHERE (action=? AND date=? AND showid=? AND season=? AND episode=? AND quality=? AND resource=? AND provider=?)",
                [Quality.compositeStatus(SNATCHED, 4), datetime.datetime.today().strftime(dateFormat), 12345, 2, 4, 4, "searchName", "unknown"])
        myDB.action("DELETE FROM history WHERE (action=? AND date=? AND showid=? AND season=? AND episode=? AND quality=? AND resource=? AND provider=?)",
                [Quality.compositeStatus(SNATCHED, 4), datetime.datetime.today().strftime(dateFormat), 54321, 1, 3, 4, "searchName", "unknown"])
   
    def test_logDownload(self):
        myDB = db.DBConnection("../sickbeard.db")
        
        history.logDownload(Episode(23456,5,11),"dlFilename")
        
        #check if elements added
        res=myDB.select("SELECT COUNT(*) FROM history WHERE (action=? AND date=? AND showid=? AND season=? AND episode=? AND quality=? AND resource=? AND provider=?)",
                ["DOWNLOADED", datetime.datetime.today().strftime(dateFormat), 23456, 5, 11, -1, "dlFilename", -1])

        self.assertEqual(len(res),1)
        
        #delete just-added elements
        myDB.action("DELETE FROM history WHERE (action=? AND date=? AND showid=? AND season=? AND episode=? AND quality=? AND resource=? AND provider=?)",
                ["DOWNLOADED", datetime.datetime.today().strftime(dateFormat), 23456, 5, 11, -1, "dlFilename", -1])
   


if __name__ == '__main__':
    suite = unittest.TestLoader().loadTestsFromTestCase(HistoryTests)
    unittest.TextTestRunner(verbosity=2).run(suite)
